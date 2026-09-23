"""`score` and `judge` commands. Registered on the main app in cli.py."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from skillordeal.config import load_trial
from skillordeal.lock import LockError, image_ref, inspect_image, read_lock
from skillordeal.runner import RoundPaths
from skillordeal.score import ScoreError, read_jsonl, scores_dir
from skillordeal.score import judge as judge_mod
from skillordeal.score.pipeline import run_score
from skillordeal.secrets import AuthError, resolve_credentials

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


def judge(
    trial: TrialArg,
    rnd: RoundOpt,
    max_cost_usd: Annotated[
        float | None, typer.Option(help="Stop judging after this much spend")
    ] = None,
    batch_size: Annotated[int, typer.Option(help="Findings per judge call")] = 15,
    dry_run: Annotated[
        bool, typer.Option(help="Print the batches and the exact blinded prompts, call nothing")
    ] = False,
) -> None:
    """Have the trial's judge model rule on every finding (blinded, cached), then re-score."""
    paths = RoundPaths(trial.resolve().parent, rnd)
    try:
        lt = load_trial(trial)
        lk = read_lock(paths.lock)
        model = judge_mod.require_judge(lt)
        res = run_score(lt, paths)
    except (ScoreError, LockError, ValueError) as e:
        _fail(str(e))
    rows = res.findings
    cache_dir = Path(lt.trial.runtime.cache_dir).expanduser()
    cache = judge_mod.JudgeCache(cache_dir, model, judge_mod.prompt_sha())
    cached = {r["finding_hash"] for r in rows if cache.get(r["finding_hash"]) is not None}

    if dry_run:
        try:
            batches = judge_mod.plan_batches(lt, lk, rows, batch_size=batch_size, skip=cached)
        except ScoreError as e:
            _fail(str(e))
        err.print(
            f"judge {model.slug}, cache {cache.dir}\n"
            f"{len(cached)} findings cached, {sum(len(b.items) for b in batches)} to judge "
            f"in {len(batches)} batches"
        )
        for b in batches:
            ids = {r["finding_hash"]: r["finding_id"] for r in b.items}
            err.print(f"\n[bold]== {b.batch_id}[/] arena={b.arena} findings={len(b.items)}")
            for ref, h in b.refs.items():
                err.print(f"  {ref} -> {ids[h]}  {h[:12]}")
            print(b.prompt)  # plain stdout, exactly what the judge would get
        return

    try:
        creds = resolve_credentials(lt.trial.runtime.auth)
    except AuthError as e:
        _fail(str(e))
    try:
        image = inspect_image(image_ref(lt.trial.runtime))
    except LockError as e:
        _fail(str(e))
    if lk["image"].get("id") and image["id"] != lk["image"]["id"]:
        err.print(
            f"[yellow]warning:[/] image {image['id'][:12]} differs from the locked "
            f"{lk['image']['id'][:12]}; the judge runs in whatever image is there now"
        )
    try:
        run = judge_mod.run_judge(
            lt,
            lk,
            rows,
            scores_dir(paths),
            creds,
            batch_size=batch_size,
            max_cost_usd=max_cost_usd,
            log=lambda m: err.print(f"[dim]{m}[/]"),
        )
    except ScoreError as e:
        _fail(str(e))
    for p in run.problems:
        err.print(f"[yellow]{p}[/]")
    out.print(
        f"judged {run.judged} new, {run.cached} cached, {run.missing} still pending, "
        f"spent ${run.spent_usd:.3f}"
    )
    _score(trial, rnd, None)
    counts: dict[str, int] = {}
    for r in read_jsonl(scores_dir(paths) / "judge.jsonl"):
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    out.print("verdicts: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"))
