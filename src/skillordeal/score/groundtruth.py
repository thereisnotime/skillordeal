"""Ground truth: load an arena's `groundtruth.yaml` and match findings against it.

Matching rules are in docs/data-contracts.md. Each finding is tied to its single best issue;
per bout, the first finding tied to an issue is its `tp` and later ones are `dup`. Findings
that match nothing are `fp` when the ground truth is `complete`, and `unknown` otherwise (we
can't call something wrong if the list of real issues isn't known to be exhaustive).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from skillordeal.config import LoadedTrial, Strict
from skillordeal.hashing import file_sha256
from skillordeal.score.findings import normalize_cwe, normalize_path
from skillordeal.yamlio import load_yaml

Category = Literal["security", "quality", "maintainability", "performance", "other"]
Severity = Literal["critical", "high", "medium", "low", "info"]
GtVerdict = Literal["tp", "dup", "fp", "unknown"]
MatchBasis = Literal["cwe", "category"]


class Location(Strict):
    file: str
    lines: tuple[int, int]  # inclusive

    @field_validator("file")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_path(v)

    @field_validator("lines")
    @classmethod
    def _ordered(cls, v: tuple[int, int]) -> tuple[int, int]:
        if v[0] < 1 or v[1] < v[0]:
            raise ValueError(f"lines {list(v)} must be [start, end] with 1 <= start <= end")
        return v


class Issue(Strict):
    id: str
    title: str
    category: Category
    cwe: list[str] = Field(default_factory=list)
    severity: Severity
    locations: list[Location] = Field(min_length=1)
    source: Literal["documented", "manual", "cve", "seeded", "human-label"]
    notes: str = ""

    @field_validator("cwe", mode="before")
    @classmethod
    def _cwe(cls, v: Any) -> list[str]:
        return normalize_cwe(v)


class GroundTruth(Strict):
    arena: str
    sha: str
    # true only when every real issue in the arena is listed; unlocks fp (and real precision)
    complete: bool = False
    window: int = Field(default=5, ge=0)
    issues: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> GroundTruth:
        ids = [i.id for i in self.issues]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate issue ids: {dupes}")
        return self


@dataclass
class LoadedGroundTruth:
    gt: GroundTruth
    path: Path
    warnings: list[str]


def load_groundtruth(path: Path) -> GroundTruth:
    return GroundTruth.model_validate(load_yaml(path.read_text()))


def groundtruth_for(
    lt: LoadedTrial, lock: dict[str, Any], arena_id: str
) -> LoadedGroundTruth | None:
    """The arena's ground truth, with warnings when it doesn't line up with the lock."""
    arena = next((a for a in lt.arenas if a.id == arena_id), None)
    if arena is None or not arena.groundtruth:
        return None
    path = (lt.arena_files[arena_id].parent / arena.groundtruth).resolve()
    gt = load_groundtruth(path)
    warnings = []
    la = (lock.get("arenas") or {}).get(arena_id) or {}
    if gt.arena != arena_id:
        warnings.append(f"{path}: arena {gt.arena!r} != {arena_id!r}")
    if la.get("sha") and gt.sha != la["sha"]:
        warnings.append(
            f"{arena_id}: ground truth was written against {gt.sha[:12]}, "
            f"round is locked to {la['sha'][:12]}; line numbers may be off"
        )
    locked = (la.get("groundtruth") or {}).get("sha256")
    if locked and file_sha256(path) != locked:
        warnings.append(f"{arena_id}: {path.name} changed since the round was locked")
    elif not locked:
        warnings.append(f"{arena_id}: the lock has no ground truth hash (added after locking)")
    return LoadedGroundTruth(gt=gt, path=path, warnings=warnings)


# --- matching ---------------------------------------------------------------------------


def _span(f: dict[str, Any]) -> tuple[int, int] | None:
    start = f.get("line_start")
    if not isinstance(start, int):
        return None
    end = f.get("line_end")
    end = end if isinstance(end, int) else start
    return min(start, end), max(start, end)


@dataclass(frozen=True)
class Hit:
    """One way a finding matches an issue, with what's needed to pick the best issue."""

    issue: Issue
    basis: MatchBasis
    exact: bool  # the ranges overlap without the window
    distance: float  # |midpoint(finding) - midpoint(location)|

    @property
    def rank(self) -> tuple[bool, float, str]:
        return (not self.exact, self.distance, self.issue.id)


def match_issue(f: dict[str, Any], issue: Issue, window: int) -> Hit | None:
    """How `f` matches `issue`, or None.

    Kind: when both the finding and the issue name CWEs, they must share an exact id (basis
    `cwe`); otherwise the categories must be equal (basis `category`). Place: same normalized
    file, and line ranges that overlap once the location is widened by ±window. Of several
    matching locations the closest one counts.
    """
    span = _span(f)
    if span is None:
        return None
    cwes = set(normalize_cwe(f.get("cwe")))
    basis: MatchBasis
    if cwes and issue.cwe:
        if not cwes & set(issue.cwe):
            return None
        basis = "cwe"
    elif f.get("category") == issue.category:
        basis = "category"
    else:
        return None
    file = normalize_path(f.get("file"))
    best: Hit | None = None
    for loc in issue.locations:
        a, b = loc.lines
        if loc.file != file or span[0] > b + window or span[1] < a - window:
            continue
        hit = Hit(
            issue,
            basis,
            exact=span[0] <= b and span[1] >= a,
            distance=abs((span[0] + span[1]) - (a + b)) / 2,
        )
        if best is None or hit.rank < best.rank:
            best = hit
    return best


def matches(f: dict[str, Any], issue: Issue, window: int) -> bool:
    return match_issue(f, issue, window) is not None


def best_hit(f: dict[str, Any], gt: GroundTruth, window: int) -> Hit | None:
    """The most plausible issue: exact line overlap beats window-only overlap, then the
    closest midpoints win, then the issue id (so ties are deterministic)."""
    hits = [h for i in gt.issues if (h := match_issue(f, i, window)) is not None]
    return min(hits, key=lambda h: h.rank) if hits else None


def match_bout(
    findings: list[dict[str, Any]], gt: GroundTruth, window: int | None = None
) -> list[dict[str, Any]]:
    """One gt_matches row per finding of a single bout, in findings.json order.

    Each finding is tied to its best issue. The first finding tied to an issue is the `tp`,
    later ones are `dup`. A nearby second issue is not credited instead: the finding most
    likely describes the issue it was tied to.
    """
    w = gt.window if window is None else window
    seen: set[str] = set()
    rows = []
    for f in findings:
        hit = best_hit(f, gt, w)
        verdict: GtVerdict
        if hit is None:
            verdict = "fp" if gt.complete else "unknown"
        elif hit.issue.id in seen:
            verdict = "dup"
        else:
            verdict = "tp"
            seen.add(hit.issue.id)
        dist = None if hit is None else hit.distance
        rows.append(
            {
                "finding_id": f.get("finding_id"),
                "finding_hash": f.get("finding_hash"),
                "verdict": verdict,
                "issue_id": hit.issue.id if hit else None,
                "match_basis": hit.basis if hit else None,
                "line_distance": int(dist) if dist is not None and dist.is_integer() else dist,
            }
        )
    return rows


def _ratio(a: int, b: int) -> float | None:
    return a / b if b else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None:
        return None
    return 2 * p * r / (p + r) if p + r else 0.0


def _r4(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def bout_aggregates(rows: list[dict[str, Any]], gt: GroundTruth) -> dict[str, Any]:
    """tp/dup/fp/unknown counts plus precision, recall and f1 for one bout.

    With incomplete ground truth, unmatched findings might still be real, so only a lower
    bound on precision (tp / (tp + unknown)) is reported and `precision`/`f1` stay empty.
    """
    n = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("tp", "dup", "fp", "unknown")}
    matched = {r["issue_id"] for r in rows if r["verdict"] == "tp"}
    recall = _ratio(len(matched), len(gt.issues))
    lower = _ratio(n["tp"], n["tp"] + n["fp"] + n["unknown"])
    precision = _ratio(n["tp"], n["tp"] + n["fp"]) if gt.complete else None
    return {
        **n,
        "gt_issues": len(gt.issues),
        "gt_complete": gt.complete,
        "issues_found": len(matched),
        "precision": _r4(precision),
        "precision_lower_bound": _r4(lower),
        "recall": _r4(recall),
        "f1": _r4(_f1(precision, recall)),
        "f1_lower_bound": _r4(_f1(lower, recall)),
    }
