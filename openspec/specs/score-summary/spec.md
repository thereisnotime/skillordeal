# score-summary Specification

## Purpose
TBD - created by archiving change groundtruth. Update Purpose after archive.
## Requirements
### Requirement: Verdict precedence
For each finding the engine SHALL take the first decisive verdict from human labels, then ground truth, then the judge, where `unsure`, `unknown` and `unverifiable` are not decisive, and record its source in `verdicts.jsonl`.

#### Scenario: Human overrides ground truth
- **WHEN** ground truth says `tp` and the latest human label says `fp`
- **THEN** the finding's verdict is `fp` with source `human`

#### Scenario: Unsure human label
- **WHEN** the only human label is `unsure` and ground truth says `tp`
- **THEN** the verdict is `tp` with source `gt`

### Requirement: Label overrides
Later lines in `labels/labels.jsonl` SHALL override earlier ones for the same `finding_hash` and labeler, and several labelers SHALL be combined by majority with ties left undecided.

#### Scenario: Relabel
- **WHEN** a labeler first writes `tp` and later `fp` for one finding
- **THEN** only `fp` counts

### Requirement: Summary table
`score` SHALL write `summary.csv`, and `summary.parquet` when pyarrow is installed, with every `bouts.csv` column plus per-bout ground-truth, judge, human and final aggregates and the number of clusters; aggregates SHALL be empty for bouts that are not `ok`.

#### Scenario: pyarrow missing
- **WHEN** the analysis extra is not installed
- **THEN** `summary.csv` is written and the command fails with a message naming `uv sync --extra analysis`

