"""Run one bout: prepare arena + context, start the container, monitor, collect."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zstandard

from skillordeal import __version__, gitsrc
from skillordeal.adapters import claude_code as cc
from skillordeal.blind import Blinder, blind_terms
from skillordeal.config import (
    CONTEXT_FILES,
    Arena,
    AuthMode,
    Contender,
    ContenderKind,
    LoadedTrial,
    ModelSpec,
    Task,
)
from skillordeal.egress import Egress, EgressError
from skillordeal.lock import image_ref, materialize
from skillordeal.matrix import BoutKey
from skillordeal.monitor import Monitor
from skillordeal.schemas import load as load_schema
from skillordeal.secrets import Credentials, Scrubber
from skillordeal.verify import render_verify_prompt, verify_template

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
    extra: list[str] | None = None,
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
    args += extra or []  # network / egress proxy settings
    for var in creds.env:  # by NAME only; value comes from the podman process env
        args += ["-e", var]
    return [*args, image, *command]


def _podman(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["podman", *args], capture_output=True, text=True, check=check)


# --- container runner ------------------------------------------------------------------


@dataclass
class ContainerRun:
    lines: list[str]
    stderr: str
    exit_code: int
    timed_out: bool = False
    oom_killed: bool = False
    wall_s: float = 0.0
    resources: dict[str, Any] = field(default_factory=dict)
    create_error: str | None = None  # `podman create` failed; nothing ran


@dataclass
class Launch:
    """Everything a runner needs to start one agent container and stream its output."""

    name: str
    create: list[str]
    env: dict[str, str]
    prompt: str
    timeout_s: int
    max_total_tokens: int | None
    max_turns: int | None
    resources: Path  # where the 1 s resource samples go
    sample_interval_s: float


# Launch -> ContainerRun. Tests swap in a fake; the real one drives podman.
BoutRunner = Callable[[Launch], ContainerRun]


def podman_bout_runner(launch: Launch) -> ContainerRun:
    name = launch.name
    _podman("rm", "-f", name)
    proc = subprocess.run(
        launch.create, capture_output=True, text=True, env=launch.env, check=False
    )
    if proc.returncode != 0:
        return ContainerRun([], proc.stderr, proc.returncode, create_error=proc.stderr.strip())

    lines: list[str] = []
    res_file = launch.resources.open("w")
    mon = Monitor(container=name, out=res_file, interval=launch.sample_interval_s)
    mon.start()
    t0 = time.monotonic()
    run = subprocess.Popen(
        ["podman", "start", "--attach", "--interactive", name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=launch.env,
    )
    timed_out = threading.Event()

    def killer() -> None:
        if run.poll() is None:
            timed_out.set()
            _podman("kill", name)

    timer = threading.Timer(launch.timeout_s, killer)
    timer.start()
    stderr_chunks: list[str] = []
    err_reader = threading.Thread(
        target=lambda: stderr_chunks.append(run.stderr.read()), daemon=True
    )  # type: ignore[union-attr]
    err_reader.start()
    assert run.stdin and run.stdout
    run.stdin.write(launch.prompt)
    run.stdin.close()
    watch = LimitWatch(launch.max_total_tokens, launch.max_turns)
    for line in run.stdout:
        lines.append(line)
        if watch.feed(line) and not timed_out.is_set():
            _podman("kill", name)  # keep reading so the partial transcript is kept
    exit_code = run.wait()
    timer.cancel()
    err_reader.join(timeout=5)
    wall = time.monotonic() - t0
    mon.stop()
    res_file.close()
    oom = _podman("inspect", "--format", "{{.State.OOMKilled}}", name).stdout.strip() == "true"
    _podman("rm", "-f", name)
    return ContainerRun(
        lines,
        "".join(stderr_chunks),
        exit_code,
        timed_out=timed_out.is_set(),
        oom_killed=oom,
        wall_s=wall,
        resources=mon.summary(),
    )


# --- one stage -------------------------------------------------------------------------


@dataclass
class BoutEnv:
    """What every stage of one bout shares."""

    key: BoutKey
    lt: LoadedTrial
    lock: dict[str, Any]
    creds: Credentials
    scrub: Scrubber
    arena: Arena
    task: Task
    model: ModelSpec
    arena_dir: Path
    work: Path
    runner: BoutRunner


@dataclass
class StageResult:
    status: str
    fields: dict[str, Any]  # record fields this stage produced (usage, init, error, ...)
    findings: dict[str, Any] | None = None  # the structured output when status is ok


def run_stage(
    env: BoutEnv,
    contender: Contender,
    lc: dict[str, Any],
    task_prompt: str,
    out_dir: Path,
    *,
    name: str,
    tag: str = "",
) -> StageResult:
    """Run one agent container for `contender` and collect its artifacts into `out_dir`.

    A plain bout is one stage. A pipeline bout runs one per stage, each in a fresh container
    with its own context and config dir. Writes prompt.md, transcript.jsonl.zst, stderr.log,
    resources.jsonl and (when there is output) findings.json, but not record.json.
    """
    rt = env.lt.trial.runtime
    cache_dir = Path(rt.cache_dir).expanduser()
    scrub, creds = env.scrub, env.creds
    ctx_dir, cfg_dir = env.work / f"ctx{tag}", env.work / f"cfg{tag}"
    cfg_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    fields: dict[str, Any] = {}

    # 1. context dir, verified against the lock
    facts = materialize(contender, lc.get("sha"), cache_dir, ctx_dir)
    if facts["tree_hash"] != lc["tree_hash"]:
        return StageResult("error", {"error": "contender-drift: tree hash differs from lock"})

    # 2. credentials
    if creds.mode == AuthMode.credentials_file and creds.credentials_file:
        shutil.copy2(creds.credentials_file, cfg_dir / ".credentials.json")
        os.chmod(cfg_dir / ".credentials.json", 0o600)

    # 3. command
    builtins = env.lock["image"].get("builtins", {})
    spec = cc.BoutSpec(
        contender=contender,
        skill_name=lc.get("skill_name"),
        plugin_name=lc.get("plugin_name"),
        expected_skills=lc.get("expected_skills", []),
        model=env.model,
        task=env.task,
        invocation=env.lt.trial.invocation,
        auth_mode=creds.mode,
        budget_usd=env.arena.budget_usd or env.model.max_budget_usd,
        builtin_skills=tuple(builtins.get("skills", [])),
        builtin_plugins=tuple(builtins.get("plugins", [])),
    )
    prompt = cc.build_prompt(spec, task_prompt)
    (out_dir / "prompt.md").write_text(prompt)
    command = cc.build_command(spec, load_schema("findings"))
    fields["command"] = [scrub(a) for a in command]

    # 4. run, behind the egress proxy (fake runners never touch podman, so no proxy for them)
    egress = Egress(name, image_ref(rt), rt.network, enabled=env.runner is podman_bout_runner)
    try:
        egress.__enter__()
    except EgressError as e:
        return StageResult("error", {**fields, "error": str(e)})
    try:
        create = podman_create_args(
            name, image_ref(rt), rt.limits, creds, env.arena_dir, ctx_dir, cfg_dir, command,
            extra=egress.podman_args,
        )  # fmt: skip
        run = env.runner(
            Launch(
                name=name,
                create=create,
                env={**os.environ, **creds.env},
                prompt=prompt,
                timeout_s=rt.limits.timeout_s,
                max_total_tokens=rt.limits.max_total_tokens,
                max_turns=rt.limits.max_turns,
                resources=out_dir / "resources.jsonl",
                sample_interval_s=rt.sample_interval_s,
            )
        )
    finally:
        egress.__exit__(None, None, None)
    fields["egress"] = egress.summary()
    if run.create_error is not None:
        return StageResult(
            "error", {**fields, "error": f"podman create failed: {scrub(run.create_error)}"}
        )

    # 5. artifacts (scrubbed)
    scrubbed = [scrub(line) for line in run.lines]
    cctx = zstandard.ZstdCompressor(level=12)
    (out_dir / "transcript.jsonl.zst").write_bytes(cctx.compress("".join(scrubbed).encode()))
    (out_dir / "stderr.log").write_text(scrub(run.stderr))
    (out_dir / "resources.jsonl").touch()

    ps = cc.parse_stream(scrubbed)
    usage = cc.usage_summary(ps.result)
    findings = cc.structured_output(ps.result)
    fields.update(
        {
            "exit_code": run.exit_code,
            "oom_killed": run.oom_killed,
            "container_wall_s": round(run.wall_s, 3),
            "init": {
                "model": (ps.init or {}).get("model"),
                "skills": cc._skill_names(ps.init or {}),
                "builtin_skills": list(spec.builtin_skills),
                "mcp_servers": (ps.init or {}).get("mcp_servers"),
                "tools": (ps.init or {}).get("tools"),
                "cli_version": (ps.init or {}).get("claude_code_version"),
            },
            "usage": usage,
            "models_used": sorted((usage.get("per_model") or {}).keys()),
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
            "resources": run.resources,
        }
    )

    # The runner kills on a crossed limit as it streams; the status is decided from the
    # transcript so every runner (and a re-read) agrees.
    watch = LimitWatch(rt.limits.max_total_tokens, rt.limits.max_turns)
    for line in scrubbed:
        watch.feed(line)
    if run.timed_out:
        return StageResult("timeout", {**fields, "error": f"exceeded {rt.limits.timeout_s}s"})
    if watch.tripped:
        return StageResult("limit_exceeded", {**fields, "error": f"limit hit: {watch.tripped}"})
    problems = cc.isolation_problems(ps, spec)
    if problems:
        return StageResult("invalid", {**fields, "invalid_reasons": problems})
    if usage.get("terminal_reason") == "structured_output_retry_exhausted":
        # The agent kept answering in some other format (often the skill's own). That is a
        # property of the contender, so it's a final result, not a retryable error.
        (out_dir / "findings.json").write_text(
            json.dumps({"raw_result": scrub(str((ps.result or {}).get("result")))}, indent=2)
        )
        return StageResult(
            "schema_violation",
            {**fields, "findings_count": 0, "error": "structured output retries exhausted"},
        )
    if ps.result is None or usage.get("is_error"):
        reason = "; ".join(ps.api_errors) or usage.get("terminal_reason") or "no result event"
        return StageResult("error", {**fields, "error": f"agent error: {reason}"})
    if not isinstance(findings, dict) or not isinstance(findings.get("findings"), list):
        (out_dir / "findings.json").write_text(
            json.dumps({"raw_result": scrub(str(ps.result.get("result")))}, indent=2)
        )
        return StageResult("schema_violation", {**fields, "findings_count": 0})
    (out_dir / "findings.json").write_text(scrub(json.dumps(findings, indent=2)) + "\n")
    return StageResult("ok", {**fields, "findings_count": len(findings["findings"])}, findings)


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
    runner: BoutRunner = podman_bout_runner,
) -> dict[str, Any]:
    rt = lt.trial.runtime
    cache_dir = Path(rt.cache_dir).expanduser()
    contender = lt.contender(key.contender)
    arena: Arena = next(a for a in lt.arenas if a.id == key.arena)
    task: Task = next(t for t in lt.trial.tasks if t.id == key.task)
    model: ModelSpec = next(m for m in lt.trial.models if m.slug == key.model)
    lc, la = lock["contenders"][contender.id], lock["arenas"][arena.id]

    work = Path(rt.work_dir).resolve() / key.bout_id
    if work.exists():
        shutil.rmtree(work)
    arena_dir = work / "arena"
    work.mkdir(parents=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    scrub = Scrubber(creds.secret_values)
    started_at = dt.datetime.now(dt.UTC)

    record: dict[str, Any] = {
        "record_version": RECORD_VERSION,
        "bout_id": key.bout_id,
        "trial": lt.trial.id,
        "lock_hash": lock["lock_hash"],
        "contender": _contender_facts(contender, lc),
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
        used = extra.pop("models_used", None)
        record.update(extra)
        if used is not None:
            record["model"] = {**record["model"], "used": used}
        record["status"] = status
        record["finished_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        record["wall_s"] = round((dt.datetime.now(dt.UTC) - started_at).total_seconds(), 3)
        (out_dir / "record.json").write_text(
            scrub(json.dumps(record, indent=2, default=str)) + "\n"
        )
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
        return record

    # The arena is prepared once and mounted read-only into every stage.
    prep = prepare_arena(arena, la["sha"], cache_dir, arena_dir)
    record["arena"]["prepared"] = {k: prep[k] for k in ("files", "removed")}
    if prep["leftover_context"]:
        return finish(
            "invalid", invalid_reasons=[f"arena-context-file: {prep['leftover_context']}"]
        )

    env = BoutEnv(key, lt, lock, creds, scrub, arena, task, model, arena_dir, work, runner)
    task_prompt = render_prompt(lt.prompts[task.id], arena)
    if contender.kind == ContenderKind.pipeline:
        return _run_pipeline(env, contender, lc, task_prompt, out_dir, record, finish)
    res = run_stage(env, contender, lc, task_prompt, out_dir, name=f"so-{key.bout_id}")
    return finish(res.status, **res.fields)


def _contender_facts(c: Contender, lc: dict[str, Any]) -> dict[str, Any]:
    facts = {
        "id": c.id,
        "kind": c.kind.value,
        "sha": lc.get("sha"),
        "tree_hash": lc["tree_hash"],
        "skill_name": lc.get("skill_name"),
        "role": c.role,
    }
    if c.kind == ContenderKind.pipeline:
        facts["stages"] = list(c.stages)
    return facts


# --- pipelines -------------------------------------------------------------------------

# Stage-1 artifacts that are also kept at the top level, so a pipeline bout dir looks like
# any other bout dir. findings.json at the top is the last stage's output instead.
TOP_LEVEL_FROM_STAGE_1 = ("prompt.md", "transcript.jsonl.zst", "stderr.log", "resources.jsonl")


def _run_pipeline(
    env: BoutEnv,
    pipe: Contender,
    lc: dict[str, Any],
    task_prompt: str,
    out_dir: Path,
    record: dict[str, Any],
    finish: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Stage 1 is the task itself; each later stage verifies the previous stage's findings."""
    template = verify_template()
    blind = Blinder(blind_terms(env.lock))
    stages_dir = out_dir / "stages"
    if stages_dir.exists():
        shutil.rmtree(stages_dir)  # a retried error bout starts clean
    summaries: list[dict[str, Any]] = []
    results: list[StageResult] = []
    prev: list[dict[str, Any]] | None = None
    last_dir: Path | None = None
    failed: dict[str, Any] | None = None

    for n, (sid, slc) in enumerate(zip(pipe.stages, lc["stage_locks"], strict=True), 1):
        stage = env.lt.contender(sid)
        sdir = stages_dir / f"{n}-{sid}"
        if n > 1 and not prev:
            # Nothing survived to verify, so the pipeline's answer is already known.
            summaries.append(
                {"n": n, "contender": sid, "status": "skipped", "reason": "no findings to verify"}
            )
            continue
        body = task_prompt if n == 1 else render_verify_prompt(template, env.arena, prev, blind)
        started = dt.datetime.now(dt.UTC)
        res = run_stage(
            env, stage, slc, body, sdir, name=f"so-{env.key.bout_id}-s{n}", tag=f"-s{n}"
        )
        results.append(res)
        last_dir = sdir
        stage_record = {
            "record_version": RECORD_VERSION,
            "bout_id": env.key.bout_id,
            "stage": n,
            "pipeline": pipe.id,
            "contender": _contender_facts(stage, slc),
            **{k: v for k, v in res.fields.items() if k != "models_used"},
            "model": {
                "requested": env.model.id,
                "effort": env.model.effort.value if env.model.effort else None,
                "used": res.fields.get("models_used"),
            },
            "status": res.status,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
        (sdir / "record.json").write_text(
            env.scrub(json.dumps(stage_record, indent=2, default=str)) + "\n"
        )
        f = res.fields
        summaries.append(
            {
                "n": n,
                "contender": sid,
                "status": res.status,
                "findings_count": f.get("findings_count"),
                "usage": f.get("usage") or {},
                "resources": f.get("resources") or {},
                "egress": f.get("egress"),
                "skill_fired": f.get("skill_fired"),
                **{k: f[k] for k in ("error", "invalid_reasons") if k in f},
            }
        )
        if res.status != "ok":
            failed = {"n": n, "contender": sid}
            break
        prev = (res.findings or {}).get("findings") or []

    for name in TOP_LEVEL_FROM_STAGE_1:
        src = stages_dir / f"1-{pipe.stages[0]}" / name
        if src.exists():
            shutil.copy2(src, out_dir / name)
    top_findings = out_dir / "findings.json"
    top_findings.unlink(missing_ok=True)
    if last_dir is not None and (last_dir / "findings.json").exists():
        shutil.copy2(last_dir / "findings.json", top_findings)

    first, last = results[0].fields, results[-1]  # stage 1 always runs
    fired = [s["skill_fired"] for s in summaries if s.get("skill_fired") is not None]
    extra: dict[str, Any] = {
        "stages": summaries,
        "findings_before_verify": first.get("findings_count"),
        "usage": sum_usage([r.fields.get("usage") or {} for r in results]),
        "resources": combine_resources([r.fields.get("resources") or {} for r in results]),
        "egress": merge_egress([r.fields.get("egress") for r in results]),
        "tool_calls": _sum_counts([r.fields.get("tool_calls") or {} for r in results]),
        "models_used": sorted({m for r in results for m in r.fields.get("models_used") or []}),
        "skill_fired": all(fired) if fired else None,
        # Stage 1 is the one comparable to a plain bout (same task prompt).
        "first_turn_prompt_tokens": first.get("first_turn_prompt_tokens"),
        "init": first.get("init"),
        "container_wall_s": round(sum(r.fields.get("container_wall_s") or 0 for r in results), 3),
    }
    if failed is not None:
        extra["failed_stage"] = failed
        for k in ("error", "invalid_reasons"):
            if k in last.fields:
                extra[k] = last.fields[k]
        if last.status == "schema_violation":
            extra["findings_count"] = 0
        return finish(last.status, **extra)
    return finish("ok", findings_count=len(prev or []), **extra)


def _sum_counts(dicts: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for d in dicts:
        for k, v in d.items():
            if isinstance(v, int | float) and not isinstance(v, bool):
                out[k] = (out.get(k) or 0) + v
            elif k not in out:
                out[k] = v
    return out


def sum_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    """Add up per-stage usage so a pipeline bout reads like one bout to score and report."""
    usages = [u for u in usages if u]
    if not usages:
        return {}
    numeric = ("duration_ms", "duration_api_ms", "num_turns", "total_cost_usd")
    out: dict[str, Any] = {
        k: sum(u[k] for u in usages if isinstance(u.get(k), int | float))
        if any(isinstance(u.get(k), int | float) for u in usages)
        else None
        for k in numeric
    }
    out["tokens"] = _sum_counts([u.get("tokens") or {} for u in usages])
    per_model: dict[str, dict[str, Any]] = {}
    for u in usages:
        for m, pm in (u.get("per_model") or {}).items():
            per_model[m] = _sum_counts([per_model.get(m, {}), pm])
    out["per_model"] = per_model
    out["permission_denials"] = sum(u.get("permission_denials") or 0 for u in usages)
    out["is_error"] = any(u.get("is_error") for u in usages)
    for k in ("subtype", "terminal_reason", "stop_reason"):
        out[k] = usages[-1].get(k)
    out["session_ids"] = [u.get("session_id") for u in usages]
    return out


def combine_resources(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Peaks are the max over stages, CPU and IO are summed. Per-comm detail stays per stage."""
    avail = [s for s in summaries if s.get("available")]
    if not avail:
        return {"available": False, "reason": "no stage had resource samples"}
    out: dict[str, Any] = {"available": True, "stages": len(avail)}
    for k in ("memory_peak_bytes", "rss_peak_kb", "threads_peak", "fds_peak", "pids_peak"):
        vals = [s[k] for s in avail if isinstance(s.get(k), int | float)]
        out[k] = max(vals) if vals else None
    for k in (
        "samples",
        "cpu_usage_s",
        "cpu_user_s",
        "cpu_system_s",
        "cpu_throttled_s",
        "io_read_bytes",
        "io_write_bytes",
    ):
        out[k] = sum(s.get(k) or 0 for s in avail)
    samples = out["samples"] or 0
    if samples:
        weighted = sum((s.get("rss_mean_kb") or 0) * (s.get("samples") or 0) for s in avail)
        out["rss_mean_kb"] = round(weighted / samples)
    out["note"] = "pipeline: peaks are the max over stages, cpu/io are summed"
    return out


def merge_egress(summaries: list[dict[str, Any] | None]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in summaries:
        if not s:
            continue
        out.setdefault("mode", s.get("mode"))
        for k in ("allowed", "denied"):
            if k in s:
                out[k] = _sum_counts([out.get(k) or {}, s[k]])
    return out
