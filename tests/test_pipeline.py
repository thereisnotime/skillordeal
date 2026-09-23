import json
from pathlib import Path

import pytest
import zstandard
from conftest import GOOD_FINDING
from pydantic import ValidationError

from skillordeal import lock as L
from skillordeal.blind import Blinder, blind_terms
from skillordeal.bout import ContainerRun, run_bout, sum_usage
from skillordeal.config import Arena, AuthMode, Contender, load_trial
from skillordeal.lock import build_lock, check_drift, write_lock
from skillordeal.matrix import expand
from skillordeal.runner import RoundPaths
from skillordeal.score import read_csv
from skillordeal.score.findings import BOUT_STR_COLUMNS
from skillordeal.score.pipeline import run_score
from skillordeal.secrets import Credentials
from skillordeal.verify import render_verify_prompt, verify_template

MODEL = "claude-haiku-4-5-20251001"
CREDS = Credentials(mode=AuthMode.oauth, env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-fake-token"})
SECOND = {**GOOD_FINDING, "title": "Hardening only", "line_start": 1, "line_end": 1}


@pytest.fixture
def pipe_trial(trial_dir: Path, tmp_path: Path) -> Path:
    """trial_dir plus an fp-check verifier skill and a my-skill -> fp-check pipeline.

    The trial lists only `my-skill` and `pipe`, so `fp-check` exists only as a stage.
    """
    fp = tmp_path / "skills" / "fp-check"
    fp.mkdir(parents=True)
    (fp / "SKILL.md").write_text("---\nname: fp-check\ndescription: verify\n---\nBe strict.\n")
    cf = trial_dir / "contenders.yaml"
    cf.write_text(
        cf.read_text()
        + f"  - {{id: fp-check, kind: skill, path: {fp}, role: verifier}}\n"
        + "  - {id: pipe, kind: pipeline, stages: [my-skill, fp-check]}\n"
        + "  - {id: pipe-base, kind: pipeline, stages: [my-skill, baseline]}\n"
    )
    tf = trial_dir / "trial.yaml"
    tf.write_text(
        tf.read_text().replace(
            "contenders_file: contenders.yaml\n",
            "contenders_file: contenders.yaml\ncontenders: [my-skill, pipe, pipe-base]\n",
        )
    )
    return tf


# --- config ---------------------------------------------------------------------------


def _contenders(trial_dir: Path, body: str) -> None:
    (trial_dir / "contenders.yaml").write_text("contenders:\n" + body)


def test_pipeline_needs_two_stages_and_no_source():
    with pytest.raises(ValidationError, match="at least 2 stages"):
        Contender(id="p", kind="pipeline", stages=["a"])
    with pytest.raises(ValidationError, match="belong on the stage"):
        Contender(id="p", kind="pipeline", stages=["a", "b"], repo="x")
    with pytest.raises(ValidationError, match="own stage"):
        Contender(id="p", kind="pipeline", stages=["p", "b"])
    with pytest.raises(ValidationError, match="only pipeline"):
        Contender(id="s", kind="skill", path="/tmp", stages=["a", "b"])
    assert Contender(id="p", kind="pipeline", stages=["a", "baseline"]).repo is None


def test_pipeline_unknown_stage(trial_dir):
    _contenders(trial_dir, "  - {id: pipe, kind: pipeline, stages: [nope, baseline]}\n")
    with pytest.raises(ValueError, match="unknown stage 'nope'"):
        load_trial(trial_dir / "trial.yaml")


def test_pipeline_nested_rejected(trial_dir, tmp_path):
    _contenders(
        trial_dir,
        f"  - {{id: s, kind: skill, path: {tmp_path / 'skills' / 'my-skill'}}}\n"
        "  - {id: inner, kind: pipeline, stages: [s, baseline]}\n"
        "  - {id: outer, kind: pipeline, stages: [s, inner]}\n",
    )
    with pytest.raises(ValueError, match="itself a pipeline"):
        load_trial(trial_dir / "trial.yaml")


def test_stage_only_contenders_are_in_the_pool(pipe_trial):
    lt = load_trial(pipe_trial)
    assert [c.id for c in lt.contenders] == ["baseline", "my-skill", "pipe", "pipe-base"]
    assert lt.contender("fp-check").role == "verifier"
    assert lt.contender("baseline").kind == "baseline"


# --- lock -------------------------------------------------------------------------------


def test_lock_records_stage_facts(pipe_trial):
    lock = build_lock(load_trial(pipe_trial), skip_image=True)
    assert "fp-check" not in lock["contenders"]  # stage only, so no bouts of its own
    p = lock["contenders"]["pipe"]
    assert p["kind"] == "pipeline" and p["stages"] == ["my-skill", "fp-check"]
    ids = [s["id"] for s in p["stage_locks"]]
    assert ids == ["my-skill", "fp-check"]
    assert p["stage_locks"][1]["expected_skills"] == ["ordeal:fp-check"]
    assert p["stage_locks"][0]["run_hash"] == lock["contenders"]["my-skill"]["run_hash"]
    assert p["verify_prompt_sha256"]
    assert lock["contenders"]["pipe-base"]["stage_locks"][1]["kind"] == "baseline"
    # the lock round-trips through YAML without anchors
    write_lock(lock, pipe_trial.parent / "l.yaml")
    assert "&" not in (pipe_trial.parent / "l.yaml").read_text().split("contenders:")[1]


def test_pipeline_run_hash_stable_and_sensitive(pipe_trial, tmp_path, monkeypatch):
    lt = load_trial(pipe_trial)
    a = build_lock(lt, skip_image=True)
    b = build_lock(lt, skip_image=True)
    assert a["contenders"]["pipe"]["run_hash"] == b["contenders"]["pipe"]["run_hash"]
    assert {k.bout_id for k in expand(a)} == {k.bout_id for k in expand(b)}

    # a new verify prompt moves only pipeline bouts
    monkeypatch.setattr(L, "verify_prompt_sha", lambda: "different")
    c = build_lock(lt, skip_image=True)
    assert c["contenders"]["pipe"]["run_hash"] != a["contenders"]["pipe"]["run_hash"]
    assert c["contenders"]["my-skill"]["run_hash"] == a["contenders"]["my-skill"]["run_hash"]
    monkeypatch.undo()

    # editing the stage-only verifier is drift on the pipeline, and moves its run_hash
    (tmp_path / "skills" / "fp-check" / "SKILL.md").write_text("---\nname: fp-check\n---\nnew\n")
    lt2 = load_trial(pipe_trial)
    assert "contenders.pipe.tree_hash changed" in check_drift(a, lt2, skip_image=True)
    d = build_lock(lt2, skip_image=True)
    assert d["contenders"]["pipe"]["run_hash"] != a["contenders"]["pipe"]["run_hash"]
    assert d["contenders"]["pipe-base"]["run_hash"] == a["contenders"]["pipe-base"]["run_hash"]


def test_adding_a_pipeline_keeps_other_bout_ids(trial_dir, pipe_trial):
    lock = build_lock(load_trial(pipe_trial), skip_image=True)
    with_pipe = {k.bout_id for k in expand(lock) if k.contender in {"baseline", "my-skill"}}
    for cid in ("pipe", "pipe-base"):
        del lock["contenders"][cid]
    assert {k.bout_id for k in expand(lock)} == with_pipe


# --- verify prompt ------------------------------------------------------------------------


def test_verify_prompt_carries_findings_blinded(pipe_trial):
    lock = build_lock(load_trial(pipe_trial), skip_image=True)
    leaky = {
        **GOOD_FINDING,
        "description": "Found by my-skill in bout b-0123456789abcdef; fp-check would agree.",
        "evidence": "```\nos.system(input())\n```",
    }
    arena = Arena(id="demo", repo="x", ref="main", language="python")
    text = render_verify_prompt(verify_template(), arena, [leaky], Blinder(blind_terms(lock)))
    assert "app/main.py" in text and GOOD_FINDING["title"] in text
    for name in ("my-skill", "fp-check", "pipe", "b-0123456789abcdef"):
        assert name not in text
    assert "[redacted]" in text
    # the data sits in a fence longer than any backtick run inside it
    assert "````json\n" in text
    body = text.split("````json\n", 1)[1].rsplit("\n````", 1)[0]
    assert json.loads(body)["findings"][0]["file"] == "app/main.py"
    assert "{findings}" not in text and "{count}" not in text


# --- running -------------------------------------------------------------------------------


def _stream(skills, findings, *, cost, tokens, structured=True):
    init = {
        "type": "system",
        "subtype": "init",
        "model": MODEL,
        "skills": skills,
        "plugins": [{"name": "ordeal"}] if skills else [],
        "mcp_servers": [],
        "tools": ["Read", "Grep"],
    }
    assistant = {
        "type": "assistant",
        "message": {"id": "m1", "usage": {"input_tokens": tokens}, "content": []},
    }
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": cost,
        "num_turns": 3,
        "duration_ms": 1000,
        "usage": {"input_tokens": tokens, "output_tokens": 10},
    }
    if not structured:
        result["result"] = "I could not produce JSON"
    return [json.dumps(e) + "\n" for e in (init, assistant, result)]


def _write_output(launch, findings):
    """Play the agent's part: write /out/findings.json into the mounted host dir."""
    cmd = launch.create
    mount = next(
        cmd[i + 1] for i, a in enumerate(cmd) if a == "-v" and cmd[i + 1].endswith(":/out:rw")
    )
    host = Path(mount.split(":")[0])
    (host / "findings.json").write_text(json.dumps({"summary": "s", "findings": findings}))


class FakeRunner:
    """Stage 1 reports two findings, a verifier keeps the first one (or misbehaves)."""

    def __init__(self, stage1=(GOOD_FINDING, SECOND), stage2="ok", stage2_skills=None):
        self.stage1, self.stage2, self.stage2_skills = list(stage1), stage2, stage2_skills
        self.launches = []

    def __call__(self, launch):
        self.launches.append(launch)
        cmd = launch.create
        plugin = cmd[cmd.index("--plugin-dir") + 1] if "--plugin-dir" in cmd else None
        if launch.name.endswith("-s2"):
            skills = self.stage2_skills or (["ordeal:fp-check"] if plugin else [])
            lines = _stream(
                skills, [GOOD_FINDING], cost=0.2, tokens=200, structured=self.stage2 == "ok"
            )
            if self.stage2 == "ok":
                _write_output(launch, [GOOD_FINDING])
        else:
            lines = _stream(["ordeal:my-skill"], self.stage1, cost=0.1, tokens=100)
            _write_output(launch, self.stage1)
        return ContainerRun(lines, "", 0, wall_s=1.5)


@pytest.fixture
def locked(pipe_trial):
    lt = load_trial(pipe_trial)
    lock = build_lock(lt, skip_image=True)
    lock["lock_hash"] = "test"
    paths = RoundPaths(pipe_trial.parent, "r01")
    write_lock(lock, paths.lock)
    keys = {(k.contender, k.rep): k for k in expand(lock)}
    return lt, lock, paths, keys


def _run(locked, contender, runner, rep=1):
    lt, lock, paths, keys = locked
    k = keys[(contender, rep)]
    d = paths.bout(k.bout_id)
    return run_bout(k, lt, lock, CREDS, d, runner=runner), d


def test_plain_bout_through_fake_runner(locked):
    rec, d = _run(locked, "my-skill", FakeRunner())
    assert rec["status"] == "ok" and rec["findings_count"] == 2
    assert rec["model"]["used"] == []  # no modelUsage in the fake stream
    assert "stages" not in rec
    for f in ("record.json", "findings.json", "prompt.md", "transcript.jsonl.zst", "stderr.log"):
        assert (d / f).exists()


def test_pipeline_success(locked):
    runner = FakeRunner()
    rec, d = _run(locked, "pipe", runner)
    assert rec["status"] == "ok"
    assert rec["findings_before_verify"] == 2 and rec["findings_count"] == 1
    assert [s["status"] for s in rec["stages"]] == ["ok", "ok"]
    assert [s["contender"] for s in rec["stages"]] == ["my-skill", "fp-check"]
    assert rec["contender"]["stages"] == ["my-skill", "fp-check"]

    # summed usage: score and report read these exactly as for a plain bout
    u = rec["usage"]
    assert u["total_cost_usd"] == pytest.approx(0.3)
    assert u["tokens"]["input_tokens"] == 300 and u["num_turns"] == 6
    assert u["duration_ms"] == 2000
    assert rec["container_wall_s"] == 3.0

    # top level: final findings, stage-1 prompt and transcript
    assert json.loads((d / "findings.json").read_text())["findings"] == [GOOD_FINDING]
    s1, s2 = d / "stages" / "1-my-skill", d / "stages" / "2-fp-check"
    assert (d / "prompt.md").read_text() == (s1 / "prompt.md").read_text()
    assert (d / "transcript.jsonl.zst").read_bytes() == (s1 / "transcript.jsonl.zst").read_bytes()
    for sd in (s1, s2):
        for f in ("prompt.md", "findings.json", "transcript.jsonl.zst", "stderr.log"):
            assert (sd / f).exists(), sd / f
        assert (sd / "resources.jsonl").exists()
        assert json.loads((sd / "record.json").read_text())["status"] == "ok"

    # stage 2 is a fresh container with the verifier's plugin and the verify prompt
    p2 = (s2 / "prompt.md").read_text()
    assert p2.startswith("/ordeal:fp-check\n")
    assert "Hardening only" in p2 and "verifying the findings" in p2
    assert "my-skill" not in p2.split("\n", 1)[1]
    l1, l2 = runner.launches
    assert l1.name != l2.name
    assert l1.create[l1.create.index("-v") + 3] != l2.create[l2.create.index("-v") + 3]
    zd = zstandard.ZstdDecompressor()
    assert "ordeal:fp-check" in zd.decompress((s2 / "transcript.jsonl.zst").read_bytes()).decode()


def test_pipeline_stage2_schema_violation(locked):
    rec, d = _run(locked, "pipe", FakeRunner(stage2="bad"))
    assert rec["status"] == "schema_violation"
    assert rec["failed_stage"] == {"n": 2, "contender": "fp-check"}
    assert rec["findings_before_verify"] == 2 and rec["findings_count"] == 0
    assert "raw_result" in json.loads((d / "findings.json").read_text())
    assert rec["usage"]["total_cost_usd"] == pytest.approx(0.3)


def test_pipeline_isolation_gate_per_stage(locked):
    rec, _ = _run(locked, "pipe", FakeRunner(stage2_skills=["ordeal:my-skill"]))
    assert rec["status"] == "invalid" and rec["failed_stage"]["n"] == 2
    assert "skills-mismatch" in rec["invalid_reasons"][0]


def test_pipeline_baseline_verifier_and_empty_stage1(locked):
    rec, d = _run(locked, "pipe-base", FakeRunner())
    assert rec["status"] == "ok" and rec["findings_count"] == 1
    p2 = (d / "stages" / "2-baseline" / "prompt.md").read_text()
    assert p2.startswith("You are verifying")  # no slash command for a plain pass

    runner = FakeRunner(stage1=())
    rec, _ = _run(locked, "pipe", runner, rep=2)
    assert rec["status"] == "ok" and rec["findings_count"] == 0
    assert [s["status"] for s in rec["stages"]] == ["ok", "skipped"]
    assert len(runner.launches) == 1


def test_score_reads_pipeline_round(locked):
    lt, lock, paths, keys = locked
    for k in keys.values():
        run_bout(k, lt, lock, CREDS, paths.bout(k.bout_id), runner=FakeRunner())
    res = run_score(lt, paths)
    rows = {(b["contender"], b["rep"]): b for b in res.bouts}
    assert rows[("pipe", 1)]["findings"] == 1
    assert rows[("pipe", 1)]["findings_before_verify"] == 2
    assert rows[("pipe", 1)]["stages"] == 2
    assert rows[("pipe", 1)]["cost_usd"] == pytest.approx(0.3)
    assert rows[("my-skill", 1)]["stages"] is None
    csv_rows = read_csv(res.scores / "bouts.csv", BOUT_STR_COLUMNS)
    assert {"stages", "findings_before_verify"} <= set(csv_rows[0])
    pipe_findings = [r for r in res.findings if r["contender"] == "pipe"]
    assert len(pipe_findings) == 2  # one per rep, after verification


def test_sum_usage_handles_missing():
    assert sum_usage([{}, {}]) == {}
    u = sum_usage([{"total_cost_usd": 0.1, "tokens": {"total_tokens": 5}}, {}])
    assert u["total_cost_usd"] == 0.1 and u["tokens"] == {"total_tokens": 5}
    assert u["duration_ms"] is None


@pytest.mark.podman
def test_pipeline_real_image_offline(pipe_trial):
    """Real podman runner, no reachable API: stage 1 starts, times out, the pipeline stops."""
    tf = pipe_trial
    tf.write_text(
        tf.read_text().replace(
            "runtime: {",
            "runtime: {limits: {timeout_s: 20}, "
            "network: {allow: [], internal_network: skillordeal-test-internal}, ",
        )
    )
    lt = load_trial(tf)
    lock = build_lock(lt, skip_image=True)
    lock["lock_hash"] = "test"
    k = next(k for k in expand(lock) if k.contender == "pipe" and k.rep == 1)
    out = tf.parent / "rounds" / "r01" / "bouts" / k.bout_id
    rec = run_bout(k, lt, lock, CREDS, out)
    assert rec["status"] == "timeout", rec.get("error")
    assert rec["failed_stage"] == {"n": 1, "contender": "my-skill"}
    assert "ordeal:my-skill" in rec["init"]["skills"]
    assert rec["egress"]["mode"] == "allowlist"
    assert (out / "stages" / "1-my-skill" / "record.json").exists()
    assert not (out / "stages" / "2-fp-check").exists()
