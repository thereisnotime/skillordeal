## Why

Single-pass review skills report a lot of false positives. Research and practice suggest that a finder followed by an independent verifier (for example Sentry's security-review, then Trail of Bits' fp-check) beats either one alone. The engine can only measure one skill per bout today, so that combination can't be benchmarked against the finder alone or the baseline.

## What Changes

- New contender kind `pipeline` with `stages: [<contender id>, ...]` (2+ stages from the same contenders file, `baseline` allowed as a plain verifier pass). Validation rejects unknown stages, nested pipelines (and so cycles) and source fields on the pipeline itself.
- Stages that are only used inside a pipeline are materialized and locked (as a copy inside the pipeline's lock entry) without getting bouts of their own.
- The pipeline's `run_hash` hashes the stage run hashes in order plus the new verify prompt's hash, so bout IDs follow.
- A pipeline bout runs stage 1 on the task, then each later stage in a fresh container with its own skill and a prompt from the new `src/skillordeal/prompts/verify.md` that carries the previous stage's findings as fenced, blinded JSON. The first failing stage ends the bout with its status and is named in `failed_stage`.
- Artifacts: the usual top-level files (final findings, stage 1's prompt and transcript), plus `stages/<n>-<contender>/` per stage. The record gains `stages`, `findings_before_verify` and usage/cost/tokens/duration/turns summed over stages.
- `bouts.csv` gains `stages` and `findings_before_verify`.
- `bout.py` is refactored around one reusable `run_stage` and an injectable container runner, so plain and pipeline bouts share one code path and can be tested offline.

## Capabilities

### New Capabilities
- `pipeline-contenders`: finder + verifier pipelines as contenders.

### Modified Capabilities
None. Plain bouts behave as before; their IDs don't change.

## Impact

`config.py`, `lock.py`, `bout.py`, new `verify.py`, `blind.py` (blinding moved out of the judge so bouts can use it), `prompts/verify.md`, `score/findings.py`, README and data contracts.
