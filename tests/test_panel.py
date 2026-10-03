import json
import re
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from skillordeal.cli import app
from skillordeal.config import AuthMode, JudgeMode, JudgeSpec, load_trial
from skillordeal.lock import build_lock, read_lock
from skillordeal.runner import RoundPaths
from skillordeal.score import judge as J
from skillordeal.score import panel as P
from skillordeal.score import read_jsonl, scores_dir
from skillordeal.score.pipeline import run_score
from skillordeal.secrets import Credentials

JUDGE_ID = "claude-sonnet-4-6"
CREDS = Credentials(
    mode=AuthMode.oauth, env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-fake-1234567890"}
)
LEAKS = (
    "sentry-security-review",
    "security-review",
    "baseline",
    "b-faac35f5551c1b7b",
    "b-f369f8477abd9627",
    "contender",
)


@pytest.fixture
def setup(smoke_trial, monkeypatch):
    """(trial with a panel judge, lock, findings rows, scores dir); no arena export."""
    lt = load_trial(smoke_trial)
    lt.trial.judge = JudgeSpec(id=JUDGE_ID, mode=JudgeMode.panel)
    paths = RoundPaths(smoke_trial.parent, "smoke")
    rows = run_score(lt, paths).findings

    def fake_prepare(arena, sha, cache_dir, dest):
        dest.mkdir(parents=True, exist_ok=True)
        return {"removed": [], "leftover_context": [], "files": 0}

    monkeypatch.setattr(J, "prepare_arena", fake_prepare)
    return lt, read_lock(paths.lock), rows, scores_dir(paths)


def lens_of(prompt):
    return re.search(r"Your lens is \*\*([a-z0-9-]+)\*\*", prompt).group(1)


class FakeVoters:
    """Votes `by_lens[lens]` (default true_positive) on every ref; drops refs per lens."""

    def __init__(self, by_lens=None, *, cost=0.05, drop=None, tools=None):
        self.by_lens, self.cost = by_lens or {}, cost
        self.drop, self.tools = drop or {}, tools
        self.calls = []

    def __call__(self, create, env, prompt, timeout_s):
        lens = lens_of(prompt)
        self.calls.append((create, prompt, lens))
        refs = [
            r for r in re.findall(r'"ref": "(F\d+)"', prompt) if r not in self.drop.get(lens, ())
        ]
        init = {
            "type": "system",
            "subtype": "init",
            "model": JUDGE_ID,
            "skills": ["design", "doctor"],
            "tools": list(self.tools or ("Read", "Grep", "Glob", "StructuredOutput")),
            "plugins": [{"name": "telemetry"}],
            "mcp_servers": [],
        }
        votes = [
            {
                "finding_ref": r,
                "vote": self.by_lens.get(lens, "true_positive"),
                "attacker": f"{lens} attacker",
                "gain": f"{lens} gain",
                "confidence": "high",
                "rationale": f"{lens}: sqli/app.py:35",
            }
            for r in refs
        ]
        result = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "total_cost_usd": self.cost,
            "structured_output": {"votes": votes},
        }
        return J.ContainerRun([json.dumps(init) + "\n", json.dumps(result) + "\n"], "", 0)


def quiet(m):
    return None


# --- aggregation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("votes", "verdict", "confidence", "agreement", "unanimous"),
    [
        (["true_positive"] * 3, "valid", "high", 1.0, True),
        (["false_positive"] * 3, "invalid", "high", 1.0, True),
        (["true_positive", "true_positive", "false_positive"], "valid", "medium", 0.6667, False),
        (["false_positive", "false_positive", "true_positive"], "invalid", "medium", 0.6667, False),
        (["true_positive", "true_positive", "unverifiable"], "valid", "medium", 0.6667, False),
        # tie between decisive votes
        (["true_positive", "false_positive", "unverifiable"], "unverifiable", "low", 0.3333, False),
        (["true_positive", "false_positive"], "unverifiable", "low", 0.5, False),
        # mostly unverifiable
        (["true_positive", "unverifiable", "unverifiable"], "unverifiable", "low", 0.6667, False),
        (["unverifiable"] * 3, "unverifiable", "low", 1.0, True),
        # wrap-around voters: 3 to 2 is still a majority
        (["true_positive"] * 3 + ["false_positive"] * 2, "valid", "medium", 0.6, False),
    ],
)
def test_aggregate(votes, verdict, confidence, agreement, unanimous):
    got = P.aggregate(votes)
    assert got == {
        "verdict": verdict,
        "confidence": confidence,
        "agreement": agreement,
        "unanimous": unanimous,
    }


def test_aggregate_needs_votes():
    with pytest.raises(ValueError):
        P.aggregate([])


# --- config -----------------------------------------------------------------------------------


def test_judge_spec_defaults_and_slots():
    j = JudgeSpec(id=JUDGE_ID)
    assert j.mode == JudgeMode.fact and j.voters == 3
    assert j.lenses == ["reachability", "impact", "correctness"]
    assert [s for s, _ in j.voter_slots()] == ["reachability", "impact", "correctness"]
    five = JudgeSpec(id=JUDGE_ID, mode="panel", voters=5)
    assert five.voter_slots() == [
        ("reachability", "reachability"),
        ("impact", "impact"),
        ("correctness", "correctness"),
        ("reachability-2", "reachability"),
        ("impact-2", "impact"),
    ]
    with pytest.raises(ValidationError, match="every lens needs"):
        JudgeSpec(id=JUDGE_ID, voters=2)
    with pytest.raises(ValidationError):
        JudgeSpec(id=JUDGE_ID, lenses=["vibes"])
    with pytest.raises(ValidationError, match="repeat"):
        JudgeSpec(id=JUDGE_ID, lenses=["impact", "impact"], voters=2)


def test_ruling_fields_stay_out_of_the_lock(smoke_trial):
    lt = load_trial(smoke_trial)
    fact = build_lock(lt, skip_image=True)
    text = smoke_trial.read_text().replace(
        f"judge: {{id: {JUDGE_ID}}}", f"judge: {{id: {JUDGE_ID}, mode: panel, voters: 5}}"
    )
    smoke_trial.write_text(text)
    lt2 = load_trial(smoke_trial)
    assert lt2.trial.judge.mode == JudgeMode.panel
    panel = build_lock(lt2, skip_image=True)
    assert panel["trial_hash"] == fact["trial_hash"]
    assert (
        panel["judge"]
        == fact["judge"]
        == {
            "id": JUDGE_ID,
            "effort": None,
            "max_budget_usd": 5.0,
        }
    )


def test_every_builtin_lens_has_a_prompt():
    _, lenses = P.split_template(P.prompt_file())
    assert set(lenses) == {"reachability", "impact", "correctness"}
    for name in lenses:
        t = P.lens_template(P.prompt_file(), name)
        assert "{lens" not in t and "{findings}" in t and "<!-- lens" not in t


# --- blinding and batching ------------------------------------------------------------------


def test_each_lens_gets_the_same_blinded_batch(setup):
    lt, lock, rows, _ = setup
    rows = [dict(r) for r in rows]
    rows[0]["description"] = (
        "Found by sentry-security-review (skill ordeal:security-review) in b-faac35f5551c1b7b:3."
    )
    plan = P.plan(lt, lock, rows, batch_size=5)
    assert [len(b.items) for b in plan.batches] == [5, 5, 5, 2]
    _, lens_text = P.split_template(plan.text)
    for b in plan.batches:
        payloads = set()
        for slot, lens in plan.slots:
            prompt = plan.prompt(lt, b, lens)
            assert f"Your lens is **{lens}**" in prompt
            assert lens_text[lens] in prompt
            others = [t for name, t in lens_text.items() if name != lens]
            assert not any(t in prompt for t in others), slot
            for leak in LEAKS:
                assert leak not in prompt, (lens, leak)
            for r in rows:
                assert r["finding_id"] not in prompt and r["finding_hash"] not in prompt
            payloads.add(prompt.split("Findings:\n\n", 1)[1])
        assert len(payloads) == 1  # every voter sees exactly the same findings
    first = plan.prompt(lt, plan.batches[0], "reachability")
    assert "[redacted]" in "".join(plan.prompt(lt, b, "impact") for b in plan.batches)
    again = P.plan(lt, lock, list(reversed(rows)), batch_size=5)
    assert first == again.prompt(lt, again.batches[0], "reachability")


# --- running ----------------------------------------------------------------------------------


def test_run_panel_majority_rows_and_cache(setup):
    lt, lock, rows, scores = setup
    fake = FakeVoters({"reachability": "false_positive"})
    res = P.run_panel(lt, lock, rows, scores, CREDS, batch_size=10, runner=fake, log=quiet)
    assert len(fake.calls) == 6  # 2 batches x 3 voters
    assert sorted(c[2] for c in fake.calls) == sorted(["reachability", "impact", "correctness"] * 2)
    assert res.judged == 17 and res.votes == 51 and res.missing == 0 and res.cached == 0
    assert res.spent_usd == pytest.approx(0.30)

    out = read_jsonl(scores / "judge.jsonl")
    assert len(out) == 17
    row = out[0]
    assert row["mode"] == "panel" and row["verdict"] == "valid" and row["confidence"] == "medium"
    assert row["agreement"] == 0.6667 and row["unanimous"] is False
    assert row["lenses"] == ["reachability", "impact", "correctness"]
    assert [v["lens"] for v in row["votes"]] == row["lenses"]
    assert [v["vote"] for v in row["votes"]] == ["false_positive", "true_positive", "true_positive"]
    assert row["attacker"] == "impact attacker" and row["gain"] == "impact gain"
    assert row["rationale"].startswith("impact: ")
    for k in ("finding_hash", "finding_id", "judge_model", "cluster_id", "prompt_sha", "batch_id"):
        assert row[k], k
    assert row["prompt_sha"] == P.prompt_sha()

    # each voter's run leaves its own record, named after batch and slot
    runs = sorted(p.name for p in (scores / "judge_runs").iterdir())
    assert len(runs) == 6 and all(re.fullmatch(r"j-[0-9a-f]{16}-[a-z]+", r) for r in runs)
    rec = json.loads((scores / "judge_runs" / runs[0] / "record.json").read_text())
    assert rec["mode"] == "panel" and rec["votes"] in (7, 10)
    create = fake.calls[0][0]
    assert any(a.endswith(":/arena:ro") for a in create)
    assert "sk-ant-oat01-fake-1234567890" not in " ".join(create)

    again = FakeVoters()
    res2 = P.run_panel(lt, lock, rows, scores, CREDS, runner=again, log=quiet)
    assert again.calls == [] and res2.cached == 17 and res2.judged == 0
    assert read_jsonl(scores / "judge.jsonl") == out

    summ = run_score(lt, RoundPaths(lt.root, "smoke")).summary
    assert sum(r["judge_valid"] for r in summ) == 17


def test_unanimous_refutation_is_invalid(setup):
    lt, lock, rows, scores = setup
    fake = FakeVoters(dict.fromkeys(("reachability", "impact", "correctness"), "false_positive"))
    P.run_panel(lt, lock, rows, scores, CREDS, batch_size=20, runner=fake, log=quiet)
    out = read_jsonl(scores / "judge.jsonl")
    assert {r["verdict"] for r in out} == {"invalid"}
    assert all(r["unanimous"] and r["confidence"] == "high" for r in out)
    summ = run_score(lt, RoundPaths(lt.root, "smoke")).summary
    assert sum(r["judge_invalid"] for r in summ) == 17


def test_fact_and_panel_caches_never_mix(setup):
    lt, lock, rows, scores = setup
    fact = FakeVotersFact()
    J.run_judge(lt, lock, rows, scores, CREDS, batch_size=20, runner=fact, log=quiet)
    assert len(fact.calls) == 1
    # a full fact cache does not answer for the panel
    voters = FakeVoters()
    res = P.run_panel(lt, lock, rows, scores, CREDS, batch_size=20, runner=voters, log=quiet)
    assert len(voters.calls) == 3 and res.cached == 0 and res.judged == 17
    assert all(r["mode"] == "panel" for r in read_jsonl(scores / "judge.jsonl"))
    # and a full panel cache does not answer for the fact judge
    root = Path(lt.trial.runtime.cache_dir) / "judge" / JUDGE_ID
    dirs = sorted(p.name for p in root.iterdir())
    assert len(dirs) == 2 and dirs[1] == f"panel-{P.prompt_sha()}" and dirs[0] == J.prompt_sha()
    assert sorted(p.name for p in (root / dirs[1]).iterdir()) == sorted(
        ["reachability", "impact", "correctness"]
    )
    fact2 = FakeVotersFact()
    J.run_judge(lt, lock, rows, scores, CREDS, batch_size=20, runner=fact2, log=quiet)
    assert fact2.calls == []
    out = read_jsonl(scores / "judge.jsonl")
    assert len(out) == 17 and not any("mode" in r or "votes" in r for r in out)


class FakeVotersFact:
    def __init__(self):
        self.calls = []

    def __call__(self, create, env, prompt, timeout_s):
        from test_judge import stream

        self.calls.append(prompt)
        assert "Your lens is" not in prompt
        return J.ContainerRun(stream(prompt), "", 0)


def test_resume_after_partial_run(setup):
    lt, lock, rows, scores = setup
    first = FakeVoters(drop={"impact": ("F2",)})
    res = P.run_panel(lt, lock, rows, scores, CREDS, batch_size=20, runner=first, log=quiet)
    assert len(first.calls) == 3
    assert res.judged == 16 and res.missing == 1 and res.votes == 50
    assert any("no vote for F2" in p for p in res.problems)
    assert len(read_jsonl(scores / "judge.jsonl")) == 16

    second = FakeVoters()
    res2 = P.run_panel(lt, lock, rows, scores, CREDS, batch_size=20, runner=second, log=quiet)
    # only the missing voter runs, on a batch of just the finding it hasn't voted on
    assert [c[2] for c in second.calls] == ["impact"]
    assert second.calls[0][1].count('"ref": ') == 1
    assert res2.judged == 1 and res2.cached == 16 and res2.missing == 0 and res2.votes == 1
    assert len(read_jsonl(scores / "judge.jsonl")) == 17


def test_budget_spans_all_voters_and_resumes(setup):
    lt, lock, rows, scores = setup
    fake = FakeVoters(cost=0.3)
    res = P.run_panel(
        lt, lock, rows, scores, CREDS, batch_size=20, max_cost_usd=0.5, runner=fake, log=quiet
    )
    # two voters ran, then the budget stopped the third
    assert [c[2] for c in fake.calls] == ["reachability", "impact"]
    assert res.spent_usd == pytest.approx(0.6)
    assert any("budget" in p for p in res.problems)
    budgets = [c[0][c[0].index("--max-budget-usd") + 1] for c in fake.calls]
    assert budgets == ["0.50", "0.20"]
    assert res.judged == 0 and res.missing == 17 and res.votes == 34
    assert read_jsonl(scores / "judge.jsonl") == []

    rest = FakeVoters()
    res2 = P.run_panel(lt, lock, rows, scores, CREDS, batch_size=20, runner=rest, log=quiet)
    assert [c[2] for c in rest.calls] == ["correctness"]
    assert res2.judged == 17 and res2.missing == 0


def test_isolation_failure_casts_no_votes(setup):
    lt, lock, rows, scores = setup
    fake = FakeVoters(tools=("Read", "Bash", "StructuredOutput"))
    res = P.run_panel(lt, lock, rows, scores, CREDS, batch_size=20, runner=fake, log=quiet)
    assert res.votes == 0 and res.missing == 17
    assert any("unexpected-tools" in p for p in res.problems)
    cache = Path(lt.trial.runtime.cache_dir) / "judge"
    assert not list(cache.rglob("*.json"))


# --- CLI ----------------------------------------------------------------------------------------


def _no_calls(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("dry run must not start anything")

    monkeypatch.setattr(J, "podman_runner", boom)
    monkeypatch.setattr(P, "run_panel", boom)
    monkeypatch.setattr(J, "run_judge", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def test_cli_dry_run_panel(smoke_trial, monkeypatch):
    _no_calls(monkeypatch)
    r = CliRunner().invoke(
        app,
        ["judge", str(smoke_trial), "-r", "smoke", "--mode", "panel", "--dry-run",
         "--batch-size", "10"],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    assert "voters: reachability, impact, correctness" in r.output
    assert "17 to vote on in 2 batches" in r.output
    assert r.output.count("You are one voter on a panel") == 3  # one prompt per lens
    for lens in ("reachability", "impact", "correctness"):
        assert f"== prompt for lens {lens}" in r.output
        assert f"Your lens is **{lens}**" in r.output
    prompts = r.output.split("You are one voter", 1)[1]
    assert "sentry-security-review" not in prompts and "b-faac35f5551c1b7b" not in prompts


def test_cli_mode_comes_from_trial(smoke_trial, monkeypatch):
    _no_calls(monkeypatch)
    smoke_trial.write_text(
        smoke_trial.read_text().replace(
            f"judge: {{id: {JUDGE_ID}}}", f"judge: {{id: {JUDGE_ID}, mode: panel}}"
        )
    )
    r = CliRunner().invoke(app, ["judge", str(smoke_trial), "-r", "smoke", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "You are one voter" in r.output and "You are checking the findings" not in r.output
    # --mode fact overrides the trial back to the fact judge
    r = CliRunner().invoke(
        app, ["judge", str(smoke_trial), "-r", "smoke", "--dry-run", "--mode", "fact"]
    )
    assert r.exit_code == 0, r.output
    assert "You are checking the findings" in r.output and "You are one voter" not in r.output
