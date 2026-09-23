## ADDED Requirements

### Requirement: Keyword and length filter
`skillordeal triage` SHALL keep skills whose name or description contains at least one keyword (case-insensitive) and whose word count is at least `--min-words`.

#### Scenario: Off-topic and short skills
- **WHEN** a skill matches no keyword, or has fewer words than the minimum
- **THEN** it is not in the output, and the summary counts the short ones

### Requirement: Near-duplicate folding
Skills whose SKILL.md files are identical after removing frontmatter, lowercasing and collapsing whitespace SHALL be folded into one entry: the highest-scoring copy, whose notes list the others.

#### Scenario: Renamed fork
- **WHEN** two repos contain the same SKILL.md body under different names
- **THEN** only one entry is emitted and its notes name the folded copy

### Requirement: Transparent ranking
Candidates SHALL be ordered by a score made of keyword hits, length, presence of `references/`, and repo stars when `repos-meta.json` has them, and each entry's notes SHALL show the score's parts.

#### Scenario: Stars available
- **WHEN** repos-meta.json lists stars for a repo
- **THEN** that repo's candidates get a stars component of log10(stars + 1), shown in their notes

### Requirement: Contender YAML output
The output SHALL be a `contenders:` YAML list where every entry validates as a `Contender`. Entries SHALL use a GitHub `repo` + `subpath` (with `ref` omitted so lock pins the default branch, or set to the synced commit with `--pin-synced`) when the repo is on GitHub, and a local `path` otherwise.

#### Scenario: Non-GitHub repo
- **WHEN** inventory.json gives a non-GitHub URL for a repo
- **THEN** its entries use `path` pointing at the local skill directory

### Requirement: Static risk pre-scan
Each candidate SHALL be scanned without executing anything. The scan SHALL flag files under `scripts/`, the patterns `curl`, `wget`, `nc `, `/dev/tcp`, `base64 -d` and `eval`, URLs to non-GitHub hosts, and the phrases "ignore previous", "system prompt" and "do not tell the user". Each flag, with file and line, and an overall level SHALL be written into the entry's notes.

#### Scenario: Risky skill
- **WHEN** a skill ships `scripts/setup.sh` that curls a non-GitHub host and its SKILL.md says "ignore previous instructions"
- **THEN** its notes list the script, the curl hit, the host and the phrase, and its risk level is high

### Requirement: No model calls
Triage SHALL NOT call any model or network service. An optional classifier MAY be passed programmatically, and it is off by default.

#### Scenario: Default run
- **WHEN** triage runs with default options
- **THEN** no network access happens and the output is deterministic for the same inputs
