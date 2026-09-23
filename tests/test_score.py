import builtins
import json
import random
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skillordeal.cli import app
from skillordeal.score import ScoreError, read_csv, read_jsonl
from skillordeal.score.dedup import assign_clusters, same_problem, unique_per_contender
from skillordeal.score.findings import (
    BOUT_STR_COLUMNS,
    finding_hash,
    normalize_cwe,
    normalize_path,
)
from skillordeal.score.groundtruth import GroundTruth, bout_aggregates, match_bout
from skillordeal.score.summary import build_summary, final_verdicts, human_verdicts, write_summary

FIXTURES = Path(__file__).parent / "fixtures"

FINDING = {
    "title": "SQL injection",
    "category": "security",
    "severity": "high",
    "confidence": "high",
    "cwe": "CWE-89",
    "file": "app/db.py",
    "line_start": 10,
    "line_end": 12,
    "description": "user input in query",
    "evidence": "cur.execute(q % name)",
}


def f(n=1, bout="b-0000000000000001", arena="demo", contender="c1", **kw):
    row = {**FINDING, **kw}
    row["file"] = normalize_path(row["file"])
    return {
        **row,
        "finding_id": f"{bout}:{n}",
        "finding_hash": finding_hash({**FINDING, **kw}),
        "bout_id": bout,
        "arena": arena,
        "contender": contender,
    }


def gt(complete=False, window=5, **issue):
    base = {
        "id": "sqli",
        "title": "SQLi",
        "category": "security",
        "cwe": ["CWE-89"],
        "severity": "high",
        "locations": [{"file": "app/db.py", "lines": [20, 25]}],
        "source": "seeded",
    }
    return GroundTruth.model_validate(
        {
            "arena": "demo",
            "sha": "0" * 40,
            "complete": complete,
            "window": window,
            "issues": [{**base, **issue}],
        }
    )


# --- identity ----------------------------------------------------------------------------


def test_finding_hash_stable_and_scoped():
    h = finding_hash(FINDING)
    assert h == finding_hash(dict(reversed(list(FINDING.items()))))  # key order irrelevant
    assert len(h) == 64
    # only the identity fields count
    assert h == finding_hash({**FINDING, "severity": "low", "evidence": "x", "recommendation": "y"})
    assert h != finding_hash({**FINDING, "line_end": 13})
    # missing optional fields hash as null
    no_end = {k: v for k, v in FINDING.items() if k != "line_end"}
    assert finding_hash(no_end) == finding_hash({**no_end, "line_end": None})
    # pinned value: changing this breaks every judge cache and label file
    assert h == "7f0feb3c881814b65437522a3c515b58ea3b00ea0328dc46bed1b42ae8ff77bb"


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("app/db.py", "app/db.py"),
        ("./app/db.py", "app/db.py"),
        ("/arena/app/db.py", "app/db.py"),
        ("/arena/./app/db.py", "app/db.py"),
        ("././app/db.py", "app/db.py"),
        (" app\\db.py ", "app/db.py"),
        ("arena/app.py", "arena/app.py"),  # a real top-level dir called arena is kept
        (None, ""),
    ],
)
def test_normalize_path(raw, want):
    assert normalize_path(raw) == want


def test_normalize_cwe():
    assert normalize_cwe("cwe-89") == ["CWE-89"]
    assert normalize_cwe(["89", "CWE-79", ""]) == ["CWE-79", "CWE-89"]
    assert normalize_cwe(None) == []


# --- ground truth matching --------------------------------------------------------------------


def test_match_line_overlap_edges():
    g = gt()  # issue at 20-25, window 5 -> 15..30
    assert match_bout([f(line_start=10, line_end=15)], g)[0]["verdict"] == "tp"  # touches 15
    assert match_bout([f(line_start=10, line_end=14)], g)[0]["verdict"] == "unknown"
    assert match_bout([f(line_start=30, line_end=40)], g)[0]["verdict"] == "tp"  # touches 30
    assert match_bout([f(line_start=31, line_end=None)], g)[0]["verdict"] == "unknown"
    # line_end missing -> line_start only
    assert match_bout([f(line_start=22, line_end=None)], g)[0]["verdict"] == "tp"


def test_match_window_zero_and_override():
    g = gt(window=0)
    assert match_bout([f(line_start=18, line_end=19)], g)[0]["verdict"] == "unknown"
    assert match_bout([f(line_start=18, line_end=19)], g, window=1)[0]["verdict"] == "tp"


def test_match_needs_same_file():
    assert match_bout([f(file="app/other.py", line_start=22)], gt())[0]["verdict"] == "unknown"
    assert match_bout([f(file="/arena/app/db.py", line_start=22)], gt())[0]["verdict"] == "tp"


def _one(row, g):
    r = match_bout([row], g)[0]
    return r["verdict"], r["match_basis"]


def test_match_cwe_decides_when_both_have_one():
    # shared cwe, category differs -> match on cwe
    assert _one(f(category="quality", line_start=22), gt()) == ("tp", "cwe")
    # both name a cwe and they differ: a miss even though the category is equal
    assert _one(f(cwe="CWE-79", line_start=22), gt()) == ("unknown", None)
    # related but not identical ids are a miss too (no parent/child walking)
    assert _one(f(cwe="CWE-943", line_start=22), gt()) == ("unknown", None)


def test_match_falls_back_to_category():
    # finding has no cwe -> category decides
    assert _one(f(cwe=None, line_start=22), gt()) == ("tp", "category")
    assert _one(f(cwe=None, category="quality", line_start=22), gt()) == ("unknown", None)
    # issue has no cwe -> category decides, whatever the finding cites
    assert _one(f(cwe="CWE-79", line_start=22), gt(cwe=[])) == ("tp", "category")
    assert _one(f(cwe="CWE-79", category="other", line_start=22), gt(cwe=[])) == (
        "unknown",
        None,
    )


def test_match_dup_and_complete():
    rows = [f(1, line_start=22), f(2, line_start=24, title="again"), f(3, file="x.py")]
    inc = match_bout(rows, gt())
    assert [r["verdict"] for r in inc] == ["tp", "dup", "unknown"]
    assert [r["issue_id"] for r in inc] == ["sqli", "sqli", None]
    comp = match_bout(rows, gt(complete=True))
    assert [r["verdict"] for r in comp] == ["tp", "dup", "fp"]

    a = bout_aggregates(comp, gt(complete=True))
    assert (a["tp"], a["dup"], a["fp"], a["unknown"]) == (1, 1, 1, 0)
    assert a["precision"] == 0.5 and a["recall"] == 1.0 and a["f1"] == 0.6667
    b = bout_aggregates(inc, gt())
    assert b["precision"] is None and b["f1"] is None
    assert b["precision_lower_bound"] == 0.5 and b["recall"] == 1.0


def _issues(*spans, cwe=("CWE-89",)):
    """Ground truth with one issue per (id, [a, b]) span in app/db.py."""
    return GroundTruth.model_validate(
        {
            "arena": "demo",
            "sha": "0" * 40,
            "issues": [
                {
                    "id": iid,
                    "title": "t",
                    "category": "security",
                    "cwe": list(cwe),
                    "severity": "high",
                    "locations": [{"file": "app/db.py", "lines": span}],
                    "source": "seeded",
                }
                for iid, span in spans
            ],
        }
    )


def _pick(g, **kw):
    r = match_bout([f(**kw)], g)[0]
    return r["issue_id"], r["line_distance"]


def test_ambiguous_match_prefers_exact_overlap():
    # both within the window; only i-b overlaps without it, even though i-a is listed first
    g = _issues(("i-a", [10, 10]), ("i-b", [14, 16]))
    assert _pick(g, line_start=14, line_end=14) == ("i-b", 1)


def test_ambiguous_match_then_closest_midpoint_then_id():
    # window-only on both sides: the closer midpoint wins
    g = _issues(("i-a", [10, 10]), ("i-b", [17, 17]))
    assert _pick(g, line_start=14, line_end=14) == ("i-b", 3)
    # exact on both: closer midpoint again (finding mid 12, i-x mid 11.5, i-y mid 14)
    g = _issues(("i-y", [12, 16]), ("i-x", [10, 13]))
    assert _pick(g, line_start=11, line_end=13) == ("i-x", 0.5)
    # full tie -> issue id order, independent of file order
    g = _issues(("i-z", [10, 10]), ("i-c", [20, 20]))
    assert _pick(g, line_start=15, line_end=15) == ("i-c", 5)


def test_later_finding_on_same_best_issue_is_dup():
    g = _issues(("i-a", [10, 12]), ("i-b", [16, 16]))
    rows = match_bout([f(1, line_start=11), f(2, line_start=11, title="again")], g)
    # i-b is also within the window, but i-a is the better match for both
    assert [(r["verdict"], r["issue_id"]) for r in rows] == [("tp", "i-a"), ("dup", "i-a")]
    assert rows[0]["match_basis"] == "cwe"


def test_groundtruth_rejects_bad_lines():
    with pytest.raises(ValueError):
        gt(locations=[{"file": "a", "lines": [5, 2]}])


# --- dedup ------------------------------------------------------------------------------------


def _dedup_rows():
    return [
        f(1, bout="b-a", contender="c1", line_start=10, line_end=12),
        f(2, bout="b-b", contender="c2", line_start=14, line_end=14, title="x"),  # within 5
        f(3, bout="b-b", contender="c2", line_start=40, line_end=40, title="far"),
        f(4, bout="b-c", contender="c3", line_start=1, line_end=2, cwe="CWE-79", title="xss"),
        f(5, bout="b-c", contender="c3", line_start=11, cwe=None, title="nocwe"),  # category
        f(6, bout="b-d", contender="c1", arena="other", line_start=10),  # other arena
    ]


def test_dedup_clusters():
    rows = _dedup_rows()
    c = assign_clusters(rows)
    ids = [c[r["finding_id"]] for r in rows]
    assert ids[0] == ids[1] == ids[4]
    assert len({ids[0], ids[2], ids[3], ids[5]}) == 4
    assert all(i.startswith("c-") for i in ids)
    # same lines, both cite a CWE, different CWEs -> different problems
    assert not same_problem(f(1), f(2, cwe="CWE-79"))
    assert same_problem(f(1), f(2, cwe=None, title="t"))


def test_dedup_deterministic_under_shuffle():
    rows = _dedup_rows()
    want = assign_clusters(rows)
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert assign_clusters(shuffled) == want


def test_unique_per_contender():
    rows = _dedup_rows()
    c = assign_clusters(rows)
    for r in rows:
        r["cluster_id"] = c[r["finding_id"]]
    u = {(x["arena"], x["contender"]): x for x in unique_per_contender(rows)}
    assert u[("demo", "c1")]["clusters"] == 1 and u[("demo", "c1")]["exclusive_clusters"] == 0
    assert u[("demo", "c2")]["clusters"] == 2 and u[("demo", "c2")]["exclusive_clusters"] == 1
    assert u[("other", "c1")]["exclusive_clusters"] == 1


# --- summary ------------------------------------------------------------------------------------


def test_verdict_precedence_human_gt_judge():
    rows = [f(n, title=str(n)) for n in range(1, 6)]
    h = [r["finding_hash"] for r in rows]
    gt_rows = [
        {"finding_id": rows[0]["finding_id"], "verdict": "tp"},
        {"finding_id": rows[1]["finding_id"], "verdict": "tp"},
        {"finding_id": rows[2]["finding_id"], "verdict": "unknown"},
        {"finding_id": rows[3]["finding_id"], "verdict": "unknown"},
    ]
    judge = [
        {"finding_hash": h[0], "verdict": "invalid"},
        {"finding_hash": h[1], "verdict": "invalid"},
        {"finding_hash": h[2], "verdict": "valid"},
        {"finding_hash": h[3], "verdict": "unverifiable"},
    ]
    labels = [
        {"finding_hash": h[0], "labeler": "human:a", "verdict": "tp"},
        {"finding_hash": h[0], "labeler": "human:a", "verdict": "fp"},  # later line wins
        {"finding_hash": h[1], "labeler": "human:a", "verdict": "unsure"},  # falls through
    ]
    v = final_verdicts(rows, gt_rows, judge, labels)
    assert [(x["verdict"], x["source"]) for x in v] == [
        ("fp", "human"),
        ("tp", "gt"),
        ("tp", "judge"),
        ("unknown", None),
        ("unknown", None),
    ]


def test_human_majority_and_tie():
    labs = [
        {"finding_hash": "a", "labeler": "x", "verdict": "tp"},
        {"finding_hash": "a", "labeler": "y", "verdict": "tp"},
        {"finding_hash": "a", "labeler": "z", "verdict": "fp"},
        {"finding_hash": "b", "labeler": "x", "verdict": "tp"},
        {"finding_hash": "b", "labeler": "y", "verdict": "fp"},
    ]
    hv = human_verdicts(labs)
    assert hv["a"]["verdict"] == "tp" and hv["b"]["verdict"] is None


def test_summary_blanks_non_ok_bouts():
    bouts = [
        {"bout_id": "b-0000000000000001", "status": "ok"},
        {"bout_id": "b-2", "status": "invalid"},
    ]
    rows = [f(1, cluster_id="c-1")]
    v = final_verdicts(
        rows, [], [{"finding_hash": rows[0]["finding_hash"], "verdict": "valid"}], []
    )
    s = build_summary(bouts, rows, v, {})
    assert s[0]["judge_valid"] == 1 and s[0]["final_tp"] == 1 and s[0]["clusters"] == 1
    assert s[1]["judge_valid"] is None and s[1]["final_tp"] is None


# --- end to end on the real smoke round ---------------------------------------------------------


def test_score_smoke_round(smoke_trial):
    root = smoke_trial.parent
    labels = root / "labels" / "labels.jsonl"
    r = CliRunner().invoke(app, ["score", str(smoke_trial), "-r", "smoke"])
    assert r.exit_code == 0, r.output
    scores = root / "rounds" / "smoke" / "scores"
    for name in (
        "findings.jsonl",
        "gt_matches.jsonl",
        "bouts.csv",
        "summary.csv",
        "summary.parquet",
        "unique.csv",
        "verdicts.jsonl",
    ):
        assert (scores / name).exists(), name

    findings = read_jsonl(scores / "findings.jsonl")
    assert len(findings) == 17
    assert findings[0]["finding_id"] == "b-f369f8477abd9627:1"
    assert all(x["cluster_id"].startswith("c-") for x in findings)
    raw = json.loads((root / "rounds/smoke/bouts/b-f369f8477abd9627/findings.json").read_text())[
        "findings"
    ][0]
    assert findings[0]["finding_hash"] == finding_hash(raw)

    bouts = {b["contender"]: b for b in read_csv(scores / "bouts.csv", BOUT_STR_COLUMNS)}
    base = bouts["baseline"]
    assert base["status"] == "ok" and base["findings"] == 10
    assert base["tokens_total"] == 750624 and base["turns"] == 34
    assert base["rss_peak_kb"] == 262956 and base["cpu_s"] == pytest.approx(4.5015, abs=1e-3)
    assert base["duration_s"] == pytest.approx(84.433)

    gm = read_jsonl(scores / "gt_matches.jsonl")
    assert {x["verdict"] for x in gm} <= {"tp", "dup", "unknown"}  # incomplete gt: no fp
    by_bout = {}
    for x in gm:
        by_bout.setdefault(x["finding_id"].split(":")[0], []).append(x)
    for bid, xs in by_bout.items():
        tps = sorted(x["issue_id"] for x in xs if x["verdict"] == "tp")
        assert tps == sorted(
            [
                "dvpwa-sqli-student-create",
                "dvpwa-md5-passwords",
                "dvpwa-xss-autoescape",
                "dvpwa-csrf-disabled",
            ]
        ), bid

    import pyarrow.parquet as pq

    summ = {row["contender"]: row for row in pq.read_table(scores / "summary.parquet").to_pylist()}
    assert summ["baseline"]["recall"] == 0.8 and summ["baseline"]["precision"] is None
    assert summ["baseline"]["gt_issues"] == 5
    assert summ["baseline"]["precision_lower_bound"] == 0.4  # 4 tp / 10

    # a human label overrides ground truth on the next score
    fid = next(x for x in gm if x["verdict"] == "tp")
    labels.parent.mkdir()
    labels.write_text(
        json.dumps(
            {
                "finding_hash": fid["finding_hash"],
                "finding_id": fid["finding_id"],
                "arena": "dvpwa",
                "verdict": "fp",
                "labeler": "human:t",
            }
        )
        + "\n"
    )
    assert CliRunner().invoke(app, ["score", str(smoke_trial), "-r", "smoke"]).exit_code == 0
    v = {x["finding_id"]: x for x in read_jsonl(scores / "verdicts.jsonl")}
    assert v[fid["finding_id"]]["verdict"] == "fp" and v[fid["finding_id"]]["source"] == "human"


def test_score_warns_on_groundtruth_sha_mismatch(smoke_trial):
    gtf = smoke_trial.parent / "arenas" / "dvpwa" / "groundtruth.yaml"
    gtf.write_text(gtf.read_text().replace("a1d8f89fac2e", "deadbeef0000"))
    r = CliRunner().invoke(app, ["score", str(smoke_trial), "-r", "smoke"])
    assert r.exit_code == 0
    assert "ground truth was written against deadbeef0000" in r.output


def test_real_dvpwa_groundtruth(smoke_trial):
    """The hand-checked dvpwa ground truth (copied from skillordeal-trials) on the smoke round.

    Several issues sit within the window of each other there, so this pins which issue each
    real finding lands on.
    """
    gtf = smoke_trial.parent / "arenas" / "dvpwa" / "groundtruth.yaml"
    gtf.write_text((FIXTURES / "dvpwa-groundtruth.yaml").read_text())
    r = CliRunner().invoke(app, ["score", str(smoke_trial), "-r", "smoke"])
    assert r.exit_code == 0, r.output
    scores = smoke_trial.parent / "rounds" / "smoke" / "scores"
    findings = {x["finding_id"]: x for x in read_jsonl(scores / "findings.jsonl")}
    got = {
        (
            findings[m["finding_id"]]["contender"],
            findings[m["finding_id"]]["file"],
            findings[m["finding_id"]]["line_start"],
        ): (m["verdict"], m["issue_id"], m["match_basis"])
        for m in read_jsonl(scores / "gt_matches.jsonl")
    }
    assert got[("baseline", "sqli/app.py", 33)] == ("tp", "dvpwa-xss-review-stored", "cwe")
    # app.py:25-29 is also within the window of debug-mode (line 24), but the CWE decides
    assert got[("baseline", "sqli/app.py", 25)] == ("tp", "dvpwa-csrf-protection-disabled", "cwe")
    assert got[("baseline", "sqli/templates/course.jinja2", 20)] == (
        "dup",
        "dvpwa-xss-review-stored",
        "cwe",
    )
    assert got[("baseline", "sqli/middlewares.py", 20)] == (
        "tp",
        "dvpwa-session-cookie-flags",
        "cwe",
    )
    # CWE-384 on the cookie line: the session-fixation issue lives in views.py, so no match
    assert got[("sentry-security-review", "sqli/middlewares.py", 20)] == ("unknown", None, None)
    # MD5 cited as CWE-326/327, ground truth says CWE-916/328/759: exact ids only -> miss
    assert got[("baseline", "sqli/dao/user.py", 40)] == ("unknown", None, None)
    summ = {x["contender"]: x for x in read_csv(scores / "summary.csv", BOUT_STR_COLUMNS)}
    assert (summ["baseline"]["tp"], summ["baseline"]["dup"], summ["baseline"]["unknown"]) == (
        6,
        2,
        2,
    )
    assert summ["baseline"]["recall"] == round(6 / 19, 4)
    assert summ["sentry-security-review"]["tp"] == 4


def test_summary_without_pyarrow(tmp_path, monkeypatch):
    real = builtins.__import__

    def no_pyarrow(name, *a, **kw):
        if name.startswith("pyarrow"):
            raise ImportError(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    with pytest.raises(ScoreError, match="uv sync --extra analysis"):
        write_summary([{"bout_id": "b-1", "status": "ok"}], tmp_path)
    assert (tmp_path / "summary.csv").exists()
    assert not (tmp_path / "summary.parquet").exists()
