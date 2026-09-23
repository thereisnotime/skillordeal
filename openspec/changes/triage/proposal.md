## Why

skills-collection holds about 10,500 SKILL.md files across 100 repos. Picking contenders by hand means grepping for "security", missing forks of the same skill, and not noticing that a skill ships a script that curls something. We want a shortlist that is repeatable and explainable, in the engine's own contender format, with a cheap first look at risk before anything is run.

## What Changes

- New `skillordeal triage --skills-list PATH [--keywords ...] [--out candidates.yaml] [--min-words 200] [--top N] [--pin-synced]`.
- It filters on keywords in name + description, drops short skills, and folds near-duplicates by normalized SKILL.md hash.
- Candidates are ranked by a transparent score (keyword hits, length, `references/`, repo stars from `repos-meta.json`), and every part of the score is written into the entry's notes.
- It emits `contenders:` YAML that validates against `Contender`. Entries prefer a GitHub `repo` + `subpath`, where the ref is either the default branch, resolved at lock, or the synced commit with `--pin-synced`. Otherwise they get a local `path`.
- A static risk pre-scan per skill covers `scripts/` files, `curl|wget|nc |/dev/tcp|base64 -d|eval`, URLs to non-GitHub hosts, and prompt-injection phrases. Results go into the notes as a level: none/low/medium/high.
- No model calls. There is a typed hook for an optional classifier, left unwired.

## Capabilities

### New Capabilities
- `triage`: deterministic contender shortlisting with a static risk pre-scan.

### Modified Capabilities
None.

## Impact

New module `skillordeal.triage`. It reads skills-collection files only and needs no network.
