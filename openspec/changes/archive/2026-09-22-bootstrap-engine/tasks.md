## 1. Repo skeleton

- [x] 1.1 pyproject (uv, Python 3.14), .gitignore, .env.example, .tool-versions, colored justfile
- [x] 1.2 README with quickstart and glossary

## 2. Config and schemas

- [x] 2.1 Pydantic models: runtime, models, contender, arena, task, trial
- [x] 2.2 JSON schemas: findings, record

## 3. Locking

- [x] 3.1 Resolve git refs to SHAs, tree-hash skill dirs, pin image digest and CLI version
- [x] 3.2 Reject model aliases; `--locked` drift check
- [x] 3.3 Deterministic bout IDs

## 4. Sandbox and adapter

- [x] 4.1 Containerfile with pinned Node LTS + Claude Code CLI
- [x] 4.2 Arena preparation: clone at SHA, strip globs, remove .git, context-file check
- [x] 4.3 Claude Code adapter: command builder for api_key / oauth / credentials_file
- [x] 4.4 stream-json parser: init gate, usage, cost, tool counts, skill-fired, structured output

## 5. Resource monitor

- [x] 5.1 cgroup path discovery for rootless podman
- [x] 5.2 1 s sampler and exit totals, summary

## 6. Secrets

- [x] 6.1 Auth resolution from env var name / env file
- [x] 6.2 Scrubber on every artifact; gitleaks in `just verify`

## 7. CLI and tests

- [x] 7.1 `skillordeal bout` running one bout end to end
- [x] 7.2 pytest: lock drift, bout ID, isolation gate, stream parser, scrubber, monitor parsing

## Follow-ups (not in this change)

- Egress allowlist (proxy so bouts can only reach the model API)
- `pipeline` contender kind: finder bout, then a fresh verifier bout (e.g. ToB fp-check) over its findings
- More adapters (Codex CLI, Gemini CLI, opencode)
