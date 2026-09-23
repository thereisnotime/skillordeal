"""Turn a scored round into RESULTS.md, report.html and results.csv/parquet.

Input is `rounds/<round>/scores/summary.parquet` (falls back to summary.csv, then bouts.csv),
read with duckdb. Output numbers follow three rules:

- every cell says how many bouts it rests on (n ok / n total);
- bouts that didn't finish ok are counted and listed, never dropped silently;
- intervals are 95% percentile bootstrap CIs over reps with a fixed seed, so re-running the
  report gives the same numbers.

Needs the `analysis` extra (duckdb, numpy, pyarrow, matplotlib); imports are lazy so the rest
of the engine works without it.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skillordeal.rounddata import Round, read_yaml

BASELINE = "baseline"
DEFAULT_SEED = 20260923
DEFAULT_RESAMPLES = 2000


class ReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class Metric:
    key: str  # column in the output
    label: str
    column: str  # source column in summary/bouts
    fmt: str  # "int", "ratio", "usd", "tokens", "sec", "mb", "num"
    scale: float = 1.0


QUALITY = (
    Metric("findings", "findings", "findings", "num"),
    Metric("tp", "TP", "tp", "num"),
    Metric("precision", "precision", "precision", "ratio"),
    Metric("recall", "recall", "recall", "ratio"),
    Metric("f1", "F1", "f1", "ratio"),
    Metric("judge_valid", "judge-valid", "judge_valid", "num"),
)
COST = (
    Metric("cost_usd", "cost $", "cost_usd", "usd"),
    Metric("tokens", "tokens", "tokens_total", "tokens"),
    Metric("duration_s", "duration s", "duration_s", "sec"),
    Metric("turns", "turns", "turns", "num"),
    Metric("rss_peak_mb", "RSS peak MB", "rss_peak_kb", "mb", 1 / 1024),
)
METRICS = QUALITY + COST


# --- loading ---------------------------------------------------------------------------


def _deps() -> tuple[Any, Any]:
    try:
        import duckdb
        import numpy as np
    except ImportError as e:
        raise ReportError(
            f"report needs the analysis extra ({e.name} missing): uv sync --extra analysis"
        ) from e
    return duckdb, np


def load_rows(rnd: Round) -> tuple[list[dict[str, Any]], str]:
    """Rows of the best available per-bout table, and which file they came from."""
    duckdb, _ = _deps()
    candidates = [
        (rnd.scores / "summary.parquet", "select * from read_parquet(?)"),
        (rnd.scores / "summary.csv", "select * from read_csv_auto(?, header=true)"),
        (rnd.scores / "bouts.csv", "select * from read_csv_auto(?, header=true)"),
    ]
    for path, sql in candidates:
        if path.exists():
            cur = duckdb.connect().execute(sql, [str(path)])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()], path.name
    raise ReportError(
        f"no scores in {rnd.scores} (looked for summary.parquet, summary.csv, bouts.csv); "
        "run `skillordeal score` first"
    )


def _num(v: Any) -> float:
    if v is None or v == "" or isinstance(v, bool):
        return float(v) if isinstance(v, bool) else math.nan
    try:
        x = float(v)
    except TypeError, ValueError:
        return math.nan
    return x


def _truthy(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return None if math.isnan(float(v)) else bool(v)
    s = str(v).strip().lower()
    return {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}.get(s)


# --- stats -----------------------------------------------------------------------------


@dataclass
class Est:
    mean: float
    lo: float
    hi: float
    n: int

    @property
    def ok(self) -> bool:
        return self.n > 0 and not math.isnan(self.mean)


def bootstrap(values: list[float], *, seed: int, resamples: int) -> Est:
    """Mean and 95% percentile bootstrap CI. n < 2 gives no interval."""
    _, np = _deps()
    x = np.asarray([v for v in values if not math.isnan(v)], dtype=float)
    n = int(x.size)
    if n == 0:
        return Est(math.nan, math.nan, math.nan, 0)
    m = float(x.mean())
    if n < 2:
        return Est(m, math.nan, math.nan, n)
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, n, size=(resamples, n))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return Est(m, float(lo), float(hi), n)


def bootstrap_diff(a: list[float], b: list[float], *, seed: int, resamples: int) -> Est:
    """mean(a) - mean(b) with a CI from resampling both groups independently."""
    _, np = _deps()
    xa = np.asarray([v for v in a if not math.isnan(v)], dtype=float)
    xb = np.asarray([v for v in b if not math.isnan(v)], dtype=float)
    if not xa.size or not xb.size:
        return Est(math.nan, math.nan, math.nan, 0)
    m = float(xa.mean() - xb.mean())
    n = int(min(xa.size, xb.size))
    if xa.size < 2 or xb.size < 2:
        return Est(m, math.nan, math.nan, n)
    rng = np.random.default_rng(seed)
    da = xa[rng.integers(0, xa.size, size=(resamples, xa.size))].mean(axis=1)
    db = xb[rng.integers(0, xb.size, size=(resamples, xb.size))].mean(axis=1)
    lo, hi = np.percentile(da - db, [2.5, 97.5])
    return Est(m, float(lo), float(hi), n)


# --- aggregation -----------------------------------------------------------------------


@dataclass
class Cell:
    arena: str
    task: str
    model: str
    contender: str
    bouts: list[dict[str, Any]]
    est: dict[str, Est] = field(default_factory=dict)
    delta: dict[str, Est] = field(default_factory=dict)
    cost_per_tp: float = math.nan
    fired_rate: float = math.nan
    fired_n: int = 0
    injected: float = math.nan
    spent_usd: float = 0.0

    @property
    def group(self) -> tuple[str, str, str]:
        return (self.arena, self.task, self.model)

    @property
    def ok(self) -> list[dict[str, Any]]:
        return [b for b in self.bouts if b.get("status") == "ok"]

    @property
    def statuses(self) -> Counter[str]:
        return Counter(str(b.get("status") or "unknown") for b in self.bouts)

    def values(self, m: Metric) -> list[float]:
        return [_num(b.get(m.column)) * m.scale for b in self.ok]


def aggregate(rows: list[dict[str, Any]], *, seed: int, resamples: int) -> list[Cell]:
    by: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = tuple(str(r.get(k) or "") for k in ("arena", "task", "model", "contender"))
        by.setdefault(key, []).append(r)  # type: ignore[arg-type]
    cells = [Cell(*k, bouts=sorted(v, key=lambda b: _num(b.get("rep")))) for k, v in by.items()]
    for c in cells:
        for m in METRICS:
            c.est[m.key] = bootstrap(c.values(m), seed=seed, resamples=resamples)
        pairs = [
            (t, x)
            for t, x in zip(c.values(QUALITY[1]), c.values(COST[0]), strict=True)
            if not (math.isnan(t) or math.isnan(x))
        ]
        if pairs and sum(t for t, _ in pairs) > 0:
            c.cost_per_tp = sum(x for _, x in pairs) / sum(t for t, _ in pairs)
        fired = [_truthy(b.get("skill_fired")) for b in c.ok]
        fired = [f for f in fired if f is not None]
        c.fired_n = len(fired)
        if fired:
            c.fired_rate = sum(fired) / len(fired)
        c.spent_usd = sum(x for x in (_num(b.get("cost_usd")) for b in c.bouts) if x == x)

    base = {c.group: c for c in cells if c.contender == BASELINE}
    for c in cells:
        b = base.get(c.group)
        if not b or c is b:
            continue
        for key, m in (("tp", QUALITY[1]), ("f1", QUALITY[4]), ("cost_usd", COST[0])):
            c.delta[key] = bootstrap_diff(c.values(m), b.values(m), seed=seed, resamples=resamples)
        mine = [_num(x.get("first_turn_prompt_tokens")) for x in c.ok]
        theirs = [_num(x.get("first_turn_prompt_tokens")) for x in b.ok]
        mine, theirs = [x for x in mine if x == x], [x for x in theirs if x == x]
        if mine and theirs:
            c.injected = sum(mine) / len(mine) - sum(theirs) / len(theirs)

    cells.sort(key=lambda c: (c.group, c.contender != BASELINE, c.contender))
    return cells


# --- formatting ------------------------------------------------------------------------


def fmt_value(x: float, kind: str) -> str:
    if x is None or math.isnan(x):
        return "n/a"
    if kind == "ratio":
        return f"{x:.2f}"
    if kind == "usd":
        return f"{x:.3f}" if abs(x) < 10 else f"{x:.2f}"
    if kind == "tokens":
        ax = abs(x)
        return f"{x / 1e6:.2f}M" if ax >= 1e6 else f"{x / 1e3:.1f}k" if ax >= 1e3 else f"{x:.0f}"
    if kind in ("sec", "mb"):
        return f"{x:.0f}"
    return f"{x:.1f}" if abs(x - round(x)) > 1e-9 else f"{x:.0f}"


def fmt_est(e: Est, kind: str, signed: bool = False) -> str:
    if not e.ok:
        return "n/a"
    v = fmt_value(e.mean, kind)
    if signed and e.mean > 0:
        v = "+" + v
    if math.isnan(e.lo):
        return v
    return f"{v} [{fmt_value(e.lo, kind)}, {fmt_value(e.hi, kind)}]"


def _md(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _bout_links(c: Cell) -> str:
    parts = []
    for b in c.bouts:
        bid = str(b.get("bout_id") or "")
        rep = b.get("rep")
        label = f"#{int(_num(rep))}" if not math.isnan(_num(rep)) else bid
        mark = "" if b.get("status") == "ok" else f" ({b.get('status')})"
        parts.append(f"[{label}{mark}](bouts/{bid}/)")
    return " ".join(parts)


def _n(c: Cell) -> str:
    return f"{len(c.ok)}/{len(c.bouts)}"


def _groups(cells: list[Cell]) -> dict[tuple[str, str, str], list[Cell]]:
    out: dict[tuple[str, str, str], list[Cell]] = {}
    for c in cells:
        out.setdefault(c.group, []).append(c)
    return out


def _has(cells: list[Cell], key: str) -> bool:
    return any(c.est[key].ok for c in cells)


def injection_flag(c: Cell) -> str:
    if c.contender == BASELINE or math.isnan(c.injected):
        return ""
    return "skill may not have loaded" if c.injected <= 0 else ""


# --- context (header) ------------------------------------------------------------------


@dataclass
class Context:
    title: str
    question: str
    trial_id: str
    round: str
    lock: dict[str, Any]
    source: str
    trial_file: Path
    generated: str

    @property
    def models(self) -> list[str]:
        return [
            m["id"] + (f" (effort {m['effort']})" if m.get("effort") else "")
            for m in self.lock.get("models") or []
        ]


def _context(trial_file: Path, rnd: Round, source: str) -> Context:
    t = read_yaml(trial_file)
    return Context(
        title=str(t.get("title") or t.get("id") or trial_file.parent.name),
        question=str(t.get("question") or ""),
        trial_id=str(t.get("id") or ""),
        round=rnd.name,
        lock=read_yaml(rnd.lock),
        source=source,
        trial_file=trial_file,
        generated=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )


def _error_of(rnd: Round, bout_id: str) -> str:
    p = rnd.bouts / bout_id / "record.json"
    if not p.exists():
        return "no record.json"
    try:
        rec = json.loads(p.read_text())
    except json.JSONDecodeError:
        return "unreadable record.json"
    reasons = rec.get("invalid_reasons") or []
    return str(rec.get("error") or "; ".join(map(str, reasons)) or "")


def _repro(ctx: Context) -> str:
    eng = (ctx.lock.get("engine") or {}).get("version", "?")
    trial = f"trials/{ctx.trial_file.parent.name}/trial.yaml"
    return "\n".join(
        [
            "```bash",
            "git clone https://github.com/thereisnotime/skillordeal-trials",
            "cd skillordeal-trials",
            f"uv tool install git+https://github.com/thereisnotime/skillordeal@v{eng}",
            f"skillordeal run    {trial} -r {ctx.round}   # refuses to start on lock drift",
            f"skillordeal score  {trial} -r {ctx.round}",
            f"skillordeal judge  {trial} -r {ctx.round}   # optional, costs money",
            f"skillordeal report {trial} -r {ctx.round}",
            "```",
            "",
            "Finished bouts are skipped, so `run` on an existing round only fills gaps. "
            "For an independent reproduction, lock a new round from the same trial "
            "(`skillordeal lock ... -r r02`) and compare.",
        ]
    )


# --- markdown --------------------------------------------------------------------------


def render_markdown(ctx: Context, cells: list[Cell], rnd: Round, resamples: int, seed: int) -> str:
    lk, img = ctx.lock, ctx.lock.get("image") or {}
    judge = (lk.get("judge") or {}).get("id") if lk.get("judge") else None
    total = sum(len(c.bouts) for c in cells)
    ok = sum(len(c.ok) for c in cells)
    spent = sum(c.spent_usd for c in cells)
    lines = [
        f"# {_md(ctx.title)}: round {ctx.round}",
        "",
        f"> {_md(ctx.question)}" if ctx.question else "",
        "",
        "| | |",
        "|---|---|",
        f"| trial | `{ctx.trial_id}` |",
        f"| lock hash | `{lk.get('lock_hash', 'n/a')}` |",
        f"| engine | skillordeal {(lk.get('engine') or {}).get('version', 'n/a')} |",
        f"| agent CLI | {(lk.get('runtime') or {}).get('cli_name', 'claude-code')} "
        f"{img.get('cli_version', 'n/a')} |",
        f"| image | `{img.get('ref', 'n/a')}` id `{str(img.get('id') or 'n/a')[:12]}` "
        f"digest `{img.get('digest') or 'n/a'}` |",
        f"| models | {', '.join(f'`{m}`' for m in ctx.models) or 'n/a'} |",
        f"| judge | {f'`{judge}`' if judge else 'none'} |",
        f"| reps, invocation | {lk.get('reps', 'n/a')}, {lk.get('invocation', 'n/a')} |",
        f"| bouts | {ok} ok of {total}; ${spent:.2f} spent (client-side estimate) |",
        f"| scores from | `scores/{ctx.source}` |",
        f"| generated | {ctx.generated} |",
        "",
        f"Cells show the mean over ok bouts with a 95% bootstrap CI in brackets "
        f"({resamples} resamples, seed {seed}); no interval means n < 2. **n** is ok bouts "
        "over all bouts in the cell. Cost and resource numbers are per bout. "
        "Resource numbers describe the client harness, not model-side compute.",
        "",
    ]
    if ctx.source == "bouts.csv":
        lines += [
            "> **No quality scores yet.** Only `bouts.csv` was found, so TP, precision, recall "
            "and F1 are n/a. Run `skillordeal score` for ground-truth metrics.",
            "",
        ]
    for (arena, task, model), group in _groups(cells).items():
        lines += [f"## {arena} · {task} · `{model}`", ""]
        qm = [m for m in QUALITY if _has(group, m.key) or m.key in ("findings", "tp")]
        lines += [
            "**Quality**",
            "",
            "| contender | n | " + " | ".join(m.label for m in qm) + " | bouts |",
            "|---|---|" + "---:|" * len(qm) + "---|",
        ]
        for c in group:
            vals = " | ".join(fmt_est(c.est[m.key], m.fmt) for m in qm)
            lines.append(f"| {_md(c.contender)} | {_n(c)} | {vals} | {_bout_links(c)} |")
        lines += [
            "",
            "**Cost and resources**",
            "",
            "| contender | n | " + " | ".join(m.label for m in COST) + " | spent, all bouts |",
            "|---|---|" + "---:|" * (len(COST) + 1),
        ]
        for c in group:
            vals = " | ".join(fmt_est(c.est[m.key], m.fmt) for m in COST)
            lines.append(f"| {_md(c.contender)} | {_n(c)} | {vals} | ${c.spent_usd:.3f} |")
        lines += [
            "",
            "**Against baseline**",
            "",
            "| contender | Δ TP | Δ F1 | Δ cost $ | cost per TP $ | skill fired | "
            "first-turn tokens vs baseline | check |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        if not any(c.contender == BASELINE for c in group):
            lines.append("| (no baseline in this group) | | | | | | | |")
        for c in group:
            if c.contender == BASELINE:
                continue
            fired = f"{c.fired_rate:.0%} (n={c.fired_n})" if not math.isnan(c.fired_rate) else "n/a"
            inj = "n/a" if math.isnan(c.injected) else f"{c.injected:+,.0f}"
            flag = injection_flag(c)
            lines.append(
                f"| {_md(c.contender)} | {fmt_est(c.delta.get('tp', _NA), 'num', True)} "
                f"| {fmt_est(c.delta.get('f1', _NA), 'ratio', True)} "
                f"| {fmt_est(c.delta.get('cost_usd', _NA), 'usd', True)} "
                f"| {fmt_value(c.cost_per_tp, 'usd')} | {fired} | {inj} "
                f"| {'⚠ ' + flag if flag else 'ok' if inj != 'n/a' else ''} |"
            )
        lines.append("")

    bad = [(c, b) for c in cells for b in c.bouts if b.get("status") != "ok"]
    lines += ["## Bouts that did not finish ok", ""]
    if not bad:
        lines += ["None. Every bout finished with status `ok`.", ""]
    else:
        counts = Counter(str(b.get("status")) for _, b in bad)
        lines += [
            ", ".join(f"**{k}**: {v}" for k, v in sorted(counts.items()))
            + ". These are excluded from the means above but counted in **n** and in spend.",
            "",
            "| bout | contender | arena | task | model | rep | status | reason |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for c, b in bad:
            bid = str(b.get("bout_id"))
            lines.append(
                f"| [{bid}](bouts/{bid}/) | {_md(c.contender)} | {c.arena} | {c.task} "
                f"| `{c.model}` | {b.get('rep')} | {b.get('status')} "
                f"| {_md(_error_of(rnd, bid))[:200]} |"
            )
        lines.append("")
    lines += [
        "## How to reproduce",
        "",
        _repro(ctx),
        "",
        "## Reading the numbers",
        "",
        "- **TP / precision / recall / F1** come from `scores/` (human labels override ground "
        "truth, which overrides the judge). Recall is measured against the issues listed in "
        "the arena's ground truth. Unless that list is marked complete, unmatched findings are "
        "`unknown` rather than false positives, so precision is only a lower bound.",
        "- **Δ** columns are contender minus baseline in the same arena, task and model, with "
        "a CI from resampling both sides.",
        "- **cost per TP** is total cost of the ok bouts over their total TPs.",
        "- **first-turn tokens vs baseline** is this contender's mean first-turn prompt tokens "
        "minus the baseline's. A skill that loaded should add tokens; zero or less is flagged.",
        "- **skill fired** is the share of ok bouts where the skill was invoked (forced "
        "invocation counts as fired).",
        "- Cost is the CLI's client-side estimate (notional under OAuth).",
        "",
    ]
    return "\n".join(lines)


_NA = Est(math.nan, math.nan, math.nan, 0)


# --- exports ---------------------------------------------------------------------------


def export_rows(cells: list[Cell]) -> list[dict[str, Any]]:
    out = []
    for c in cells:
        row: dict[str, Any] = {
            "arena": c.arena,
            "task": c.task,
            "model": c.model,
            "contender": c.contender,
            "n_bouts": len(c.bouts),
            "n_ok": len(c.ok),
            **{f"n_{k}": v for k, v in sorted(c.statuses.items()) if k != "ok"},
        }
        for m in METRICS:
            e = c.est[m.key]
            row |= {m.key: _clean(e.mean), f"{m.key}_lo": _clean(e.lo), f"{m.key}_hi": _clean(e.hi)}
        for k in ("tp", "f1", "cost_usd"):
            e = c.delta.get(k, _NA)
            row |= {
                f"delta_{k}": _clean(e.mean),
                f"delta_{k}_lo": _clean(e.lo),
                f"delta_{k}_hi": _clean(e.hi),
            }
        row |= {
            "cost_per_tp": _clean(c.cost_per_tp),
            "skill_fired_rate": _clean(c.fired_rate),
            "first_turn_tokens_vs_baseline": _clean(c.injected),
            "injection_flag": injection_flag(c) or None,
            "spent_usd": round(c.spent_usd, 6),
            "bout_ids": " ".join(str(b.get("bout_id")) for b in c.bouts),
        }
        out.append(row)
    keys: list[str] = []
    for r in out:
        keys += [k for k in r if k not in keys]
    return [{k: r.get(k) for k in keys} for r in out]


def _clean(x: float) -> float | None:
    return None if x is None or math.isnan(x) else round(float(x), 6)


def write_exports(rows: list[dict[str, Any]], root: Path) -> list[Path]:
    csv_path = root / "results.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else [])
        w.writeheader()
        w.writerows(rows)
    written = [csv_path]
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return written
    pq.write_table(pa.Table.from_pylist(rows), root / "results.parquet")
    return [*written, root / "results.parquet"]


# --- entry point -----------------------------------------------------------------------


def build_report(
    trial_file: Path,
    rnd: Round,
    *,
    markdown_only: bool = False,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
) -> list[Path]:
    rows, source = load_rows(rnd)
    if not rows:
        raise ReportError(f"scores/{source} has no rows")
    cells = aggregate(rows, seed=seed, resamples=resamples)
    ctx = _context(trial_file.resolve(), rnd, source)
    md = render_markdown(ctx, cells, rnd, resamples, seed)
    rnd.root.mkdir(parents=True, exist_ok=True)
    (rnd.root / "RESULTS.md").write_text(md)
    written = [rnd.root / "RESULTS.md"]
    written += write_exports(export_rows(cells), rnd.root)
    if not markdown_only:
        from skillordeal.report_html import render_html

        (rnd.root / "report.html").write_text(render_html(ctx, cells, rnd, resamples, seed))
        written.append(rnd.root / "report.html")
    return written
