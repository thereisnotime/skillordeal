"""`skillordeal score`: findings, ground truth, dedup and summary for one round. No network."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skillordeal.config import LoadedTrial
from skillordeal.runner import RoundPaths
from skillordeal.score import (
    dedup,
    groundtruth,
    labels_file,
    read_jsonl,
    scores_dir,
    summary,
    write_csv,
    write_jsonl,
)
from skillordeal.score.findings import BOUT_COLUMNS, bout_rows, finding_rows, load_round


@dataclass
class ScoreResult:
    scores: Path
    bouts: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    gt_rows: list[dict[str, Any]]
    unique: list[dict[str, Any]]
    summary: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)


def run_score(lt: LoadedTrial, paths: RoundPaths, window: int | None = None) -> ScoreResult:
    """Regenerate everything under scores/ except judge.jsonl, which is only read.

    Raises ScoreError after writing the CSVs when pyarrow is missing for the parquet file.
    """
    out = scores_dir(paths)
    rnd = load_round(paths)
    bouts = bout_rows(rnd)
    rows = finding_rows(rnd)
    warnings: list[str] = []

    cids = dedup.assign_clusters(rows, dedup.DEFAULT_WINDOW if window is None else window)
    for r in rows:
        r["cluster_id"] = cids[r["finding_id"]]

    gt_rows: list[dict[str, Any]] = []
    gt_aggs: dict[str, dict[str, Any]] = {}
    for arena in rnd.lock["arenas"]:
        loaded = groundtruth.groundtruth_for(lt, rnd.lock, arena)
        if loaded is None:
            continue
        warnings += loaded.warnings
        for b in rnd.bouts:
            if b.key.arena != arena or b.status != "ok":
                continue
            mine = [r for r in rows if r["bout_id"] == b.key.bout_id]
            matched = groundtruth.match_bout(mine, loaded.gt, window)
            gt_rows += matched
            gt_aggs[b.key.bout_id] = groundtruth.bout_aggregates(matched, loaded.gt)

    unique = dedup.unique_per_contender(rows)
    judge_rows = read_jsonl(out / "judge.jsonl")
    labels = summary.read_labels(labels_file(paths))
    verdicts = summary.final_verdicts(rows, gt_rows, judge_rows, labels)
    summ = summary.build_summary(bouts, rows, verdicts, gt_aggs)

    written = [out / n for n in ("findings.jsonl", "gt_matches.jsonl", "verdicts.jsonl")]
    write_jsonl(written[0], rows)
    write_jsonl(written[1], gt_rows)
    write_jsonl(written[2], verdicts)
    write_csv(out / "bouts.csv", bouts, BOUT_COLUMNS)
    write_csv(out / "unique.csv", unique)
    written += [out / "bouts.csv", out / "unique.csv"]
    res = ScoreResult(out, bouts, rows, gt_rows, unique, summ, warnings, written)
    res.written += summary.write_summary(summ, out)
    return res
