## Why

Ground truth is never complete, so most findings on a real arena end up `unknown`. A model that actually opens the cited code can rule on them far cheaper than a human, as long as it can't tell whose finding it's looking at, can't be steered by the arena or the finding text, and doesn't get paid for twice.

## What Changes

- `score/judge.py`: a blinded judge that runs in the same runner image and sandbox as a bout (read-only arena, empty config, no MCP, no skills, tools `Read,Grep,Glob` only), with a `--json-schema` verdict schema (`schemas/verdicts.schema.json`) and the prompt in `prompts/judge.md`.
- Blinding: only file/lines/category/cwe/title/description/evidence are sent, contender and skill names and bout ids are redacted from the text, and findings are shuffled deterministically and batched per arena.
- Cache per contract at `~/.cache/skillordeal/judge/<model>/<prompt_sha>/<finding_hash>.json`; cached findings are skipped.
- `scores/judge.jsonl`, per-call artifacts in `scores/judge_runs/`, spend tracking with `--max-cost-usd`.
- `skillordeal judge TRIAL -r ROUND [--max-cost-usd] [--batch-size 15] [--dry-run]` and `just judge`.
- `adapters/claude_code.plain_spec`: a contender-less spec so the judge reuses `build_command` and the isolation gate unchanged.

## Capabilities

### New Capabilities
- `llm-judge`: blinded, sandboxed, cached model verdicts for findings.

### Modified Capabilities
None. Bout command building is unchanged; the judge reuses it.

## Impact

Costs money when not cached or dry-run. Needs the runner image and a credential, like `run`. Uses `trial.judge`, which already existed in the config and lock.
