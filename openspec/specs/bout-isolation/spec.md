# bout-isolation Specification

## Purpose
TBD - created by archiving change bootstrap-engine. Update Purpose after archive.
## Requirements
### Requirement: Bout runs with only the intended skill
The engine SHALL run each bout in a fresh container with an empty config directory, no MCP servers, no memory, no CLAUDE.md and exactly the contender's skill (or none for the baseline).

#### Scenario: Baseline bout
- **WHEN** a bout runs with contender `baseline`
- **THEN** the `system/init` event lists no user skills and no MCP servers, and the bout status is `ok`

#### Scenario: Skill bout
- **WHEN** a bout runs with a skill contender
- **THEN** the `system/init` event lists exactly that skill among non-bundled skills

### Requirement: Isolation gate rejects contaminated bouts
The engine SHALL mark a bout `invalid` and exclude it from scoring when the init event shows unexpected skills, MCP servers, or a model different from the locked model ID, or when the prepared arena still contains `CLAUDE.md` or `AGENTS.md`.

#### Scenario: Leaked CLAUDE.md
- **WHEN** an arena contains `CLAUDE.md` after stripping
- **THEN** the bout is marked `invalid` with reason `arena-context-file` before the agent starts

#### Scenario: Model mismatch
- **WHEN** the init event model differs from the locked model ID
- **THEN** the bout is marked `invalid` with reason `model-mismatch`

### Requirement: Bout record
The engine SHALL write `record.json`, `findings.json`, `transcript.jsonl.zst`, `resources.jsonl` and `stderr.log` per bout, with the record holding versions, hashes, timing, token usage per model, cost, turn count, tool-call counts, skill-fired flag and status.

#### Scenario: Successful bout artifacts
- **WHEN** a bout finishes
- **THEN** all five artifacts exist and `record.json` validates against `schemas/record.schema.json`

