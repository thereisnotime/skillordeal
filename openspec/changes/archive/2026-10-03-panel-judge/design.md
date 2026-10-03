## Context

The fact judge (`score/judge.py`) batches blinded, shuffled findings per arena, runs one sandboxed Claude Code call per batch with only Read, Grep and Glob, and caches one verdict per finding. Its prompt asks "does the code have this problem as described", which nearly every finding from a competent auditor passes. The missing questions are the ones a security reviewer asks first: who writes the input, may the code trust them, and what does the attacker get that they didn't already have.

The verifier in Anthropic's claude-security plugin (scan-verifier) handles this by having each check try to disprove a finding from one angle; the finding survives only if refutation fails.

## Goals / Non-Goals

**Goals:** a judge whose `valid` means "a reachable, attacker-controlled path with a real security gain", good enough that human review is optional; the same isolation, blinding and budget guarantees as the fact judge; fact mode untouched; resumable, cached runs.

**Non-Goals:** custom lens text in `trial.yaml` (built-in lenses only for now), weighting votes by confidence, letting a voter run code, changing the summary's verdict precedence.

## Decisions

- **Separate module, shared plumbing.** `score/panel.py` holds planning, aggregation and the run loop. `judge.py` exposes `run_call` (one sandboxed call: work dirs, credentials, egress, `podman_create_args`, scrubbed transcript, isolation and tool checks) and `Batch.payload` (the blinded findings), and its own `_judge_batch` now uses `run_call`. Fact mode's prompts, records, rows and cache files are pinned by a golden digest recorded before the refactor.
- **Config on `Trial.judge`.** `JudgeSpec(ModelSpec)` adds `mode`, `voters` (>= number of lenses) and `lenses` (a non-repeating subset of the built-ins). One block keeps the judge's model and its ruling together. `voters > len(lenses)` wraps around: slots `reachability`, `impact`, `correctness`, `reachability-2`, ... A second voter on a lens is another independent sample of the same prompt.
- **Ruling fields stay out of the lock.** `trial_hash` and `lock.judge` use only the `ModelSpec` part. Judging happens after the bouts and can be switched with `--mode`; it must not make a locked round look drifted, and existing trials keep their hash.
- **One template, lens sections.** `judge_panel.md` is the shared instructions with `{lens_name}` and `{lens}` placeholders, followed by `<!-- lens: NAME -->` sections. The lens is substituted before `{findings}`, so finding text is never substituted into. Every voter gets the shared rules: try to refute, name the attacker and whether the code may trust them, name the gain, self-inflicted/same-privilege = false positive, deployment preconditions are hurdles unless a shipped default closes the path, cite file:lines, no running code. Each voter still rules on the whole finding; the lens says where to push hardest.
- **Majority, computed by the engine.** Decisive votes are `true_positive`/`false_positive`. More `unverifiable` than decisive votes, or a decisive tie, gives `unverifiable`; otherwise the majority wins (`valid`/`invalid`), confidence `high` when unanimous, `medium` otherwise, `low` when unverifiable. `agreement` = share of the most common vote. A strict veto ("any refutation kills it") was considered, as scan-verifier does with sequential checks; with independent, noisy LLM voters a single stray refutation would flip too many real findings, so majority is the default and veto is left as a possible future `rule` option.
- **Cache per finding per slot.** `judge/<model>/panel-<sha>/<slot>/<finding_hash>.json`, where sha covers the whole panel prompt file and the vote schema. The `panel-` prefix separates it from fact verdicts of the same model. The verdict is recomputed from cached votes on every run, so changing `voters` or `lenses` reuses every vote that still applies. A finding gets a `judge.jsonl` row only once all its slots have voted.
- **Same batch for every voter.** Batches are cut once over findings missing any vote (same deterministic shuffle as the fact judge), and each slot runs a batch unless all its findings already have that slot's vote. On a fresh run all voters see identical batches; after an interruption the leftover batch is again shared. Calls go batch by batch, voter by voter, so a budget stop leaves whole findings rather than one lens's votes for everything.
- **Budget across voters.** `--max-cost-usd` is checked before every call and caps each call's `--max-budget-usd` to what's left, exactly as in fact mode.
- **Report reads the mode from `judge.jsonl`.** The lock no longer records it, and `--mode` can override it, so the scores are the source of truth. A context variable set by `build_report` relabels the `judge_valid` metric as "panel-valid" for every table, caption and takeaway of one report. A takeaway names the judge mode for both modes, and a per-contender table shows panel verdicts and judge agreement.

## Risks / Trade-offs

- Three times the cost of fact mode per finding. Mitigated by caching, `--max-cost-usd`, and `--dry-run` to size a run first.
- Majority means one correct refutation (say, reachability) can be outvoted by two voters that didn't look at that angle closely. Every voter gets the full threat-model rules, not just its lens, which narrows this; the per-vote record in `judge.jsonl` lets a reader find split decisions.
- Panel and fact rows overwrite each other in `judge.jsonl` (whichever mode ran last). Both caches persist, so switching back is free.
- Built-in lenses only. Adding a lens means adding a section to the prompt (which starts a fresh panel cache) and to the `JudgeLens` literal.
