"""Everything the review server serves: findings joined with scores and labels, code excerpts."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from skillordeal.bout import prepare_arena
from skillordeal.config import Arena
from skillordeal.rounddata import (
    Round,
    append_label,
    bout_findings,
    effective_labels,
    findings_from_bouts,
    make_label,
    normalize_path,
    read_jsonl,
    read_labels,
    read_yaml,
)

CONTEXT_LINES = 15
MAX_EXCERPT_BYTES = 2_000_000
# Fields that could tell a reviewer which contender wrote a finding.
BLIND_FIELDS = ("contender", "bout_id", "rep")


class ExcerptError(ValueError):
    pass


def read_excerpt(
    root: Path,
    file: str,
    line_start: int | None,
    line_end: int | None,
    context: int = CONTEXT_LINES,
) -> dict[str, Any]:
    """Lines around [line_start, line_end] of root/file, refusing anything outside root.

    `file` comes from agent output, so treat it as hostile: absolute paths, `..` and symlinks
    that resolve outside the arena are all rejected.
    """
    rel = normalize_path(file)
    if not rel or Path(rel).is_absolute() or "\x00" in rel:
        raise ExcerptError("bad path")
    base = root.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base) or target == base:
        raise ExcerptError("path escapes the arena")
    if not target.is_file():
        raise ExcerptError(f"{rel}: not a file in the arena")
    if target.stat().st_size > MAX_EXCERPT_BYTES:
        raise ExcerptError(f"{rel}: too large to show")
    text = target.read_bytes().decode("utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(line_start or 1))
    end = max(start, int(line_end or start))
    lo = max(1, start - context)
    hi = min(len(lines), end + context)
    return {
        "file": rel,
        "line_start": start,
        "line_end": end,
        "total_lines": len(lines),
        "lines": [{"n": n, "text": lines[n - 1]} for n in range(lo, hi + 1)],
        "out_of_range": start > len(lines),
    }


class ReviewState:
    """Loads a round once and answers the server's questions about it."""

    def __init__(
        self,
        rnd: Round,
        *,
        arenas: dict[str, Arena],
        arena_files: dict[str, Path],
        lock: dict[str, Any],
        cache_dir: Path,
        labeler: str,
        show_contenders: bool = False,
        token: str = "",
        trial_title: str = "",
    ):
        self.rnd = rnd
        self.arenas = arenas
        self.arena_files = arena_files
        self.lock = lock
        self.cache_dir = cache_dir
        self.labeler = labeler
        self.show_contenders = show_contenders
        self.token = token
        self.trial_title = trial_title
        self.warnings: list[str] = []
        self._prep_lock = threading.Lock()
        self._prepared: dict[str, Path] = {}
        self._load()

    # --- loading ---------------------------------------------------------------------

    def _load(self) -> None:
        scores = self.rnd.scores
        rows = read_jsonl(scores / "findings.jsonl")
        if not rows:
            rows = findings_from_bouts(self.rnd)
            if rows:
                self.warnings.append(
                    "scores/findings.jsonl not found; findings read straight from bouts "
                    "(no ground-truth or judge verdicts until `skillordeal score` runs)"
                )
        gt = {r.get("finding_id"): r for r in read_jsonl(scores / "gt_matches.jsonl")}
        gt_by_hash = {r.get("finding_hash"): r for r in gt.values()}
        # one verdict per (finding_hash, judge_model); a later line wins
        judged: dict[tuple[str, str], dict[str, Any]] = {}
        for r in read_jsonl(scores / "judge.jsonl"):
            judged[(str(r.get("finding_hash") or ""), str(r.get("judge_model") or ""))] = {
                k: r.get(k) for k in ("judge_model", "verdict", "confidence", "rationale")
            }
        judge: dict[str, list[dict[str, Any]]] = {}
        for (h, _), v in judged.items():
            judge.setdefault(h, []).append(v)

        details: dict[str, list[dict[str, Any]]] = {}
        self.findings: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        for r in rows:
            fid = str(r.get("finding_id") or "")
            bout_id, _, n = fid.rpartition(":")
            if not bout_id or not n.isdigit():
                continue
            if bout_id not in details:
                details[bout_id] = bout_findings(self.rnd.bouts, bout_id)
            full = details[bout_id][int(n) - 1] if int(n) <= len(details[bout_id]) else {}
            g = gt.get(fid) or gt_by_hash.get(r.get("finding_hash")) or {}
            f = {
                **{k: full.get(k) for k in ("description", "evidence", "recommendation")},
                **r,
                "bout_id": r.get("bout_id") or bout_id,
                "file": normalize_path(r.get("file") or full.get("file")),
                "gt": {"verdict": g.get("verdict"), "issue_id": g.get("issue_id")} if g else None,
                "judge": judge.get(r.get("finding_hash", ""), []),
            }
            self.findings.append(f)
            self.by_id[fid] = f
        # Group by place in the code, not by contender: interleaves contenders (blind) and
        # puts likely duplicates next to each other.
        self.findings.sort(
            key=lambda f: (
                str(f.get("arena") or ""),
                str(f.get("file") or ""),
                int(f.get("line_start") or 0),
                str(f.get("finding_hash") or ""),
            )
        )
        self.issues = {aid: self._issues(aid) for aid in self.arenas}

    def _issues(self, arena_id: str) -> list[dict[str, Any]]:
        a = self.arenas[arena_id]
        if not a.groundtruth:
            return []
        gt = read_yaml((self.arena_files[arena_id].parent / a.groundtruth).resolve())
        return [
            {k: i.get(k) for k in ("id", "title", "category", "cwe", "severity", "locations")}
            for i in gt.get("issues") or []
            if isinstance(i, dict) and i.get("id")
        ]

    # --- api -------------------------------------------------------------------------

    def meta(self) -> dict[str, Any]:
        return {
            "trial": self.trial_title,
            "round": self.rnd.name,
            "labeler": self.labeler,
            "blind": not self.show_contenders,
            "arenas": sorted({str(f.get("arena")) for f in self.findings if f.get("arena")}),
            "issues": self.issues,
            "warnings": self.warnings,
        }

    def findings_payload(self) -> list[dict[str, Any]]:
        labels = effective_labels(read_labels(self.rnd.labels))
        by_hash: dict[str, list[dict[str, Any]]] = {}
        for (h, _who), lab in labels.items():
            by_hash.setdefault(h, []).append(lab)
        out = []
        for f in self.findings:
            item = dict(f)
            if not self.show_contenders:
                for k in BLIND_FIELDS:
                    item.pop(k, None)
            h = f.get("finding_hash", "")
            mine = labels.get((h, self.labeler))
            item["label"] = (
                {k: mine.get(k) for k in ("verdict", "issue_id", "note", "ts")} if mine else None
            )
            item["other_labels"] = [
                {"labeler": x["labeler"], "verdict": x.get("verdict")}
                for x in by_hash.get(h, [])
                if x["labeler"] != self.labeler
            ]
            out.append(item)
        return out

    def add_label(self, body: dict[str, Any]) -> dict[str, Any]:
        f = self.by_id.get(str(body.get("finding_id") or ""))
        if not f:
            raise KeyError("unknown finding_id")
        issue_id = body.get("issue_id") or None
        if issue_id is not None:
            known = {i["id"] for i in self.issues.get(str(f.get("arena")), [])}
            if issue_id not in known:
                raise ValueError(f"unknown issue_id {issue_id!r} for arena {f.get('arena')}")
        label = make_label(
            finding_hash=f["finding_hash"],
            finding_id=f["finding_id"],
            arena=str(f.get("arena") or ""),
            verdict=str(body.get("verdict") or ""),
            issue_id=issue_id,
            labeler=self.labeler,
            note=str(body.get("note") or "")[:4000],
        )
        append_label(self.rnd.labels, label)
        return label

    def arena_root(self, arena_id: str) -> Path:
        """The arena exactly as the agent saw it (same export + strip), prepared once."""
        with self._prep_lock:
            if arena_id in self._prepared:
                return self._prepared[arena_id]
            a = self.arenas.get(arena_id)
            sha = ((self.lock.get("arenas") or {}).get(arena_id) or {}).get("sha")
            if not a or not sha:
                raise ExcerptError(f"arena {arena_id!r} is not in the lock")
            dest = self.cache_dir / "review" / "arenas" / f"{arena_id}-{sha[:12]}"
            marker = dest.parent / f"{dest.name}.json"
            if not (marker.exists() and dest.is_dir()):
                facts = prepare_arena(a, sha, self.cache_dir, dest)
                marker.write_text(json.dumps({"sha": sha, **facts}) + "\n")
            self._prepared[arena_id] = dest
            return dest

    def excerpt(self, finding_id: str) -> dict[str, Any]:
        f = self.by_id.get(finding_id)
        if not f:
            raise KeyError("unknown finding_id")
        root = self.arena_root(str(f.get("arena")))
        return read_excerpt(root, str(f.get("file") or ""), f.get("line_start"), f.get("line_end"))
