## Why

A round leaves one directory per bout. To compare contenders we need everything in a couple of flat files: one row per finding with a stable identity, and one row per bout with the numbers people ask about (tokens, cost, time, turns, RAM). Nothing downstream (ground truth, judge, review UI, reports) should have to walk bout directories.

## What Changes

- New `skillordeal.score` package with `findings.py`: loads every bout of a locked round, computes `finding_id` and `finding_hash` exactly as in docs/data-contracts.md, and normalizes file paths.
- `scores/findings.jsonl` (only `ok` bouts contribute findings) and `scores/bouts.csv` (every bout, any status).
- New `skillordeal score TRIAL -r ROUND` command in `cli_score.py`, registered from `cli.py`, and a `just score` recipe.

## Capabilities

### New Capabilities
- `score-collection`: a round's bouts become `findings.jsonl` and `bouts.csv` with stable finding identities.

### Modified Capabilities
None.

## Impact

Read-only over bout directories; writes only under `rounds/<round>/scores/`. No network, no new dependencies.
