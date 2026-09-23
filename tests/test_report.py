import csv
import math
import re

import pytest

from skillordeal.report import aggregate, bootstrap, build_report, load_rows
from skillordeal.rounddata import Round

pytest.importorskip("duckdb")
pytest.importorskip("numpy")


def test_bootstrap_is_deterministic():
    xs = [0.1, 0.4, 0.35, 0.8, 0.5]
    a = bootstrap(xs, seed=7, resamples=500)
    b = bootstrap(xs, seed=7, resamples=500)
    assert (a.mean, a.lo, a.hi, a.n) == (b.mean, b.lo, b.hi, b.n)
    assert a.lo <= a.mean <= a.hi
    one = bootstrap([3.0], seed=7, resamples=500)
    assert one.mean == 3.0 and math.isnan(one.lo) and one.n == 1
    assert bootstrap([math.nan], seed=7, resamples=500).n == 0


def test_report_markdown(scored_round):
    rd = Round(scored_round.parent, "r01")
    written = build_report(scored_round, rd, markdown_only=True, resamples=300)
    names = {p.name for p in written}
    assert {"RESULTS.md", "results.csv", "results.parquet"} <= names
    assert "report.html" not in names
    md = (rd.root / "RESULTS.md").read_text()
    assert "> Q?" in md and "lock hash" in md
    # every bout of each contender is linked relative to the round dir
    ids = [p.name for p in rd.bouts.iterdir()]
    for bid in ids:
        assert f"(bouts/{bid}/)" in md
    # CI columns: mean [lo, hi] for n >= 2, and n = ok/total per cell
    assert re.search(r"\| my-skill \| 2/2 \| 2 \[2, 2\] \| 1 \[1, 1\] \|", md)
    assert re.search(r"\d+\.\d{3} \[\d+\.\d{3}, \d+\.\d{3}\]", md)
    assert "| wrapped | 1/2 |" in md
    # the failed bout is counted and listed with its reason
    assert "**error**: 1" in md and "agent error: overloaded" in md
    # forced-injection check: wrapped adds no first-turn tokens over baseline
    wrapped = next(
        line for line in md.splitlines() if line.startswith("| wrapped |") and "⚠" in line
    )
    assert "skill may not have loaded" in wrapped
    skill = next(
        line for line in md.splitlines() if line.startswith("| my-skill |") and "+1,200" in line
    )
    assert "+1,200" in skill and "⚠" not in skill
    assert "## How to reproduce" in md
    # deterministic: same inputs, same file
    build_report(scored_round, rd, markdown_only=True, resamples=300)
    md2 = (rd.root / "RESULTS.md").read_text()
    strip = lambda s: re.sub(r"\| generated \|.*\|", "", s)
    assert strip(md) == strip(md2)


def test_report_exports_and_html(scored_round):
    rd = Round(scored_round.parent, "r01")
    build_report(scored_round, rd, resamples=200)
    rows = list(csv.DictReader((rd.root / "results.csv").open()))
    by = {r["contender"]: r for r in rows}
    assert by["wrapped"]["n_ok"] == "1" and by["wrapped"]["n_error"] == "1"
    assert float(by["my-skill"]["cost_usd"]) == pytest.approx(0.155)
    assert float(by["my-skill"]["delta_tp"]) == pytest.approx(0.0)
    page = (rd.root / "report.html").read_text()
    assert "<svg" in page and "<script src" not in page and "http://" not in page.split("<svg")[0]
    assert page.count("<svg") >= 4
    assert "var(--series-1)" in page and "<title>" in page
    assert 'href="bouts/' in page


def test_fallback_to_bouts_csv(scored_round):
    rd = Round(scored_round.parent, "r01")
    summary = rd.scores / "summary.csv"
    keep = [
        "bout_id",
        "contender",
        "arena",
        "task",
        "model",
        "rep",
        "status",
        "findings",
        "cost_usd",
    ]
    rows = list(csv.DictReader(summary.open()))
    summary.unlink()
    with (rd.scores / "bouts.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keep, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    got, source = load_rows(rd)
    assert source == "bouts.csv" and len(got) == len(rows)
    cells = aggregate(got, seed=1, resamples=100)
    assert all(not c.est["tp"].ok for c in cells)
    build_report(scored_round, rd, markdown_only=True, resamples=100)
    assert "No quality scores yet" in (rd.root / "RESULTS.md").read_text()


def test_parquet_preferred(scored_round):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    rd = Round(scored_round.parent, "r01")
    rows = list(csv.DictReader((rd.scores / "summary.csv").open()))
    for r in rows:
        r["tp"] = 5
    pq.write_table(pa.Table.from_pylist(rows), rd.scores / "summary.parquet")
    got, source = load_rows(rd)
    assert source == "summary.parquet" and all(int(r["tp"]) == 5 for r in got)
