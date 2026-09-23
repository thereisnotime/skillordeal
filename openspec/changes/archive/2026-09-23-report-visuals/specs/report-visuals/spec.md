## ADDED Requirements

### Requirement: Standalone chart files
`skillordeal report` SHALL write SVG charts into `rounds/<round>/charts/`: `quality-vs-cost-<arena>.svg`, `findings-breakdown-<arena>.svg` and `delta-vs-baseline-<arena>.svg` per arena, plus `recall-by-contender.svg`, `cost-tokens.svg` and `resources.svg` for the round. Each chart SHALL have a transparent background and colors that read on both GitHub's light and dark themes.

#### Scenario: Scored round
- **WHEN** the report runs on a round with ground truth, costs and resources
- **THEN** every chart listed above is written for every arena that has the data

#### Scenario: Stale charts
- **WHEN** `charts/` holds SVGs from an earlier run that this run doesn't produce
- **THEN** they are removed

### Requirement: Deterministic chart bytes
Chart files SHALL be byte-identical when the report is re-run on the same data with the same seed and engine version: no dates or creator metadata, a fixed SVG hash salt and ids derived from the chart name.

#### Scenario: Re-run
- **WHEN** the report is generated twice from the same scores
- **THEN** every file in `charts/` has the same bytes both times

### Requirement: Charts degrade gracefully
A chart whose data is missing SHALL be skipped and not referenced. Cells with n = 1 SHALL be drawn without an interval, and NaN values SHALL be left out, never crash the report.

#### Scenario: No ground truth
- **WHEN** the summary has no ground-truth columns but has judge-valid
- **THEN** `recall-by-contender.svg` is not written, and quality vs cost and Δ vs baseline use judge-valid

#### Scenario: Only bouts.csv
- **WHEN** the report falls back to `bouts.csv`
- **THEN** only the cost, tokens and resources charts are written and the report succeeds

### Requirement: Charts embedded in RESULTS.md
`RESULTS.md` SHALL embed every written chart with a relative `![alt](charts/<file>.svg)` link under a "Charts" section, per arena for arena charts, each followed by a one-line caption that says what to read from it and how many bouts it rests on.

#### Scenario: Links resolve
- **WHEN** RESULTS.md is written
- **THEN** every `charts/` path it references exists in the round directory

### Requirement: Mermaid round overview
`RESULTS.md` SHALL contain a "Round at a glance" ```` ```mermaid ```` flowchart from bouts planned to status counts, and from findings in ok bouts to verdict totals. Node ids SHALL be opaque generated ids and every label SHALL be quoted.

#### Scenario: Counts
- **WHEN** a round has 6 planned bouts, 5 ok and 1 error
- **THEN** the flowchart shows "6 bouts planned", "ok: 5" and "error: 1", and the verdict totals match the summary

### Requirement: Mermaid pipeline flow
For pipeline contenders (bouts whose record has `stages`), `RESULTS.md` SHALL show a small mermaid flow of findings per stage, from the finder to the last verifier.

#### Scenario: Pipeline round
- **WHEN** a pipeline contender's finder reported 12 findings and the verifier kept 7
- **THEN** the flow shows the finder stage with 12 and the verifier stage with 7

#### Scenario: No pipelines
- **WHEN** no bout has stages
- **THEN** no pipeline diagram is written

### Requirement: One chart implementation
`report.html` SHALL inline the same chart SVGs, recolored to the page's CSS variables, rather than drawing its own.

#### Scenario: HTML charts
- **WHEN** report.html is written
- **THEN** it contains the charts as inline SVG using `var(--series-1)` and hover titles

### Requirement: Charts flag
`skillordeal report` SHALL accept `--charts/--no-charts`, default on. With `--no-charts` it SHALL NOT write `charts/` and RESULTS.md SHALL NOT reference chart files.

#### Scenario: Disabled
- **WHEN** the report runs with `--no-charts`
- **THEN** no `charts/` directory is created and RESULTS.md has no `charts/` links
