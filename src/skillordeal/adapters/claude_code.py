"""Claude Code CLI adapter: builds the headless command and parses stream-json output."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from skillordeal.config import (
    BASELINE,
    AuthMode,
    Contender,
    ContenderKind,
    Invocation,
    ModelSpec,
    Task,
)

CTX = "/ctx"
OUT = "/out"  # the only writable path a bout has; holds findings.json
ARENA = "/arena"
BOUT = "/bout"


@dataclass
class BoutSpec:
    contender: Contender
    skill_name: str | None
    plugin_name: str | None
    expected_skills: list[str]
    model: ModelSpec
    task: Task
    invocation: Invocation
    auth_mode: AuthMode
    budget_usd: float
    # What the CLI loads by itself, probed offline at lock time.
    builtin_skills: tuple[str, ...] = ()
    builtin_plugins: tuple[str, ...] = ()


def build_prompt(spec: BoutSpec, task_prompt: str) -> str:
    if spec.contender.kind != ContenderKind.baseline and spec.invocation == Invocation.forced:
        return f"/{spec.skill_name}\n\n{task_prompt}"
    return task_prompt


def build_command(
    spec: BoutSpec, schema: dict[str, Any] | None = None, *, output_file: bool = False
) -> list[str]:
    """Headless command for one run.

    `schema` uses the CLI's structured output (fine for small outputs like judge verdicts).
    `output_file` instead lets the agent write only `/out/findings.json`, which the engine
    validates itself: long nested reports through structured output got mangled (the model
    sometimes wrote the findings array as XML inside another field).
    """
    tools = list(dict.fromkeys([*spec.task.tools, *spec.contender.extra_tools]))
    if spec.contender.kind == ContenderKind.baseline:
        tools = [t for t in tools if t != "Skill"]
    allowed = [t for t in tools if t != "Bash"] + spec.task.allowed_bash
    if output_file:
        tools = list(dict.fromkeys([*tools, "Write", "Edit"]))
        allowed += [f"Write(/{OUT}/**)", f"Edit(/{OUT}/**)", f"Read(/{OUT}/**)"]
    settings = {
        "disableBundledSkills": True,
        "autoMemoryEnabled": False,
        "includeCoAuthoredBy": False,
        "cleanupPeriodDays": 1,
    }
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        spec.model.id,
        "--restricted",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
        "--settings",
        json.dumps(settings, separators=(",", ":")),
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--tools",
        ",".join(tools),
        "--allowedTools",
        ",".join(allowed),
        "--max-budget-usd",
        f"{spec.budget_usd:.2f}",
        "--exclude-dynamic-system-prompt-sections",
    ]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    if output_file:
        cmd += ["--add-dir", OUT]
    if spec.auth_mode == AuthMode.api_key:
        cmd.append("--bare")  # skips CLAUDE.md discovery, hooks, plugin sync, keychain, OAuth
    else:
        cmd += ["--setting-sources", ""]
    if spec.model.effort:
        cmd += ["--effort", spec.model.effort.value]
    if spec.contender.kind == ContenderKind.baseline:
        cmd.append("--disable-slash-commands")
    else:
        cmd += ["--plugin-dir", f"{CTX}/plugins/{spec.plugin_name}"]
    return cmd


def plain_spec(
    model: ModelSpec,
    tools: list[str],
    auth_mode: AuthMode,
    budget_usd: float,
    *,
    builtin_skills: tuple[str, ...] = (),
    builtin_plugins: tuple[str, ...] = (),
) -> BoutSpec:
    """A spec with no contender at all, for engine-side runs such as the judge.

    Going through the baseline path keeps every isolation flag of a real bout (and the same
    isolation gate) instead of maintaining a second command builder.
    """
    return BoutSpec(
        contender=BASELINE,
        skill_name=None,
        plugin_name=None,
        expected_skills=[],
        model=model,
        task=Task(id="engine", prompt_file="-", tools=tools, allowed_bash=[]),
        invocation=Invocation.forced,
        auth_mode=auth_mode,
        budget_usd=budget_usd,
        builtin_skills=builtin_skills,
        builtin_plugins=builtin_plugins,
    )


# --- stream parsing ---------------------------------------------------------------


@dataclass
class ParsedStream:
    init: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    tool_calls: Counter[str] = field(default_factory=Counter)
    skills_invoked: list[str] = field(default_factory=list)
    assistant_messages: int = 0
    bad_lines: int = 0
    events: int = 0
    api_errors: list[str] = field(default_factory=list)
    # Prompt size of the first request. Forced skills are expanded inline (no Skill tool call),
    # so comparing this against the baseline is how the report confirms injection.
    first_turn_prompt_tokens: int | None = None


def parse_stream(lines: list[str]) -> ParsedStream:
    ps = ParsedStream()
    ps_errors: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            ps.bad_lines += 1
            continue
        ps.events += 1
        typ = ev.get("type")
        if typ == "system" and ev.get("subtype") == "init":
            ps.init = ev
        elif typ == "result":
            ps.result = ev
        elif typ == "assistant":
            ps.assistant_messages += 1
            if ps.first_turn_prompt_tokens is None:
                u = (ev.get("message") or {}).get("usage") or {}
                ps.first_turn_prompt_tokens = sum(
                    int(u.get(k) or 0)
                    for k in (
                        "input_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    )
                )
            if ev.get("error"):
                texts = [b.get("text", "") for b in (ev.get("message") or {}).get("content") or []]
                ps_errors.append(f"{ev['error']}: {' '.join(texts)[:300]}")
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    ps.tool_calls[name] += 1
                    if name == "Skill":
                        inp = block.get("input") or {}
                        ps.skills_invoked.append(str(inp.get("skill") or inp.get("command") or inp))
    ps.api_errors = ps_errors
    return ps


def _skill_names(init: dict[str, Any]) -> list[str]:
    raw = init.get("skills") or []
    return sorted(s if isinstance(s, str) else str(s.get("name")) for s in raw)


def isolation_problems(ps: ParsedStream, spec: BoutSpec) -> list[str]:
    """Check the init event against what this bout is supposed to see."""
    if ps.init is None:
        return ["no-init-event"]
    problems = []
    loaded = set(_skill_names(ps.init)) - set(spec.builtin_skills)
    expected = set(spec.expected_skills)
    if loaded != expected:
        problems.append(f"skills-mismatch: loaded={sorted(loaded)} expected={sorted(expected)}")
    mcp = ps.init.get("mcp_servers") or []
    if mcp:
        problems.append(f"mcp-servers-present: {[m.get('name', m) for m in mcp]}")
    model = ps.init.get("model")
    if model and model != spec.model.id:
        problems.append(f"model-mismatch: {model} != {spec.model.id}")
    plugins = [p.get("name", p) if isinstance(p, dict) else p for p in ps.init.get("plugins") or []]
    want_plugins = [spec.plugin_name] if spec.plugin_name else []
    extra = sorted(set(map(str, plugins)) - set(want_plugins) - set(spec.builtin_plugins))
    if extra:
        problems.append(f"unexpected-plugins: {extra}")
    return problems


def usage_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {}
    usage = result.get("usage") or {}
    per_model = {}
    for model, u in (result.get("modelUsage") or {}).items():
        per_model[model] = {
            "input_tokens": u.get("inputTokens", 0),
            "output_tokens": u.get("outputTokens", 0),
            "cache_read_tokens": u.get("cacheReadInputTokens", 0),
            "cache_creation_tokens": u.get("cacheCreationInputTokens", 0),
            "cost_usd": u.get("costUSD"),
        }
    tokens = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
    }
    if per_model:  # modelUsage includes subagents, usage does not
        tokens = {k: sum(m[k] for m in per_model.values()) for k in tokens}
    tokens["total_tokens"] = sum(tokens.values())
    return {
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error", False),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "num_turns": result.get("num_turns"),
        "total_cost_usd": result.get("total_cost_usd"),
        "tokens": tokens,
        "per_model": per_model,
        "permission_denials": len(result.get("permission_denials") or []),
        "session_id": result.get("session_id"),
        "terminal_reason": result.get("terminal_reason"),
        "stop_reason": result.get("stop_reason"),
    }


def structured_output(result: dict[str, Any] | None) -> Any:
    if not result:
        return None
    so = result.get("structured_output")
    if so is not None:
        return so
    # Fall back to a JSON body in the final text, then give up (recorded as schema violation).
    text = (result.get("result") or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError, TypeError:
        return None
