# Data contracts

Every file the engine reads or writes after a bout. Scoring, the review UI and reports all communicate only through these files, so each stage can be re-run on its own from the shell.

## Finding identity

The bout's `findings.json` follows `src/skillordeal/schemas/findings.schema.json`. Each finding gets:

- `finding_id` = `<bout_id>:<n>`, where `n` is its 1-based position in `findings.json` `findings[]`.
- `finding_hash` = sha256 of the canonical JSON (sorted keys, no whitespace) of these fields only: `{file, line_start, line_end, category, cwe, title, description}`. It's used as a cache key and survives re-numbering.
  - Values are taken exactly as the bout reported them (the path is **not** normalized first), and a missing optional field is hashed as `null`, so the object always has all seven keys.
  - Canonical JSON is `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, hashed as UTF-8 (`skillordeal.score.findings.finding_hash`).

Only bouts with status `ok` contribute findings. Other statuses still get a `bouts.csv` row.

## Ground truth: `arenas/<arena>/groundtruth.yaml` (trials repo)

```yaml
arena: dvpwa
sha: a1d8f89fac2e57093189853c6527c2b01fc1d9c1   # commit the truth was written against
complete: false                          # true only if every real issue is listed (default false)
window: 5                                # line window for matching (default 5)
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

The file is found through the arena definition (`groundtruth:` in `arena.yaml`, relative to it) and its sha256 is pinned in the round's lock. `score` warns when the file changed since locking or when its `sha` differs from the arena commit the round was locked to.

**Matching.** Paths are normalized on both sides (no leading `./` or `/arena/`), and a finding's lines are `[line_start, line_end or line_start]`. A finding matches an issue when:

- **Kind:** if the finding has a CWE and the issue's `cwe` list is not empty, they must share an exact id (`match_basis: cwe`; related ids such as parent and child CWEs are a miss). Otherwise, when the finding has no CWE or the issue has none, the categories must be equal (`match_basis: category`).
- **Place:** its `file` equals one of the issue's locations, and its lines overlap that location's `lines` widened by ±`window`.

When several issues (or several locations of one issue) match, the finding is tied to the single best one, ranked by:

1. exact line overlap (without the window) before window-only overlap,
2. then the smallest distance between range midpoints, `line_distance = |mid(finding) - mid(location)|` (can be a half line),
3. then issue `id`, so ties are deterministic.

Per bout, the first finding (in `findings.json` order) tied to an issue is the `tp` and later ones are `dup`, even if a second nearby issue is still unmatched. A finding that matches no issue is `fp` on an arena whose ground truth is marked `complete: true`, and `unknown` otherwise.

Per bout: `precision = tp / (tp + fp)` and `f1` only when the ground truth is complete; `precision_lower_bound = tp / (tp + fp + unknown)` and `f1_lower_bound` always; `recall = distinct issues with a tp / issues`. `dup` findings don't count towards precision.

## Human labels: `trials/<trial>/labels/labels.jsonl` (trials repo)

Written by the review UI, one JSON object per line. Later lines override earlier ones for the same `finding_hash` and `labeler`.

```json
{"finding_hash": "…", "finding_id": "b-…:3", "arena": "dvpwa", "verdict": "tp",
 "issue_id": "dvpwa-sqli-student-create", "labeler": "human:thereisnotime",
 "ts": "2026-09-23T10:00:00Z", "note": ""}
```

- `verdict`: `tp` | `fp` | `dup` | `unsure`
- `issue_id` is optional and links the finding to a ground-truth issue. A `tp` with no `issue_id` is a candidate for promotion into ground truth (`skillordeal gt-promote`).
- Several labelers on one finding are combined by majority over `tp`/`fp`/`dup`; a tie counts as undecided.

## Scores: `rounds/<round>/scores/` (trials repo)

Written by `skillordeal score` and `skillordeal judge`. Everything here can be regenerated. Rows may carry more fields than listed.

| File | Rows | Key fields |
|---|---|---|
| `findings.jsonl` | one per finding | finding_id, finding_hash, bout_id, contender, arena, task, model, rep, category, severity, confidence, cwe, file (normalized), line_start, line_end (`line_start` when missing), title, description, evidence, recommendation, cluster_id |
| `gt_matches.jsonl` | one per finding of an arena with ground truth | finding_id, finding_hash, verdict (`tp`/`dup`/`fp`/`unknown`), issue_id, match_basis (`cwe`/`category`), line_distance |
| `judge.jsonl` | one per judged finding | finding_hash, finding_id, judge_model, verdict (`valid`/`invalid`/`unverifiable`, validity only; overlap is left to clusters), confidence, rationale, cluster_id, prompt_sha, batch_id, judged_at |
| `verdicts.jsonl` | one per finding | finding_id, finding_hash, bout_id, cluster_id, human, gt, judge, issue_id, verdict (`tp`/`fp`/`dup`/`unknown`), source (`human`/`gt`/`judge`) |
| `bouts.csv` | one per bout | bout_id, contender, arena, task, model, rep, status, findings, cost_usd, tokens_total, input/output/cache tokens, duration_s, api_s, turns, skill_fired, first_turn_prompt_tokens, rss_peak_kb, threads_peak, fds_peak, cpu_s |
| `unique.csv` | one per arena × contender | bouts, findings, clusters, exclusive_clusters (clusters no other contender found) |
| `summary.parquet`, `summary.csv` | bouts.csv plus per-bout aggregates | tp, dup, fp, unknown, gt_issues, gt_complete, issues_found, precision, precision_lower_bound, recall, f1, f1_lower_bound, judge_valid/invalid/unverifiable/pending, human_tp/fp/dup/unsure, final_tp/fp/dup/unknown, final_precision, clusters |
| `judge_runs/<batch_id>/` | one per judge call | prompt.md, record.json (refs → finding_hash, usage, problems), transcript.jsonl.zst, stderr.log |

Aggregates are blank for bouts that aren't `ok`.

**Verdict precedence** when several sources exist: human, then ground truth, then judge. Only decisive answers count (`tp`/`fp`/`dup`, judge `valid`→tp, `invalid`→fp); `unsure`, `unknown` and `unverifiable` fall through to the next source.

**Clusters** (`cluster_id`) group findings about the same problem across all bouts of an arena: same normalized file, lines overlapping within ±5 (or `score --window`), and a shared CWE when both cite one, else the same category. Clusters are connected components, and `cluster_id = "c-" + sha256("<arena>:<smallest member finding_hash>")[:16]`.

## Judge cache: `~/.cache/skillordeal/judge/<judge_model>/<prompt_sha>/<finding_hash>.json`

Contains the same object as a `judge.jsonl` row (with the `cluster_id` of the round that first judged it). It's shared across rounds, so re-judging costs nothing.

- `<judge_model>` is the trial's `judge` model slug (`id`, or `id@effort`); the root is `runtime.cache_dir`.
- `<prompt_sha>` = sha256 of `src/skillordeal/prompts/judge.md`, a newline, and the canonical JSON of `schemas/verdicts.schema.json`. Editing either starts a fresh cache.
