## Context

Bouts write `record.json` and `findings.json` (see bootstrap-engine). The bout list comes from the lock, not from whatever directories exist, so a round's scores always cover exactly the locked matrix: `lock.read_lock` + `matrix.expand` + `runner.RoundPaths`.

## Goals / Non-Goals

**Goals:** stable finding identity, one flat row per finding and per bout, re-runnable at any time.

**Non-Goals:** judging correctness (groundtruth, llm-judge changes), plotting.

## Decisions

- **`finding_hash` hashes the raw fields.** The seven identity fields are hashed as the bout reported them, with missing optional fields as `null` and `yamlio.canonical_json` (sorted keys, compact, `ensure_ascii=False`). Normalizing first would make the hash depend on our normalization rules; keeping it a pure function of findings.json means the review UI or any other tool can compute it the same way. A test pins one hash value.
- **Paths are normalized in the rows**, not in the hash: strip leading `./` and `/arena/` (the container mount point), turn backslashes into slashes. A top-level directory that happens to be called `arena/` is left alone.
- **Only `ok` bouts contribute findings.** `invalid`, `schema_violation`, `limit_exceeded`, `timeout`, `error` and not-yet-run (`pending`) bouts still get a `bouts.csv` row so reports can show what was excluded.
- **`line_end` falls back to `line_start`** in `findings.jsonl`, which is how matching treats it anyway.
- **CSV round-trips.** `write_csv` writes `None` as empty and booleans as `true`/`false`; `read_csv` turns them back and keeps id/name columns as text.
- **CLI lives in `cli_score.py`** and is registered with two lines at the bottom of `cli.py`, to keep that file's diff small while other commands are added in parallel.

## Risks / Trade-offs

- Findings that differ only in path spelling (`./a.py` vs `a.py`) get different hashes, so they are judged twice. Cheap, and dedup puts them in one cluster.
