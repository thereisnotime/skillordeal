## Why

A round ends as a pile of bout directories and score files. People need one page that answers the trial's question. It has to show the uncertainty, the sample size and the failures, and it has to link to the raw bouts so any number can be checked. It should render on GitHub, and it should also exist as a richer offline HTML file.

## What Changes

- New `skillordeal report TRIAL -r ROUND [--markdown-only] [--seed] [--resamples]` command.
- It reads `scores/summary.parquet`, falling back to `summary.csv` and then `bouts.csv`, with duckdb (lazy import, `analysis` extra).
- It writes `RESULTS.md`, `report.html`, `results.csv` and `results.parquet` into `rounds/<round>/`.
- Per arena × task × model × contender it shows the mean and a 95% bootstrap CI over reps (numpy, fixed seed) for findings, TP, precision, recall, F1, judge-valid, cost, tokens, duration, turns and RSS peak. It also shows Δ vs baseline with a two-sample bootstrap CI, cost per TP, skill-fired rate, and the forced-injection check. That check is first-turn prompt tokens minus the baseline mean, flagged when ≤ 0.
- Every cell carries n (ok/total), every bout is linked, and bouts that didn't finish ok are counted and listed with the reason from their record.

## Capabilities

### New Capabilities
- `report`: honest, reproducible round reports in Markdown, HTML and tabular exports.

### Modified Capabilities
None.

## Impact

New modules `skillordeal.report` and `skillordeal.report_html`, plus CSS/JS assets in `skillordeal/report_assets/`. Uses the existing `analysis` extra (duckdb, numpy, pyarrow, matplotlib); the core install doesn't import them.
