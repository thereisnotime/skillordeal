"""CLI commands for after a round has run: review, report, triage.

Kept out of cli.py so that module stays small; cli.py registers these with `app.command()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

out = Console(soft_wrap=True)
err = Console(stderr=True, soft_wrap=True)

TrialArg = Annotated[Path, typer.Argument(help="Path to trial.yaml", exists=True, dir_okay=False)]
RoundOpt = Annotated[str, typer.Option("--round", "-r", help="Round name, e.g. r01")]


def _fail(msg: str) -> None:
    err.print(f"[red]error:[/] {msg}")
    raise typer.Exit(1)


def review(
    trial: TrialArg,
    rnd: RoundOpt,
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1 (0 picks a free one)")] = 8765,
    labeler: Annotated[
        str | None, typer.Option(help="Your name in labels.jsonl (default: $USER)")
    ] = None,
    show_contenders: Annotated[
        bool, typer.Option(help="Show which contender wrote each finding (off = blind)")
    ] = False,
) -> None:
    """Label a round's findings in a local browser UI (blind to contenders by default)."""
    from skillordeal.config import load_trial
    from skillordeal.lock import read_lock
    from skillordeal.review.data import ReviewState
    from skillordeal.review.server import make_server
    from skillordeal.rounddata import Round, labeler_name

    lt = load_trial(trial)
    rd = Round(trial.resolve().parent, rnd)
    if not rd.root.exists():
        _fail(f"{rd.root} not found")
    state = ReviewState(
        rd,
        arenas={a.id: a for a in lt.arenas},
        arena_files=lt.arena_files,
        lock=read_lock(rd.lock),
        cache_dir=Path(lt.trial.runtime.cache_dir).expanduser(),
        labeler=labeler_name(labeler),
        show_contenders=show_contenders,
        trial_title=lt.trial.title,
    )
    for w in state.warnings:
        err.print(f"[yellow]warning:[/] {w}")
    try:
        srv = make_server(state, port)
    except OSError as e:
        _fail(f"can't listen on 127.0.0.1:{port}: {e}")
    url = f"http://127.0.0.1:{srv.server_address[1]}/?token={state.token}"
    out.print(
        f"[bold]{len(state.findings)}[/] findings, labeler [cyan]{state.labeler}[/], "
        f"{'contender names shown' if show_contenders else 'blind'}",
        f"\nlabels -> {rd.labels}\nopen this URL (it carries the session token), Ctrl-C to stop:",
        soft_wrap=True,
    )
    print(url, flush=True)  # plain print: rich would wrap the token across lines
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def report(
    trial: TrialArg,
    rnd: RoundOpt,
    markdown_only: Annotated[
        bool, typer.Option(help="Only RESULTS.md and the exports, no report.html")
    ] = False,
    charts: Annotated[
        bool, typer.Option(help="Write SVG charts to rounds/<round>/charts/ and embed them")
    ] = True,
    seed: Annotated[int, typer.Option(help="Bootstrap seed")] = 20260923,
    resamples: Annotated[int, typer.Option(help="Bootstrap resamples per cell")] = 2000,
) -> None:
    """Write RESULTS.md, charts/, report.html and results.csv/parquet for a scored round."""
    from skillordeal.report import ReportError, build_report
    from skillordeal.rounddata import Round

    rd = Round(trial.resolve().parent, rnd)
    try:
        written = build_report(
            trial, rd, markdown_only=markdown_only, charts=charts, seed=seed, resamples=resamples
        )
    except ReportError as e:
        _fail(str(e))
    for p in written:
        out.print(f"[green]wrote[/] {p}")


def triage(
    skills_list: Annotated[
        Path, typer.Option(help="skills-collection skills-list.json", dir_okay=False)
    ] = Path("~/Private/Projects/P/skills-collection/skills-list.json"),
    keywords: Annotated[
        list[str], typer.Option("--keywords", "-k", help="Keyword, repeatable")
    ] = [],  # noqa: B006
    out_file: Annotated[Path, typer.Option("--out", "-o", help="Output YAML")] = Path(
        "candidates.yaml"
    ),
    min_words: Annotated[int, typer.Option(help="Skip SKILL.md files shorter than this")] = 200,
    top: Annotated[int, typer.Option(help="Keep only the N best (0 = all)")] = 0,
    pin_synced: Annotated[
        bool,
        typer.Option(help="ref = the commit skills-collection synced (what was scanned)"),
    ] = False,
) -> None:
    """Shortlist skills from skills-collection into contender YAML, with a risk pre-scan."""
    from skillordeal.triage import DEFAULT_KEYWORDS, TriageError, run_triage

    try:
        res = run_triage(
            skills_list.expanduser(),
            keywords=keywords or list(DEFAULT_KEYWORDS),
            min_words=min_words,
            top=top,
            pin_synced=pin_synced,
        )
    except TriageError as e:
        _fail(str(e))
    out_file.write_text(res.yaml)
    levels = {lvl: sum(1 for c in res.candidates if c.risk == lvl) for lvl in ("high", "medium")}
    out.print(
        f"[green]wrote[/] {out_file}: {len(res.candidates)} candidates "
        f"({res.matched} matched, {res.duplicates} near-duplicates folded, "
        f"{res.too_short} under {min_words} words; risk "
        f"[red]{levels['high']} high[/], [yellow]{levels['medium']} medium[/])"
    )
