## Why

Ground truth for real codebases is never complete, so recall and precision need human verdicts on what the agents actually reported. Labeling in a text editor is slow and invites bias: you see which skill wrote a finding before you judge it. We need a quick, local, blind way to label findings. The labels should land in the plain file format that scoring already understands.

## What Changes

- New `skillordeal review TRIAL -r ROUND [--port] [--labeler] [--show-contenders]` command.
- A stdlib `http.server` bound to 127.0.0.1 serves one self-contained HTML page and a small JSON API: meta, findings, code excerpt, post label.
- Findings come from `scores/findings.jsonl`, joined with `gt_matches.jsonl`, `judge.jsonl`, the full text from each bout's `findings.json`, and existing labels. If the file isn't there, they're read straight from the bouts.
- Code excerpts come from the arena exported at the locked sha through the same `prepare_arena` the bout used, prepared once per arena in the cache. Paths are checked against traversal.
- Labels are appended to `<trial_dir>/labels/labels.jsonl` exactly as `docs/data-contracts.md` specifies.
- Blind by default, with a per-session random token required on every API call.

## Capabilities

### New Capabilities
- `review-ui`: local, blind, keyboard-driven labeling of findings into labels.jsonl.

### Modified Capabilities
None.

## Impact

New package `skillordeal.review` plus the shared `skillordeal.rounddata` readers. No new dependencies. Touches `cli.py` with one registration line.
