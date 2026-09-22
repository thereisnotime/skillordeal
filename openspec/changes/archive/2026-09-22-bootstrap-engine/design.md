## Context

The engine is generic. It knows nothing about any particular study; trials (in `skillordeal-trials`) hand it config and get back bout directories.

## Goals / Non-Goals

**Goals:** reproducible single bouts, locked inputs, zero ambient context, cheap-to-read artifacts, everything runnable from shell.

**Non-Goals (this change):** matrix scheduling, CI, scoring, judge, review UI, reports. Those are later changes.

## Decisions

- **Claude Code CLI over the Agent SDK.** The thing people actually run is the CLI, and `stream-json` gives us the init event (skills, MCP servers, model), per-message usage and the final result with cost. The SDK can be a second adapter later.
- **`--bare` for API-key auth, `--setting-sources ""` for OAuth.** `--bare` skips CLAUDE.md discovery, hooks, plugin sync and auto-memory but does not read OAuth tokens, so OAuth runs use the non-bare path with the same env isolation. Both are verified by the same isolation gate, so the difference cannot silently leak context.
- **Skill delivery via `--add-dir /ctx`** where `/ctx/.claude/skills/<name>/` holds a read-only copy. Plugins use `--plugin-dir`. Baseline gets an empty ctx.
- **Structured findings via `--json-schema`.** One schema for every contender keeps results comparable; non-compliance is recorded, never hand-fixed.
- **Podman, rootless.** Each bout gets a fresh tmpfs `HOME`/`CLAUDE_CONFIG_DIR`. The arena is mounted read-only, `.git` removed, `strip:` globs deleted.
- **Resource monitoring from the host.** We read the container's cgroup (`/sys/fs/cgroup/.../libpod-<id>.scope`) and walk `cgroup.procs` each second reading `/proc/<pid>/{status,stat,fd}`. No agent inside the container, so it cannot perturb or be tampered with by the skill.
- **Secrets passed by name.** podman `--env NAME` (no value) inherits from the podman process env, which we build in Python. Values never hit argv. Every text artifact goes through a scrubber that replaces known secret values and `sk-ant-*` patterns.
- **Bout ID** = sha256 over the canonical JSON of all locked inputs + rep index. Same inputs, same ID, so runs are idempotent and resumable.

## Risks / Trade-offs

- Third-party skills are untrusted. Mitigation: read-only tool allowlist, no write tools, read-only mounts, no host credentials except the model token, container boundary.
- `total_cost_usd` is a client-side estimate, and under OAuth it is notional. Recorded with `cost_is_estimate`.
- Model nondeterminism: handled with repetitions, not seeds.
