"""Run one bout: prepare arena + context, start the container, monitor, collect."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import zstandard

from skillordeal import __version__, gitsrc
from skillordeal.adapters import claude_code as cc
from skillordeal.config import (
    CONTEXT_FILES,
    Arena,
    AuthMode,
    Contender,
    LoadedTrial,
    ModelSpec,
    Task,
)
from skillordeal.lock import image_ref, materialize
from skillordeal.matrix import BoutKey
from skillordeal.monitor import Monitor
from skillordeal.schemas import load as load_schema
from skillordeal.secrets import Credentials, Scrubber

RECORD_VERSION = 1
# Outcomes that are results in their own right; `error` bouts get retried on resume.
FINAL_STATUSES = {"ok", "invalid", "schema_violation", "limit_exceeded", "timeout"}


class LimitWatch:
    """Tracks tokens and turns from the live stream and says when a ceiling is crossed."""

    def __init__(self, max_total_tokens: int | None, max_turns: int | None):
        self.max_total_tokens, self.max_turns = max_total_tokens, max_turns
        self.usage: dict[str, int] = {}  # message id -> tokens (a message spans several events)
        self.tripped: str | None = None

    def feed(self, line: str) -> str | None:
        if self.tripped or '"assistant"' not in line:
            return self.tripped
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return None
        msg = ev.get("message") or {}
        if ev.get("type") != "assistant" or not msg.get("id"):
            return None
        u = msg.get("usage") or {}
        self.usage[msg["id"]] = sum(
            int(u.get(k) or 0)
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        )
        total = sum(self.usage.values())
        if self.max_total_tokens and total > self.max_total_tokens:
            self.tripped = f"max_total_tokens {self.max_total_tokens} (seen {total})"
        elif self.max_turns and len(self.usage) > self.max_turns:
            self.tripped = f"max_turns {self.max_turns}"
        return self.tripped


class BoutError(RuntimeError):
    pass


def is_complete(out_dir: Path) -> bool:
    rec = out_dir / "record.json"
    if not rec.exists():
        return False
    try:
        return json.loads(rec.read_text()).get("status") in FINAL_STATUSES
    except json.JSONDecodeError:
        return False


# --- arena preparation -----------------------------------------------------------


def prepare_arena(arena: Arena, sha: str, cache_dir: Path, dest: Path) -> dict[str, Any]:
    gitsrc.export(cache_dir, arena.repo, sha, "", dest)
    removed = []
    for pattern in [*CONTEXT_FILES, *arena.strip]:
        # Bare names match at any depth, paths with a slash match from the root.
        globs = (
            [pattern]
            if "/" in pattern.rstrip("/")
            else [pattern.rstrip("/"), f"**/{pattern.rstrip('/')}"]
        )
        for g in globs:
            for p in sorted(dest.glob(g), reverse=True):
                if not p.exists() and not p.is_symlink():
                    continue
                removed.append(p.relative_to(dest).as_posix())
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    leftovers = [
        p.relative_to(dest).as_posix()
        for name in ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md", ".claude")
        for p in dest.rglob(name)
    ]
    files = sum(1 for p in dest.rglob("*") if p.is_file())
    return {"removed": sorted(set(removed)), "leftover_context": leftovers, "files": files}


def render_prompt(template: str, arena: Arena) -> str:
    """Fill {arena}, {language} and {scope}. Plain replace, so code braces in prompts survive."""
    values = {
        "{arena}": arena.id,
        "{language}": arena.language,
        "{scope}": ", ".join(arena.scope) if arena.scope else "the whole repository",
    }
    for k, v in values.items():
        template = template.replace(k, v)
    return template


# --- podman --------------------------------------------------------------------------


def podman_create_args(
    name: str,
    image: str,
    rt_limits: Any,
    creds: Credentials,
    arena_dir: Path,
    ctx_dir: Path,
    cfg_dir: Path,
    command: list[str],
) -> list[str]:
    args = [
        "podman",
        "create",
        "--name",
        name,
        "--interactive",
        "--userns=keep-id",
        "--read-only",
        "--read-only-tmpfs=false",
        "--tmpfs",
        "/tmp:rw,size=1g,mode=1777",
        "--tmpfs",
        "/home/runner:rw,size=512m,mode=0700,U",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--cpus",
        str(rt_limits.cpus),
        "--memory",
        rt_limits.memory,
        "--pids-limit",
        str(rt_limits.pids),
        "--workdir",
        cc.ARENA,
        "-v",
        f"{arena_dir}:{cc.ARENA}:ro",
        "-v",
        f"{ctx_dir}:{cc.CTX}:ro",
        "-v",
        f"{cfg_dir}:/cfg:rw",
        "-e",
        "HOME=/home/runner",
        "-e",
        "CLAUDE_CONFIG_DIR=/cfg",
        "-e",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY=1",
        "-e",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
        "-e",
        "DISABLE_AUTOUPDATER=1",
        "-e",
        "DISABLE_TELEMETRY=1",
        "-e",
        "DISABLE_ERROR_REPORTING=1",
        "-e",
        "NO_COLOR=1",
    ]
    if rt_limits.max_output_tokens:
        args += ["-e", f"CLAUDE_CODE_MAX_OUTPUT_TOKENS={rt_limits.max_output_tokens}"]
    for var in creds.env:  # by NAME only; value comes from the podman process env
        args += ["-e", var]
    return [*args, image, *command]


def _podman(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["podman", *args], capture_output=True, text=True, check=check)


# --- the bout ------------------------------------------------------------------------


def run_bout(
    key: BoutKey,
    lt: LoadedTrial,
    lock: dict[str, Any],
    creds: Credentials,
    out_dir: Path,
    *,
    keep_work: bool = False,
    log: Any = print,
) -> dict[str, Any]:
    rt = lt.trial.runtime
    cache_dir = Path(rt.cache_dir).expanduser()
    contender: Contender = next(c for c in lt.contenders if c.id == key.contender)
    arena: Arena = next(a for a in lt.arenas if a.id == key.arena)
    task: Task = next(t for t in lt.trial.tasks if t.id == key.task)
    model: ModelSpec = next(m for m in lt.trial.models if m.slug == key.model)
    lc, la = lock["contenders"][contender.id], lock["arenas"][arena.id]

    work = Path(rt.work_dir).resolve() / key.bout_id
    if work.exists():
        shutil.rmtree(work)
    arena_dir, ctx_dir, cfg_dir = work / "arena", work / "ctx", work / "cfg"
    cfg_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    scrub = Scrubber(creds.secret_values)
    started_at = dt.datetime.now(dt.UTC)

    record: dict[str, Any] = {
        "record_version": RECORD_VERSION,
        "bout_id": key.bout_id,
        "trial": lt.trial.id,
        "lock_hash": lock["lock_hash"],
        "contender": {
            "id": contender.id,
            "kind": contender.kind.value,
            "sha": lc.get("sha"),
            "tree_hash": lc["tree_hash"],
            "skill_name": lc.get("skill_name"),
            "role": contender.role,
        },
        "arena": {"id": arena.id, "sha": la["sha"], "language": arena.language},
        "task": {"id": task.id, "prompt_sha256": lock["tasks"][task.id]["prompt_sha256"]},
        "model": {"requested": model.id, "effort": model.effort.value if model.effort else None},
        "rep": key.rep,
        "invocation": lock["invocation"],
        "versions": {
            "engine": __version__,
            "cli": lock["image"]["cli_version"],
            "image_id": lock["image"].get("id"),
            "image_ref": lock["image"]["ref"],
        },
        "auth_mode": creds.mode.value,
        "limits": rt.limits.model_dump(mode="json"),
        # total_cost_usd is computed client-side by the CLI; under OAuth it is notional.
        "cost_is_estimate": True,
        "started_at": started_at.isoformat(timespec="seconds"),
    }

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        record.update(extra)
        record["status"] = status
        record["finished_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        record["wall_s"] = round((dt.datetime.now(dt.UTC) - started_at).total_seconds(), 3)
        (out_dir / "record.json").write_text(
            scrub(json.dumps(record, indent=2, default=str)) + "\n"
        )
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
        return record

    # 1. context dir, verified against the lock
    facts = materialize(contender, lc.get("sha"), cache_dir, ctx_dir)
    if facts["tree_hash"] != lc["tree_hash"]:
        return finish("error", error="contender-drift: tree hash differs from lock")

    # 2. arena
    prep = prepare_arena(arena, la["sha"], cache_dir, arena_dir)
    record["arena"]["prepared"] = {k: prep[k] for k in ("files", "removed")}
    if prep["leftover_context"]:
        return finish(
            "invalid", invalid_reasons=[f"arena-context-file: {prep['leftover_context']}"]
        )

    # 3. credentials
    if creds.mode == AuthMode.credentials_file and creds.credentials_file:
        shutil.copy2(creds.credentials_file, cfg_dir / ".credentials.json")
        os.chmod(cfg_dir / ".credentials.json", 0o600)

    # 4. command
    spec = cc.BoutSpec(
        contender=contender,
        skill_name=lc.get("skill_name"),
        plugin_name=lc.get("plugin_name"),
        expected_skills=lc.get("expected_skills", []),
        model=model,
        task=task,
        invocation=lt.trial.invocation,
        auth_mode=creds.mode,
        budget_usd=arena.budget_usd or model.max_budget_usd,
        builtin_skills=tuple(lock["image"].get("builtins", {}).get("skills", [])),
        builtin_plugins=tuple(lock["image"].get("builtins", {}).get("plugins", [])),
    )
    task_prompt = render_prompt(lt.prompts[task.id], arena)
    prompt = cc.build_prompt(spec, task_prompt)
    (out_dir / "prompt.md").write_text(prompt)
    command = cc.build_command(spec, load_schema("findings"))
    record["command"] = [scrub(a) for a in command]

    name = f"so-{key.bout_id}"
    _podman("rm", "-f", name)
    create = podman_create_args(
        name, image_ref(rt), rt.limits, creds, arena_dir, ctx_dir, cfg_dir, command
    )
    env = {**os.environ, **creds.env}
    proc = subprocess.run(create, capture_output=True, text=True, env=env, check=False)
    if proc.returncode != 0:
        return finish("error", error=f"podman create failed: {scrub(proc.stderr.strip())}")

    # 5. run with monitor
    transcript_lines: list[str] = []
    res_file = (out_dir / "resources.jsonl").open("w")
    mon = Monitor(container=name, out=res_file, interval=rt.sample_interval_s)
    mon.start()
    t0 = time.monotonic()
    run = subprocess.Popen(
        ["podman", "start", "--attach", "--interactive", name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    timed_out = threading.Event()

    def killer() -> None:
        if run.poll() is None:
            timed_out.set()
            _podman("kill", name)

    timer = threading.Timer(rt.limits.timeout_s, killer)
    timer.start()
    stderr_chunks: list[str] = []
    err_reader = threading.Thread(
        target=lambda: stderr_chunks.append(run.stderr.read()), daemon=True
    )  # type: ignore[union-attr]
    err_reader.start()
    assert run.stdin and run.stdout
    run.stdin.write(prompt)
    run.stdin.close()
    watch = LimitWatch(rt.limits.max_total_tokens, rt.limits.max_turns)
    for line in run.stdout:
        transcript_lines.append(line)
        if watch.feed(line) and not timed_out.is_set():
            _podman("kill", name)  # keep reading so the partial transcript is kept
    exit_code = run.wait()
    timer.cancel()
    err_reader.join(timeout=5)
    wall = time.monotonic() - t0
    mon.stop()
    res_file.close()
    inspect = _podman("inspect", "--format", "{{.State.OOMKilled}}", name).stdout.strip()
    _podman("rm", "-f", name)

    # 6. artifacts (scrubbed)
    scrubbed = [scrub(line) for line in transcript_lines]
    cctx = zstandard.ZstdCompressor(level=12)
    (out_dir / "transcript.jsonl.zst").write_bytes(cctx.compress("".join(scrubbed).encode()))
    (out_dir / "stderr.log").write_text(scrub("".join(stderr_chunks)))

    ps = cc.parse_stream(scrubbed)
    usage = cc.usage_summary(ps.result)
    findings = cc.structured_output(ps.result)
    record.update(
        {
            "exit_code": exit_code,
            "oom_killed": inspect == "true",
            "container_wall_s": round(wall, 3),
            "init": {
                "model": (ps.init or {}).get("model"),
                "skills": cc._skill_names(ps.init or {}),
                "builtin_skills": list(spec.builtin_skills),
                "mcp_servers": (ps.init or {}).get("mcp_servers"),
                "tools": (ps.init or {}).get("tools"),
                "cli_version": (ps.init or {}).get("claude_code_version"),
            },
            "usage": usage,
            "model": {**record["model"], "used": sorted((usage.get("per_model") or {}).keys())},
            "tool_calls": dict(ps.tool_calls),
            # auto mode: did Claude call the Skill tool? forced mode: injected by the slash command.
            "skill_fired": (
                None
                if not spec.skill_name
                else spec.invocation.value == "forced" or bool(ps.skills_invoked)
            ),
            "first_turn_prompt_tokens": ps.first_turn_prompt_tokens,
            "skills_invoked": ps.skills_invoked,
            "events": ps.events,
            "resources": mon.summary(),
        }
    )

    if timed_out.is_set():
        return finish("timeout", error=f"exceeded {rt.limits.timeout_s}s")
    if watch.tripped:
        return finish("limit_exceeded", error=f"limit hit: {watch.tripped}")
    problems = cc.isolation_problems(ps, spec)
    if problems:
        return finish("invalid", invalid_reasons=problems)
    if ps.result is None or usage.get("is_error"):
        reason = "; ".join(ps.api_errors) or usage.get("terminal_reason") or "no result event"
        return finish("error", error=f"agent error: {reason}")
    if not isinstance(findings, dict) or not isinstance(findings.get("findings"), list):
        (out_dir / "findings.json").write_text(
            json.dumps({"raw_result": scrub(str(ps.result.get("result")))}, indent=2)
        )
        return finish("schema_violation", findings_count=0)
    (out_dir / "findings.json").write_text(scrub(json.dumps(findings, indent=2)) + "\n")
    return finish("ok", findings_count=len(findings["findings"]))
