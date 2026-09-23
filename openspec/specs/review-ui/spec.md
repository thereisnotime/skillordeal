# review-ui Specification

## Purpose
TBD - created by archiving change review-ui. Update Purpose after archive.
## Requirements
### Requirement: Local-only server with a session token
`skillordeal review` SHALL listen on 127.0.0.1 only and SHALL require the random token printed in its startup URL on every `/api/` request.

#### Scenario: Missing token
- **WHEN** a request to `/api/findings` or a POST to `/api/labels` carries no token or a wrong one
- **THEN** the server answers 403 and nothing is written to labels.jsonl

#### Scenario: Non-JSON post
- **WHEN** a POST to `/api/labels` has a content type other than `application/json`
- **THEN** the server answers 415

### Requirement: Findings joined with scores and labels
The findings endpoint SHALL return each finding from `scores/findings.jsonl` with its description, evidence and recommendation from the bout's `findings.json`, its ground-truth verdict and issue, its judge verdicts, the current labeler's latest label, and other labelers' verdicts.

#### Scenario: No scores yet
- **WHEN** `scores/findings.jsonl` does not exist
- **THEN** findings are read from the bout directories, and a warning says that gt and judge verdicts are missing

### Requirement: Blind by default
Unless `--show-contenders` is given, the server SHALL NOT send contender, bout id or rep for any finding.

#### Scenario: Default run
- **WHEN** review starts without `--show-contenders`
- **THEN** no finding in the API response has a `contender`, `bout_id` or `rep` field

### Requirement: Code excerpt from the arena the agent saw
The excerpt endpoint SHALL return the cited lines ±15 with line numbers, read from the arena exported at the locked sha and prepared with the bout's strip rules, and SHALL refuse paths that leave the arena.

#### Scenario: Traversal in agent output
- **WHEN** a finding cites `../../etc/passwd`, an absolute path, or a symlink that resolves outside the arena
- **THEN** the endpoint answers 400 and reads nothing outside the arena

#### Scenario: Stripped files
- **WHEN** the arena contains `CLAUDE.md` or a `strip:` glob match
- **THEN** that file is absent from the prepared copy, as it was for the agent

### Requirement: Labels appended per contract
Posting a verdict SHALL append one JSON line to `<trial_dir>/labels/labels.jsonl` with `finding_hash`, `finding_id`, `arena`, `verdict` (`tp|fp|dup|unsure`), `issue_id`, `labeler`, `ts` and `note`. Readers SHALL treat a later line as overriding an earlier one for the same `finding_hash` and `labeler`.

#### Scenario: Relabel
- **WHEN** a finding is labeled `tp` and then `fp` by the same labeler
- **THEN** the file has two lines and the finding shows `fp`

#### Scenario: Unknown issue
- **WHEN** a label names an `issue_id` that is not in the arena's ground truth
- **THEN** the server answers 400 and appends nothing

### Requirement: Keyboard-driven page
The page SHALL support `t`/`f`/`d`/`u` for verdicts, `j`/`k` to move and `/` to search. It SHALL have filters for arena, category, severity and unlabeled-only, a progress counter, and light/dark via `prefers-color-scheme`.

#### Scenario: Label and advance
- **WHEN** the reviewer presses `t` on a finding
- **THEN** the label is saved, the progress counter updates and the next finding is shown

