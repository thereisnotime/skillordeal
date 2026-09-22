"""skillordeal command line. Every step is a plain shell command; no LLM needed to drive it."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
import zstandard
from rich.console import Console
from rich.table import Table

from skillordeal import __version__
from skillordeal.config import load_trial
from skillordeal.lock import LockError, build_lock, check_drift, read_lock, write_lock
from skillordeal.matrix import expand, shard
from skillordeal.runner import RoundPaths, round_status, run_round
from skillordeal.secrets import AuthError, resolve_credentials

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
out = Console()
err = Console(stderr=True)

TrialArg = Annotated[Path, typer.Argument(help="Path to trial.yaml", exists=True, dir_okay=False)]
RoundOpt = Annotated[str, typer.Option("--round", "-r", help="Round name, e.g. r01")]


def _fail(msg: str) -> None:
    err.print(f"[red]error:[/] {msg}")
    raise typer.Exit(1)


def _paths(trial: Path, rnd: str) -> RoundPaths:
    return RoundPaths(trial.resolve().parent, rnd)


@app.command()
def version() -> None:
    """Print the engine version."""
    out.print(__version__)


@app.command()
def validate(trial: TrialArg) -> None:
    """Load and validate a trial and everything it references."""
    try:
        lt = load_trial(trial)
    except Exception as e:
        _fail(str(e))
    t = lt.trial
    out.print(
        f"[green]ok[/] {t.id}: {len(lt.contenders)} contenders, {len(lt.arenas)} arenas, "
        f"{len(t.tasks)} tasks, {len(t.models)} models, {t.reps} reps"
    )


@app.command()
def lock(
    trial: TrialArg,
    rnd: RoundOpt,
    skip_image: Annotated[
        bool, typer.Option(help="Don't inspect the image (planning only)")
    ] = False,
    force: Annotated[bool, typer.Option(help="Overwrite an existing lock")] = False,
) -> None:
    """Resolve every input to an immutable value and write rounds/<round>/lock.yaml."""
    paths = _paths(trial, rnd)
    if paths.lock.exists() and not force:
        _fail(f"{paths.lock} exists; a round's lock is immutable (use a new round or --force)")
    try:
        lk = build_lock(load_trial(trial), skip_image=skip_image)
    except (LockError, ValueError) as e:
        _fail(str(e))
    write_lock(lk, paths.lock)
    out.print(f"[green]locked[/] {paths.lock} ({lk['lock_hash'][:12]})")


@app.command()
def plan(
    trial: TrialArg,
    rnd: RoundOpt,
    shards: Annotated[int, typer.Option(help="Emit a CI matrix with this many shards")] = 0,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List the bouts of a locked round, or emit a GitHub Actions shard matrix."""
    paths = _paths(trial, rnd)
    keys = expand(read_lock(paths.lock))
    if shards:
        matrix = {"shard": [f"{i}/{shards}" for i in range(1, shards + 1)]}
        print(json.dumps(matrix))
        return
    if as_json:
        print(json.dumps([k.__dict__ for k in keys], indent=2))
        return
    tbl = Table("bout", "contender", "arena", "task", "model", "rep")
    for k in keys:
        tbl.add_row(k.bout_id, k.contender, k.arena, k.task, k.model, str(k.rep))
    out.print(tbl)
    out.print(f"{len(keys)} bouts")


@app.command()
def run(
    trial: TrialArg,
    rnd: RoundOpt,
    jobs: Annotated[
        int, typer.Option("--jobs", "-j", help="Parallel bouts (1 = one after another)")
    ] = 0,
    shard_spec: Annotated[str, typer.Option("--shard", help="i/N: run only this CI shard")] = "",
    contender: Annotated[list[str], typer.Option(help="Only these contenders")] = [],  # noqa: B006
    arena: Annotated[list[str], typer.Option(help="Only these arenas")] = [],  # noqa: B006
    task: Annotated[list[str], typer.Option(help="Only these tasks")] = [],  # noqa: B006
    rep: Annotated[list[int], typer.Option(help="Only these reps")] = [],  # noqa: B006
    max_cost_usd: Annotated[
        float | None, typer.Option(help="Stop the round after this much spend")
    ] = None,
    max_tokens: Annotated[
        int | None, typer.Option(help="Stop the round after this many tokens")
    ] = None,
    keep_work: Annotated[
        bool, typer.Option(help="Keep prepared arena/ctx dirs for debugging")
    ] = False,
    no_drift_check: Annotated[
        bool, typer.Option(help="Skip the lock drift check (not reproducible!)")
    ] = False,
    dry_run: Annotated[bool, typer.Option(help="Show what would run")] = False,
) -> None:
    """Run the bouts of a locked round. Finished bouts are skipped, so this resumes."""
    paths = _paths(trial, rnd)
    lt = load_trial(trial)
    lk = read_lock(paths.lock)
    if not no_drift_check:
        problems = check_drift(lk, lt)
        if problems:
            _fail("lock drift, refusing to run:\n  " + "\n  ".join(problems))
    keys = expand(lk)
    if shard_spec:
        i, n = (int(x) for x in shard_spec.split("/"))
        keys = shard(keys, i, n)
    keys = [
        k
        for k in keys
        if (not contender or k.contender in contender)
        and (not arena or k.arena in arena)
        and (not task or k.task in task)
        and (not rep or k.rep in rep)
    ]
    if dry_run:
        for k in keys:
            out.print(f"{k.bout_id}  {k.label()}")
        out.print(f"{len(keys)} bouts")
        return
    try:
        creds = resolve_credentials(lt.trial.runtime.auth)
    except AuthError as e:
        _fail(str(e))
    results = run_round(
        keys,
        lt,
        lk,
        creds,
        paths,
        jobs=jobs or lt.trial.runtime.concurrency,
        keep_work=keep_work,
        max_cost_usd=max_cost_usd,
        max_tokens=max_tokens,
    )
    bad = [r for r in results if r.get("status") not in {"ok"}]
    if bad:
        err.print(f"[yellow]{len(bad)} bouts not ok[/]")


@app.command()
def status(
    trial: TrialArg,
    rnd: RoundOpt,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show per-bout status, findings, cost, tokens, time and peak RAM."""
    paths = _paths(trial, rnd)
    rows = round_status(expand(read_lock(paths.lock)), paths)
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    tbl = Table(
        "bout",
        "contender",
        "arena",
        "rep",
        "status",
        "findings",
        "cost $",
        "tokens",
        "time s",
        "fired",
        "rss MB",
    )
    for r in rows:
        tbl.add_row(
            r["bout_id"],
            r["contender"],
            r["arena"],
            str(r["rep"]),
            r["status"],
            str(r["findings"] if r["findings"] is not None else "-"),
            f"{r['cost_usd']:.3f}" if r["cost_usd"] else "-",
            str(r["tokens"] or "-"),
            f"{r['duration_s']:.0f}" if r["duration_s"] else "-",
            {True: "yes", False: "no", None: "-"}[r["skill_fired"]],
            str(r["rss_peak_mb"] or "-"),
        )
    out.print(tbl)


@app.command()
def transcript(bout_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    """Print a bout's decompressed stream-json transcript (pipe into jq)."""
    data = (bout_dir / "transcript.jsonl.zst").read_bytes()
    sys.stdout.write(zstandard.ZstdDecompressor().decompress(data).decode())


@app.command()
def show(bout_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    """Summarize one bout: record highlights and its findings."""
    rec = json.loads((bout_dir / "record.json").read_text())
    usage = rec.get("usage") or {}
    res = rec.get("resources") or {}
    out.print(
        f"[bold]{rec['bout_id']}[/] {rec.get('status')}  "
        f"{rec.get('contender', {}).get('id')} × {rec.get('arena', {}).get('id')}"
    )
    out.print(
        f"model={rec.get('model')} turns={usage.get('num_turns')} "
        f"cost=${usage.get('total_cost_usd')} tokens={usage.get('tokens')}"
    )
    out.print(
        f"rss_peak_kb={res.get('rss_peak_kb')} threads_peak={res.get('threads_peak')} "
        f"fds_peak={res.get('fds_peak')} cpu_s={res.get('cpu_usage_s')}"
    )
    for r in rec.get("invalid_reasons") or []:
        out.print(f"[yellow]invalid:[/] {r}")
    if rec.get("error"):
        out.print(f"[red]error:[/] {rec['error']}")
    f = bout_dir / "findings.json"
    if f.exists():
        data = json.loads(f.read_text())
        for i, x in enumerate(data.get("findings") or [], 1):
            out.print(
                f"{i:>3}. [{x.get('severity')}] {x.get('title')}  "
                f"[dim]{x.get('file')}:{x.get('line_start')} {x.get('cwe') or ''}[/]"
            )


if __name__ == "__main__":
    app()
