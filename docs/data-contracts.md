# Data contracts

Every file the engine reads or writes after a bout. Scoring, the review UI and reports all communicate only through these files, so each stage can be re-run on its own from the shell.

## Finding identity

The bout's `findings.json` follows `src/skillordeal/schemas/findings.schema.json`. Each finding gets:

- `finding_id` = `<bout_id>:<n>`, where `n` is its 1-based position in `findings.json` `findings[]`.
- `finding_hash` = sha256 of the canonical JSON (sorted keys, no whitespace) of these fields only: `{file, line_start, line_end, category, cwe, title, description}`. It's used as a cache key and survives re-numbering.

## Ground truth: `arenas/<arena>/groundtruth.yaml` (trials repo)

```yaml
arena: dvpwa
sha: a1d8f89fac2e57093189853c6527c2b01fc1d9c1   # commit the truth was written against
issues:
  - id: dvpwa-sqli-student-create        # stable slug, never reused
    title: SQL injection in student creation
    category: security                   # same enum as findings
    cwe: [CWE-89]                        # zero or more
    severity: high                       # critical|high|medium|low|info
    locations:                           # any one matching location counts
      - file: sqli/dao/student.py
        lines: [38, 45]                  # inclusive range
    source: documented                   # documented|manual|cve|seeded|human-label
    notes: ""
```

**Matching:** a finding matches an issue when all three hold:
- its `file` equals a location's `file` (normalized: no leading `./` or `/arena/`)
- `[line_start, line_end or line_start]` overlaps the location's `lines` widened by the window (±`window`, default 5)
- the categories are equal, **or** the finding's `cwe` is in the issue's `cwe`

One issue can be matched by several findings. The first match is the TP and later ones are `dup`. A finding that matches no issue is `fp` for precision on an arena whose ground truth is marked `complete: true`, and `unknown` otherwise.

## Human labels: `trials/<trial>/labels/labels.jsonl` (trials repo)

Written by the review UI, one JSON object per line. Later lines override earlier ones for the same `finding_hash` and `labeler`.

```json
{"finding_hash": "…", "finding_id": "b-…:3", "arena": "dvpwa", "verdict": "tp",
 "issue_id": "dvpwa-sqli-student-create", "labeler": "human:thereisnotime",
 "ts": "2026-09-23T10:00:00Z", "note": ""}
```

- `verdict`: `tp` | `fp` | `dup` | `unsure`
- `issue_id` is optional and links the finding to a ground-truth issue. A `tp` with no `issue_id` is a candidate for promotion into ground truth (`skillordeal gt-promote`).

## Scores: `rounds/<round>/scores/` (trials repo)

Written by `skillordeal score` and `skillordeal judge`. Everything here can be regenerated.

| File | Rows | Key fields |
|---|---|---|
| `findings.jsonl` | one per finding | finding_id, finding_hash, bout_id, contender, arena, task, model, rep, category, severity, confidence, cwe, file, line_start, line_end, title |
| `gt_matches.jsonl` | one per finding | finding_id, finding_hash, verdict (`tp`/`dup`/`fp`/`unknown`), issue_id |
| `judge.jsonl` | one per judged finding | finding_hash, judge_model, verdict (`valid`/`invalid`/`duplicate`/`unverifiable`), confidence, rationale, cluster_id |
| `bouts.csv` | one per bout | bout_id, contender, arena, task, model, rep, status, findings, cost_usd, tokens_total, input/output/cache tokens, duration_s, api_s, turns, skill_fired, first_turn_prompt_tokens, rss_peak_kb, threads_peak, fds_peak, cpu_s |
| `summary.parquet` | bouts.csv plus per-bout gt/judge/human aggregates | tp, fp, dup, precision, recall, f1, judge_valid, human_tp, … |

**Verdict precedence** when several sources exist: human, then ground truth, then judge.

## Judge cache: `~/.cache/skillordeal/judge/<judge_model>/<prompt_sha>/<finding_hash>.json`

Contains the same object as a `judge.jsonl` row. It's shared across rounds, so re-judging costs nothing.
