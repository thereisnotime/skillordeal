## 1. Repo skeleton

- [ ] 1.1 pyproject (uv, Python 3.14), .gitignore, .env.example, .tool-versions, colored justfile
- [ ] 1.2 README with quickstart and glossary

## 2. Config and schemas

- [ ] 2.1 Pydantic models: runtime, models, contender, arena, task, trial
- [ ] 2.2 JSON schemas: findings, record

## 3. Locking

- [ ] 3.1 Resolve git refs to SHAs, tree-hash skill dirs, pin image digest and CLI version
- [ ] 3.2 Reject model aliases; `--locked` drift check
- [ ] 3.3 Deterministic bout IDs

## 4. Sandbox and adapter

- [ ] 4.1 Containerfile with pinned Node LTS + Claude Code CLI
- [ ] 4.2 Arena preparation: clone at SHA, strip globs, remove .git, context-file check
- [ ] 4.3 Claude Code adapter: command builder for api_key / oauth / credentials_file
- [ ] 4.4 stream-json parser: init gate, usage, cost, tool counts, skill-fired, structured output

## 5. Resource monitor

- [ ] 5.1 cgroup path discovery for rootless podman
- [ ] 5.2 1 s sampler and exit totals, summary

## 6. Secrets

- [ ] 6.1 Auth resolution from env var name / env file
- [ ] 6.2 Scrubber on every artifact; gitleaks in `just verify`

## 7. CLI and tests

- [ ] 7.1 `skillordeal bout` running one bout end to end
- [ ] 7.2 pytest: lock drift, bout ID, isolation gate, stream parser, scrubber, monitor parsing
