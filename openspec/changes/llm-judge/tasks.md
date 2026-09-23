## 1. Command and sandbox

- [x] 1.1 `plain_spec()` in the Claude Code adapter; judge command via `build_command` (existing tests unchanged)
- [x] 1.2 Container via `prepare_arena` + `podman_create_args`, credentials by name, scrubbed artifacts
- [x] 1.3 Isolation gate and tool check on the judge's init event

## 2. Prompt, schema, blinding

- [x] 2.1 `prompts/judge.md` (open the code, skeptical, attacker-controlled input, hardening is invalid)
- [x] 2.2 `schemas/verdicts.schema.json`
- [x] 2.3 Blinded payload, name/id redaction, deterministic shuffle, per-arena batches

## 3. Cache and budget

- [x] 3.1 Cache per contract with atomic writes; skip cached findings
- [x] 3.2 Spend tracking, `--max-cost-usd`, per-call budget cap
- [x] 3.3 `judge.jsonl` and `judge_runs/<batch_id>/`; re-score afterwards

## 4. CLI

- [x] 4.1 `judge(trial, round, max_cost_usd, batch_size, dry_run)` in `cli_score.py`, registered from `cli.py`
- [x] 4.2 `just judge` recipe

## 5. Tests

- [x] 5.1 Blinding and batching (no contender id, skill name or bout id in the prompt), stable order
- [x] 5.2 Command isolation flags
- [x] 5.3 Fake runner: rows written, cache hit on second run, missing verdict retried, isolation failure not cached, budget stop
- [x] 5.4 Dry run makes no calls; missing judge fails
- [x] 5.5 Podman (offline, marked `podman`): judge container exposes only read tools
