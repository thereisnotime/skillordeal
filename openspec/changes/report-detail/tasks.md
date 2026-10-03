## 1. Data

- [x] 1.1 `report_detail.load_detail`: findings (fallback to bout dirs), final verdicts, ground-truth issues read leniently, tool calls from records; never raises on missing or broken files
- [x] 1.2 Derived views: coverage per issue, clusters, shared/unique agreement, severity and CWE counts, tool calls
- [x] 1.3 Tables for coverage, problems, CWE, severity and tool calls with bout links by rep

## 2. Charts

- [x] 2.1 Coverage grid (issues by severity × contenders) and problems grid for arenas without ground truth
- [x] 2.2 Shared vs unique problems, findings by severity
- [x] 2.3 Per-bout strip plots and cost/time per TP
- [x] 2.4 Bout status (only when something failed), skill-load check, tool-call mix with turns
- [x] 2.5 Colors validated with the dataviz validator on #ffffff and #0d1117; shared 10 in width
- [x] 2.6 Rendered every chart of the r01 round to PNG on both backgrounds and checked them

## 3. RESULTS.md and report.html

- [x] 3.1 Key takeaways generated from the data with n and bout links
- [x] 3.2 Per-arena sections: charts with "How to read" lines, tables, `<details>` for long tables
- [x] 3.3 report.html: takeaways, all charts, sortable detail tables, per-contender findings drill-down

## 4. Tests and docs

- [x] 4.1 Tests: new charts written and byte-identical, takeaways content, CI-excludes-zero, coverage grid and tables, details blocks, html drill-down, no ground truth, no findings or records, all-ok status skip
- [x] 4.2 README Report section
