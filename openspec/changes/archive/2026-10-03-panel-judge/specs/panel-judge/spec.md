## ADDED Requirements

### Requirement: Judge mode configuration
`trial.yaml` `judge` SHALL accept `mode` (`fact` or `panel`, default `fact`), `voters` (default 3) and `lenses` (default `reachability`, `impact`, `correctness`). `voters` SHALL be at least the number of lenses, lenses SHALL NOT repeat, and voters beyond the number of lenses SHALL wrap around the lenses as extra slots (`reachability-2`, ...). These fields SHALL NOT affect the lock or the trial hash.

#### Scenario: Default stays fact
- **WHEN** `judge` sets only a model id
- **THEN** `skillordeal judge` runs the fact judge with its existing prompt, cache and output

#### Scenario: Too few voters
- **WHEN** `judge` sets three lenses and `voters: 2`
- **THEN** loading the trial fails, saying every lens needs a voter

#### Scenario: Switching mode is not drift
- **WHEN** a locked trial's judge changes from fact to `mode: panel, voters: 5`
- **THEN** the trial hash and the lock's judge entry are unchanged

### Requirement: CLI mode override
`skillordeal judge --mode fact|panel` SHALL override the trial's judge mode for that run.

#### Scenario: Override to panel
- **WHEN** the trial's judge is in fact mode and `judge --mode panel --dry-run` runs
- **THEN** it prints panel voter prompts and no fact prompt

### Requirement: Lens voters
In panel mode each voter slot SHALL be a separate call in the judge sandbox (read-only arena, Read/Grep/Glob only, same isolation checks and egress as the fact judge), prompted from `prompts/judge_panel.md` with its lens section, and answering with `schemas/panel_votes.schema.json`: per finding ref a vote (`true_positive`, `false_positive` or `unverifiable`), the attacker, the gain, a confidence and a rationale. The prompt SHALL tell the voter to try to refute each finding, to cite the deciding lines, that self-inflicted and same-privilege issues are `false_positive`, that deployment preconditions are hurdles rather than refutations unless a shipped default closes the path, and that it may not run code.

#### Scenario: One call per lens
- **WHEN** the panel judges two batches with the default three lenses
- **THEN** six calls are made, one per batch per lens

#### Scenario: Isolation failure
- **WHEN** a voter's init event shows an unexpected tool
- **THEN** none of that call's votes are cached and the problem is reported

### Requirement: Blinded, shared batches
Panel batches SHALL be blinded and deterministically shuffled per arena exactly like fact batches, and every voter SHALL see the same findings in the same order for a batch.

#### Scenario: Self-naming finding
- **WHEN** a finding names its skill and bout id
- **THEN** no lens prompt contains them, nor any contender id, finding id or finding hash

#### Scenario: Same batch for every lens
- **WHEN** the prompts for one batch are rendered for each lens
- **THEN** their findings sections are identical and each contains only its own lens section

### Requirement: Majority aggregation
The engine SHALL derive the verdict from the votes: `unverifiable` when unverifiable votes outnumber decisive ones or decisive votes tie, otherwise `valid` for a `true_positive` majority and `invalid` for a `false_positive` majority. `judge.jsonl` panel rows SHALL keep the fact row fields and add `mode: panel`, the per-slot `votes` with attacker and gain, `attacker`, `gain`, `agreement`, `unanimous` and `lenses`. The summary SHALL map `valid` to tp and `invalid` to fp as for the fact judge.

#### Scenario: Two of three
- **WHEN** reachability votes `false_positive` and impact and correctness vote `true_positive`
- **THEN** the verdict is `valid` with confidence `medium`, agreement 0.6667 and `unanimous` false

#### Scenario: Tie
- **WHEN** the votes are one `true_positive`, one `false_positive` and one `unverifiable`
- **THEN** the verdict is `unverifiable`

#### Scenario: Mostly unverifiable
- **WHEN** two of three votes are `unverifiable`
- **THEN** the verdict is `unverifiable`

### Requirement: Per-vote cache and resume
Panel votes SHALL be cached at `<cache_dir>/judge/<judge_model>/panel-<panel_sha>/<slot>/<finding_hash>.json`, where `panel_sha` covers the panel prompt file and the vote schema. Fact verdicts SHALL NOT answer for the panel or the reverse. A finding SHALL get a `judge.jsonl` row only when every slot has voted, and a re-run SHALL only call the slots still missing votes.

#### Scenario: Fact cache does not answer for the panel
- **WHEN** every finding has a cached fact verdict and the panel runs
- **THEN** the panel calls its voters and writes panel rows

#### Scenario: Resume after a dropped ref
- **WHEN** the impact voter omitted one finding in the first run
- **THEN** the next run makes one call, for the impact lens, with only that finding

#### Scenario: All cached
- **WHEN** every slot has voted on every finding
- **THEN** no call is made and `judge.jsonl` is rewritten with the same rows

### Requirement: Panel spend ceiling
`judge --max-cost-usd X` in panel mode SHALL count every voter's spend, stop starting calls once spend reaches X, cap each call's budget to what is left, keep the votes already cast, and let a later run finish the rest.

#### Scenario: Budget reached mid-panel
- **WHEN** each call costs 0.3 and X is 0.5
- **THEN** two voters run, the third does not, their votes are cached, and the next run calls only the third

### Requirement: Panel dry run
`judge --dry-run` in panel mode SHALL print the voters, the batches with each ref's finding and cached slots, and one rendered prompt per lens, without resolving credentials or starting containers.

#### Scenario: Preview
- **WHEN** `judge --mode panel --dry-run --batch-size 10` runs on the smoke round
- **THEN** it reports 17 findings in 2 batches and prints exactly three prompts, one per lens
