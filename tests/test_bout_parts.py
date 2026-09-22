import json
from pathlib import Path

from skillordeal.adapters import claude_code as cc
from skillordeal.bout import LimitWatch, prepare_arena, render_prompt
from skillordeal.config import BASELINE, Arena, AuthMode, Contender, ModelSpec, Task
from skillordeal.gitsrc import resolve
from skillordeal.monitor import parse_io_stat, parse_kv, parse_proc_stat_ticks, parse_proc_status


def spec(kind="skill", **kw):
    c = BASELINE if kind == "baseline" else Contender(id="x", kind=kind, path="/tmp")
    return cc.BoutSpec(
        contender=c,
        skill_name=None if kind == "baseline" else "ordeal:x",
        plugin_name=None if kind == "baseline" else "ordeal",
        expected_skills=[] if kind == "baseline" else ["ordeal:x"],
        model=ModelSpec(id="claude-haiku-4-5-20251001"),
        task=Task(id="t", prompt_file="t.md"),
        invocation="forced",
        auth_mode=kw.get("auth", AuthMode.oauth),
        budget_usd=1.0,
        builtin_skills=("design", "doctor"),
        builtin_plugins=("telemetry",),
    )


def init(skills, plugins=(), model="claude-haiku-4-5-20251001", mcp=()):
    return json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "model": model,
            "skills": list(skills),
            "plugins": [{"name": p} for p in plugins],
            "mcp_servers": list(mcp),
        }
    )


def test_prepare_arena_strips_context(arena_repo, tmp_path):
    a = Arena(id="demo", repo=arena_repo.as_uri(), ref="main", language="python", strip=["dist/"])
    cache = tmp_path / "cache"
    sha = resolve(cache, a.repo, a.ref)
    dest = tmp_path / "prepared"
    info = prepare_arena(a, sha, cache, dest)
    assert not (dest / "CLAUDE.md").exists()
    assert not (dest / "app" / "AGENTS.md").exists()
    assert not (dest / "sub" / ".claude").exists()
    assert not (dest / "dist").exists()
    assert not (dest / ".git").exists()
    assert (dest / "app" / "main.py").exists()
    assert info["leftover_context"] == []


def test_command_isolation_flags():
    cmd = cc.build_command(spec(), {"type": "object"})
    assert "--restricted" in cmd and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--plugin-dir") + 1] == "/ctx/plugins/ordeal"
    api = cc.build_command(spec(auth=AuthMode.api_key), {})
    assert "--bare" in api and "--setting-sources" not in api
    base = cc.build_command(spec("baseline"), {})
    assert "--disable-slash-commands" in base and "--plugin-dir" not in base
    assert "Skill" not in base[base.index("--tools") + 1].split(",")


def test_forced_prompt():
    assert cc.build_prompt(spec(), "do it").startswith("/ordeal:x\n")
    assert cc.build_prompt(spec("baseline"), "do it") == "do it"


def test_gate_accepts_expected_plus_builtins():
    ps = cc.parse_stream([init(["ordeal:x", "doctor", "design"], ["ordeal", "telemetry"])])
    assert cc.isolation_problems(ps, spec()) == []


def test_gate_rejects_leaks():
    ps = cc.parse_stream(
        [
            init(
                ["ordeal:x", "evil"],
                ["ordeal", "other"],
                mcp=[{"name": "gh"}],
                model="claude-opus-5-5",
            )
        ]
    )
    probs = " ".join(cc.isolation_problems(ps, spec()))
    for needle in (
        "skills-mismatch",
        "unexpected-plugins",
        "mcp-servers-present",
        "model-mismatch",
    ):
        assert needle in probs
    assert cc.isolation_problems(cc.parse_stream([]), spec()) == ["no-init-event"]


def test_parse_usage_and_tools():
    lines = [
        init(["ordeal:x"]),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "usage": {"input_tokens": 5, "cache_read_input_tokens": 100},
                    "content": [
                        {"type": "tool_use", "name": "Skill", "input": {"skill": "ordeal:x"}},
                        {"type": "tool_use", "name": "Read", "input": {}},
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "total_cost_usd": 0.5,
                "num_turns": 2,
                "structured_output": {"summary": "s", "findings": []},
                "modelUsage": {
                    "m": {
                        "inputTokens": 5,
                        "outputTokens": 7,
                        "cacheReadInputTokens": 100,
                        "cacheCreationInputTokens": 0,
                        "costUSD": 0.5,
                    }
                },
            }
        ),
    ]
    ps = cc.parse_stream(lines)
    assert ps.tool_calls == {"Skill": 1, "Read": 1}
    assert ps.skills_invoked == ["ordeal:x"]
    assert ps.first_turn_prompt_tokens == 105
    u = cc.usage_summary(ps.result)
    assert u["tokens"]["total_tokens"] == 112
    assert cc.structured_output(ps.result) == {"summary": "s", "findings": []}


def test_limit_watch_tokens_and_turns():
    w = LimitWatch(max_total_tokens=100, max_turns=None)
    msg = lambda i, n: json.dumps(
        {"type": "assistant", "message": {"id": i, "usage": {"input_tokens": n}}}
    )
    assert w.feed(msg("a", 60)) is None
    assert w.feed(msg("a", 60)) is None  # same message, several events
    assert "max_total_tokens" in w.feed(msg("b", 60))
    t = LimitWatch(None, max_turns=1)
    t.feed(msg("a", 1))
    assert "max_turns" in t.feed(msg("b", 1))


def test_render_prompt_keeps_braces():
    a = Arena(id="demo", repo="x", ref="main", language="go")
    assert (
        render_prompt("{arena} {language} {scope} {{}} {x}", a)
        == "demo go the whole repository {{}} {x}"
    )


def test_monitor_parsers():
    assert parse_kv("usage_usec 10\nuser_usec 7\n")["usage_usec"] == 10
    assert parse_io_stat("8:0 rbytes=10 wbytes=5\n8:16 rbytes=1 wbytes=1\n") == {
        "rbytes": 11,
        "wbytes": 6,
    }
    st = parse_proc_status("Name:\tnode\nVmRSS:\t  1234 kB\nThreads:\t9\n")
    assert st == {"comm": "node", "rss_kb": 1234, "threads": 9}
    stat = Path("/proc/self/stat").read_text()
    assert parse_proc_stat_ticks(stat) >= 0
    assert parse_proc_stat_ticks("1 (a b) S " + " ".join(["0"] * 10) + " 3 4 0") == 7
