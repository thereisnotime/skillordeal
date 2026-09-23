# groundtruth-matching Specification

## Purpose
TBD - created by archiving change groundtruth. Update Purpose after archive.
## Requirements
### Requirement: Ground truth file
The engine SHALL load `groundtruth.yaml` from the path given by the arena definition, validate it (arena, sha, complete default false, window default 5, issues with id, title, category, cwe list, severity, locations with file and inclusive lines, source, notes), and warn when its `sha` differs from the arena commit locked for the round or when the file changed since locking.

#### Scenario: Truth written against another commit
- **WHEN** the ground truth `sha` differs from the locked arena sha
- **THEN** `score` still completes and prints a warning naming both commits

#### Scenario: Bad line range
- **WHEN** a location has `lines: [5, 2]`
- **THEN** loading fails with a validation error

### Requirement: Match kind
A finding SHALL match an issue on kind only if, when both the finding and the issue name CWEs, they share an exact CWE id; otherwise their categories SHALL be equal.

#### Scenario: CWE mismatch with equal category
- **WHEN** a finding with CWE-79 and category security overlaps an issue with CWE-89 and category security
- **THEN** it does not match

#### Scenario: Category fallback
- **WHEN** the finding has no CWE, or the issue's cwe list is empty
- **THEN** equal categories match and the row records `match_basis: category`

### Requirement: Match place
A finding SHALL match an issue location only if the normalized files are equal and `[line_start, line_end or line_start]` overlaps the location's lines widened by ±window.

#### Scenario: Window edge
- **WHEN** the issue is at lines 20-25 with window 5 and the finding ends on line 15
- **THEN** it matches, and a finding ending on line 14 does not

### Requirement: Best issue
When several issues match a finding, the engine SHALL tie it to the one with exact (unwidened) overlap first, then the smallest midpoint distance, then the lowest issue id, and record that `line_distance`.

#### Scenario: Neighbouring issues
- **WHEN** a finding at line 14 is within the window of an issue at line 10 and overlaps an issue at lines 14-16
- **THEN** it is tied to the issue at 14-16 with `line_distance` 1

### Requirement: Per-bout verdicts
Per bout, the first finding tied to an issue SHALL be `tp` and later ones `dup`; a finding that matches nothing SHALL be `fp` when the ground truth is complete and `unknown` otherwise.

#### Scenario: Incomplete ground truth
- **WHEN** a bout has 4 tp, 2 dup and 4 unmatched findings on incomplete ground truth
- **THEN** its row has `precision` empty, `precision_lower_bound` 0.5 and recall equal to distinct matched issues over all issues

