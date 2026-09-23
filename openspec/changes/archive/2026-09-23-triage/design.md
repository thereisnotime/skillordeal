## Context

skills-collection layout, next to `skills-list.json`:

- `skills-list.json`: `{repo_dir: [{name, description, path, remote_path, lines, words, ...}]}`, where `repo_dir` is `owner--repo` and `path` is relative to `skills/`
- `inventory.json`: `[{url, dir, description}]`, the canonical repo URLs
- `repos-meta.json`: `{repo_dir: {stars, created_at, pushed_at}}`
- `commits.json`: `{repo_dir: sha}`, the commit that was synced (so, what we scanned)

## Goals / Non-Goals

**Goals:** a deterministic, explainable ranking; output that `lock` accepts as is; fork/duplicate folding; a first-pass risk signal.

**Non-Goals:** judging skill quality with a model, a security verdict, fetching repos.

## Decisions

- **Keyword match** is a case-insensitive substring match on `name + description`. The defaults cover security and code review. `vulnerab` catches both vulnerability and vulnerable.
- **Near-duplicate** = the same sha256 over SKILL.md with the frontmatter removed, lowercased, and whitespace collapsed. That catches forks that only rename or reflow. The highest-scoring copy is kept, and the others are listed in its notes.
- **Score** = 3 × keyword hits + min(words, 3000)/1000 + 2 if a `references/` (or `reference/`) dir exists + log10(stars + 1). It's simple on purpose, and the parts are printed so anyone can disagree with the weights.
- **Source.** The repo URL comes from `inventory.json` (fallback: `owner--repo`). `subpath` is the parent of `remote_path`. `ref` is omitted by default, so `lock` resolves the default branch, as requested. `--pin-synced` sets it to the commits.json sha, so the scanned tree and the locked tree are the same. Non-GitHub repos, or skills with no `remote_path`, fall back to a local `path`.
- **Risk pre-scan.** Every text file under the skill dir (≤ 1 MB, symlinks skipped) is grepped for the listed patterns, prompt-injection phrases, and URL hosts outside GitHub. Each hit is tagged `[code]` (under scripts/, a code suffix, or a shebang) or `[doc]`. Security skills are full of curl and eval examples in prose, so the level separates those. high: code that fetches, decodes or evals, or an injection phrase in SKILL.md or code. medium: other code hits, including shipping scripts. low: prose only.
- **YAML output** is hand-assembled from `dump_yaml` per entry, so each entry can carry a comment line with score and risk. Every entry is validated with `Contender.model_validate` before writing.
- **Classifier hook.** `run_triage(classifier=...)` takes a `Callable[[Candidate, str], str | None]` whose note is appended to the entry. There's no CLI flag yet (TODO).

## Risks / Trade-offs

- Substring keywords give false positives (an "audit" skill for SEO). Triage is a shortlist for a human, not a selection.
- A regex pre-scan is easy to evade. It exists to catch the lazy cases and to point reviewers at the files worth reading. The container sandbox is still the real control.
