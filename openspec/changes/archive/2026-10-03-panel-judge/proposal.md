## Why

The judge only checks code facts: "valid" means the code does what the finding claims. On a real codebase it accepted 694 of 695 findings, because it never asks who controls the input or whether the attacker gains anything beyond what they already have. Without a threat model, judge-valid can't stand in for a human reviewer, so every round still needs manual labelling before precision means anything.

## What Changes

- `trial.yaml` `judge` gains `mode: fact | panel` (default `fact`, today's judge, output and cache unchanged), `voters` (default 3) and `lenses` (default `reachability`, `impact`, `correctness`).
- Panel mode runs one blinded voter per lens (wrapping around when there are more voters than lenses). Each voter is its own call in the judge sandbox, gets the same shuffled batch, and is told to try to refute each finding through its lens, name the attacker and their gain, cite the deciding lines, treat self-inflicted and same-privilege issues as false positives, and treat deployment preconditions as hurdles unless a shipped default closes the path. Prompt: `prompts/judge_panel.md` (one template, one section per lens). Output: `schemas/panel_votes.schema.json`.
- The engine aggregates votes by majority over decisive votes into `valid` / `invalid`; ties and mostly-unverifiable panels are `unverifiable`. `judge.jsonl` panel rows keep the existing fields and add `mode`, `votes`, `agreement`, `unanimous`, `lenses`, `attacker`, `gain`.
- Votes are cached per finding per voter slot under `judge/<model>/panel-<sha>/<slot>/`, so fact and panel results never mix and an interrupted run resumes with only the missing voters.
- `skillordeal judge --mode panel` overrides the trial's mode; `--dry-run` prints the batches and one rendered prompt per lens; `--max-cost-usd` covers all voters.
- The report says "panel-valid" when `judge.jsonl` came from the panel, names the judge mode in the key takeaways and adds a per-contender judge agreement table.
- `mode`, `voters` and `lenses` stay out of the lock and trial hash.

## Capabilities

### New Capabilities
- `panel-judge`: threat-model-aware judging by a panel of lens voters, with engine-side majority aggregation, per-vote caching and resumable, budgeted runs.

### Modified Capabilities
- `report`: adds a requirement for naming the judge mode and showing panel agreement. Existing requirements are unchanged.

## Impact

New `skillordeal.score.panel`, `prompts/judge_panel.md`, `schemas/panel_votes.schema.json`. `score/judge.py` exposes its container call (`run_call`) and batch payloads for reuse; fact behaviour is unchanged and pinned by a golden test. `config.JudgeSpec` replaces `ModelSpec` for `Trial.judge`. `lock.py` hashes the trial without the ruling fields. `report.py`, `report_detail.py`, `report_html.py` read the judge mode from `judge.jsonl`. No new dependencies.
