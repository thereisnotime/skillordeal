"""Scoring: turn bout directories into rows under rounds/<round>/scores/ (docs/data-contracts.md).

Every stage reads and writes plain files, so each one can be re-run on its own.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from skillordeal.runner import RoundPaths


class ScoreError(RuntimeError):
    pass


def scores_dir(paths: RoundPaths) -> Path:
    return paths.root / "scores"


def labels_file(paths: RoundPaths) -> Path:
    return paths.trial_dir / "labels" / "labels.jsonl"


# --- plain file io -----------------------------------------------------------------


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = columns or list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: _csv_cell(r.get(k)) for k in cols})


def _csv_cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def _coerce(v: str) -> Any:
    if v == "":
        return None
    if v in ("true", "false"):
        return v == "true"
    for t in (int, float):
        try:
            return t(v)
        except ValueError:
            pass
    return v


def read_csv(path: Path, keep_str: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Read a CSV written by `write_csv`, turning cells back into None/bool/int/float.

    Columns in `keep_str` (ids, names) are left as text so a contender called `123` stays one.
    """
    if not path.exists():
        return []
    keep = set(keep_str)
    with path.open(newline="") as f:
        return [
            {k: (v if k in keep else _coerce(v)) for k, v in row.items()}
            for row in csv.DictReader(f)
        ]
