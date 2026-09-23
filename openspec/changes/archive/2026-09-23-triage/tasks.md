## 1. Inputs

- [x] 1.1 Read skills-list.json, inventory.json (URLs), repos-meta.json (stars), commits.json (synced sha)
- [x] 1.2 Keyword filter on name + description; min-words filter

## 2. Ranking

- [x] 2.1 Normalized SKILL.md hash; fold near-duplicates into the best-scoring copy
- [x] 2.2 Transparent score with parts in notes; `--top`

## 3. Risk pre-scan

- [x] 3.1 scripts/ listing; curl/wget/nc/dev-tcp/base64 -d/eval; non-GitHub URL hosts; injection phrases
- [x] 3.2 [code]/[doc] tagging and none/low/medium/high level

## 4. Output

- [x] 4.1 Contender YAML (repo + subpath, optional synced ref, local path fallback), validated per entry
- [x] 4.2 Classifier hook (typed, unwired) with TODO for a CLI flag

## 5. CLI and tests

- [x] 5.1 `skillordeal triage` registered from cli.py; `just triage`
- [x] 5.2 Tests: fixture collection with a duplicate and a risky script, pin-synced, local-path fallback, risk levels
