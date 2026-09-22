"""Run a round: many bouts, locally with a worker pool or as one CI shard."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from skillordeal.bout import is_complete, run_bout
from skillordeal.config import LoadedTrial
from skillordeal.matrix import BoutKey
from skillordeal.secrets import Credentials

console = Console(stderr=True)


@dataclass
class RoundPaths:
    trial_dir: Path
    round: str

    @property
    def root(self) -> Path:
        return self.trial_dir / "rounds" / self.round

    @property
    def lock(self) -> Path:
        return self.root / "lock.yaml"

    @property
    def bouts(self) -> Path:
        return self.root / "bouts"

    def bout(self, bout_id: str) -> Path:
        return self.bouts / bout_id


def run_round(
    keys: list[BoutKey],
    lt: LoadedTrial,
    lock: dict[str, Any],
    creds: Credentials,
    paths: RoundPaths,
    *,
    jobs: int = 1,
    keep_work: bool = False,
    max_cost_usd: float | None = None,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    todo = [k for k in keys if not is_complete(paths.bout(k.bout_id))]
    skipped = len(keys) - len(todo)
    console.print(
        f"[bold]{len(keys)}[/] bouts, [green]{skipped}[/] already done, "
        f"[cyan]{len(todo)}[/] to run with -j {jobs}"
    )
    spent = 0.0
    tokens = 0
    results: list[dict[str, Any]] = []

    def one(k: BoutKey) -> dict[str, Any]:
        console.print(f"[dim]→ {k.bout_id}[/] {k.label()}")
        try:
            return run_bout(k, lt, lock, creds, paths.bout(k.bout_id), keep_work=keep_work)
        except Exception as e:  # keep the round going; record the failure
            out = paths.bout(k.bout_id)
            out.mkdir(parents=True, exist_ok=True)
            rec = {"bout_id": k.bout_id, "status": "error", "error": f"{type(e).__name__}: {e}"}
            (out / "record.json").write_text(json.dumps(rec, indent=2) + "\n")
            return rec

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(one, k): k for k in todo}  # not-yet-started ones can be cancelled
        for fut in as_completed(futures):
            if fut.cancelled():
                continue
            k, rec = futures[fut], fut.result()
            results.append(rec)
            cost = (rec.get("usage") or {}).get("total_cost_usd") or 0.0
            spent += cost
            tokens += ((rec.get("usage") or {}).get("tokens") or {}).get("total_tokens") or 0
            color = {"ok": "green", "invalid": "yellow"}.get(rec["status"], "red")
            console.print(
                f"[{color}]{rec['status']:>16}[/] {k.label()}  "
                f"findings={rec.get('findings_count', '-')} cost=${cost:.3f} total=${spent:.2f} "
                f"tokens={tokens:,}"
            )
            over = (max_cost_usd is not None and spent >= max_cost_usd) or (
                max_tokens is not None and tokens >= max_tokens
            )
            if over:
                console.print("[red]round ceiling reached (cost or tokens), cancelling the rest[/]")
                for f in futures:
                    f.cancel()
    return results


def round_status(keys: list[BoutKey], paths: RoundPaths) -> list[dict[str, Any]]:
    rows = []
    for k in keys:
        rec_path = paths.bout(k.bout_id) / "record.json"
        rec = json.loads(rec_path.read_text()) if rec_path.exists() else {}
        usage = rec.get("usage") or {}
        rows.append(
            {
                "bout_id": k.bout_id,
                "contender": k.contender,
                "arena": k.arena,
                "task": k.task,
                "model": k.model,
                "rep": k.rep,
                "status": rec.get("status", "pending"),
                "findings": rec.get("findings_count"),
                "cost_usd": usage.get("total_cost_usd"),
                "tokens": (usage.get("tokens") or {}).get("total_tokens"),
                "duration_s": (usage.get("duration_ms") or 0) / 1000 or None,
                "skill_fired": rec.get("skill_fired"),
                "rss_peak_mb": round(
                    ((rec.get("resources") or {}).get("rss_peak_kb") or 0) / 1024, 1
                )
                or None,
            }
        )
    return rows
