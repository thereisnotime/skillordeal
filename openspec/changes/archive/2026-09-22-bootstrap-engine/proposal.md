## Why

People compare agent skills (SKILL.md packs) on vibes and star counts. We want a reproducible way to run one skill, and nothing else, against a pinned codebase and measure what comes out: findings, tokens, cost, wall time and client resource usage. This change lays down the engine so a single bout can run end to end, fully isolated and fully pinned.

## What Changes

- New Python package `skillordeal` (uv, Python 3.14) with a typer CLI and a colored justfile.
- Pydantic config models for trials, contenders, arenas, tasks, models and runtime.
- `lock` command that resolves every moving part (engine version, container image digest, Claude Code CLI version, model IDs, skill commit + tree hash, arena commit) into `lock.yaml`, and a `--locked` mode that refuses to run on drift.
- Container image (rootless podman) with pinned Node LTS and pinned Claude Code CLI.
- Claude Code adapter that runs a bout headless with zero ambient context and verifies isolation from the `system/init` event.
- Resource monitor: cgroup v2 totals plus a 1 s per-process sampler (RSS, threads, fds, CPU).
- Auth via API key, OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`, sourced from a named host env var or env file) or a copied credentials file, with secret scrubbing on every artifact.

## Capabilities

### New Capabilities
- `bout-isolation`: one bout runs with exactly the intended skill, no ambient context, and is rejected otherwise.
- `locking`: all inputs are pinned and drift is detected.
- `resource-monitor`: per-bout client resource metrics.
- `auth-secrets`: credentials reach the container without leaking into argv, logs, artifacts or git.

### Modified Capabilities
None (new project).

## Impact

New repo. Needs podman (rootless, cgroup v2) and uv on the host. Trials live in the separate `skillordeal-trials` repo.
