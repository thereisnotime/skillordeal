"""Blinded LLM judge: a model re-reads the cited code and rules on each finding.

The judge runs in the same runner image and sandbox as a bout (read-only arena, empty config,
no MCP, no skills) with only Read, Grep and Glob. It never sees who produced a finding: only
file/lines/category/cwe/title/description/evidence go into the prompt, with contender and skill
names redacted from the text, and findings from all contenders are shuffled together.

Verdicts are cached per (judge model, prompt, finding_hash), so re-judging costs nothing and
the cache is shared across rounds.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zstandard

from skillordeal.adapters import claude_code as cc
from skillordeal.bout import podman_create_args, prepare_arena
from skillordeal.config import Arena, AuthMode, LoadedTrial, ModelSpec
from skillordeal.lock import WRAPPER_PLUGIN, image_ref
from skillordeal.schemas import load as load_schema
from skillordeal.score import ScoreError, write_jsonl
from skillordeal.secrets import Credentials, Scrubber
from skillordeal.yamlio import canonical_json, sha256_text

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "judge.md"
TOOLS = ["Read", "Grep", "Glob"]
# The only finding fields the judge sees.
BLIND_FIELDS = ("file", "line_start", "line_end", "category", "cwe", "title", "description")
BLIND_TEXT = ("title", "description", "evidence")
VERDICTS = {"valid", "invalid", "duplicate", "unverifiable"}
CONFIDENCES = {"high", "medium", "low"}
DEFAULT_BATCH = 15
REDACTED = "[redacted]"
_IDS = re.compile(r"\bb-[0-9a-f]{16}(?::\d+)?\b")  # bout and finding ids


def prompt_template() -> str:
    return PROMPT_PATH.read_text()


def prompt_sha(template: str | None = None) -> str:
    """Identity of the judging instructions: the template plus the verdict schema."""
    t = prompt_template() if template is None else template
    return sha256_text(t + "\n" + canonical_json(load_schema("verdicts")))


def require_judge(lt: LoadedTrial) -> ModelSpec:
    if lt.trial.judge is None:
        raise ScoreError(
            f"trial {lt.trial.id} has no judge model; add `judge: {{id: <full model id>}}` "
            "to trial.yaml"
        )
    return lt.trial.judge


# --- blinding ---------------------------------------------------------------------------


def blind_terms(lock: dict[str, Any]) -> list[str]:
    """Names that would give a contender away if a finding happened to mention them."""
    terms: set[str] = set()
    for cid, c in (lock.get("contenders") or {}).items():
        if c.get("kind") == "baseline":
            continue
        terms.add(cid)
        for name in (c.get("skill_name"), c.get("plugin_name")):
            if name and name != WRAPPER_PLUGIN:
                terms.add(name)
                terms.add(name.split(":")[-1])
        for s in c.get("expected_skills") or []:
            terms.add(s.split(":")[-1])
    return sorted((t for t in terms if len(t) >= 3), key=len, reverse=True)


class Blinder:
    def __init__(self, terms: list[str]):
        pat = "|".join(re.escape(t) for t in terms)
        self._terms = re.compile(rf"(?<![\w-])(?:{pat})(?![\w-])", re.I) if pat else None

    def __call__(self, text: Any) -> Any:
        if not isinstance(text, str):
            return text
        text = _IDS.sub(REDACTED, text)
        return self._terms.sub(REDACTED, text) if self._terms else text


def blinded(row: dict[str, Any], ref: str, blind: Blinder) -> dict[str, Any]:
    out: dict[str, Any] = {"ref": ref}
    for k in (*BLIND_FIELDS, "evidence"):
        v = row.get(k)
        if v is not None and v != "":
            out[k] = blind(v) if k in BLIND_TEXT else v
    return out


# --- batching -----------------------------------------------------------------------------


@dataclass
class Batch:
    arena: str
    items: list[dict[str, Any]]  # finding rows, one per finding_hash, in prompt order
    prompt: str = ""
    refs: dict[str, str] = field(default_factory=dict)  # ref -> finding_hash

    @property
    def batch_id(self) -> str:
        return "j-" + sha256_text("\n".join(sorted(self.refs.values())))[:16]


def shuffled(hashes: list[str]) -> list[str]:
    """Deterministic shuffle seeded by the set of hashes, so order doesn't track contenders."""
    order = sorted(hashes)
    random.Random(int(sha256_text("\n".join(order)), 16)).shuffle(order)
    return order


def render_prompt(template: str, arena: Arena, findings: list[dict[str, Any]]) -> str:
    body = json.dumps(findings, indent=2, ensure_ascii=False)
    text = template.replace("{language}", arena.language).replace("{count}", str(len(findings)))
    return text.replace("{findings}", body)  # last, so finding text is never substituted into


def plan_batches(
    lt: LoadedTrial,
    lock: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    batch_size: int = DEFAULT_BATCH,
    skip: set[str] | None = None,
    template: str | None = None,
) -> list[Batch]:
    """Group unjudged findings by arena, shuffle them, and cut batches of `batch_size`."""
    if batch_size < 1:
        raise ScoreError("batch size must be at least 1")
    template = prompt_template() if template is None else template
    blind = Blinder(blind_terms(lock))
    skip = skip or set()
    by_arena: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r["finding_hash"] in skip:
            continue
        by_arena.setdefault(r["arena"], {}).setdefault(r["finding_hash"], r)  # first row wins
    arenas = {a.id: a for a in lt.arenas}
    batches = []
    for aid in sorted(by_arena):
        uniq = by_arena[aid]
        order = shuffled(list(uniq))
        for i in range(0, len(order), batch_size):
            b = Batch(arena=aid, items=[uniq[h] for h in order[i : i + batch_size]])
            payload = []
            for n, item in enumerate(b.items, 1):
                ref = f"F{n}"
                b.refs[ref] = item["finding_hash"]
                payload.append(blinded(item, ref, blind))
            b.prompt = render_prompt(template, arenas[aid], payload)
            batches.append(b)
    return batches


# --- cache ----------------------------------------------------------------------------------


class JudgeCache:
    """~/.cache/skillordeal/judge/<judge_model>/<prompt_sha>/<finding_hash>.json"""

    def __init__(self, cache_dir: Path, model: ModelSpec, psha: str):
        self.dir = cache_dir / "judge" / model.slug / psha

    def get(self, finding_hash: str) -> dict[str, Any] | None:
        p = self.dir / f"{finding_hash}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None  # a torn write; judge it again

    def put(self, finding_hash: str, obj: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{finding_hash}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.dir / f"{finding_hash}.json")


# --- running ----------------------------------------------------------------------------------


@dataclass
class ContainerRun:
    lines: list[str]
    stderr: str
    exit_code: int
    timed_out: bool = False


# (create args, env, prompt, timeout_s) -> ContainerRun. Swapped out in tests.
Runner = Callable[[list[str], dict[str, str], str, int], ContainerRun]


def podman_runner(
    create: list[str], env: dict[str, str], prompt: str, timeout_s: int
) -> ContainerRun:
    name = create[create.index("--name") + 1]
    subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)
    proc = subprocess.run(create, capture_output=True, text=True, env=env, check=False)
    if proc.returncode != 0:
        return ContainerRun([], proc.stderr, proc.returncode)
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
            subprocess.run(["podman", "kill", name], capture_output=True, check=False)

    timer = threading.Timer(timeout_s, killer)
    timer.start()
    err: list[str] = []
    reader = threading.Thread(target=lambda: err.append(run.stderr.read()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    assert run.stdin and run.stdout
    run.stdin.write(prompt)
    run.stdin.close()
    lines = list(run.stdout)
    code = run.wait()
    timer.cancel()
    reader.join(timeout=5)
    subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)
    return ContainerRun(lines, "".join(err), code, timed_out.is_set())


@dataclass
class JudgeRun:
    rows: list[dict[str, Any]]  # judge.jsonl rows, one per finding with a verdict
    spent_usd: float = 0.0
    judged: int = 0  # new verdicts this run
    cached: int = 0  # verdicts served from the cache
    missing: int = 0  # findings still without a verdict (errors, budget)
    problems: list[str] = field(default_factory=list)


def parse_verdicts(output: Any, batch: Batch) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """finding_hash -> verdict fields, plus complaints about anything malformed."""
    problems = []
    if not isinstance(output, dict) or not isinstance(output.get("verdicts"), list):
        return {}, ["no structured verdicts in the judge output"]
    got: dict[str, dict[str, Any]] = {}
    for v in output["verdicts"]:
        ref = v.get("finding_ref") if isinstance(v, dict) else None
        if ref not in batch.refs:
            problems.append(f"unknown ref {ref!r}")
            continue
        if v.get("verdict") not in VERDICTS or v.get("confidence") not in CONFIDENCES:
            problems.append(
                f"{ref}: bad verdict/confidence {v.get('verdict')}/{v.get('confidence')}"
            )
            continue
        got[batch.refs[ref]] = {
            "verdict": v["verdict"],
            "confidence": v["confidence"],
            "rationale": str(v.get("rationale") or ""),
        }
    missing = [r for r, h in batch.refs.items() if h not in got]
    if missing:
        problems.append(f"no verdict for {', '.join(missing)}")
    return got, problems


def run_judge(
    lt: LoadedTrial,
    lock: dict[str, Any],
    rows: list[dict[str, Any]],
    scores: Path,
    creds: Credentials,
    *,
    batch_size: int = DEFAULT_BATCH,
    max_cost_usd: float | None = None,
    runner: Runner = podman_runner,
    log: Callable[[str], None] = print,
) -> JudgeRun:
    """Judge every finding in `rows` that isn't cached yet, then write scores/judge.jsonl."""
    model = require_judge(lt)
    rt = lt.trial.runtime
    cache_dir = Path(rt.cache_dir).expanduser()
    template = prompt_template()
    psha = prompt_sha(template)
    cache = JudgeCache(cache_dir, model, psha)
    verdicts: dict[str, dict[str, Any]] = {}
    for h in {r["finding_hash"] for r in rows}:
        if (hit := cache.get(h)) is not None:
            verdicts[h] = hit
    res = JudgeRun(rows=[], cached=len(verdicts))
    batches = plan_batches(lt, lock, rows, batch_size=batch_size, skip=set(verdicts))
    todo = sum(len(b.items) for b in batches)
    log(f"{len(verdicts)} cached, {todo} to judge in {len(batches)} batches")

    scrub = Scrubber(creds.secret_values)
    builtins = lock["image"].get("builtins") or {}
    runs_dir = scores / "judge_runs"
    work_root = Path(rt.work_dir).resolve() / f"judge-{os.getpid()}"
    arenas = {a.id: a for a in lt.arenas}
    prepared: dict[str, Path] = {}
    try:
        for b in batches:
            left = None if max_cost_usd is None else max_cost_usd - res.spent_usd
            if left is not None and left <= 0:
                res.problems.append(f"judge budget ${max_cost_usd:.2f} reached, stopping")
                break
            budget = model.max_budget_usd if left is None else min(model.max_budget_usd, left)
            spec = cc.plain_spec(
                model,
                TOOLS,
                creds.mode,
                max(budget, 0.01),
                builtin_skills=tuple(builtins.get("skills") or []),
                builtin_plugins=tuple(builtins.get("plugins") or []),
            )
            if b.arena not in prepared:
                dest = work_root / b.arena / "arena"
                prepare_arena(arenas[b.arena], lock["arenas"][b.arena]["sha"], cache_dir, dest)
                prepared[b.arena] = dest
            cost, got, problems = _judge_batch(
                b, spec, prepared[b.arena], work_root, lt, creds, scrub, runner, runs_dir
            )
            res.spent_usd += cost
            now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            for h, v in got.items():
                obj = {
                    "finding_hash": h,
                    "judge_model": model.slug,
                    **v,
                    "cluster_id": next(
                        r.get("cluster_id") for r in b.items if r["finding_hash"] == h
                    ),
                    "prompt_sha": psha,
                    "batch_id": b.batch_id,
                    "judged_at": now,
                }
                cache.put(h, obj)
                verdicts[h] = obj
            res.judged += len(got)
            res.problems += [f"{b.batch_id}: {p}" for p in problems]
            log(
                f"{b.batch_id} {b.arena}: {len(got)}/{len(b.items)} verdicts, "
                f"${cost:.3f} (total ${res.spent_usd:.2f})"
            )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    for r in rows:
        if (hit := verdicts.get(r["finding_hash"])) is not None:
            res.rows.append(
                {**hit, "finding_id": r["finding_id"], "cluster_id": r.get("cluster_id")}
            )
    res.missing = len({r["finding_hash"] for r in rows} - set(verdicts))
    write_jsonl(scores / "judge.jsonl", res.rows)
    return res


def _judge_batch(
    b: Batch,
    spec: cc.BoutSpec,
    arena_dir: Path,
    work_root: Path,
    lt: LoadedTrial,
    creds: Credentials,
    scrub: Scrubber,
    runner: Runner,
    runs_dir: Path,
) -> tuple[float, dict[str, dict[str, Any]], list[str]]:
    rt = lt.trial.runtime
    out = runs_dir / b.batch_id
    out.mkdir(parents=True, exist_ok=True)
    work = work_root / b.batch_id
    ctx_dir, cfg_dir = work / "ctx", work / "cfg"
    ctx_dir.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)
    if creds.mode == AuthMode.credentials_file and creds.credentials_file:
        shutil.copy2(creds.credentials_file, cfg_dir / ".credentials.json")
        os.chmod(cfg_dir / ".credentials.json", 0o600)

    command = cc.build_command(spec, load_schema("verdicts"))
    create = podman_create_args(
        f"so-{b.batch_id}", image_ref(rt), rt.limits, creds, arena_dir, ctx_dir, cfg_dir, command
    )
    (out / "prompt.md").write_text(b.prompt)
    run = runner(create, {**os.environ, **creds.env}, b.prompt, rt.limits.timeout_s)
    lines = [scrub(line) for line in run.lines]
    cctx = zstandard.ZstdCompressor(level=12)
    (out / "transcript.jsonl.zst").write_bytes(cctx.compress("".join(lines).encode()))
    (out / "stderr.log").write_text(scrub(run.stderr))

    ps = cc.parse_stream(lines)
    usage = cc.usage_summary(ps.result)
    cost = float(usage.get("total_cost_usd") or 0.0)
    problems = cc.isolation_problems(ps, spec)
    tools = set((ps.init or {}).get("tools") or []) - {*TOOLS, "StructuredOutput"}
    if tools:
        problems.append(f"unexpected-tools: {sorted(tools)}")
    got: dict[str, dict[str, Any]] = {}
    if run.timed_out:
        problems.append(f"timed out after {rt.limits.timeout_s}s")
    elif problems:
        pass  # isolation failed: don't trust (or cache) anything this run said
    elif ps.result is None or usage.get("is_error"):
        problems.append(
            "agent error: " + ("; ".join(ps.api_errors) or str(usage.get("terminal_reason")))
        )
    else:
        got, bad = parse_verdicts(cc.structured_output(ps.result), b)
        problems += bad
    record = {
        "batch_id": b.batch_id,
        "arena": b.arena,
        "judge_model": spec.model.slug,
        "refs": b.refs,
        "command": [scrub(a) for a in command],
        "exit_code": run.exit_code,
        "usage": usage,
        "verdicts": len(got),
        "problems": problems,
    }
    (out / "record.json").write_text(scrub(json.dumps(record, indent=2, default=str)) + "\n")
    return cost, got, problems
