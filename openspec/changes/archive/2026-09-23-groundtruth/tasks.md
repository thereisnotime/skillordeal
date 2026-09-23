## 1. Ground truth

- [x] 1.1 Pydantic models for `groundtruth.yaml` (defaults, CWE normalization, line range check, unique ids)
- [x] 1.2 Locate the file via the arena definition; warn on sha mismatch and on changes since locking
- [x] 1.3 Matcher: CWE-first kind with category fallback, ±window place, best issue ranking
- [x] 1.4 Per-bout tp/dup/fp/unknown, `match_basis`, `line_distance` in `gt_matches.jsonl`
- [x] 1.5 Aggregates: precision (complete only), lower bounds, recall, f1

## 2. Dedup

- [x] 2.1 Union-find clustering per arena and file
- [x] 2.2 Stable `cluster_id`
- [x] 2.3 `unique.csv` (clusters and exclusive clusters per contender)

## 3. Summary

- [x] 3.1 Human label reading (latest per labeler, majority across labelers)
- [x] 3.2 Merged verdicts with precedence and fall-through, `verdicts.jsonl`
- [x] 3.3 Per-bout summary rows; lazy pyarrow import with a clear error
- [x] 3.4 `score/pipeline.py` running collect, match, dedup, summary

## 4. Docs and tests

- [x] 4.1 docs/data-contracts.md matching rules and new files
- [x] 4.2 Matcher tests: overlap edges, window, file, CWE-only, category fallback, ambiguity ranking, dup, complete vs incomplete
- [x] 4.3 Dedup determinism under shuffling; unique per contender
- [x] 4.4 Precedence and label override tests
- [x] 4.5 Smoke round with a synthetic ground truth and with the real dvpwa ground truth
