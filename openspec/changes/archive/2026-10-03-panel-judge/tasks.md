## 1. Config and lock

- [x] 1.1 `JudgeSpec` with `mode`, `voters`, `lenses`, validation and voter slots (wrap-around)
- [x] 1.2 Keep the ruling fields out of `trial_hash` and `lock.judge`

## 2. Panel judge

- [x] 2.1 `prompts/judge_panel.md`: shared refutation rules plus reachability, impact and correctness sections
- [x] 2.2 `schemas/panel_votes.schema.json`
- [x] 2.3 Pull the sandboxed call out of the fact judge (`run_call`, `Batch.payload`) without changing fact output (golden digest)
- [x] 2.4 `score/panel.py`: plan shared batches, per-slot vote cache, majority aggregation, budgeted resumable run, judge.jsonl panel rows
- [x] 2.5 `judge --mode`, panel `--dry-run` with one prompt per lens

## 3. Report

- [x] 3.1 Read judge mode from judge.jsonl; "panel-valid" labels, voters in the header
- [x] 3.2 Judge-mode takeaway and per-contender judge agreement table

## 4. Tests and docs

- [x] 4.1 Tests: aggregation (majority, ties, unverifiable), lens blinding, fact/panel cache separation, resume, budget stop, isolation failure, CLI dry-run and mode override, lock hash, fact output unchanged, report labels and agreement table
- [x] 4.2 README Judge section and docs/data-contracts.md
