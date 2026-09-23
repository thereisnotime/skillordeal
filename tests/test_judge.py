import json
import re
import subprocess
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skillordeal.adapters import claude_code as cc
from skillordeal.cli import app
from skillordeal.config import AuthMode, ModelSpec, load_trial
from skillordeal.lock import read_lock
from skillordeal.runner import RoundPaths
from skillordeal.score import ScoreError, read_jsonl, scores_dir
from skillordeal.score import judge as J
from skillordeal.score.pipeline import run_score
from skillordeal.secrets import Credentials

JUDGE_ID = "claude-sonnet-4-6"


@pytest.fixture
def setup(smoke_trial, monkeypatch):
    """(trial, lock, findings rows, scores dir) with arena export stubbed out (no network)."""
    lt = load_trial(smoke_trial)
    paths = RoundPaths(smoke_trial.parent, "smoke")
    rows = run_score(lt, paths).findings

    def fake_prepare(arena, sha, cache_dir, dest):
        dest.mkdir(parents=True, exist_ok=True)
        return {"removed": [], "leftover_context": [], "files": 0}

    monkeypatch.setattr(J, "prepare_arena", fake_prepare)
    return lt, read_lock(paths.lock), rows, scores_dir(paths)


def stream(
    prompt,
    *,
    model=JUDGE_ID,
    tools=("Read", "Grep", "Glob", "StructuredOutput"),
    skills=("design", "doctor"),
    cost=0.05,
    verdict="valid",
    drop=(),
):
    refs = [r for r in re.findall(r'"ref": "(F\d+)"', prompt) if r not in drop]
    init = {
        "type": "system",
        "subtype": "init",
        "model": model,
        "skills": list(skills),
        "tools": list(tools),
        "plugins": [{"name": "telemetry"}],
        "mcp_servers": [],
    }
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": cost,
        "structured_output": {
            "verdicts": [
                {"finding_ref": r, "verdict": verdict, "confidence": "high", "rationale": "ok"}
                for r in refs
            ]
        },
    }
    return [json.dumps(init) + "\n", json.dumps(result) + "\n"]


class FakeRunner:
    def __init__(self, **kw):
        self.calls, self.kw = [], kw

    def __call__(self, create, env, prompt, timeout_s):
        self.calls.append((create, prompt))
        return J.ContainerRun(stream(prompt, **self.kw), "", 0)


CREDS = Credentials(
    mode=AuthMode.oauth, env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-fake-1234567890"}
)


def test_batches_are_blinded_grouped_and_deterministic(setup):
    lt, lock, rows, _ = setup
    # a finding that names its own skill and a bout id in the text
    rows = [dict(r) for r in rows]
    rows[0]["description"] = (
        "Found by sentry-security-review (skill ordeal:security-review) in b-faac35f5551c1b7b:3."
    )
    batches = J.plan_batches(lt, lock, rows, batch_size=5)
    assert [len(b.items) for b in batches] == [5, 5, 5, 2]
    assert {b.arena for b in batches} == {"dvpwa"}
    again = J.plan_batches(lt, lock, list(reversed(rows)), batch_size=5)
    assert [b.refs for b in batches] == [b.refs for b in again]  # input order doesn't matter
    assert [b.prompt for b in batches] == [b.prompt for b in again]

    text = "\n".join(b.prompt for b in batches)
    for leak in [
        "sentry-security-review",
        "security-review",
        "baseline",
        "b-faac35f5551c1b7b",
        "b-f369f8477abd9627",
        "contender",
        "recommendation",
    ]:
        assert leak not in text, leak
    for r in rows:
        assert r["finding_id"] not in text and r["finding_hash"] not in text
    assert "[redacted]" in text

    # the shuffle mixes contenders instead of keeping bout order
    order = [r["contender"] for b in batches for r in b.items]
    assert order != sorted(order) and order != sorted(order, reverse=True)

    payload = json.loads(batches[0].prompt.split("Findings:\n\n", 1)[1])
    assert set(payload[0]) <= {"ref", *J.BLIND_FIELDS, "evidence"}
    assert payload[0]["ref"] == "F1"


def test_same_hash_judged_once(setup):
    lt, lock, rows, _ = setup
    dup = {**rows[0], "finding_id": "b-f369f8477abd9627:99"}
    batches = J.plan_batches(lt, lock, [*rows, dup], batch_size=50)
    assert sum(len(b.items) for b in batches) == len(rows)


def test_judge_command_isolation():
    spec = cc.plain_spec(ModelSpec(id=JUDGE_ID), J.TOOLS, AuthMode.oauth, 1.0)
    cmd = cc.build_command(spec, {"type": "object"})
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--model") + 1] == JUDGE_ID
    for flag in (
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
    ):
        assert flag in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--plugin-dir" not in cmd


def test_run_judge_writes_rows_and_uses_cache(setup):
    lt, lock, rows, scores = setup
    fake = FakeRunner()
    res = J.run_judge(lt, lock, rows, scores, CREDS, batch_size=10, runner=fake, log=lambda m: None)
    assert len(fake.calls) == 2 and res.judged == 17 and res.cached == 0
    assert res.spent_usd == pytest.approx(0.10)
    out = read_jsonl(scores / "judge.jsonl")
    assert len(out) == 17 and {r["verdict"] for r in out} == {"valid"}
    assert {
        "finding_hash",
        "judge_model",
        "verdict",
        "confidence",
        "rationale",
        "cluster_id",
    } <= set(out[0])
    assert out[0]["judge_model"] == JUDGE_ID

    # the container gets the arena read-only and the token only by name
    create = fake.calls[0][0]
    assert any(a.endswith(":/arena:ro") for a in create)
    assert "sk-ant-oat01-fake-1234567890" not in " ".join(create)

    cache = J.JudgeCache(Path(lt.trial.runtime.cache_dir), lt.trial.judge, J.prompt_sha())
    assert len(list(cache.dir.glob("*.json"))) == 17

    second = FakeRunner()
    res2 = J.run_judge(lt, lock, rows, scores, CREDS, runner=second, log=lambda m: None)
    assert second.calls == [] and res2.cached == 17 and res2.judged == 0
    assert len(read_jsonl(scores / "judge.jsonl")) == 17

    # the summary picks the verdicts up on the next score
    summ = run_score(lt, RoundPaths(lt.root, "smoke")).summary
    assert sum(r["judge_valid"] for r in summ) == 17


def test_missing_verdicts_are_retried(setup):
    lt, lock, rows, scores = setup
    res = J.run_judge(
        lt,
        lock,
        rows,
        scores,
        CREDS,
        batch_size=20,
        runner=FakeRunner(drop=("F2",)),
        log=lambda m: None,
    )
    assert res.judged == 16 and res.missing == 1
    assert any("no verdict for F2" in p for p in res.problems)
    again = FakeRunner()
    res2 = J.run_judge(lt, lock, rows, scores, CREDS, runner=again, log=lambda m: None)
    assert len(again.calls) == 1 and res2.judged == 1 and res2.missing == 0


def test_isolation_failure_is_not_cached(setup):
    lt, lock, rows, scores = setup
    res = J.run_judge(
        lt,
        lock,
        rows,
        scores,
        CREDS,
        runner=FakeRunner(tools=("Read", "Bash", "StructuredOutput"), skills=("evil",)),
        log=lambda m: None,
    )
    assert res.judged == 0 and res.missing == 17
    assert any("unexpected-tools" in p for p in res.problems)
    assert any("skills-mismatch" in p for p in res.problems)
    assert read_jsonl(scores / "judge.jsonl") == []


def test_budget_stops_judging(setup):
    lt, lock, rows, scores = setup
    fake = FakeRunner(cost=0.3)
    res = J.run_judge(
        lt,
        lock,
        rows,
        scores,
        CREDS,
        batch_size=5,
        max_cost_usd=0.5,
        runner=fake,
        log=lambda m: None,
    )
    assert len(fake.calls) == 2 and res.spent_usd == pytest.approx(0.6)
    assert any("budget" in p for p in res.problems)
    # the per-call CLI budget never exceeds what's left
    budgets = [c[0][c[0].index("--max-budget-usd") + 1] for c in fake.calls]
    assert budgets == ["0.50", "0.20"]


def test_judge_requires_model(setup):
    lt, lock, rows, scores = setup
    lt.trial.judge = None
    with pytest.raises(ScoreError, match="no judge model"):
        J.run_judge(lt, lock, rows, scores, CREDS, runner=FakeRunner(), log=lambda m: None)


def test_dry_run_calls_nothing(smoke_trial, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("dry run must not start anything")

    monkeypatch.setattr(J, "podman_runner", boom)
    monkeypatch.setattr(J, "run_judge", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    r = CliRunner().invoke(
        app, ["judge", str(smoke_trial), "-r", "smoke", "--dry-run", "--batch-size", "10"]
    )
    assert r.exit_code == 0, r.output
    assert "17 to judge in 2 batches" in r.output
    assert r.output.count("You are checking the findings") == 2
    assert "sentry-security-review" not in r.output.split("You are checking", 1)[1]


def test_dry_run_without_judge_fails(smoke_trial):
    smoke_trial.write_text(smoke_trial.read_text().replace(f"judge: {{id: {JUDGE_ID}}}\n", ""))
    r = CliRunner().invoke(app, ["judge", str(smoke_trial), "-r", "smoke", "--dry-run"])
    assert r.exit_code == 1 and "no judge model" in r.output


@pytest.mark.podman
def test_judge_container_sees_only_read_tools(tmp_path):
    """Offline: the CLI emits its init event before any API call, so this costs nothing."""
    ref = "localhost/skillordeal-runner:dev"
    spec = cc.plain_spec(ModelSpec(id=JUDGE_ID), J.TOOLS, AuthMode.oauth, 0.01)
    cmd = cc.build_command(spec, {"type": "object", "properties": {}})
    name = "so-judge-probe-test"
    proc = subprocess.Popen(
        [
            "podman",
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--network=none",
            "--userns=keep-id",
            "-e",
            "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-probe-000000000000",
            "-e",
            "CLAUDE_CONFIG_DIR=/tmp/c",
            "-e",
            "HOME=/tmp",
            ref,
            *cmd,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    timer = threading.Timer(
        120, lambda: subprocess.run(["podman", "kill", name], capture_output=True)
    )
    timer.start()
    try:
        proc.stdin.write("probe")
        proc.stdin.close()
        init = None
        for line in proc.stdout:
            ev = json.loads(line) if line.startswith("{") else {}
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                init = ev
                break
    finally:
        timer.cancel()
        subprocess.run(["podman", "rm", "-f", name], capture_output=True)
        proc.wait(timeout=30)
    assert init is not None
    assert set(init["tools"]) <= {"Read", "Grep", "Glob", "StructuredOutput"}
    assert not init.get("mcp_servers")
