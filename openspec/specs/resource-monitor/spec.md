# resource-monitor Specification

## Purpose
TBD - created by archiving change bootstrap-engine. Update Purpose after archive.
## Requirements
### Requirement: Per-bout resource metrics
The engine SHALL sample every process in the bout's container once per second (RSS, threads, open fds, CPU ticks) and read cgroup v2 totals (memory.peak, cpu.stat, pids.peak, io.stat) at exit.

#### Scenario: Samples recorded
- **WHEN** a bout runs for at least two seconds
- **THEN** `resources.jsonl` has at least two sample rows and `record.json` has a `resources` summary with peak RSS, peak threads, peak fds and CPU seconds

#### Scenario: Monitor unavailable
- **WHEN** the cgroup path cannot be found
- **THEN** the bout still runs and the summary records `resources.available = false` with a reason

