# llm-judge Specification

## Purpose
TBD - created by archiving change llm-judge. Update Purpose after archive.
## Requirements
### Requirement: Judge model required
`skillordeal judge` SHALL use the trial's `judge` model and fail with a clear message when it is not set.

#### Scenario: No judge configured
- **WHEN** `trial.yaml` has no `judge`
- **THEN** `judge` (including `--dry-run`) exits non-zero saying no judge model is set

### Requirement: Sandboxed judge
The judge SHALL run in the runner image with the arena at the locked commit mounted read-only, the same isolation flags as a bout, tools limited to Read, Grep and Glob, and a JSON schema requiring one `{finding_ref, verdict, confidence, rationale}` per finding.

#### Scenario: Tool surface
- **WHEN** the judge container starts
- **THEN** its init event lists no tools beyond Read, Grep, Glob and StructuredOutput and no MCP servers

#### Scenario: Isolation failure
- **WHEN** the init event shows an extra tool or skill
- **THEN** that call's verdicts are discarded, not cached, and reported as problems

### Requirement: Blinding
The judge prompt SHALL contain only file, lines, category, CWE, title, description and evidence of each finding, with contender ids, skill names and bout ids redacted, and findings shuffled deterministically and batched per arena.

#### Scenario: Self-naming finding
- **WHEN** a finding's description mentions its skill name and bout id
- **THEN** neither appears in the prompt

#### Scenario: Stable batches
- **WHEN** the same findings are passed in a different order
- **THEN** the batches and prompts are identical

### Requirement: Verdict cache
Verdicts SHALL be cached at `<cache_dir>/judge/<judge_model>/<prompt_sha>/<finding_hash>.json` and cached findings SHALL NOT be sent again.

#### Scenario: Second run
- **WHEN** `judge` runs again on a round whose findings are all cached
- **THEN** no container is started and `judge.jsonl` still has every verdict

#### Scenario: Missing verdict
- **WHEN** the model omits a ref
- **THEN** that finding stays uncached and is judged on the next run

### Requirement: Spend ceiling
`judge --max-cost-usd X` SHALL stop starting new calls once spend reaches X and cap each call's budget to what is left.

#### Scenario: Budget reached
- **WHEN** two calls have spent more than X
- **THEN** no further call starts and the command reports the budget stop

### Requirement: Dry run
`judge --dry-run` SHALL print the batches and the exact blinded prompts without resolving credentials or starting containers.

#### Scenario: Preview
- **WHEN** `judge --dry-run --batch-size 10` runs on the smoke round
- **THEN** it prints two batches and their prompts and makes no call

