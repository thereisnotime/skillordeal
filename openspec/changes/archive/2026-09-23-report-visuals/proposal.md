## Why

The trials repo is read on GitHub, where `RESULTS.md` is all tables. The charts only exist inside `report.html`, which GitHub shows as source, so anyone browsing a round never sees quality against cost, how much of the output is noise, or where the budget went. GitHub does render committed images referenced from markdown and ```` ```mermaid ```` blocks, but it runs no JS and strips inline `<svg>`, so the charts have to be separate files.

## What Changes

- `skillordeal report` writes standalone, deterministic SVG charts (matplotlib) into `rounds/<round>/charts/`: quality vs cost per arena (Pareto frontier, CIs, baseline highlighted), recall per arena, findings breakdown by verdict per arena, tokens and cost, client resources, and Δ vs baseline per arena. A chart whose data is missing is skipped, never drawn empty.
- `RESULTS.md` gets a "Charts" section that embeds them with relative `![...](charts/...svg)` links, each with a one-line caption saying what to read and the n behind it.
- `RESULTS.md` gets a "Round at a glance" mermaid flowchart (bouts planned → statuses → findings → verdicts) and, when the round has pipeline contenders, a small mermaid flow of findings per stage.
- `report.html` inlines the same SVGs instead of drawing its own, so there is one chart implementation.
- New `--charts/--no-charts` flag on `report`, default on.

## Capabilities

### New Capabilities
- `report-visuals`: chart files and mermaid diagrams that render in GitHub markdown.

### Modified Capabilities
None. The `report` requirements still hold; `report.html` keeps inline, theme-following charts, now built from the shared chart code.

## Impact

New modules `skillordeal.report_charts` and `skillordeal.report_mermaid`. `report_html` loses its own chart functions. Uses matplotlib from the existing `analysis` extra. The trials repo commits `rounds/<round>/charts/` next to `RESULTS.md`.
