## 1. Config and lock

- [x] 1.1 `ContenderKind.pipeline`, `Contender.stages`, validation (2+ stages, no source fields, no self-reference)
- [x] 1.2 `load_trial` checks stage ids and nesting, and builds a pool with stage-only contenders
- [x] 1.3 Lock pipeline entries with `stage_locks`, folded tree/config hashes, `verify_prompt_sha256` and `run_hash`
- [x] 1.4 Drift check covers stage edits and the verify prompt

## 2. Running

- [x] 2.1 `prompts/verify.md` and `verify.py` (fenced, blinded findings)
- [x] 2.2 Move blinding to `blind.py`; `blind_terms` covers pipeline stages
- [x] 2.3 Refactor `bout.py` into `run_stage` plus an injectable `BoutRunner`
- [x] 2.4 Pipeline bouts: per-stage dirs and records, first-failure stop, skipped stages, summed usage/resources/egress
- [x] 2.5 `bouts.csv` columns `stages` and `findings_before_verify`

## 3. Tests and docs

- [x] 3.1 Config validation, lock facts, run_hash stability and sensitivity
- [x] 3.2 Verify prompt contains prior findings and is blinded
- [x] 3.3 Fake-runner pipeline runs: success, stage-2 schema violation, verifier isolation, skipped stage, summed usage, score on the round
- [x] 3.4 Podman test: real image, offline, pipeline stops at stage 1 timeout
- [x] 3.5 README "Pipelines" section and data-contracts stage layout
