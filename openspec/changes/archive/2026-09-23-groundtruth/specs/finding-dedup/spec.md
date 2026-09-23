## ADDED Requirements

### Requirement: Deterministic clusters
The engine SHALL cluster findings of one arena across all bouts, linking two findings when they cite the same normalized file, their lines overlap within ±window, and they share a CWE (when both cite one) or otherwise the same category; clusters are the connected components.

#### Scenario: Row order
- **WHEN** the same findings are clustered in a different order
- **THEN** every finding gets the same `cluster_id`

#### Scenario: Different CWEs on the same lines
- **WHEN** two findings on the same lines cite CWE-89 and CWE-79
- **THEN** they are in different clusters

### Requirement: Stable cluster id
`cluster_id` SHALL be `c-` followed by the first 16 hex characters of sha256 of `<arena>:<smallest member finding_hash>`.

#### Scenario: Same finding in two arenas
- **WHEN** identical findings appear in two arenas
- **THEN** they get different cluster ids

### Requirement: Unique findings per contender
`score` SHALL write `unique.csv` with, per arena and contender, the number of bouts, findings, distinct clusters, and clusters no other contender found.

#### Scenario: Only one contender found it
- **WHEN** a cluster's findings all come from one contender
- **THEN** it counts towards that contender's `exclusive_clusters`
