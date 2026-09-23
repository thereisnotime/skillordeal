"""Cluster findings that describe the same problem, across all bouts of an arena.

Pure heuristic, no model: two findings are linked when they cite the same file, their line
ranges overlap within ±window, and they agree on the kind of problem. Clusters are the
connected components of that graph, so the result doesn't depend on row order.

"Agree on the kind" means a shared CWE when both findings cite one, and the same category
otherwise. Nearly every audit finding is `security`, so letting category alone decide would
merge an SQL injection with an XSS three lines below it.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from skillordeal.score.findings import normalize_cwe
from skillordeal.yamlio import sha256_text

DEFAULT_WINDOW = 5


def _span(f: dict[str, Any]) -> tuple[int, int] | None:
    s, e = f.get("line_start"), f.get("line_end")
    if not isinstance(s, int):
        return None
    e = e if isinstance(e, int) else s
    return min(s, e), max(s, e)


def same_problem(a: dict[str, Any], b: dict[str, Any], window: int = DEFAULT_WINDOW) -> bool:
    if a["arena"] != b["arena"] or a["file"] != b["file"]:
        return False
    sa, sb = _span(a), _span(b)
    if sa is None or sb is None or sa[0] > sb[1] + window or sb[0] > sa[1] + window:
        return False
    ca, cb = set(normalize_cwe(a.get("cwe"))), set(normalize_cwe(b.get("cwe")))
    if ca and cb:
        return bool(ca & cb)
    return a.get("category") == b.get("category")


def cluster_id(arena: str, member_hashes: list[str]) -> str:
    """Stable id: derived from the smallest member finding_hash (and the arena)."""
    return "c-" + sha256_text(f"{arena}:{min(member_hashes)}")[:16]


def assign_clusters(rows: list[dict[str, Any]], window: int = DEFAULT_WINDOW) -> dict[str, str]:
    """finding_id -> cluster_id for every row (singletons get their own cluster)."""
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_file: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_file[(r["arena"], r["file"])].append(i)
    for idx in by_file.values():
        for x, i in enumerate(idx):
            for j in idx[x + 1 :]:
                if same_problem(rows[i], rows[j], window):
                    parent[find(i)] = find(j)

    members: dict[int, list[int]] = defaultdict(list)
    for i in range(len(rows)):
        members[find(i)].append(i)
    out = {}
    for group in members.values():
        cid = cluster_id(rows[group[0]]["arena"], [rows[i]["finding_hash"] for i in group])
        for i in group:
            out[rows[i]["finding_id"]] = cid
    return out


def unique_per_contender(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per arena and contender: findings, distinct clusters, and clusters nobody else found.

    `rows` need a `cluster_id`. Counts are over all reps, so a contender with more reps has
    more chances; compare `clusters` with that in mind (or look at per-bout numbers).
    """
    found_by: dict[tuple[str, str], set[str]] = defaultdict(set)  # (arena, cluster) -> contenders
    per: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        found_by[(r["arena"], r["cluster_id"])].add(r["contender"])
        p = per.setdefault(
            (r["arena"], r["contender"]), {"bouts": set(), "findings": 0, "clusters": set()}
        )
        p["bouts"].add(r["bout_id"])
        p["findings"] += 1
        p["clusters"].add(r["cluster_id"])
    out = []
    for (arena, contender), p in sorted(per.items()):
        exclusive = [c for c in p["clusters"] if found_by[(arena, c)] == {contender}]
        out.append(
            {
                "arena": arena,
                "contender": contender,
                "bouts": len(p["bouts"]),
                "findings": p["findings"],
                "clusters": len(p["clusters"]),
                "exclusive_clusters": len(exclusive),
            }
        )
    return out
