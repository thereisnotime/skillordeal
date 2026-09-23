## Why

Counting findings says nothing about whether they are right. Arenas with known issues let us score a bout mechanically (tp, dup, fp or unknown, precision, recall), and a merged summary lets reports read one table instead of four sources. Contenders also report the same bug in slightly different ways, so we need to know how many distinct problems each one found.

## What Changes

- `score/groundtruth.py`: pydantic models for `groundtruth.yaml` and the matcher from docs/data-contracts.md (CWE first with a category fallback, ±window line overlap, best issue wins, first tie per bout is tp and later ones dup, unmatched is fp only on complete ground truth). Per-bout aggregates. Warnings when the ground truth doesn't line up with the lock.
- `score/dedup.py`: deterministic clustering of findings across all bouts of an arena, stable `cluster_id`, and unique findings per contender (`unique.csv`).
- `score/summary.py`: per-finding merged verdicts (human > ground truth > judge) in `verdicts.jsonl`, and per-bout `summary.parquet` / `summary.csv`.
- `score` now runs collection, ground truth, dedup and summary in one go (`score/pipeline.py`).
- docs/data-contracts.md: matching rules spelled out exactly (basis, ranking, `match_basis`, `line_distance`), plus the extra files.

## Capabilities

### New Capabilities
- `groundtruth-matching`: findings are matched against an arena's ground truth and scored per bout.
- `finding-dedup`: findings about the same problem share a stable cluster id.
- `score-summary`: one merged verdict per finding and one summary row per bout.

### Modified Capabilities
None.

## Impact

`summary.parquet` needs pyarrow from the `analysis` extra; it's imported lazily and `summary.csv` is written regardless. No network.
