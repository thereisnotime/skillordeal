"""Readers and writers for the files in docs/data-contracts.md.

The review UI and the report only talk to scoring through these files, so this module is the
one place that knows their layout. It never scores anything itself.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillordeal.yamlio import load_yaml, sha256_obj

VERDICTS = ("tp", "fp", "dup", "unsure")
HASH_FIELDS = ("file", "line_start", "line_end", "category", "cwe", "title", "description")


@dataclass
class Round:
    """Paths for one round of one trial. `trial_dir` holds trial.yaml."""

    trial_dir: Path
    name: str

    @property
    def root(self) -> Path:
        return self.trial_dir / "rounds" / self.name

    @property
    def lock(self) -> Path:
        return self.root / "lock.yaml"

    @property
    def bouts(self) -> Path:
        return self.root / "bouts"

    @property
    def scores(self) -> Path:
        return self.root / "scores"

    @property
    def labels(self) -> Path:
        return self.trial_dir / "labels" / "labels.jsonl"


# --- generic jsonl -------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """All objects in a jsonl file. Blank and truncated lines are skipped, not fatal."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # a half-written last line from an interrupted writer
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


# --- findings ------------------------------------------------------------------------


def normalize_path(p: str | None) -> str:
    p = (p or "").strip()
    for prefix in ("/arena/", "./"):
        while p.startswith(prefix):
            p = p[len(prefix) :]
    return p


def finding_hash(f: dict[str, Any]) -> str:
    return sha256_obj({k: f.get(k) for k in HASH_FIELDS})


def bout_findings(bouts_dir: Path, bout_id: str) -> list[dict[str, Any]]:
    path = bouts_dir / bout_id / "findings.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    items = data.get("findings") if isinstance(data, dict) else None
    return [x for x in items or [] if isinstance(x, dict)]


def findings_from_bouts(rnd: Round) -> list[dict[str, Any]]:
    """Rebuild findings.jsonl-shaped rows straight from bout dirs.

    Used only when `skillordeal score` hasn't run yet, so there is no gt or judge data.
    """
    rows = []
    if not rnd.bouts.exists():
        return rows
    for bdir in sorted(p for p in rnd.bouts.iterdir() if p.is_dir()):
        rec_path = bdir / "record.json"
        rec = json.loads(rec_path.read_text()) if rec_path.exists() else {}
        for n, f in enumerate(bout_findings(rnd.bouts, bdir.name), 1):
            rows.append(
                {
                    "finding_id": f"{bdir.name}:{n}",
                    "finding_hash": finding_hash(f),
                    "bout_id": bdir.name,
                    "contender": (rec.get("contender") or {}).get("id"),
                    "arena": (rec.get("arena") or {}).get("id"),
                    "task": (rec.get("task") or {}).get("id"),
                    "model": (rec.get("model") or {}).get("requested"),
                    "rep": rec.get("rep"),
                    **{
                        k: f.get(k)
                        for k in (
                            "category",
                            "severity",
                            "confidence",
                            "cwe",
                            "file",
                            "line_start",
                            "line_end",
                            "title",
                        )
                    },
                }
            )
    return rows


# --- labels --------------------------------------------------------------------------

_label_lock = threading.Lock()


def read_labels(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def effective_labels(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """(finding_hash, labeler) -> label. Later lines override earlier ones."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r.get("finding_hash") and r.get("labeler"):
            out[(r["finding_hash"], r["labeler"])] = r
    return out


def make_label(
    *,
    finding_hash: str,
    finding_id: str,
    arena: str,
    verdict: str,
    labeler: str,
    issue_id: str | None = None,
    note: str = "",
    ts: str | None = None,
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    return {
        "finding_hash": finding_hash,
        "finding_id": finding_id,
        "arena": arena,
        "verdict": verdict,
        "issue_id": issue_id or None,
        "labeler": labeler,
        "ts": ts or dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": note or "",
    }


def append_label(path: Path, label: dict[str, Any]) -> None:
    """Append one line; flushed and fsynced so a crash never loses an acknowledged label."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(label, ensure_ascii=False, separators=(", ", ": ")) + "\n"
    with _label_lock, path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def labeler_name(name: str | None) -> str:
    if not name:
        import getpass

        name = getpass.getuser()
    return name if ":" in name else f"human:{name}"


# --- misc ----------------------------------------------------------------------------


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = load_yaml(path.read_text())
    return data if isinstance(data, dict) else {}
