## Why

The r01 secure-coding round shows the gap: every chart is a mean with a CI, so a reader can see that `samber-golang-security` scores 7.3 TP against the baseline's 6, but not which vulnerabilities it caught that others missed, that 10 of dvpwa's 19 known issues were found by nobody, how much the three reps disagree, or that one contender never produced a valid bout. Picking a skill needs those answers, and today they are only recoverable by reading `scores/*.jsonl` by hand.

## What Changes

- New finding-level charts per arena: a ground-truth coverage grid (issue × contender, shaded by the share of ok bouts that found it), the same grid over finding clusters for arenas without ground truth, shared-vs-unique problems per contender, findings by self-reported severity, per-bout strip plots (quality, findings, cost, time), and cost and time per TP.
- New round-wide charts: bout status per contender (only when something failed), first-turn prompt tokens vs baseline (did the skill load), and tool-call mix with turns.
- `RESULTS.md` opens with "Key takeaways": factual bullets generated from the numbers (failed contenders, Δ CIs that exclude 0, best mean and cheapest per TP, issues nobody found, single-contender problems, skill-loading check), each linking the bouts behind it.
- `RESULTS.md` is reorganized per arena: charts each followed by a "How to read" line, then the tables, with long tables (cost, coverage, problems, CWE, severity, tool calls) in collapsed `<details>` blocks.
- `report.html` shows the takeaways, every chart, the new tables (sortable) and a per-contender drill-down listing every finding with its verdicts and bout link.
- All charts share a 10 in (960 px) width so they read at GitHub's content width.

## Capabilities

### New Capabilities
- `report-detail`: finding-level views (coverage, clusters, severity, CWE, tool calls), per-bout spread, efficiency, run health and generated takeaways in RESULTS.md and report.html.

### Modified Capabilities
None. The existing `report` and `report-visuals` requirements still hold; the new charts follow the same file, determinism and graceful-skip rules.

## Impact

New modules `skillordeal.report_detail` (loading and derived views, takeaways, tables) and `skillordeal.report_charts_detail`. `report.py`, `report_charts.py` and `report_html.py` call into them. Reads `scores/findings.jsonl`, `verdicts.jsonl`, `gt_matches.jsonl`, `judge.jsonl`, the arena's `groundtruth.yaml` and each bout's `record.json`; any of them may be missing. No new dependencies, no network.
