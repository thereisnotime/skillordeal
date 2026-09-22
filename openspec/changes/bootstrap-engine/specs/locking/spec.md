## ADDED Requirements

### Requirement: Lock resolves every input
`skillordeal lock` SHALL write a lock file pinning engine version, image reference with digest, CLI version, full model IDs, each contender's source commit and tree hash, and each arena's commit.

#### Scenario: Model alias rejected
- **WHEN** a model is configured as an alias such as `opus`
- **THEN** locking fails and names the offending entry

### Requirement: Locked runs refuse drift
When run with `--locked`, the engine SHALL recompute hashes and refuse to start if any contender tree hash, arena commit, engine version or image digest differs from the lock.

#### Scenario: Edited skill
- **WHEN** a SKILL.md changes after locking
- **THEN** a `--locked` run exits non-zero naming the contender

### Requirement: Deterministic bout IDs
The engine SHALL derive bout IDs from the locked inputs and rep index so that identical inputs give identical IDs.

#### Scenario: Resume
- **WHEN** a bout directory with a complete `record.json` already exists for a bout ID
- **THEN** the bout is skipped
