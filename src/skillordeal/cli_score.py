"""`score` command. Registered on the main app in cli.py."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from skillordeal.config import load_trial
from skillordeal.lock import LockError
from skillordeal.runner import RoundPaths
from skillordeal.score import ScoreError
from skillordeal.score.pipeline import run_score

out = Console()
err = Console(stderr=True)

TrialArg = Annotated[Path, typer.Argument(help="Path to trial.yaml", exists=True, dir_okay=False)]
RoundOpt = Annotated[str, typer.Option("--round", "-r", help="Round name, e.g. r01")]


def _fail(msg: str) -> None:
    err.print(f"[red]error:[/] {msg}")
    raise typer.Exit(1)


def _fmt(v: object) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def _score(trial: Path, rnd: str, window: int | None) -> None:
    paths = RoundPaths(trial.resolve().parent, rnd)
    try:
        res = run_score(load_trial(trial), paths, window)
    except (ScoreError, LockError, ValueError) as e:
        _fail(str(e))
    for w in res.warnings:
        err.print(f"[yellow]warning:[/] {w}")

    by_bout = {r["bout_id"]: r for r in res.summary}
    tbl = Table("contender", "arena", "bouts", "ok", "findings", "clusters", "only them", "recall")
    for u in res.unique or []:
        mine = [
            r
            for r in by_bout.values()
            if (r["contender"], r["arena"]) == (u["contender"], u["arena"])
        ]
        recalls = [r["recall"] for r in mine if r.get("recall") is not None]
        tbl.add_row(
            u["contender"],
            u["arena"],
            str(len(mine)),
            str(sum(1 for r in mine if r["status"] == "ok")),
            str(u["findings"]),
            str(u["clusters"]),
            str(u["exclusive_clusters"]),
            _fmt(sum(recalls) / len(recalls)) if recalls else "-",
        )
    out.print(tbl)
    not_ok = [r for r in res.bouts if r["status"] != "ok"]
    out.print(
        f"{len(res.bouts)} bouts ({len(not_ok)} not ok, excluded), {len(res.findings)} findings "
        f"-> {res.scores}"
    )


def score(
    trial: TrialArg,
    rnd: RoundOpt,
    window: Annotated[
        int | None,
        typer.Option(help="Line window for matching and dedup (default: ground truth's, or 5)"),
    ] = None,
) -> None:
    """Collect findings, match ground truth, cluster duplicates and write scores/ (no network)."""
    _score(trial, rnd, window)
