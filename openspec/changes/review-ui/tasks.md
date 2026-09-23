## 1. Shared readers

- [x] 1.1 `rounddata.py`: Round paths, jsonl reader tolerant of a truncated last line, finding_hash per contract
- [x] 1.2 Labels: make/append (fsync, locked), effective labels with later-line-wins
- [x] 1.3 Fallback: findings built from bout dirs when scores/findings.jsonl is missing

## 2. Server

- [x] 2.1 ReviewState: join findings + bout details + gt + judge + labels, blind field removal, sort by location
- [x] 2.2 Arena prep via `bout.prepare_arena` once per arena into the cache
- [x] 2.3 Traversal-safe excerpt (±15 lines)
- [x] 2.4 stdlib server on 127.0.0.1, token on /api/, JSON-only POST, CSP and nosniff headers

## 3. Page

- [x] 3.1 Self-contained page: queue, finding card, excerpt with highlighted lines, verdict dock
- [x] 3.2 Keyboard shortcuts, filters, progress counter, issue picker, note
- [x] 3.3 Light/dark via prefers-color-scheme

## 4. CLI and tests

- [x] 4.1 `skillordeal review` in cli_extra.py, registered from cli.py; `just review`
- [x] 4.2 Tests: label append/override, traversal rejected, token required, live server round trip
