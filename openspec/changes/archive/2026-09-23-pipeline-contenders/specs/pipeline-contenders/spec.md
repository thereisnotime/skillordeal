## ADDED Requirements

### Requirement: Pipeline contender config
The engine SHALL accept a contender of kind `pipeline` with two or more `stages` that name other contenders in the same contenders file or `baseline`, and SHALL reject a pipeline whose stages are unknown, are themselves pipelines, or include the pipeline itself, or that sets its own source.

#### Scenario: Unknown stage
- **WHEN** a pipeline lists a stage id that is not in the contenders file and is not `baseline`
- **THEN** loading the trial fails and names the pipeline and the unknown stage

#### Scenario: Nested pipeline
- **WHEN** a pipeline lists another pipeline as a stage
- **THEN** loading the trial fails with an error saying nesting isn't supported

#### Scenario: Single stage
- **WHEN** a pipeline lists fewer than two stages
- **THEN** validation fails

### Requirement: Pipeline locking
The engine SHALL lock every stage of a chosen pipeline, including stages not listed in `trial.contenders`, record the stage ids and each stage's lock facts in the pipeline's lock entry, and derive the pipeline's `run_hash` from the stage run hashes in order and the verify prompt hash.

#### Scenario: Stage-only contender
- **WHEN** a pipeline's verifier stage is not listed in `trial.contenders`
- **THEN** the lock holds its facts inside the pipeline entry and no bouts are planned for it on its own

#### Scenario: Edited verifier
- **WHEN** a stage's skill files change after locking
- **THEN** the drift check reports the pipeline and the pipeline's bout IDs change while other contenders' bout IDs stay the same

#### Scenario: Stable IDs
- **WHEN** the same trial is locked twice with no changes
- **THEN** the pipeline's `run_hash` and bout IDs are identical

### Requirement: Staged execution
The engine SHALL run stage 1 of a pipeline bout exactly like a plain bout, and each later stage in a fresh container with that stage's skill and a prompt built from `prompts/verify.md` holding the previous stage's findings as fenced JSON, with contender, skill and bout identifiers redacted. The isolation gate SHALL apply to every stage with that stage's expected skills.

#### Scenario: Verify prompt is blinded
- **WHEN** a stage 1 finding mentions a contender name or a bout id
- **THEN** the verifier's prompt contains the finding with those names replaced by `[redacted]`

#### Scenario: Verifier isolation
- **WHEN** a verifier stage's init event lists skills other than that stage's expected skills
- **THEN** the pipeline bout is `invalid` and `failed_stage` names that stage

### Requirement: Stage failure ends the pipeline
The engine SHALL stop a pipeline bout at the first stage whose status is not `ok`, give the bout that status, and record the failing stage in `failed_stage`. When stage 1 is `ok` with no findings, later stages SHALL be recorded as `skipped`.

#### Scenario: Verifier schema violation
- **WHEN** stage 2 returns output that doesn't match the findings schema
- **THEN** the bout status is `schema_violation` and `failed_stage` is `{n: 2, contender: <stage id>}`

#### Scenario: Nothing to verify
- **WHEN** stage 1 finishes `ok` with an empty findings list
- **THEN** no verifier container runs, later stages are `skipped`, and the bout is `ok` with zero findings

### Requirement: Pipeline artifacts and record
The engine SHALL write a pipeline bout's top-level `findings.json` from the last stage, `prompt.md` and `transcript.jsonl.zst` from stage 1, and a `stages/<n>-<contender>/` directory per stage with that stage's `prompt.md`, `findings.json`, `transcript.jsonl.zst`, `stderr.log`, `resources.jsonl` and `record.json`. The top-level record SHALL hold `stages`, `findings_before_verify`, and usage, cost, tokens, duration and turns summed over stages.

#### Scenario: Successful pipeline
- **WHEN** stage 1 reports two findings and the verifier keeps one
- **THEN** the bout is `ok` with `findings_count` 1, `findings_before_verify` 2, and `usage.total_cost_usd` equal to the sum of both stages

#### Scenario: Scoring a pipeline round
- **WHEN** `score` runs on a round with pipeline bouts
- **THEN** each pipeline bout's findings come from the final stage and `bouts.csv` carries `stages` and `findings_before_verify`
