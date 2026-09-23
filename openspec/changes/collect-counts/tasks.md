## 1. Collection

- [x] 1.1 `score/findings.py`: load a round from the lock (`read_lock`, `expand`, `RoundPaths`)
- [x] 1.2 `finding_id`, `finding_hash` per the contract; path and CWE normalization
- [x] 1.3 `findings.jsonl` rows (ok bouts only) and `bouts.csv` rows (every bout)
- [x] 1.4 CSV/JSONL helpers that round-trip types

## 2. CLI

- [x] 2.1 `cli_score.py` with `score(trial, round, window?)`, registered from `cli.py`
- [x] 2.2 `just score` recipe in the TRIALS menu

## 3. Tests

- [x] 3.1 Hash stability (key order, irrelevant fields, missing fields, pinned value)
- [x] 3.2 Path normalization cases
- [x] 3.3 End-to-end `score` on a trimmed copy of the real smoke round (`tests/fixtures/smoke`)
