## ADDED Requirements

### Requirement: Stable finding identity
The engine SHALL give every finding a `finding_id` of `<bout_id>:<n>` (1-based position in `findings.json`) and a `finding_hash` equal to the sha256 of the canonical JSON of `{file, line_start, line_end, category, cwe, title, description}` as reported, with missing fields as null.

#### Scenario: Irrelevant fields change
- **WHEN** two findings differ only in severity, confidence, evidence or recommendation
- **THEN** they have the same `finding_hash`

#### Scenario: Missing optional field
- **WHEN** a finding has no `line_end`
- **THEN** its hash equals the hash of the same finding with `line_end: null`

### Requirement: Normalized paths
The engine SHALL write file paths in score rows relative to the repository root, without a leading `./` or `/arena/`.

#### Scenario: Absolute container path
- **WHEN** a finding cites `/arena/./sqli/app.py`
- **THEN** its row has `file: sqli/app.py`

### Requirement: Findings and bout rows
`skillordeal score` SHALL write `scores/findings.jsonl` with one row per finding of every `ok` bout, and `scores/bouts.csv` with one row per locked bout carrying status, findings count, cost, token totals, duration, API time, turns, skill_fired, first-turn prompt tokens and peak RSS/threads/fds plus CPU seconds.

#### Scenario: Excluded bout
- **WHEN** a bout's status is `invalid`
- **THEN** it has a `bouts.csv` row with that status and contributes no rows to `findings.jsonl`

#### Scenario: Smoke round
- **WHEN** `score` runs on the recorded smoke round
- **THEN** `findings.jsonl` has 17 rows and the baseline row in `bouts.csv` shows 10 findings, 750624 tokens and 34 turns
