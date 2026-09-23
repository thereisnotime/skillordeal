"""Config models for runtime, models, contenders, arenas, tasks and trials.

Everything a bout depends on is described here. Anything that can move (a git ref,
an image tag, a model alias) is resolved to an immutable value by `lock.py`.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from skillordeal.yamlio import load_yaml

SLUG = r"^[a-z0-9][a-z0-9._-]*$"
Slug = Annotated[str, Field(pattern=SLUG)]

# Model names that float to "whatever is latest". Not allowed in a lock.
MODEL_ALIASES = {"opus", "sonnet", "haiku", "fable", "default", "best", "opusplan"}

# Files that inject context into an agent. Always stripped from arenas, and the
# isolation gate refuses a bout if any survive.
CONTEXT_FILES = (
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    "GEMINI.md",
    ".claude",
    ".mcp.json",
    ".cursorrules",
    ".cursor",
    ".github/copilot-instructions.md",
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- runtime ---------------------------------------------------------------


class AuthMode(StrEnum):
    api_key = "api_key"
    oauth = "oauth"
    credentials_file = "credentials_file"


class AuthConfig(Strict):
    mode: AuthMode = AuthMode.api_key
    # Name of the host env var holding the secret. Never the value itself.
    token_env: str | None = None
    # Optional dotenv file loaded before reading token_env (e.g. ~/Private/Secret/xxRC/.env).
    env_file: str | None = None
    # For credentials_file mode: a Claude Code .credentials.json copied into the bout's
    # throwaway config dir (the rest of ~/.claude is never mounted).
    credentials_file: str = "~/.claude/.credentials.json"

    @property
    def default_token_env(self) -> str:
        return "ANTHROPIC_API_KEY" if self.mode == AuthMode.api_key else "CLAUDE_CODE_OAUTH_TOKEN"

    @property
    def container_env(self) -> str:
        """Variable name the CLI reads inside the container."""
        return "ANTHROPIC_API_KEY" if self.mode == AuthMode.api_key else "CLAUDE_CODE_OAUTH_TOKEN"


class ImageConfig(Strict):
    name: str = "localhost/skillordeal-runner"
    tag: str = "dev"
    base: str = "docker.io/library/node:24.21.0-trixie-slim"


class LimitsConfig(Strict):
    """Per-bout ceilings. Hitting one ends the bout with status `limit_exceeded`."""

    # container (cgroup) limits; also keep timing comparable across bouts
    cpus: float = 2.0
    memory: str = "4g"
    pids: int = 1024
    timeout_s: int = 1800
    # model-side limits, enforced by the harness while streaming
    max_total_tokens: int | None = 3_000_000  # input + output + cache, all models
    max_output_tokens: int | None = None  # per response, via CLAUDE_CODE_MAX_OUTPUT_TOKENS
    max_turns: int | None = 200  # assistant messages


class NetworkMode(StrEnum):
    allowlist = "allowlist"  # internal network + proxy that only reaches `allow` hosts
    open = "open"  # default podman network, unrestricted (debugging only)


class NetworkConfig(Strict):
    mode: NetworkMode = NetworkMode.allowlist
    allow: list[str] = ["api.anthropic.com"]
    internal_network: str = "skillordeal-internal"


class RuntimeConfig(Strict):
    cli_name: Literal["claude-code"] = "claude-code"
    cli_version: str = "2.1.280"
    image: ImageConfig = ImageConfig()
    auth: AuthConfig = AuthConfig()
    limits: LimitsConfig = LimitsConfig()
    network: NetworkConfig = NetworkConfig()
    concurrency: int = 1
    sample_interval_s: float = 1.0
    work_dir: str = "work"
    cache_dir: str = "~/.cache/skillordeal"


# --- models ----------------------------------------------------------------


class Effort(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    xhigh = "xhigh"
    max = "max"


class ModelSpec(Strict):
    id: str
    effort: Effort | None = None
    max_budget_usd: float = 5.0

    @field_validator("id")
    @classmethod
    def no_alias(cls, v: str) -> str:
        if v in MODEL_ALIASES or not re.search(r"\d", v):
            raise ValueError(f"model {v!r} looks like an alias; use a full model ID")
        return v

    @property
    def slug(self) -> str:
        return self.id + (f"@{self.effort}" if self.effort else "")


# --- contenders ------------------------------------------------------------


class ContenderKind(StrEnum):
    baseline = "baseline"  # no skill at all
    skill = "skill"  # a directory with SKILL.md
    plugin = "plugin"  # a Claude Code plugin directory, loaded with --plugin-dir
    prompt = "prompt"  # a plain markdown prompt, wrapped into a SKILL.md


class Contender(Strict):
    id: Slug
    kind: ContenderKind
    # Remote source (preferred) ...
    repo: str | None = None
    ref: str | None = None  # branch, tag or sha; locked to a sha
    subpath: str = ""
    # ... or a local directory (e.g. inside skills-collection).
    path: str | None = None
    # Name the skill is invoked by. Read from SKILL.md frontmatter when omitted.
    skill_name: str | None = None
    # Extra tools this contender legitimately needs on top of the task's allowlist.
    extra_tools: list[str] = []
    # Globs removed from the contender copy (e.g. validators needing tools we don't ship).
    strip: list[str] = []
    role: Literal["finder", "verifier", "context", "general-review"] = "finder"
    license: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def source(self) -> Contender:
        if self.kind == ContenderKind.baseline:
            return self
        if bool(self.repo) == bool(self.path):
            raise ValueError(f"contender {self.id}: set exactly one of repo or path")
        return self


BASELINE = Contender(id="baseline", kind=ContenderKind.baseline, role="finder")


# --- arenas ----------------------------------------------------------------


class Arena(Strict):
    id: Slug
    repo: str
    ref: str
    language: str
    strip: list[str] = []
    # Optional focus hint passed to the task prompt.
    scope: list[str] = []
    # Ground truth file, relative to the arena definition file.
    groundtruth: str | None = None
    budget_usd: float | None = None
    notes: str = ""


# --- tasks and trials --------------------------------------------------------


class Task(Strict):
    id: Slug
    prompt_file: str
    # Tools the agent may use. Audits are read-only by default.
    tools: list[str] = ["Read", "Grep", "Glob", "Bash", "Skill"]
    allowed_bash: list[str] = ["Bash(git log *)", "Bash(git show *)", "Bash(ls *)", "Bash(wc *)"]


class Invocation(StrEnum):
    auto = "auto"  # skill loaded, Claude decides whether to use it
    forced = "forced"  # prompt starts with /<skill-name>


class Trial(Strict):
    id: Slug
    title: str
    question: str
    contenders_file: str
    contenders: list[str] = []  # ids to include; empty means all in file
    include_baseline: bool = True
    arenas_dir: str
    arenas: list[str]
    tasks: list[Task]
    models: list[ModelSpec]
    judge: ModelSpec | None = None
    reps: int = 3
    invocation: Invocation = Invocation.forced
    runtime: RuntimeConfig = RuntimeConfig()


class LoadedTrial(BaseModel):
    """A trial with every referenced file loaded and paths made absolute."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    trial: Trial
    root: Path
    contenders: list[Contender]
    arenas: list[Arena]
    arena_files: dict[str, Path]
    prompts: dict[str, str]


def _read(path: Path) -> Any:
    return load_yaml(path.read_text())


def load_trial(trial_file: Path) -> LoadedTrial:
    trial_file = trial_file.resolve()
    root = trial_file.parent
    trial = Trial.model_validate(_read(trial_file))

    cfile = (root / trial.contenders_file).resolve()
    raw = _read(cfile).get("contenders", [])
    known = [Contender.model_validate(c) for c in raw]
    by_id = {c.id: c for c in known}
    if trial.contenders:
        missing = [i for i in trial.contenders if i not in by_id]
        if missing:
            raise ValueError(f"unknown contenders in {cfile}: {missing}")
        chosen = [by_id[i] for i in trial.contenders]
    else:
        chosen = known
    if trial.include_baseline:
        chosen = [BASELINE, *chosen]
    ids = [c.id for c in chosen]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate contender ids")

    adir = (root / trial.arenas_dir).resolve()
    arenas, arena_files = [], {}
    for aid in trial.arenas:
        f = adir / aid / "arena.yaml"
        a = Arena.model_validate(_read(f))
        if a.id != aid:
            raise ValueError(f"{f}: id {a.id!r} does not match directory {aid!r}")
        arenas.append(a)
        arena_files[aid] = f

    prompts = {t.id: (root / t.prompt_file).read_text() for t in trial.tasks}
    return LoadedTrial(
        trial=trial,
        root=root,
        contenders=chosen,
        arenas=arenas,
        arena_files=arena_files,
        prompts=prompts,
    )
