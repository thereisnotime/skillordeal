## Context

`skillordeal score` writes `bouts.csv` and `summary.parquet`/`summary.csv` (one row per bout, see docs/data-contracts.md). The report consumes only those, plus `lock.yaml`, `trial.yaml` and each non-ok bout's `record.json` for its error text.

## Goals / Non-Goals

**Goals:** numbers you can defend, the same output on every re-run, links down to raw artifacts, GitHub-rendered Markdown, a self-contained HTML file that works offline, and machine-readable exports.

**Non-Goals:** significance testing across many contenders (multiple comparisons), cross-round comparison, hosted dashboards.

## Decisions

- **Cell = (arena, task, model, contender).** Means are taken over bouts with status `ok`. **n** is shown as ok/total in every row. "Spent, all bouts" includes failed bouts, since that money was spent.
- **Percentile bootstrap, fixed seed.** `numpy.random.default_rng(seed)` is created fresh for each estimate, so results don't depend on iteration order. The defaults are 2000 resamples and seed 20260923, and both appear in the report. With n < 2 the mean is shown with no interval, never a fake one. Δ vs baseline resamples both groups independently.
- **Cost per TP** is total cost over total TP, computed on bouts that have both values. When TP = 0 it's n/a, never ∞.
- **Forced-injection check.** Mean `first_turn_prompt_tokens` minus the baseline's. If it's ≤ 0, the skill text probably never reached the model, and the row is flagged "skill may not have loaded".
- **Fallback chain** is parquet → summary.csv → bouts.csv. With bouts.csv only, quality columns are n/a and a banner says so.
- **Markdown layout.** For each group there are three tables (quality, cost and resources, against baseline), which keeps them readable on GitHub. Bout links are relative (`bouts/<id>/`), so they work both in the trials repo and locally.
- **HTML charts are matplotlib SVG, inlined.** They're drawn with the dataviz reference palette's light hexes, then those hexes are rewritten to CSS variables so the SVG follows dark mode. Element ids get a per-chart prefix so several SVGs can share one document. Each mark carries an SVG `<title>` for a native tooltip, and every charted value is also in a table. The charts: a quality vs cost scatter with a Pareto frontier (baseline gray, contenders blue, direct labels), cost/tokens bars, per-arena small multiples (no dual axes), and client resources. Sorting is done by about 20 lines of inline JS.
- **Exports** have one row per cell: means, CI bounds, deltas, status counts and bout ids.

## Risks / Trade-offs

- Bootstrap CIs over 3 reps are wide and a bit optimistic. They're labeled as such, and n is always visible.
- Pooling across arenas in the overview charts mixes arenas of different difficulty. The per-arena tables and small multiples keep them separate.
- Rendering depends on matplotlib's SVG output. The id and colour rewriting is regex-based and covered by tests.
