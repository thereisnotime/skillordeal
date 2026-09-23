## Context

Ground truth lives in the trials repo next to each arena (`groundtruth:` in `arena.yaml`), and its sha256 is already pinned in the lock. The dvpwa file has 19 issues, several within a few lines of each other, and nearly every audit finding is category `security`.

## Goals / Non-Goals

**Goals:** a deterministic, explainable matcher; honest precision when the ground truth is incomplete; distinct-problem counts per contender; one table for reports.

**Non-Goals:** semantic matching with a model (that's the judge), CWE hierarchy walking, promoting labels into ground truth (`gt-promote`, later).

## Decisions

- **CWE decides when both sides have one.** "Category equal OR CWE overlap" matched almost anything in `security`, so the SQL injection finding could land on a neighbouring XSS issue. Now: if the finding cites a CWE and the issue lists any, they must share an exact id; category equality is only the fallback when one side has no CWE. Related ids (e.g. CWE-327 vs CWE-328) are a miss; ground truth authors can list several ids per issue instead. Each match row records `match_basis`.
- **Best issue, not first.** Among matching issues (and among an issue's locations): exact overlap beats window-only, then the closest midpoints (`line_distance`), then issue id. Deterministic and independent of file order.
- **tp/dup per bout.** The first finding tied to an issue is the tp, later ones are dup. A finding is never re-routed to a nearby unmatched issue: that would inflate recall with exactly the wrong-issue matches we're trying to avoid.
- **Incomplete ground truth reports bounds.** Unmatched findings are `unknown`, `precision` and `f1` stay empty, and `precision_lower_bound = tp / (tp + fp + unknown)` plus `f1_lower_bound` are always given. Ratios are computed unrounded and rounded to 4 places at the end.
- **Warnings, not failures,** when the ground truth `sha` differs from the locked arena commit, when the file changed since locking, or when the lock has no hash for it.
- **Dedup is union-find over pairs** in the same arena and file with lines overlapping within ±window (default 5) and the same kind of problem. Components don't depend on row order. Kind is the same rule as matching (shared CWE when both cite one, else the same category): with category alone, XSS at app.py:33 and CSRF at app.py:27 in dvpwa merge into one cluster. The cost is that differing CWEs for one bug (CWE-326 vs CWE-327 for MD5 passwords) stay separate clusters.
- **`cluster_id = "c-" + sha256("<arena>:<smallest member finding_hash>")[:16]`.** Stable across re-runs; adding a bout only changes the id of a cluster that gains a smaller member. The arena is part of the id so identical findings in two arenas never share a cluster.
- **Verdict precedence falls through.** Human, then ground truth, then judge, taking the first decisive answer (tp/fp/dup; judge valid/invalid/duplicate). `unsure`, `unknown` and `unverifiable` defer to the next source instead of masking it. Several human labelers are combined by majority, ties undecided.
- **Summary from in-memory rows,** written as CSV always and parquet when pyarrow is present. `judge` re-runs the whole `score` afterwards; it's cheap and keeps one code path.

## Risks / Trade-offs

- Strict CWE matching misses findings that cite a sibling CWE. Visible as `unknown` and resolved by the judge or a human label; ground truth can list alternatives.
- Window matching can still pick the wrong issue when two issues share a CWE and a spot (e.g. several XSS issues in templates). `line_distance` makes this auditable and human labels override.
