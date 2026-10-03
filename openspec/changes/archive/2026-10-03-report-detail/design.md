## Context

`report` aggregates per-bout rows (summary.csv / bouts.csv) into cells. The questions a reader has about which skill to use need the rows under those: which ground-truth issue each finding matched, which cluster it belongs to, its severity and CWE, and the tool calls in each bout's record. Those live in `scores/*.jsonl`, the trials repo's `groundtruth.yaml` and `bouts/*/record.json`.

## Goals / Non-Goals

**Goals:** show what each contender found and missed, the spread across reps, efficiency per TP, and run health; lead RESULTS.md with facts a reader can act on; never crash or draw an empty chart when a file is missing.

**Non-Goals:** new statistics (the CI and Δ machinery is unchanged), interpretation in the takeaways, re-scoring anything.

## Decisions

- **One loader, `report_detail.load_detail`.** Reads findings (falls back to the bout dirs when `findings.jsonl` is absent), joins the final verdict (`verdicts.jsonl`, else ground truth, else judge), loads ground-truth issues through the trial config leniently (any error means "no issues"), and keeps `tool_calls` from each record. Charts, markdown and HTML all take the resulting `Detail`, so they agree.
- **Coverage is per ok bout.** A cell is k of n ok bouts of that contender with a `tp` for the issue. Contenders with no ok bout are left out of the grid (the status chart and takeaways show them). The grid is skipped when no finding of the arena was matched against ground truth, because an all-empty grid would claim nobody found anything.
- **Arenas without ground truth use clusters.** The same grid, rows being finding clusters labelled by their most common file:line and CWE. A CWE heatmap was considered; with ~10 CWEs and 11 contenders the cluster grid answers "which problem" more precisely, so CWE stays a table.
- **Rows = issues, columns = contenders** in the grid: issue ids are long and there are more of them than contenders, so they get the horizontal labels.
- **Color.** Coverage uses series-1 at three opacities (0.6 / 0.8 / 1.0), so the faint end recedes toward the surface on both GitHub themes; the blended steps pass the dataviz ordinal checks on #ffffff and #0d1117. No 4-step opacity ramp passes on both surfaces, so severity uses a 4-step opaque blue ramp that does. Tool mix uses the first five categorical slots (validated adjacent pairs on both surfaces) plus muted "other". Bout status keeps ok muted and draws failures in the reserved status colors.
- **Strip plots instead of more CIs.** With n = 3 the dots are the honest picture; the mean is a tick and the baseline's mean a single vertical line (gridlines off so it is the only one).
- **Takeaways are templated facts.** Each bullet is computed from the same estimates as the tables (bootstrap with the report seed) and states n; bouts are linked by rep. Δ "excludes 0" uses the bootstrap CI; no wording beyond what the numbers say.
- **Markdown structure.** Per arena: charts with "How to read" lines, then the per task × model tables, then "What each contender found" in `<details>` blocks (blank line after `<summary>` so GitHub renders the table inside).

## Risks / Trade-offs

- More charts per arena (up to eight). Mitigated by skipping any that the data can't support and by ordering: decision charts (quality vs cost, Δ) first.
- Opacity-based cells depend on the page background; acceptable because GitHub renders the SVG on one of the two validated surfaces and report.html uses its own variables.
- Cluster labels are heuristic (most common location); the hover title and table carry the full title.
