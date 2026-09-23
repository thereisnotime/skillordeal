"""Collect a round's bouts into `findings.jsonl` (one row per finding) and `bouts.csv`.

Only `ok` bouts contribute findings. Every bout, whatever its status, gets a `bouts.csv` row
so a report can show what was excluded and why.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skillordeal.lock import read_lock
from skillordeal.matrix import BoutKey, expand
from skillordeal.runner import RoundPaths
from skillordeal.yamlio import sha256_obj

# Fields that make up a finding's identity (data-contracts.md, "Finding identity").
HASH_FIELDS = ("file", "line_start", "line_end", "category", "cwe", "title", "description")

# Columns of bouts.csv, in order.
BOUT_COLUMNS = [
    "bout_id",
    "contender",
    "arena",
    "task",
    "model",
    "rep",
    "status",
    "findings",
    "cost_usd",
    "tokens_total",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "duration_s",
    "api_s",
    "turns",
    "skill_fired",
    "first_turn_prompt_tokens",
    "rss_peak_kb",
    "threads_peak",
    "fds_peak",
    "cpu_s",
]
# Text columns that must never be coerced back to numbers when bouts.csv is re-read.
BOUT_STR_COLUMNS = ("bout_id", "contender", "arena", "task", "model", "status")

ARENA_PREFIX = "/arena/"  # where bouts see the repo; some agents cite absolute paths


def normalize_path(path: str | None) -> str:
    """Repo-relative POSIX path: no leading `./` or `/arena/` (the container mount point)."""
    p = (path or "").strip().replace("\\", "/")
    while True:
        if p.startswith(ARENA_PREFIX):
            p = p[len(ARENA_PREFIX) :]
        elif p.startswith("./"):
            p = p[2:]
        else:
            return p


def finding_hash(f: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of the identity fields, as the bout reported them.

    Missing optional fields hash as null. Values are not normalized, so the hash stays a pure
    function of findings.json.
    """
    return sha256_obj({k: f.get(k) for k in HASH_FIELDS})


def normalize_cwe(cwe: Any) -> list[str]:
    """`CWE-89`, `cwe-89`, `89` or a list of those -> sorted `["CWE-89"]`."""
    raw = cwe if isinstance(cwe, list) else [cwe] if cwe else []
    out = set()
    for c in raw:
        s = str(c).strip().upper().replace("_", "-")
        if not s:
            continue
        if s.isdigit():
            s = f"CWE-{s}"
        out.add(s)
    return sorted(out)


@dataclass
class Bout:
    key: BoutKey
    dir: Path
    record: dict[str, Any]
    findings: list[dict[str, Any]] = field(default_factory=list)  # raw, from findings.json

    @property
    def status(self) -> str:
        return self.record.get("status", "pending")


@dataclass
class Round:
    paths: RoundPaths
    lock: dict[str, Any]
    bouts: list[Bout]


def load_round(paths: RoundPaths) -> Round:
    lock = read_lock(paths.lock)
    bouts = []
    for key in expand(lock):
        d = paths.bout(key.bout_id)
        rec_path = d / "record.json"
        rec = json.loads(rec_path.read_text()) if rec_path.exists() else {}
        found: list[dict[str, Any]] = []
        if rec.get("status") == "ok" and (d / "findings.json").exists():
            data = json.loads((d / "findings.json").read_text())
            found = [f for f in data.get("findings") or [] if isinstance(f, dict)]
        bouts.append(Bout(key=key, dir=d, record=rec, findings=found))
    return Round(paths=paths, lock=lock, bouts=bouts)


def finding_rows(rnd: Round) -> list[dict[str, Any]]:
    """One row per finding of every `ok` bout, in bout then findings.json order."""
    rows = []
    for b in rnd.bouts:
        k = b.key
        for n, f in enumerate(b.findings, 1):
            line_start = f.get("line_start")
            rows.append(
                {
                    "finding_id": f"{k.bout_id}:{n}",
                    "finding_hash": finding_hash(f),
                    "bout_id": k.bout_id,
                    "contender": k.contender,
                    "arena": k.arena,
                    "task": k.task,
                    "model": k.model,
                    "rep": k.rep,
                    "category": f.get("category"),
                    "severity": f.get("severity"),
                    "confidence": f.get("confidence"),
                    "cwe": f.get("cwe"),
                    "file": normalize_path(f.get("file")),
                    "line_start": line_start,
                    "line_end": f.get("line_end") or line_start,
                    "title": f.get("title"),
                    # not part of the key-field list, but the judge and review UI need them
                    "description": f.get("description"),
                    "evidence": f.get("evidence"),
                    "recommendation": f.get("recommendation"),
                }
            )
    return rows


def bout_row(b: Bout) -> dict[str, Any]:
    rec, k = b.record, b.key
    usage = rec.get("usage") or {}
    tokens = usage.get("tokens") or {}
    res = rec.get("resources") or {}

    def secs(ms: Any) -> float | None:
        return round(ms / 1000, 3) if isinstance(ms, int | float) else None

    return {
        "bout_id": k.bout_id,
        "contender": k.contender,
        "arena": k.arena,
        "task": k.task,
        "model": k.model,
        "rep": k.rep,
        "status": b.status,
        "findings": len(b.findings) if b.status == "ok" else rec.get("findings_count"),
        "cost_usd": round(c, 6) if isinstance(c := usage.get("total_cost_usd"), float) else c,
        "tokens_total": tokens.get("total_tokens"),
        "input_tokens": tokens.get("input_tokens"),
        "output_tokens": tokens.get("output_tokens"),
        "cache_read_tokens": tokens.get("cache_read_tokens"),
        "cache_creation_tokens": tokens.get("cache_creation_tokens"),
        "duration_s": secs(usage.get("duration_ms")),
        "api_s": secs(usage.get("duration_api_ms")),
        "turns": usage.get("num_turns"),
        "skill_fired": rec.get("skill_fired"),
        "first_turn_prompt_tokens": rec.get("first_turn_prompt_tokens"),
        "rss_peak_kb": res.get("rss_peak_kb"),
        "threads_peak": res.get("threads_peak"),
        "fds_peak": res.get("fds_peak"),
        "cpu_s": res.get("cpu_usage_s"),
    }


def bout_rows(rnd: Round) -> list[dict[str, Any]]:
    return [bout_row(b) for b in rnd.bouts]
