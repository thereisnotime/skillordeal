## Context

Bouts already run headless Claude Code in a locked-down podman container (bootstrap-engine). The judge needs the same guarantees: it must read the real code, see nothing about the contender, and leave an audit trail.

## Goals / Non-Goals

**Goals:** blinded verdicts grounded in the code, zero cost for anything judged before, a hard spend ceiling, reproducible prompts.

**Non-Goals:** majority voting across several judge models, calibrating the judge against human labels (reports can do that from the files), judging across arenas in one call.

## Decisions

- **Same sandbox, same command builder.** `plain_spec()` builds a `BoutSpec` with the baseline contender, the judge model and tools `Read,Grep,Glob`, so `build_command` emits every isolation flag a bout gets (`--restricted`, empty strict MCP config, no session persistence, `--bare` or `--setting-sources ""`, `--disable-slash-commands`, no plugin dir). The container comes from `bout.podman_create_args` with the arena from `bout.prepare_arena` mounted read-only and an empty `/ctx`. Credentials come from `secrets.resolve_credentials` and go in by name; every artifact is scrubbed.
- **Isolation gate on the judge too.** The init event is checked with `isolation_problems` (skills beyond the locked builtins, MCP servers, model mismatch, unexpected plugins) plus a tool check (nothing beyond Read/Grep/Glob/StructuredOutput). A failing run's verdicts are discarded and not cached.
- **Blinding.** Only `file, line_start, line_end, category, cwe, title, description, evidence` are sent; severity, confidence and recommendation are left out too, since they carry each contender's style. Contender ids, skill and plugin names (never the neutral `ordeal` wrapper) and anything shaped like a bout or finding id are replaced with `[redacted]` in the text fields. Refs are `F1..Fn` per batch.
- **Deterministic shuffle.** Findings are deduplicated by `finding_hash`, grouped by arena, sorted and shuffled with `random.Random` seeded from the sha256 of the sorted hashes, then cut into batches of `--batch-size` (default 15). The same set of findings always gives the same batches and prompts, whatever the input order.
- **Cache key.** `<cache_dir>/judge/<model slug>/<prompt_sha>/<finding_hash>.json`, where `prompt_sha` covers the prompt template and the verdict schema. Editing either starts a fresh cache; nothing else invalidates it. Writes are atomic (tmp + rename). A finding missing from the model's answer isn't cached, so the next run retries it.
- **Budget.** Batches run one after another. Before each call the remaining budget is checked; each call's `--max-budget-usd` is `min(judge.max_budget_usd, what's left)`.
- **Dry run** plans the batches exactly as a real run would (skipping cached findings) and prints the ref-to-finding map on stderr and each exact prompt on stdout. It never resolves credentials or touches podman.
- **The runner is injectable** (`Runner` callable) so tests exercise the whole flow, including caching, retries, isolation failures and budget, with a fake container.

## Risks / Trade-offs

- A judge can still be wrong or lazy; the prompt insists on opening the code, and human labels outrank it.
- Redaction is by name. A skill that writes its own name in a novel form could still leak; the test asserts the known names don't.
- Batching trades cost for independence: verdicts within a batch can influence each other (that's also what makes `duplicate` possible).
