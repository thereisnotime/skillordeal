import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from skillordeal.config import load_trial
from skillordeal.lock import read_lock
from skillordeal.review.data import ExcerptError, ReviewState, read_excerpt
from skillordeal.review.server import make_server
from skillordeal.rounddata import Round, append_label, effective_labels, make_label, read_labels


def state_for(trial_file: Path, **kw) -> ReviewState:
    lt = load_trial(trial_file)
    rd = Round(trial_file.parent, "r01")
    return ReviewState(
        rd,
        arenas={a.id: a for a in lt.arenas},
        arena_files=lt.arena_files,
        lock=read_lock(rd.lock),
        cache_dir=Path(lt.trial.runtime.cache_dir),
        labeler=kw.pop("labeler", "human:tester"),
        token=kw.pop("token", "s3cret"),
        **kw,
    )


@pytest.fixture
def server(scored_round):
    st = state_for(scored_round)
    srv = make_server(st, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield st, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(url, *, token="s3cret", body=None, method=None, ctype="application/json"):
    headers = {"X-Review-Token": token} if token is not None else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


# --- labels ------------------------------------------------------------------------------


def test_labels_append_and_later_lines_override(tmp_path):
    path = tmp_path / "labels" / "labels.jsonl"
    common = {"finding_hash": "h1", "finding_id": "b-1:1", "arena": "demo"}
    append_label(path, make_label(**common, verdict="fp", labeler="human:a", ts="t1"))
    append_label(path, make_label(**common, verdict="tp", labeler="human:b", ts="t2"))
    append_label(
        path, make_label(**common, verdict="tp", issue_id="x-1", labeler="human:a", ts="t3")
    )
    lines = path.read_text().splitlines()
    assert len(lines) == 3  # append-only, nothing rewritten
    first = json.loads(lines[0])
    assert set(first) == {
        "finding_hash",
        "finding_id",
        "arena",
        "verdict",
        "issue_id",
        "labeler",
        "ts",
        "note",
    }
    eff = effective_labels(read_labels(path))
    assert eff[("h1", "human:a")]["verdict"] == "tp"
    assert eff[("h1", "human:a")]["issue_id"] == "x-1"
    assert eff[("h1", "human:b")]["verdict"] == "tp"


def test_bad_verdict_rejected():
    with pytest.raises(ValueError):
        make_label(finding_hash="h", finding_id="b:1", arena="a", verdict="maybe", labeler="x")


def test_truncated_label_line_is_skipped(tmp_path):
    p = tmp_path / "labels.jsonl"
    p.write_text('{"finding_hash": "h", "labeler": "human:a", "verdict": "tp"}\n{"finding_ha')
    assert len(read_labels(p)) == 1


# --- excerpts ------------------------------------------------------------------------------


def test_excerpt_rejects_traversal(tmp_path):
    root = tmp_path / "arena"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("\n".join(f"line {i}" for i in range(1, 51)))
    (tmp_path / "secret.txt").write_text("nope")
    os.symlink(tmp_path / "secret.txt", root / "src" / "link.txt")
    for bad in ("../secret.txt", "/etc/passwd", "src/../../secret.txt", "src/link.txt", ""):
        with pytest.raises(ExcerptError):
            read_excerpt(root, bad, 1, 1)
    x = read_excerpt(root, "./src/a.py", 20, 22)
    assert [line["n"] for line in x["lines"]] == list(range(5, 38))
    assert x["lines"][15] == {"n": 20, "text": "line 20"}
    # /arena/ prefixes from the container path are accepted
    assert read_excerpt(root, "/arena/src/a.py", 1, None)["lines"][0]["n"] == 1


def test_state_joins_scores_and_blinds(scored_round):
    st = state_for(scored_round)
    items = st.findings_payload()
    assert items and all("contender" not in f and "bout_id" not in f for f in items)
    good = next(f for f in items if f["file"] == "app/main.py")
    assert good["gt"] == {"verdict": "tp", "issue_id": "demo-cmdi"}
    assert good["judge"][0]["verdict"] == "valid"
    assert good["description"] == "input() flows into os.system."
    assert st.meta()["issues"]["demo"][0]["id"] == "demo-cmdi"
    shown = state_for(scored_round, show_contenders=True).findings_payload()
    assert {f["contender"] for f in shown} >= {"baseline", "my-skill"}


# --- server --------------------------------------------------------------------------------


def test_server_token_required(server):
    st, base = server
    fid = st.findings[0]["finding_id"]
    for token in (None, "wrong"):
        code, _ = call(f"{base}/api/findings", token=token)
        assert code == 403
        code, _ = call(f"{base}/api/labels", token=token, body={"finding_id": fid, "verdict": "tp"})
        assert code == 403
    assert not st.rnd.labels.exists()
    # the page itself carries no data and loads without the token
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        assert b"skillordeal review" in r.read()


def test_server_label_roundtrip(server):
    st, base = server
    code, items = call(f"{base}/api/findings")
    assert code == 200
    good = next(f for f in items if f["file"] == "app/main.py")
    code, lab = call(
        f"{base}/api/labels",
        body={"finding_id": good["finding_id"], "verdict": "tp", "issue_id": "demo-cmdi"},
    )
    assert code == 201
    assert lab["labeler"] == "human:tester" and lab["finding_hash"] == good["finding_hash"]
    code, _ = call(f"{base}/api/labels", body={"finding_id": good["finding_id"], "verdict": "fp"})
    assert code == 201
    rows = read_labels(st.rnd.labels)
    assert [r["verdict"] for r in rows] == ["tp", "fp"]
    code, items = call(f"{base}/api/findings")
    again = next(f for f in items if f["finding_id"] == good["finding_id"])
    assert again["label"]["verdict"] == "fp"
    # bad input
    assert call(f"{base}/api/labels", body={"finding_id": "nope:1", "verdict": "tp"})[0] == 404
    bad = {"finding_id": good["finding_id"], "verdict": "maybe"}
    assert call(f"{base}/api/labels", body=bad)[0] == 400
    bad = {"finding_id": good["finding_id"], "verdict": "tp", "issue_id": "made-up"}
    assert call(f"{base}/api/labels", body=bad)[0] == 400
    ok = {"finding_id": good["finding_id"], "verdict": "tp"}
    assert call(f"{base}/api/labels", body=ok, ctype="text/plain")[0] == 415


def test_server_excerpt(server):
    st, base = server
    good = next(f for f in st.findings if f["file"] == "app/main.py")
    code, x = call(f"{base}/api/excerpt?finding_id={good['finding_id']}")
    assert code == 200
    assert {"n": 2, "text": "os.system(input())"} in x["lines"]
    # the arena is the stripped export the agent saw
    root = st.arena_root("demo")
    assert not (root / "CLAUDE.md").exists() and not (root / "dist").exists()
    evil = next(f for f in st.findings if f["file"].startswith(".."))
    code, err = call(f"{base}/api/excerpt?finding_id={evil['finding_id']}")
    assert code == 400 and "escapes" in err["error"]
