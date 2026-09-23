## Context

Scoring (`skillordeal score`/`judge`) is built separately and writes `rounds/<r>/scores/*.jsonl`. The review UI only reads those files and appends to `labels/labels.jsonl`; it never scores.

## Goals / Non-Goals

**Goals:** fast keyboard labeling, blind by default, zero dependencies, safe to run next to untrusted agent output, labels in the contract format.

**Non-Goals:** multi-user hosting, authentication beyond a local session token, editing ground truth directly (that's `gt-promote`), inter-rater statistics.

## Decisions

- **stdlib `ThreadingHTTPServer` on 127.0.0.1 only.** The bind host is not configurable. No framework and no build step; the page is one HTML file shipped in the package with inline CSS/JS, and it loads nothing from the network (its CSP says `default-src 'none'`).
- **Session token on every `/api/` call, not just POSTs.** A random `secrets.token_urlsafe(24)` is printed in the startup URL and sent as `X-Review-Token`. The page moves it into `sessionStorage` and strips it from the address bar. POSTs must also be `application/json`, which keeps simple cross-site form posts out even before the token check. Comparison is constant-time.
- **Blind = the data never reaches the browser.** `contender`, `bout_id` and `rep` are removed server-side. `finding_id` stays, because labels need it and bout ids are opaque hashes. Findings are sorted by arena, file and line, so contenders interleave and duplicates cluster.
- **Excerpts from the prepared arena.** `bout.prepare_arena` exports at the locked sha and strips context files and `strip:` globs, exactly as for the bout. It goes to `<cache>/review/arenas/<arena>-<sha12>/` with a marker JSON, so it's done once per arena, and a lock guards concurrent first requests. The finding's `file` is agent output: it's normalized (`./`, `/arena/`), then rejected if it's absolute, empty, or resolves (symlinks included) outside the arena root.
- **Labels are append-only, fsynced lines.** "Later lines override" is resolved at read time per `(finding_hash, labeler)`. The labeler defaults to `human:$USER`. `issue_id` is checked against the arena's `groundtruth.yaml`.
- **Fallback when scores are missing.** Findings are built from bout dirs with `finding_hash` computed per the contract (missing fields hash as `null`), and a warning is shown. The score stage stays the source of truth whenever `findings.jsonl` exists.

## Risks / Trade-offs

- A label keyed by `finding_hash` applies to every identical finding in the round (same text, other rep). That's intended by the contract, but it means one keystroke can label several rows.
- The fallback hash has to match `skillordeal score` exactly. If the two ever disagree, labels made before scoring won't join. Mitigation: the fallback is only used when `findings.jsonl` is absent, and it's flagged in the UI.
