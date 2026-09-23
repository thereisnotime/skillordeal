# report Specification

## Purpose
TBD - created by archiving change report. Update Purpose after archive.
## Requirements
### Requirement: Read the best available scores
`skillordeal report` SHALL read `scores/summary.parquet`, else `scores/summary.csv`, else `scores/bouts.csv`, and SHALL state in the output which file it used.

#### Scenario: Only bouts.csv
- **WHEN** neither summary file exists
- **THEN** the report is written from bouts.csv, quality metrics show n/a, and a note says scoring hasn't run

#### Scenario: Nothing scored
- **WHEN** no score file exists
- **THEN** the command exits non-zero and tells the user to run `skillordeal score`

### Requirement: Mean with bootstrap CI and n per cell
For each arena × task × model × contender, the report SHALL show the mean over ok bouts and a 95% percentile bootstrap CI for findings, TP, precision, recall, F1, judge-valid, cost, tokens, duration, turns and RSS peak MB, with n shown as ok bouts over all bouts.

#### Scenario: Deterministic
- **WHEN** the report is generated twice from the same inputs and seed
- **THEN** every number is identical

#### Scenario: Single rep
- **WHEN** a cell has one ok bout
- **THEN** the mean is shown without an interval

### Requirement: Comparison with baseline
Each non-baseline row SHALL show Δ TP, Δ F1 and Δ cost against the baseline in the same arena, task and model, as well as cost per TP, skill-fired rate, and first-turn prompt tokens minus the baseline's mean.

#### Scenario: Skill did not load
- **WHEN** a contender's mean first-turn prompt tokens are less than or equal to the baseline's
- **THEN** the row is flagged "skill may not have loaded"

### Requirement: Failed bouts are never hidden
The report SHALL count bouts by status and list every bout whose status is not `ok`, with its reason from record.json, and SHALL include failed bouts in n and in total spend.

#### Scenario: One error
- **WHEN** one bout of a contender ended with status `error`
- **THEN** that contender's n reads 1/2, and the bout appears in the failed-bouts table with its error text

### Requirement: Outputs
The command SHALL write `RESULTS.md` (GitHub-renderable, with a header of trial question, lock hash, engine/CLI/image versions and models, relative `bouts/<id>/` links for every contender row, and a how-to-reproduce snippet), `report.html` (self-contained, no external resources, inline SVG charts, sortable tables, light and dark), `results.csv` and `results.parquet`. With `--markdown-only` it SHALL skip report.html.

#### Scenario: Self-contained HTML
- **WHEN** report.html is opened offline
- **THEN** all charts and tables render and no network request is made

