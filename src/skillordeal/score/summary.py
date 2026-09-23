"""Merge bouts, ground truth, judge verdicts and human labels into per-bout summary rows.

Each finding gets one final verdict, taken from the first source that has a decisive answer:
human label, then ground truth, then judge. "Not sure" answers (`unsure`, `unknown`,
`unverifiable`) fall through to the next source instead of hiding it.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from skillordeal.score import ScoreError, write_csv

# What each source's verdicts mean on the common tp/fp/dup scale. Missing = not decisive.
HUMAN_MAP = {"tp": "tp", "fp": "fp", "dup": "dup"}
GT_MAP = {"tp": "tp", "fp": "fp", "dup": "dup"}
JUDGE_MAP = {"valid": "tp", "invalid": "fp", "duplicate": "dup"}
JUDGE_VERDICTS = ("valid", "invalid", "duplicate", "unverifiable")

GT_COLUMNS = [
    "tp",
    "dup",
    "fp",
    "unknown",
    "gt_issues",
    "gt_complete",
    "issues_found",
    "precision",
    "precision_lower_bound",
    "recall",
    "f1",
    "f1_lower_bound",
]


def read_labels(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ScoreError(f"{path}:{n}: not JSON ({e})") from e
    return rows


def human_verdicts(labels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """finding_hash -> {verdict, labelers, issue_id}.

    Later lines override earlier ones for the same (finding_hash, labeler). Several labelers
    are combined by majority over decisive verdicts; a tie is left undecided.
    """
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for lab in labels:
        if lab.get("finding_hash"):
            latest[(lab["finding_hash"], str(lab.get("labeler")))] = lab
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (h, _), lab in latest.items():
        by_hash[h].append(lab)
    out = {}
    for h, labs in by_hash.items():
        votes = Counter(HUMAN_MAP[x["verdict"]] for x in labs if x.get("verdict") in HUMAN_MAP)
        top = votes.most_common(2)
        verdict = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None
        issue = next((x.get("issue_id") for x in labs if x.get("issue_id")), None)
        out[h] = {"verdict": verdict, "labelers": len(labs), "issue_id": issue}
    return out


def final_verdicts(
    findings: list[dict[str, Any]],
    gt_rows: list[dict[str, Any]],
    judge_rows: list[dict[str, Any]],
    labels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One row per finding with each source's verdict and the merged one."""
    gt = {r["finding_id"]: r for r in gt_rows}
    judge = {r["finding_hash"]: r for r in judge_rows}
    human = human_verdicts(labels)
    out = []
    for f in findings:
        h = human.get(f["finding_hash"]) or {}
        g = gt.get(f["finding_id"]) or {}
        j = judge.get(f["finding_hash"]) or {}
        final, source = "unknown", None
        for src, v, table in (
            ("human", h.get("verdict"), HUMAN_MAP),
            ("gt", g.get("verdict"), GT_MAP),
            ("judge", j.get("verdict"), JUDGE_MAP),
        ):
            if v in table:
                final, source = table[v], src
                break
        out.append(
            {
                "finding_id": f["finding_id"],
                "finding_hash": f["finding_hash"],
                "bout_id": f["bout_id"],
                "cluster_id": f.get("cluster_id"),
                "human": h.get("verdict") if h else None,
                "human_labeled": bool(h),
                "gt": g.get("verdict"),
                "issue_id": h.get("issue_id") or g.get("issue_id"),
                "judge": j.get("verdict"),
                "verdict": final,
                "source": source,
            }
        )
    return out


def build_summary(
    bouts: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
    gt_aggregates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """bouts.csv rows plus gt, judge, human and final aggregates. Non-ok bouts get blanks."""
    by_bout: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in verdicts:
        by_bout[v["bout_id"]].append(v)
    clusters: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        if f.get("cluster_id"):
            clusters[f["bout_id"]].add(f["cluster_id"])

    rows = []
    for b in bouts:
        row = dict(b)
        ok = b.get("status") == "ok"
        vs = by_bout.get(b["bout_id"], [])
        row |= {c: None for c in GT_COLUMNS} | (gt_aggregates.get(b["bout_id"]) or {})
        jc = Counter(v["judge"] for v in vs)
        hc = Counter(v["human"] for v in vs if v["human_labeled"])
        fc = Counter(v["verdict"] for v in vs)
        tp, fp = fc["tp"], fc["fp"]
        agg: dict[str, Any] = {
            **{f"judge_{k}": jc[k] for k in JUDGE_VERDICTS},
            "judge_pending": jc[None],
            "human_tp": hc["tp"],
            "human_fp": hc["fp"],
            "human_dup": hc["dup"],
            "human_unsure": sum(1 for v in vs if v["human_labeled"] and v["human"] is None),
            "final_tp": tp,
            "final_fp": fp,
            "final_dup": fc["dup"],
            "final_unknown": fc["unknown"],
            "final_precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "clusters": len(clusters[b["bout_id"]]),
        }
        row |= agg if ok else dict.fromkeys(agg)
        rows.append(row)
    return rows


def write_summary(rows: list[dict[str, Any]], scores: Path) -> list[Path]:
    """summary.csv always; summary.parquet when the `analysis` extra (pyarrow) is installed."""
    write_csv(scores / "summary.csv", rows)
    try:  # optional dependency, imported here so scoring works without it
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ScoreError(
            f"wrote {scores / 'summary.csv'}, but summary.parquet needs pyarrow: "
            "install the analysis extra (uv sync --extra analysis)"
        ) from e
    pq.write_table(pa.Table.from_pylist(rows), scores / "summary.parquet")
    return [scores / "summary.csv", scores / "summary.parquet"]
