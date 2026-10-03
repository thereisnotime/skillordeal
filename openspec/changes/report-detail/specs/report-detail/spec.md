## ADDED Requirements

### Requirement: Ground-truth coverage grid
For each arena with ground truth whose findings were matched against it, `skillordeal report` SHALL write `coverage-<arena>.svg`: one row per ground-truth issue grouped by severity, one column per contender with at least one ok bout, each cell shaded by the share of that contender's ok bouts with a true positive for the issue, plus the number of contenders that found each issue and the issues each contender found in any bout.

#### Scenario: Issue nobody found
- **WHEN** no ok bout of any contender has a true positive for an issue
- **THEN** its row is empty and marked 0 in the contenders column, and the takeaways name it

#### Scenario: Ground truth not matched
- **WHEN** the arena has a ground truth file but no finding carries a ground-truth verdict
- **THEN** the coverage grid is not written or linked

### Requirement: Problems grid without ground truth
For an arena without ground truth, the report SHALL write `problems-<arena>.svg`, the same grid over finding clusters, labelled by each cluster's most common file, line and CWE, sorted by how many contenders reported it.

#### Scenario: Arena without ground truth
- **WHEN** an arena has findings with cluster ids and no ground truth
- **THEN** `problems-<arena>.svg` is written and `coverage-<arena>.svg` is not

### Requirement: Shared and unique findings
The report SHALL write `agreement-<arena>.svg` showing, per contender, the distinct problems it reported split into found by every contender, found by some others, and found only by it, when the arena has clusters and at least two contenders.

#### Scenario: Unique problem
- **WHEN** only one contender reported a cluster
- **THEN** that cluster counts in its "only this contender" segment and the takeaways list the contender with its count

### Requirement: Per-bout spread and efficiency
The report SHALL write `reps-<arena>.svg` with one dot per ok bout for quality (TP or judge-valid), findings, cost and wall time, with the mean marked and the baseline's mean as a reference line, and `efficiency-<arena>.svg` with cost and wall time per quality unit per bout and pooled.

#### Scenario: Bouts with no TP
- **WHEN** a bout has zero TP
- **THEN** it has no per-bout ratio dot, but its cost and time still count in the pooled ratio (total over total)

### Requirement: Severity, tools, skill loading and status charts
The report SHALL write `severity-<arena>.svg` (mean findings per bout by self-reported severity), `tool-calls.svg` (mean tool calls per bout by tool from record.json, with mean turns), `skill-load.svg` (first-turn prompt tokens minus the baseline's, flagged at zero or below) and `bout-status.svg` (bout statuses per contender). The status chart SHALL only be written when at least one bout did not finish ok.

#### Scenario: All bouts ok
- **WHEN** every bout of the round finished ok
- **THEN** `bout-status.svg` is not written and the takeaways say all bouts finished ok

#### Scenario: Records without tool calls
- **WHEN** no bout record has `tool_calls`
- **THEN** `tool-calls.svg` and the tool table are skipped

### Requirement: Key takeaways
RESULTS.md SHALL start, after the header, with a "Key takeaways" list generated only from the data: bouts that did not finish ok per contender with links, per arena the highest mean quality and the baseline's, which contenders' Δ vs baseline has a 95% CI excluding 0, lowest and highest cost per quality unit, ground-truth issues found by nobody and by a single contender, problems reported by a single contender, and the first-turn token check. Every bullet SHALL state n or link the bouts it rests on.

#### Scenario: Deterministic
- **WHEN** the report is generated twice from the same inputs and seed
- **THEN** the takeaways are identical

#### Scenario: CI excludes zero
- **WHEN** a contender's bootstrap CI of Δ quality vs baseline lies entirely above 0
- **THEN** the takeaways list it as above the baseline with the Δ and its CI

### Requirement: Arena sections with readable charts and collapsible tables
RESULTS.md SHALL group charts and tables per arena, follow every chart with a "How to read" line that states its n, and put long tables (cost and resources, coverage, problems, CWE, severity, tool calls) in `<details>` blocks that GitHub renders. Charts SHALL share one width so they read at GitHub's content width.

#### Scenario: Collapsed table
- **WHEN** RESULTS.md contains a `<details>` block
- **THEN** it has a `<summary>` followed by a blank line and is closed

### Requirement: HTML drill-down
report.html SHALL show the takeaways, every chart, the detail tables as sortable tables, and per arena a collapsible section per contender listing each finding of its ok bouts with severity, CWE, location, title, final verdict and its source, matched ground-truth issue, judge verdict and a link to the bout.

#### Scenario: No finding data
- **WHEN** neither findings.jsonl nor readable bout findings exist
- **THEN** the page has no drill-down section and the report still succeeds
