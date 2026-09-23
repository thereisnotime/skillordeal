## 1. Data

- [x] 1.1 duckdb loader with parquet → summary.csv → bouts.csv fallback (lazy import, clear error without the extra)
- [x] 1.2 Aggregate cells per arena/task/model/contender; ok-only means, status counts, spend over all bouts

## 2. Statistics

- [x] 2.1 Percentile bootstrap with fixed seed; no interval for n < 2
- [x] 2.2 Two-sample bootstrap for Δ vs baseline; cost per TP; skill-fired rate; first-turn token delta + flag

## 3. Outputs

- [x] 3.1 RESULTS.md: header, per-group quality/cost/baseline tables, bout links, failed bouts, reproduce snippet
- [x] 3.2 results.csv / results.parquet exports
- [x] 3.3 report.html: inline theme-aware SVG charts (Pareto, cost/tokens, per-arena, resources), sortable tables
- [x] 3.4 `--markdown-only`

## 4. CLI and tests

- [x] 4.1 `skillordeal report` registered from cli.py; `just report`
- [x] 4.2 Tests: links + CI columns, determinism, failed-bout listing, injection flag, exports, HTML self-contained, fallbacks
