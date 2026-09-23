import csv
import json
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


# --- charts and mermaid -------------------------------------------------------------------

CHARTS = {
    "quality-vs-cost-demo.svg",
    "findings-breakdown-demo.svg",
    "delta-vs-baseline-demo.svg",
    "recall-by-contender.svg",
    "cost-tokens.svg",
    "resources.svg",
}


def _chart_links(md: str) -> set[str]:
    return set(re.findall(r"\]\(charts/([^)]+)\)", md))


def _mermaid_blocks(md: str) -> list[str]:
    return re.findall(r"```mermaid\n(.*?)\n```", md, flags=re.S)


def _check_mermaid(block: str) -> None:
    """Structural check for GitHub's mermaid: one header, quoted labels, generated ids only."""
    lines = block.splitlines()
    assert lines[0] == "flowchart LR"
    nodes = set()
    for line in lines[1:]:
        node = re.fullmatch(r'    (n\d+)\["([^"\n]*)"\]', line)
        edge = re.fullmatch(r'    (n\d+) -->(?:\|"([^"\n]*)"\|)? (n\d+)', line)
        assert node or edge, line
        if node:
            nodes.add(node.group(1))
        else:
            assert {edge.group(1), edge.group(3)} <= nodes, line


def _rewrite_summary(rd, mutate, drop=()):
    summary = rd.scores / "summary.csv"
    rows = list(csv.DictReader(summary.open()))
    for r in rows:
        mutate(r)
    keep = [k for k in rows[0] if k not in drop]
    with summary.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keep, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def test_charts_written_and_deterministic(scored_round):
    rd = Round(scored_round.parent, "r01")
    written = build_report(scored_round, rd, markdown_only=True, resamples=200)
    charts = rd.root / "charts"
    assert {p.name for p in charts.iterdir()} == CHARTS
    assert {p.name for p in written if p.parent == charts} == CHARTS
    first = {p.name: p.read_bytes() for p in charts.iterdir()}
    for data in first.values():
        text = data.decode()
        assert "<dc:date>" not in text and "<metadata>" not in text
        assert 'role="img"' in text and "<title>" in text
        assert "fill: #ffffff" not in text  # transparent, no painted surface
    build_report(scored_round, rd, markdown_only=True, resamples=200)
    assert {p.name: p.read_bytes() for p in charts.iterdir()} == first


def test_results_md_links_only_existing_charts(scored_round):
    rd = Round(scored_round.parent, "r01")
    build_report(scored_round, rd, markdown_only=True, resamples=200)
    md = (rd.root / "RESULTS.md").read_text()
    links = _chart_links(md)
    assert links == CHARTS
    assert all((rd.root / "charts" / name).is_file() for name in links)
    assert "## Charts" in md and "### demo" in md and "### All arenas" in md
    # every chart has a caption with its n right under it
    for name in links:
        caption = md.split(f"(charts/{name})", 1)[1].strip().splitlines()[0]
        assert caption.startswith("*") and "n = 5 ok bouts of 6" in caption


def test_charts_without_ground_truth(scored_round):
    rd = Round(scored_round.parent, "r01")
    build_report(scored_round, rd, markdown_only=True, resamples=100)
    assert (rd.root / "charts" / "recall-by-contender.svg").exists()
    gt = {"tp", "fp", "dup", "unknown", "precision", "recall", "f1"}
    _rewrite_summary(rd, lambda r: None, drop=gt)
    build_report(scored_round, rd, resamples=100)
    names = {p.name for p in (rd.root / "charts").iterdir()}
    assert "recall-by-contender.svg" not in names  # the stale file from the first run is gone
    assert {"quality-vs-cost-demo.svg", "delta-vs-baseline-demo.svg"} <= names
    assert _chart_links((rd.root / "RESULTS.md").read_text()) == names
    assert "judge-valid" in (rd.root / "charts" / "quality-vs-cost-demo.svg").read_text()
    assert "split by judge" in (rd.root / "charts" / "findings-breakdown-demo.svg").read_text()


def test_charts_from_bouts_csv_only(scored_round):
    rd = Round(scored_round.parent, "r01")
    summary = rd.scores / "summary.csv"
    rows = list(csv.DictReader(summary.open()))
    summary.unlink()
    keep = ["bout_id", "contender", "arena", "task", "model", "rep", "status", "findings",
            "cost_usd", "tokens_total", "rss_peak_kb"]  # fmt: skip
    with (rd.scores / "bouts.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keep, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    build_report(scored_round, rd, resamples=100)
    names = {p.name for p in (rd.root / "charts").iterdir()}
    assert names == {"cost-tokens.svg", "resources.svg"}
    assert _chart_links((rd.root / "RESULTS.md").read_text()) == names


def test_charts_survive_nan_and_single_bouts(scored_round):
    rd = Round(scored_round.parent, "r01")

    def mutate(r):
        if r["contender"] == "baseline":
            r["cost_usd"] = r["tokens_total"] = r["rss_peak_kb"] = ""
        if r["contender"] == "my-skill" and r["rep"] == "2":
            r["status"] = "timeout"

    _rewrite_summary(rd, mutate)
    build_report(scored_round, rd, resamples=100)
    names = {p.name for p in (rd.root / "charts").iterdir()}
    assert {"quality-vs-cost-demo.svg", "delta-vs-baseline-demo.svg", "cost-tokens.svg"} <= names
    md = (rd.root / "RESULTS.md").read_text()
    assert _chart_links(md) == names
    assert '"timeout: 1"' in md


def test_no_charts_flag(scored_round):
    from typer.testing import CliRunner

    from skillordeal.cli import app

    rd = Round(scored_round.parent, "r01")
    r = CliRunner().invoke(
        app, ["report", str(scored_round), "-r", "r01", "--no-charts", "--resamples", "50"]
    )
    assert r.exit_code == 0, r.output
    assert not (rd.root / "charts").exists()
    md = (rd.root / "RESULTS.md").read_text()
    assert "charts/" not in md and "## Charts" not in md
    assert "<svg" in (rd.root / "report.html").read_text()  # the page still inlines them


def test_mermaid_round_at_a_glance(scored_round):
    rd = Round(scored_round.parent, "r01")
    build_report(scored_round, rd, markdown_only=True, resamples=100)
    md = (rd.root / "RESULTS.md").read_text()
    assert "## Round at a glance" in md
    blocks = _mermaid_blocks(md)
    assert len(blocks) == 1  # no pipelines in this round
    glance = blocks[0]
    _check_mermaid(glance)
    # 3 contenders x 2 reps planned; wrapped rep 2 errored
    for label in ("6 bouts planned", "ok: 5", "error: 1", "8 findings", "tp: 5", "fp: 0"):
        assert f'"{label}"' in glance, label
    # the traversal-bait findings have no verdict column in the fixture summary
    assert '"other: 3"' in glance
    assert "timeout" not in glance


def test_mermaid_pipeline_flow(scored_round):
    rd = Round(scored_round.parent, "r01")
    for d in rd.bouts.iterdir():
        rec = json.loads((d / "record.json").read_text())
        if rec["contender"]["id"] != "my-skill":
            continue
        rec["stages"] = [
            {"n": 1, "contender": "sentry", "status": "ok", "findings_count": 5},
            {"n": 2, "contender": "fp-check", "status": "ok", "findings_count": 2},
        ]
        (d / "record.json").write_text(json.dumps(rec))
    build_report(scored_round, rd, markdown_only=True, resamples=100)
    md = (rd.root / "RESULTS.md").read_text()
    blocks = _mermaid_blocks(md)
    assert len(blocks) == 2 and "### Pipelines" in md
    flow = blocks[1]
    _check_mermaid(flow)
    assert '"my-skill (2 ok bouts)"' in flow
    assert '"stage 1, sentry: 10 findings"' in flow and '"stage 2, fp-check: 4 kept"' in flow
    assert '-->|"verify"|' in flow


def test_mermaid_labels_are_escaped():
    from skillordeal.report_mermaid import Flow

    f = Flow()
    a = f.node('say "end"')
    f.edge(a, f.node("end"))
    block = f.block()
    assert "#quot;end#quot;" in block
    (inner,) = _mermaid_blocks(block)
    _check_mermaid(inner)
