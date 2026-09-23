## 1. Charts

- [x] 1.1 `report_charts`: two-surface palette (validated on #ffffff and #0d1117), deterministic SVG serializer (hash salt, no metadata, per-chart id prefix, hover titles)
- [x] 1.2 Quality vs cost per arena with CIs, baseline highlighted, Pareto frontier and labels; TP or judge-valid
- [x] 1.3 Recall per arena, findings breakdown by verdict scheme, tokens and cost as small multiples (no dual axis), resources, Δ vs baseline dot plot
- [x] 1.4 Skip charts without data; n = 1 without whiskers; NaN left out
- [x] 1.5 Write to `rounds/<round>/charts/`, removing stale SVGs

## 2. Markdown and HTML

- [x] 2.1 "Charts" section in RESULTS.md with relative image links and captions carrying n
- [x] 2.2 Mermaid "Round at a glance" flowchart and per-pipeline stage flow, generated ids, quoted labels
- [x] 2.3 report.html inlines the shared SVGs recolored to CSS variables; old chart code removed
- [x] 2.4 `--charts/--no-charts` flag, default on

## 3. Tests and docs

- [x] 3.1 Tests: charts written, byte-identical re-run, links resolve, no-ground-truth and bouts.csv fallbacks, NaN and n = 1, `--no-charts`, mermaid counts and structure, pipeline flow
- [x] 3.2 README Report section; visual check of rendered PNGs on light and dark backgrounds
