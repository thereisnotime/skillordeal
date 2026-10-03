"""Panel judge: several blinded voters each try to refute a finding through one lens.

The fact judge only asks whether the code does what a finding says, which nearly every finding
passes. The panel asks the questions that decide whether a finding is a vulnerability: can an
attacker reach the code with input they control (`reachability`), what do they gain beyond what
their position already allows (`impact`), and is the code really as described (`correctness`).

Every voter is a separate call in the same sandbox as the fact judge (read-only arena, Read,
Grep and Glob only, blinded and shuffled batches per arena; every voter sees the same batch).
Votes are cached per finding and voter slot, so a run that stops halfway resumes where it left
off. The verdict is a plain majority over the votes, computed here, never by a model.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillordeal.adapters import claude_code as cc
from skillordeal.config import JudgeMode, JudgeSpec, LoadedTrial
from skillordeal.schemas import load as load_schema
from skillordeal.score import ScoreError, write_jsonl
from skillordeal.score import judge as J
from skillordeal.secrets import Credentials, Scrubber
from skillordeal.yamlio import canonical_json, sha256_text

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "judge_panel.md"
SCHEMA = "panel_votes"
VOTES = ("true_positive", "false_positive", "unverifiable")
VOTE_VERDICT = {"true_positive": "valid", "false_positive": "invalid"}
# What judge.jsonl keeps of each vote.
VOTE_FIELDS = ("slot", "lens", "vote", "confidence", "attacker", "gain", "rationale")
_LENS = re.compile(r"^<!-- lens: ([a-z0-9-]+) -->\n", re.M)


# --- prompt -------------------------------------------------------------------------------


def prompt_file() -> str:
    return PROMPT_PATH.read_text()


def split_template(text: str) -> tuple[str, dict[str, str]]:
    """(shared template, lens name -> lens text) from judge_panel.md."""
    parts = _LENS.split(text)
    base, rest = parts[0], parts[1:]
    lenses = {rest[i]: rest[i + 1].strip() for i in range(0, len(rest), 2)}
    return base, lenses


def lens_template(text: str, lens: str) -> str:
    """The template for one lens, with only {language}, {count} and {findings} left open."""
    base, lenses = split_template(text)
    if lens not in lenses:
        raise ScoreError(f"no lens {lens!r} in {PROMPT_PATH.name}")
    return base.replace("{lens_name}", lens).replace("{lens}", lenses[lens])


def prompt_sha(text: str | None = None) -> str:
    """Identity of the panel instructions: every lens's prompt plus the vote schema."""
    t = prompt_file() if text is None else text
    return sha256_text(t + "\n" + canonical_json(load_schema(SCHEMA)))


# --- cache ----------------------------------------------------------------------------------


class VoteCache(J.JudgeCache):
    """<cache_dir>/judge/<judge_model>/panel-<prompt_sha>/<slot>/<finding_hash>.json

    The `panel-` prefix keeps panel votes and fact verdicts apart even for the same model.
    """

    def __init__(self, cache_dir: Path, model: JudgeSpec, psha: str, slot: str):
        self.dir = cache_dir / "judge" / model.slug / f"panel-{psha}" / slot


# --- aggregation ------------------------------------------------------------------------------


def aggregate(votes: list[str]) -> dict[str, Any]:
    """Majority over decisive votes. Ties and mostly-unverifiable panels stay unverifiable.

    `agreement` is the share of voters who cast the most common vote; `unanimous` means all of
    them cast the same one.
    """
    if not votes:
        raise ValueError("no votes to aggregate")
    c = Counter(votes)
    tp, fp, unsure = c["true_positive"], c["false_positive"], c["unverifiable"]
    decided = unsure <= tp + fp and tp != fp
    verdict = ("valid" if tp > fp else "invalid") if decided else "unverifiable"
    top = max(c.values())
    unanimous = top == len(votes)
    confidence = ("high" if unanimous else "medium") if decided else "low"
    return {
        "verdict": verdict,
        "confidence": confidence,
        "agreement": round(top / len(votes), 4),
        "unanimous": unanimous,
    }


def panel_row(
    h: str,
    slots: list[tuple[str, str]],
    votes: dict[str, dict[str, Any]],
    model: JudgeSpec,
    psha: str,
) -> dict[str, Any]:
    """The judge.jsonl row for one finding whose every slot has voted."""
    ordered = [votes[s] for s, _ in slots]
    agg = aggregate([v["vote"] for v in ordered])
    winning = next((k for k, x in VOTE_VERDICT.items() if x == agg["verdict"]), None)
    backing = [v for v in ordered if v["vote"] == winning] or ordered
    return {
        "finding_hash": h,
        "judge_model": model.slug,
        "mode": JudgeMode.panel.value,
        "verdict": agg["verdict"],
        "confidence": agg["confidence"],
        "rationale": " | ".join(f"{v['slot']}: {v['rationale']}" for v in backing),
        "attacker": backing[0]["attacker"],
        "gain": backing[0]["gain"],
        "votes": [{k: v[k] for k in VOTE_FIELDS} for v in ordered],
        "agreement": agg["agreement"],
        "unanimous": agg["unanimous"],
        "lenses": [s for s, _ in slots],
        "prompt_sha": psha,
        "batch_id": ordered[0]["batch_id"],
        "judged_at": max(v["voted_at"] for v in ordered),
    }


# --- planning -------------------------------------------------------------------------------


@dataclass
class Plan:
    model: JudgeSpec
    psha: str
    text: str  # judge_panel.md
    slots: list[tuple[str, str]]  # (slot, lens) per voter
    caches: dict[str, VoteCache]
    votes: dict[str, dict[str, dict[str, Any]]]  # finding_hash -> slot -> cached vote
    complete: set[str]  # findings every slot has voted on
    batches: list[J.Batch]

    @property
    def cache_root(self) -> Path:
        return next(iter(self.caches.values())).dir.parent

    def prompt(self, lt: LoadedTrial, b: J.Batch, lens: str) -> str:
        arena = next(a for a in lt.arenas if a.id == b.arena)
        return J.render_prompt(lens_template(self.text, lens), arena, b.payload)


def plan(
    lt: LoadedTrial,
    lock: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    model: JudgeSpec | None = None,
    batch_size: int = J.DEFAULT_BATCH,
) -> Plan:
    """Load cached votes and batch every finding some voter still has to see.

    Batches are cut once over those findings, so all voters get the same batch; a voter whose
    votes are all cached for a batch skips it.
    """
    model = J.require_judge(lt) if model is None else model
    text = prompt_file()
    psha = prompt_sha(text)
    slots = model.voter_slots()
    _, known = split_template(text)
    if unknown := sorted({lens for _, lens in slots} - set(known)):
        raise ScoreError(f"no prompt for lens {', '.join(unknown)} in {PROMPT_PATH.name}")
    cache_dir = Path(lt.trial.runtime.cache_dir).expanduser()
    caches = {s: VoteCache(cache_dir, model, psha, s) for s, _ in slots}
    votes: dict[str, dict[str, dict[str, Any]]] = {}
    for h in sorted({r["finding_hash"] for r in rows}):
        for s, _ in slots:
            if (hit := caches[s].get(h)) is not None:
                votes.setdefault(h, {})[s] = hit
    complete = {h for h, vs in votes.items() if len(vs) == len(slots)}
    base, _ = split_template(text)
    batches = J.plan_batches(lt, lock, rows, batch_size=batch_size, skip=complete, template=base)
    return Plan(model, psha, text, slots, caches, votes, complete, batches)


def parse_votes(output: Any, batch: J.Batch) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """finding_hash -> vote fields, plus complaints about anything malformed."""
    if not isinstance(output, dict) or not isinstance(output.get("votes"), list):
        return {}, ["no structured votes in the voter output"]
    problems = []
    got: dict[str, dict[str, Any]] = {}
    for v in output["votes"]:
        ref = v.get("finding_ref") if isinstance(v, dict) else None
        if ref not in batch.refs:
            problems.append(f"unknown ref {ref!r}")
            continue
        if v.get("vote") not in VOTES or v.get("confidence") not in J.CONFIDENCES:
            problems.append(f"{ref}: bad vote/confidence {v.get('vote')}/{v.get('confidence')}")
            continue
        got[batch.refs[ref]] = {
            "vote": v["vote"],
            "confidence": v["confidence"],
            "attacker": str(v.get("attacker") or ""),
            "gain": str(v.get("gain") or ""),
            "rationale": str(v.get("rationale") or ""),
        }
    missing = [r for r, h in batch.refs.items() if h not in got]
    if missing:
        problems.append(f"no vote for {', '.join(missing)}")
    return got, problems


# --- running --------------------------------------------------------------------------------


def run_panel(
    lt: LoadedTrial,
    lock: dict[str, Any],
    rows: list[dict[str, Any]],
    scores: Path,
    creds: Credentials,
    *,
    model: JudgeSpec | None = None,
    batch_size: int = J.DEFAULT_BATCH,
    max_cost_usd: float | None = None,
    runner: J.Runner | None = None,
    log: Callable[[str], None] = print,
) -> J.JudgeRun:
    """Collect every missing vote, then write scores/judge.jsonl for fully voted findings."""
    runner = J.podman_runner if runner is None else runner
    p = plan(lt, lock, rows, model=model, batch_size=batch_size)
    rt = lt.trial.runtime
    cache_dir = Path(rt.cache_dir).expanduser()
    res = J.JudgeRun(rows=[], cached=len(p.complete & {r["finding_hash"] for r in rows}))
    calls = sum(
        1
        for b in p.batches
        for s, _ in p.slots
        if any(s not in p.votes.get(h, {}) for h in b.refs.values())
    )
    log(
        f"panel of {len(p.slots)} ({', '.join(s for s, _ in p.slots)}): {res.cached} cached, "
        f"{sum(len(b.items) for b in p.batches)} to vote on in {len(p.batches)} batches "
        f"({calls} calls)"
    )

    scrub = Scrubber(creds.secret_values)
    builtins = lock["image"].get("builtins") or {}
    runs_dir = scores / "judge_runs"
    work_root = Path(rt.work_dir).resolve() / f"judge-{os.getpid()}"
    arenas = {a.id: a for a in lt.arenas}
    prepared: dict[str, Path] = {}
    stop = False
    try:
        for b in p.batches:
            for slot, lens in p.slots:
                if all(slot in p.votes.get(h, {}) for h in b.refs.values()):
                    continue
                left = None if max_cost_usd is None else max_cost_usd - res.spent_usd
                if left is not None and left <= 0:
                    res.problems.append(f"judge budget ${max_cost_usd:.2f} reached, stopping")
                    stop = True
                    break
                cap = p.model.max_budget_usd if left is None else min(p.model.max_budget_usd, left)
                spec = cc.plain_spec(
                    p.model,
                    J.TOOLS,
                    creds.mode,
                    max(cap, 0.01),
                    builtin_skills=tuple(builtins.get("skills") or []),
                    builtin_plugins=tuple(builtins.get("plugins") or []),
                )
                if b.arena not in prepared:
                    dest = work_root / b.arena / "arena"
                    J.prepare_arena(
                        arenas[b.arena], lock["arenas"][b.arena]["sha"], cache_dir, dest
                    )
                    prepared[b.arena] = dest
                cost, got, problems = _vote_batch(
                    b, slot, lens, p, lt, spec, prepared[b.arena], work_root, creds, scrub,
                    runner, runs_dir,
                )  # fmt: skip
                res.spent_usd += cost
                now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
                for h, v in got.items():
                    if slot in p.votes.get(h, {}):
                        continue  # already voted in an earlier run; keep that vote
                    obj = {
                        "finding_hash": h,
                        "judge_model": p.model.slug,
                        "slot": slot,
                        "lens": lens,
                        **v,
                        "prompt_sha": p.psha,
                        "batch_id": b.batch_id,
                        "voted_at": now,
                    }
                    p.caches[slot].put(h, obj)
                    p.votes.setdefault(h, {})[slot] = obj
                    res.votes += 1
                res.problems += [f"{b.batch_id}-{slot}: {x}" for x in problems]
                log(
                    f"{b.batch_id}-{slot} {b.arena}: {len(got)}/{len(b.items)} votes, "
                    f"${cost:.3f} (total ${res.spent_usd:.2f})"
                )
            if stop:
                break
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    done: dict[str, dict[str, Any]] = {}
    for h, vs in p.votes.items():
        if all(s in vs for s, _ in p.slots):
            done[h] = panel_row(h, p.slots, vs, p.model, p.psha)
    for r in rows:
        if (hit := done.get(r["finding_hash"])) is not None:
            res.rows.append(
                {**hit, "finding_id": r["finding_id"], "cluster_id": r.get("cluster_id")}
            )
    hashes = {r["finding_hash"] for r in rows}
    res.judged = len((set(done) & hashes) - p.complete)
    res.missing = len(hashes - set(done))
    write_jsonl(scores / "judge.jsonl", res.rows)
    return res


def _vote_batch(
    b: J.Batch,
    slot: str,
    lens: str,
    p: Plan,
    lt: LoadedTrial,
    spec: cc.BoutSpec,
    arena_dir: Path,
    work_root: Path,
    creds: Credentials,
    scrub: Scrubber,
    runner: J.Runner,
    runs_dir: Path,
) -> tuple[float, dict[str, dict[str, Any]], list[str]]:
    run_id = f"{b.batch_id}-{slot}"
    out = runs_dir / run_id
    prompt = p.prompt(lt, b, lens)
    call = J.run_call(
        run_id, prompt, load_schema(SCHEMA), spec, arena_dir, work_root, lt, creds, scrub,
        runner, out,
    )  # fmt: skip
    problems = call.problems
    got: dict[str, dict[str, Any]] = {}
    if call.trusted:
        got, bad = parse_votes(call.output, b)
        problems += bad
    record = {
        "batch_id": b.batch_id,
        "mode": JudgeMode.panel.value,
        "slot": slot,
        "lens": lens,
        "arena": b.arena,
        "judge_model": spec.model.slug,
        "refs": b.refs,
        "command": call.command,
        "exit_code": call.exit_code,
        "usage": call.usage,
        "votes": len(got),
        "problems": problems,
        "egress": call.egress,
    }
    (out / "record.json").write_text(scrub(json.dumps(record, indent=2, default=str)) + "\n")
    return call.cost, got, problems
