"""Resolve everything that can move into a lock file, and check for drift.

A lock pins: engine version, container image (id + digest), CLI version, model IDs,
each contender's source commit and materialized tree hash, each arena's commit,
task prompt hashes and the findings schema hash.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from skillordeal import __version__, gitsrc
from skillordeal.config import (
    Arena,
    Contender,
    ContenderKind,
    LoadedTrial,
    RuntimeConfig,
)
from skillordeal.hashing import file_sha256, tree_hash
from skillordeal.schemas import FINDINGS_SCHEMA_PATH
from skillordeal.yamlio import dump_yaml, load_yaml, sha256_obj, sha256_text

LOCK_VERSION = 1
WRAPPER_PLUGIN = "ordeal"


class LockError(RuntimeError):
    pass


# --- contender materialization -------------------------------------------------


def _frontmatter(text: str) -> dict[str, Any]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    return load_yaml(m.group(1)) if m else {}


def _strip(root: Path, globs: list[str]) -> list[str]:
    removed = []
    for g in globs:
        for p in sorted(root.glob(g), reverse=True):
            removed.append(p.relative_to(root).as_posix())
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    return removed


def _plugin_skills(plugin_dir: Path) -> tuple[str, list[str]]:
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    name = json.loads(manifest.read_text())["name"] if manifest.exists() else plugin_dir.name
    skills = sorted(p.parent.name for p in (plugin_dir / "skills").glob("*/SKILL.md"))
    return name, skills


def materialize(c: Contender, sha: str | None, cache_dir: Path, dest: Path) -> dict[str, Any]:
    """Build the context dir a bout mounts at /ctx. Returns facts for the lock.

    Layout: dest/plugins/<plugin>/. Skills and prompts get a generated `ordeal` plugin.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if c.kind == ContenderKind.baseline:
        return {"skill_name": None, "expected_skills": [], "tree_hash": tree_hash(dest)}

    raw = dest.parent / f"{dest.name}.src"
    if c.repo:
        assert sha
        gitsrc.export(cache_dir, c.repo, sha, c.subpath, raw)
    else:
        src = Path(c.path).expanduser().resolve() / c.subpath  # type: ignore[arg-type]
        if raw.exists():
            shutil.rmtree(raw)
        if src.is_file():
            raw.mkdir(parents=True)
            shutil.copy2(src, raw / src.name)
        else:
            shutil.copytree(src, raw, symlinks=True)
    removed = _strip(raw, c.strip)

    facts: dict[str, Any] = {"stripped": removed}
    if c.kind == ContenderKind.plugin:
        pname, skills = _plugin_skills(raw)
        (dest / "plugins").mkdir()
        shutil.move(str(raw), dest / "plugins" / pname)
        name = c.skill_name or (f"{pname}:{skills[0]}" if skills else pname)
        facts |= {
            "skill_name": name,
            "plugin_name": pname,
            "expected_skills": [f"{pname}:{s}" for s in skills],
        }
    else:
        # Skills and prompts are wrapped in a neutral plugin so delivery is identical in every
        # auth mode (--add-dir skills are ignored once setting sources are disabled).
        sdir_root = dest / "plugins" / WRAPPER_PLUGIN
        (sdir_root / ".claude-plugin").mkdir(parents=True)
        (sdir_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": WRAPPER_PLUGIN,
                    "version": "0.0.0",
                    "description": "skillordeal contender wrapper",
                }
            )
            + "\n"
        )
        if c.kind == ContenderKind.skill:
            skill_md = raw / "SKILL.md"
            if not skill_md.exists():
                raise LockError(f"contender {c.id}: no SKILL.md in {c.repo or c.path}:{c.subpath}")
            name = c.skill_name or _frontmatter(skill_md.read_text()).get("name") or c.id
            (sdir_root / "skills").mkdir()
            shutil.move(str(raw), sdir_root / "skills" / name)
        else:  # prompt
            files = list(raw.rglob("*.md"))
            if len(files) != 1:
                raise LockError(f"contender {c.id}: prompt kind needs exactly one .md file")
            body = files[0].read_text()
            fm = _frontmatter(body)
            body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", body, count=1, flags=re.S)
            name = c.skill_name or c.id
            desc = fm.get("description") or f"Wrapped prompt from {c.repo or c.path}:{c.subpath}"
            sdir = sdir_root / "skills" / name
            sdir.mkdir(parents=True)
            (sdir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {json.dumps(str(desc))}\n---\n\n{body.lstrip()}"
            )
            shutil.rmtree(raw)
            facts["wrapped"] = True
        facts |= {
            "skill_name": f"{WRAPPER_PLUGIN}:{name}",
            "plugin_name": WRAPPER_PLUGIN,
            "expected_skills": [f"{WRAPPER_PLUGIN}:{name}"],
        }
    facts["tree_hash"] = tree_hash(dest)
    return facts


# --- image -----------------------------------------------------------------------


def image_ref(rt: RuntimeConfig) -> str:
    return f"{rt.image.name}:{rt.image.tag}"


def inspect_image(ref: str) -> dict[str, str]:
    proc = subprocess.run(
        ["podman", "image", "inspect", "--format", "{{.Id}}|{{.Digest}}", ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise LockError(f"image {ref} not found; run `just image` first ({proc.stderr.strip()})")
    image_id, digest = proc.stdout.strip().split("|")
    return {"ref": ref, "id": image_id, "digest": digest}


def image_cli_version(ref: str) -> str:
    proc = subprocess.run(
        ["podman", "run", "--rm", "--network=none", ref, "claude", "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    m = re.search(r"(\d+\.\d+\.\d+)", proc.stdout)
    if not m:
        raise LockError(f"could not read CLI version from image {ref}: {proc.stderr.strip()}")
    return m.group(1)


def probe_builtins(ref: str, auth_mode: str) -> dict[str, list[str]]:
    """Skills/plugins the CLI loads on its own (e.g. `doctor`), found offline.

    Runs the image with no network and a dummy token; the init event is emitted before any
    API call, so this costs nothing. The isolation gate subtracts these from what it sees.
    """
    env_name = "ANTHROPIC_API_KEY" if auth_mode == "api_key" else "CLAUDE_CODE_OAUTH_TOKEN"
    iso = ["--bare"] if auth_mode == "api_key" else ["--setting-sources", ""]
    cmd = [
        "podman",
        "run",
        "--rm",
        "-i",
        "--network=none",
        "--userns=keep-id",
        "-e",
        f"{env_name}=sk-ant-probe-000000000000",
        "-e",
        "CLAUDE_CONFIG_DIR=/tmp/cfg",
        "-e",
        "HOME=/tmp",
        ref,
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--settings",
        '{"disableBundledSkills":true}',
        "--strict-mcp-config",
        "--tools",
        "Read,Skill",
        *iso,
    ]
    name = f"so-probe-{os.getpid()}"
    cmd[3:3] = ["--name", name]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdin and proc.stdout
    proc.stdin.write("probe")
    proc.stdin.close()
    timer = threading.Timer(
        120, lambda: subprocess.run(["podman", "kill", name], capture_output=True)
    )
    timer.start()
    try:
        for line in proc.stdout:  # init comes first; offline the CLI then retries forever
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                skills = sorted(
                    x if isinstance(x, str) else x.get("name") for x in ev.get("skills") or []
                )
                plugins = sorted(
                    p.get("name") if isinstance(p, dict) else p for p in ev.get("plugins") or []
                )
                return {"skills": skills, "plugins": plugins}
    finally:
        timer.cancel()
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)
        proc.wait(timeout=30)
    raise LockError("builtin probe got no init event from the image")


# --- lock --------------------------------------------------------------------------


def _arena_facts(
    a: Arena, arena_file: Path, cache_dir: Path, pinned_sha: str | None = None
) -> dict[str, Any]:
    sha = pinned_sha or gitsrc.resolve(cache_dir, a.repo, a.ref)
    gt = None
    if a.groundtruth:
        gpath = (arena_file.parent / a.groundtruth).resolve()
        gt = {"file": a.groundtruth, "sha256": file_sha256(gpath)}
    return {
        "repo": a.repo,
        "ref": a.ref,
        "sha": sha,
        "config_hash": sha256_obj(a.model_dump(mode="json")),
        "groundtruth": gt,
    }


def _pin(pins: dict[str, Any] | None, section: str, key: str, config_hash: str) -> str | None:
    """Reuse the locked sha when the entry's config is unchanged, so a moving branch isn't drift."""
    if not pins:
        return None
    entry = pins.get(section, {}).get(key)
    if entry and entry.get("config_hash") == config_hash:
        return entry.get("sha")
    return None


def build_lock(
    lt: LoadedTrial, *, skip_image: bool = False, pins: dict[str, Any] | None = None
) -> dict[str, Any]:
    rt = lt.trial.runtime
    cache_dir = Path(rt.cache_dir).expanduser()
    tmp = tempfile.TemporaryDirectory(prefix="skillordeal-lock-")
    ctx_root = Path(tmp.name)

    image: dict[str, Any]
    if skip_image:
        image = {
            "ref": image_ref(rt),
            "id": None,
            "digest": None,
            "cli_version": rt.cli_version,
            "builtins": {"skills": [], "plugins": []},
        }
    else:
        image = inspect_image(image_ref(rt)) | {"cli_version": image_cli_version(image_ref(rt))}
        if image["cli_version"] != rt.cli_version:
            raise LockError(
                f"image has CLI {image['cli_version']}, runtime pins {rt.cli_version}; "
                "rebuild the image"
            )
        image["builtins"] = probe_builtins(image_ref(rt), rt.auth.mode.value)

    contenders: dict[str, Any] = {}
    for c in lt.contenders:
        chash = sha256_obj(c.model_dump(mode="json"))
        sha = None
        if c.repo:
            sha = _pin(pins, "contenders", c.id, chash) or gitsrc.resolve(cache_dir, c.repo, c.ref)
        local = None
        if c.path:
            local = _local_git_state(Path(c.path).expanduser())
        facts = materialize(c, sha, cache_dir, ctx_root / c.id)
        contenders[c.id] = {
            "kind": c.kind.value,
            "repo": c.repo,
            "ref": c.ref,
            "sha": sha,
            "path": c.path,
            "local_git": local,
            "subpath": c.subpath,
            "config_hash": chash,
            **facts,
        }

    arenas = {
        a.id: _arena_facts(
            a,
            lt.arena_files[a.id],
            cache_dir,
            _pin(pins, "arenas", a.id, sha256_obj(a.model_dump(mode="json"))),
        )
        for a in lt.arenas
    }
    tasks = {
        t.id: {
            "prompt_sha256": sha256_text(lt.prompts[t.id]),
            "config_hash": sha256_obj(t.model_dump(mode="json")),
        }
        for t in lt.trial.tasks
    }
    body = {
        "lock_version": LOCK_VERSION,
        "trial": lt.trial.id,
        "trial_hash": sha256_obj(lt.trial.model_dump(mode="json")),
        "engine": {"name": "skillordeal", "version": __version__},
        "image": image,
        "runtime": {
            "cli_name": rt.cli_name,
            "cli_version": rt.cli_version,
            "auth_mode": rt.auth.mode.value,
            "limits": rt.limits.model_dump(mode="json"),
        },
        "models": [m.model_dump(mode="json") for m in lt.trial.models],
        "judge": lt.trial.judge.model_dump(mode="json") if lt.trial.judge else None,
        "reps": lt.trial.reps,
        "invocation": lt.trial.invocation.value,
        "findings_schema_sha256": file_sha256(FINDINGS_SCHEMA_PATH),
        "contenders": contenders,
        "arenas": arenas,
        "tasks": tasks,
    }
    tmp.cleanup()
    return {
        **body,
        "lock_hash": sha256_obj(body),
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }


def _local_git_state(path: Path) -> dict[str, Any] | None:
    try:
        top = gitsrc._git("rev-parse", "--show-toplevel", cwd=path)
        head = gitsrc._git("rev-parse", "HEAD", cwd=path)
        dirty = bool(gitsrc._git("status", "--porcelain", "--", str(path), cwd=path))
        return {"toplevel": top, "head": head, "dirty": dirty}
    except gitsrc.GitError:
        return None


def write_lock(lock: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Generated by `skillordeal lock`. Do not edit by hand.\n" + dump_yaml(lock))


def read_lock(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LockError(f"lock file {path} not found; run `skillordeal lock` first")
    return load_yaml(path.read_text())


def check_drift(lock: dict[str, Any], lt: LoadedTrial, *, skip_image: bool = False) -> list[str]:
    """Return human-readable drift problems. Empty list means the lock still holds."""
    fresh = build_lock(lt, skip_image=skip_image, pins=lock)
    problems = []
    if fresh["engine"]["version"] != lock["engine"]["version"]:
        problems.append(
            f"engine {fresh['engine']['version']} != locked {lock['engine']['version']}"
        )
    if not skip_image:
        for k in ("id", "cli_version", "builtins"):
            if fresh["image"][k] != lock["image"][k]:
                problems.append(f"image {k} {fresh['image'][k]} != locked {lock['image'][k]}")
    for key in ("trial_hash", "findings_schema_sha256"):
        if fresh[key] != lock[key]:
            problems.append(f"{key} changed")
    for section, fields in (
        ("contenders", ("sha", "tree_hash", "config_hash")),
        ("arenas", ("sha", "config_hash", "groundtruth")),
        ("tasks", ("prompt_sha256", "config_hash")),
    ):
        old, new = lock.get(section, {}), fresh[section]
        for k in sorted(set(old) | set(new)):
            if k not in old or k not in new:
                problems.append(f"{section}.{k} added or removed")
                continue
            for f in fields:
                if old[k].get(f) != new[k].get(f):
                    problems.append(f"{section}.{k}.{f} changed")
    return problems
