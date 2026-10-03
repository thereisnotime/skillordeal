## ADDED Requirements

### Requirement: Judge mode in the report
`skillordeal report` SHALL read the judge mode from `scores/judge.jsonl`. When its rows come from the panel judge, every judge-related label in RESULTS.md and report.html SHALL say "panel-valid" instead of "judge-valid", the header SHALL list the voters, and each arena SHALL get a per-contender table of panel verdicts with judge agreement (share of judged findings with unanimous votes). When any finding was judged, the key takeaways SHALL name the judge mode and how its verdicts split.

#### Scenario: Panel round
- **WHEN** `judge.jsonl` rows have `mode: panel`
- **THEN** RESULTS.md has a "panel-valid" quality column, no "judge-valid", a takeaway starting "Judge: panel mode" and a judge agreement table

#### Scenario: Fact round
- **WHEN** `judge.jsonl` rows have no `mode`
- **THEN** RESULTS.md keeps "judge-valid", says "Judge: fact mode" in the takeaways and has no judge agreement table
