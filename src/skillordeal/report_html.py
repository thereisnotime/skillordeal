"""report.html: one self-contained page. Charts are matplotlib SVG inlined, tables sort in place.

Chart colors are drawn with the light palette and then rewritten to CSS variables, so the same
SVG follows the page into dark mode. Each mark carries an SVG <title> for a native hover tooltip;
every charted value is also in a table on the page.
"""

from __future__ import annotations

import html
import io
import math
import re
from collections import defaultdict
from importlib.resources import files
from typing import Any

from skillordeal.report import (
    BASELINE,
    COST,
    QUALITY,
    Cell,
    Context,
    Est,
    Metric,
    _bout_links,
    _groups,
    _has,
    bootstrap,
    fmt_est,
    fmt_value,
    injection_flag,
)
from skillordeal.rounddata import Round

# Light-mode hexes the charts are drawn with -> CSS variables defined in the page.
# Palette: dataviz reference instance (categorical slot 1, chrome and ink roles).
COLORS = {
    "#2a78d6": "var(--series-1)",
    "#eb6834": "var(--series-2)",
    "#898781": "var(--muted)",
    "#0b0b0b": "var(--ink)",
    "#52514e": "var(--ink-2)",
    "#e1e0d9": "var(--grid)",
    "#c3c2b7": "var(--axis)",
    "#fcfcfb": "var(--surface)",
}
S1, S2, MUTED, INK, INK2, GRID, AXIS, SURFACE = COLORS
# Every figure has the same width so text renders at one size once scaled to the page.
WIDTH = 7.6


# --- matplotlib -> inline svg ------------------------------------------------------------


class Chart:
    """Collects tooltips for one figure and turns it into theme-aware inline SVG."""

    _count = 0

    def __init__(self) -> None:
        Chart._count += 1
        self.prefix = f"c{Chart._count}"
        self.tips: dict[str, str] = {}

    def tip(self, artist: Any, text: str) -> None:
        gid = f"{self.prefix}tip{len(self.tips)}"
        artist.set_gid(gid)
        self.tips[gid] = text

    def svg(self, fig: Any, alt: str) -> str:
        import matplotlib.pyplot as plt

        buf = io.StringIO()
        fig.savefig(buf, format="svg", transparent=True, metadata={"Date": None})
        plt.close(fig)
        s = buf.getvalue()
        s = s[s.index("<svg") :]
        s = re.sub(r"<metadata>.*?</metadata>", "", s, flags=re.S)
        # unique ids per chart, since several SVGs share one document
        s = re.sub(r'id="([^"]+)"', lambda m: f'id="{self._id(m.group(1))}"', s)
        s = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{self._id(m.group(1))})", s)
        s = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{self._id(m.group(1))}"', s)
        for hexv, var in COLORS.items():
            s = re.sub(re.escape(hexv), var, s, flags=re.I)
        s = re.sub(r"font-family:[^;\"]+", "font-family: var(--font)", s)
        for gid, text in self.tips.items():
            s = s.replace(
                f'<g id="{gid}">', f'<g id="{gid}" class="tip"><title>{html.escape(text)}</title>'
            )
        s = re.sub(r'<svg ([^>]*?)width="[^"]+" height="[^"]+"', r"<svg \1", s, count=1)
        return s.replace("<svg ", f'<svg role="img" aria-label="{html.escape(alt)}" ', 1)

    def _id(self, raw: str) -> str:
        return raw if raw.startswith(self.prefix) else f"{self.prefix}-{raw}"


def _plt() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "svg.fonttype": "none",
            "svg.hashsalt": "skillordeal",
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "text.color": INK,
            "axes.labelcolor": INK2,
            "axes.edgecolor": AXIS,
            "axes.linewidth": 1,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.titlecolor": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 1,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelcolor": INK2,
            "ytick.labelcolor": INK2,
            "legend.frameon": False,
        }
    )
    return plt


def _style_axes(ax: Any, *, grid_axis: str) -> None:
    ax.grid(False)
    ax.grid(True, axis=grid_axis)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


# --- data for charts -----------------------------------------------------------------------


def _pooled(cells: list[Cell], seed: int, resamples: int) -> list[dict[str, Any]]:
    """One point per (contender, model), pooling every ok bout across arenas and tasks."""
    by: dict[tuple[str, str], list[Cell]] = defaultdict(list)
    for c in cells:
        by[(c.contender, c.model)].append(c)
    out = []
    for (contender, model), cs in by.items():
        vals: dict[str, Est] = {}
        for m in (*QUALITY, *COST):
            vals[m.key] = bootstrap(
                [v for c in cs for v in c.values(m)], seed=seed, resamples=resamples
            )
        out.append(
            {
                "contender": contender,
                "model": model,
                "est": vals,
                "n_ok": sum(len(c.ok) for c in cs),
                "n": sum(len(c.bouts) for c in cs),
            }
        )
    out.sort(key=lambda p: (p["contender"] != BASELINE, p["contender"], p["model"]))
    return out


def _quality_metric(cells: list[Cell]) -> Metric:
    for key in ("f1", "recall", "tp", "findings"):
        m = next(x for x in QUALITY if x.key == key)
        if _has(cells, key):
            return m
    return QUALITY[0]


def _name(p: dict[str, Any], multi_model: bool) -> str:
    return f"{p['contender']} · {p['model']}" if multi_model else p["contender"]


def _err(e: Est) -> tuple[float, float]:
    if math.isnan(e.lo):
        return (0.0, 0.0)
    return (max(0.0, e.mean - e.lo), max(0.0, e.hi - e.mean))


# --- charts --------------------------------------------------------------------------------


def chart_pareto(points: list[dict[str, Any]], q: Metric, multi_model: bool) -> str:
    plt = _plt()
    ch = Chart()
    pts = [p for p in points if p["est"][q.key].ok and p["est"]["cost_usd"].ok]
    fig, ax = plt.subplots(figsize=(WIDTH, 4.2))
    _style_axes(ax, grid_axis="both")
    # frontier: nobody else is at least as cheap and at least as good
    front = sorted(
        (
            p
            for p in pts
            if not any(
                o is not p
                and o["est"]["cost_usd"].mean <= p["est"]["cost_usd"].mean
                and o["est"][q.key].mean >= p["est"][q.key].mean
                and (
                    o["est"]["cost_usd"].mean < p["est"]["cost_usd"].mean
                    or o["est"][q.key].mean > p["est"][q.key].mean
                )
                for o in pts
            )
        ),
        key=lambda p: p["est"]["cost_usd"].mean,
    )
    if len(front) > 1:
        ax.plot(
            [p["est"]["cost_usd"].mean for p in front],
            [p["est"][q.key].mean for p in front],
            color=S2,
            lw=2,
            solid_capstyle="round",
            zorder=2,
            label="Pareto frontier",
        )
    seen_labels: set[str] = set()
    # alternate label sides along x so neighbours don't print on top of each other
    side = {
        id(p): i % 2 for i, p in enumerate(sorted(pts, key=lambda p: p["est"]["cost_usd"].mean))
    }
    for p in pts:
        x, y = p["est"]["cost_usd"], p["est"][q.key]
        base = p["contender"] == BASELINE
        color = MUTED if base else S1
        label = "baseline" if base else "contender"
        ax.errorbar(
            x.mean,
            y.mean,
            xerr=[[_err(x)[0]], [_err(x)[1]]],
            yerr=[[_err(y)[0]], [_err(y)[1]]],
            fmt="none",
            ecolor=color,
            elinewidth=1,
            alpha=0.45,
            zorder=3,
        )
        dot = ax.scatter(
            [x.mean],
            [y.mean],
            s=64,
            color=color,
            edgecolors=SURFACE,
            linewidths=2,
            zorder=4,
            label=None if label in seen_labels else label,
        )
        seen_labels.add(label)
        ch.tip(
            dot,
            f"{_name(p, multi_model)}\n{q.label} {fmt_est(y, q.fmt)}\n"
            f"cost ${fmt_est(x, 'usd')} per bout\nn = {p['n_ok']}/{p['n']} bouts",
        )
        ax.annotate(
            _name(p, multi_model),
            (x.mean, y.mean),
            xytext=(7, 6) if side[id(p)] == 0 else (7, -13),
            textcoords="offset points",
            fontsize=8.5,
            color=INK2,
        )
    ax.set_xlabel("mean cost per bout, $ (lower is better)")
    ax.set_ylabel(f"mean {q.label} (higher is better)")
    ax.set_title(f"Quality vs cost: {q.label} against $ per bout")
    ax.margins(x=0.25, y=0.2)
    if pts:
        ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    return ch.svg(fig, f"Scatter of mean {q.label} against mean cost per bout, per contender")


def _hbars(
    ax: Any, ch: Chart, names: list[str], ests: list[Est], m: Metric, colors: list[str]
) -> None:
    ys = list(range(len(names)))
    for y, e, color, name in zip(ys, ests, colors, names, strict=True):
        if not e.ok:
            ax.text(0, y, "  n/a", va="center", fontsize=8, color=MUTED)
            continue
        bar = ax.barh([y], [e.mean], height=0.55, color=color, zorder=2)
        ch.tip(bar.patches[0], f"{name}: {m.label} {fmt_est(e, m.fmt)} (n={e.n})")
        if not math.isnan(e.lo):
            ax.errorbar(
                e.mean,
                y,
                xerr=[[_err(e)[0]], [_err(e)[1]]],
                fmt="none",
                ecolor=INK2,
                elinewidth=1,
                capsize=2.5,
                zorder=3,
            )
        hi = e.hi if not math.isnan(e.hi) else e.mean
        ax.annotate(
            fmt_value(e.mean, m.fmt),
            (hi, y),
            xytext=(5, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=INK2,
        )
    ax.set_yticks(ys, names)
    ax.invert_yaxis()
    ax.set_title(m.label)
    ax.margins(x=0.22)
    ax.set_xlim(left=0)
    _style_axes(ax, grid_axis="x")


def chart_bars(
    points: list[dict[str, Any]], metrics: list[Metric], title: str, multi_model: bool
) -> str:
    plt = _plt()
    ch = Chart()
    names = [_name(p, multi_model) for p in points]
    colors = [MUTED if p["contender"] == BASELINE else S1 for p in points]
    fig, axes = plt.subplots(
        1,
        len(metrics),
        figsize=(WIDTH, 0.34 * len(points) + 1.3),
        sharey=True,
        squeeze=False,
    )
    for ax, m in zip(axes[0], metrics, strict=True):
        _hbars(ax, ch, names, [p["est"][m.key] for p in points], m, colors)
    fig.suptitle(title, x=0.01, ha="left", fontsize=10, fontweight="bold", color=INK)
    fig.tight_layout()
    return ch.svg(fig, title)


def chart_per_arena(cells: list[Cell], q: Metric) -> str:
    plt = _plt()
    ch = Chart()
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for c in cells:
        by[c.arena][c.contender] += c.values(q)
    arenas = sorted(by)
    contenders = sorted({c.contender for c in cells}, key=lambda n: (n != BASELINE, n))
    cols = min(3, len(arenas)) or 1
    rows = math.ceil(len(arenas) / cols) or 1
    fig, axes = plt.subplots(
        rows,
        cols,
        figsize=(WIDTH, (0.34 * len(contenders) + 1.0) * rows),
        sharey=True,
        sharex=True,
        squeeze=False,
    )
    for i, ax in enumerate(axes.flat):
        if i >= len(arenas):
            ax.set_visible(False)
            continue
        a = arenas[i]
        ests = [bootstrap(by[a].get(n, []), seed=0, resamples=1000) for n in contenders]
        colors = [MUTED if n == BASELINE else S1 for n in contenders]
        _hbars(ax, ch, contenders, ests, q, colors)
        ax.set_title(a)
    fig.suptitle(
        f"{q.label} per arena", x=0.01, ha="left", fontsize=10, fontweight="bold", color=INK
    )
    fig.tight_layout()
    return ch.svg(fig, f"{q.label} per arena, one panel per arena")


# --- page ------------------------------------------------------------------------------------


def _td(text: str, sort: float | str | None = None, cls: str = "") -> str:
    attr = ""
    if sort is not None and not (isinstance(sort, float) and math.isnan(sort)):
        attr = f' data-v="{html.escape(str(sort))}"'
    c = f' class="{cls}"' if cls else ""
    return f"<td{attr}{c}>{text}</td>"


def _est_td(e: Est, kind: str, signed: bool = False) -> str:
    if not e.ok:
        return _td('<span class="na">n/a</span>', None, "num")
    txt = fmt_est(e, kind, signed)
    main, _, ci = txt.partition(" [")
    ci_html = f' <span class="ci">[{html.escape(ci)}</span>' if ci else ""
    return _td(f"{html.escape(main)}{ci_html}", e.mean, "num")


def _links_html(c: Cell) -> str:
    md = _bout_links(c)
    return re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>',
        md,
    )


def _table(head: list[str], rows: list[str], caption: str) -> str:
    ths = "".join(
        f'<th scope="col"><button type="button">{html.escape(h)}</button></th>' for h in head
    )
    return (
        f'<div class="tbl"><table class="sortable"><caption>{html.escape(caption)}</caption>'
        f"<thead><tr>{ths}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _group_html(group: list[Cell]) -> str:
    qm = [m for m in QUALITY if _has(group, m.key) or m.key in ("findings", "tp")]
    q_rows, c_rows, d_rows = [], [], []
    for c in group:
        name = _td(html.escape(c.contender), c.contender, "name")
        n = _td(f"{len(c.ok)}/{len(c.bouts)}", len(c.ok), "num")
        q_rows.append(
            "<tr>"
            + name
            + n
            + "".join(_est_td(c.est[m.key], m.fmt) for m in qm)
            + _td(_links_html(c))
            + "</tr>"
        )
        c_rows.append(
            "<tr>"
            + name
            + n
            + "".join(_est_td(c.est[m.key], m.fmt) for m in COST)
            + _td(f"${c.spent_usd:.3f}", c.spent_usd, "num")
            + "</tr>"
        )
        if c.contender != BASELINE:
            na = Est(math.nan, math.nan, math.nan, 0)
            flag = injection_flag(c)
            fired = "n/a" if math.isnan(c.fired_rate) else f"{c.fired_rate:.0%} (n={c.fired_n})"
            inj = "n/a" if math.isnan(c.injected) else f"{c.injected:+,.0f}"
            check = (
                f'<span class="warn">⚠ {html.escape(flag)}</span>'
                if flag
                else ("ok" if inj != "n/a" else "")
            )
            d_rows.append(
                "<tr>"
                + name
                + _est_td(c.delta.get("tp", na), "num", True)
                + _est_td(c.delta.get("f1", na), "ratio", True)
                + _est_td(c.delta.get("cost_usd", na), "usd", True)
                + _td(fmt_value(c.cost_per_tp, "usd"), c.cost_per_tp, "num")
                + _td(fired, c.fired_rate, "num")
                + _td(inj, c.injected, "num")
                + _td(check)
                + "</tr>"
            )
    parts = [
        _table(
            ["contender", "n", *[m.label for m in qm], "bouts"], q_rows, "Quality, mean [95% CI]"
        ),
        _table(
            ["contender", "n", *[m.label for m in COST], "spent, all bouts"],
            c_rows,
            "Cost and resources per bout, mean [95% CI]",
        ),
    ]
    if d_rows:
        parts.append(
            _table(
                [
                    "contender",
                    "Δ TP",
                    "Δ F1",
                    "Δ cost $",
                    "cost per TP $",
                    "skill fired",
                    "first-turn tokens vs baseline",
                    "check",
                ],
                d_rows,
                "Against baseline (same arena, task, model)",
            )
        )
    return "".join(parts)


def _failed_html(cells: list[Cell], rnd: Round) -> str:
    from skillordeal.report import _error_of

    rows = []
    for c in cells:
        for b in c.bouts:
            if b.get("status") == "ok":
                continue
            bid = html.escape(str(b.get("bout_id")))
            rows.append(
                "<tr>"
                + _td(f'<a href="bouts/{bid}/">{bid}</a>', bid)
                + _td(html.escape(c.contender), c.contender)
                + _td(html.escape(c.arena), c.arena)
                + _td(html.escape(c.model), c.model)
                + _td(html.escape(str(b.get("rep"))), str(b.get("rep")))
                + _td(f'<span class="warn">{html.escape(str(b.get("status")))}</span>')
                + _td(html.escape(_error_of(rnd, str(b.get("bout_id")))[:300]))
                + "</tr>"
            )
    if not rows:
        return "<p>None. Every bout finished with status <code>ok</code>.</p>"
    return _table(
        ["bout", "contender", "arena", "model", "rep", "status", "reason"],
        rows,
        "Excluded from the means, counted in n and in spend",
    )


def _asset(name: str) -> str:
    return files("skillordeal").joinpath("report_assets", name).read_text()


def render_html(ctx: Context, cells: list[Cell], rnd: Round, resamples: int, seed: int) -> str:
    from skillordeal.report import _repro

    esc = html.escape
    lk, img = ctx.lock, ctx.lock.get("image") or {}
    points = _pooled(cells, seed, resamples)
    models = {c.model for c in cells}
    multi = len(models) > 1
    q = _quality_metric(cells)
    total = sum(len(c.bouts) for c in cells)
    ok = sum(len(c.ok) for c in cells)
    spent = sum(c.spent_usd for c in cells)
    judge = (lk.get("judge") or {}).get("id") if lk.get("judge") else None

    facts = [
        ("lock hash", f"<code>{esc(str(lk.get('lock_hash', 'n/a')))}</code>"),
        ("engine", esc(f"skillordeal {(lk.get('engine') or {}).get('version', 'n/a')}")),
        ("agent CLI", esc(f"claude-code {img.get('cli_version', 'n/a')}")),
        (
            "image",
            f"<code>{esc(str(img.get('ref', 'n/a')))}</code> "
            f"<code>{esc(str(img.get('digest') or 'n/a'))}</code>",
        ),
        ("models", ", ".join(f"<code>{esc(m)}</code>" for m in ctx.models) or "n/a"),
        ("judge", f"<code>{esc(judge)}</code>" if judge else "none"),
        ("reps, invocation", esc(f"{lk.get('reps', 'n/a')}, {lk.get('invocation', 'n/a')}")),
        ("bouts", esc(f"{ok} ok of {total}, ${spent:.2f} spent (client-side estimate)")),
        ("scores from", f"<code>scores/{esc(ctx.source)}</code>"),
        ("generated", esc(ctx.generated)),
    ]
    body = [
        f"<h1>{esc(ctx.title)}: round {esc(ctx.round)}</h1>",
        f'<p class="q">{esc(ctx.question)}</p>' if ctx.question else "",
        '<dl class="facts">' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>",
    ]
    if ctx.source == "bouts.csv":
        body.append(
            '<p class="banner">No quality scores yet: only <code>bouts.csv</code> was found, '
            "so TP, precision, recall and F1 are n/a. Run <code>skillordeal score</code>.</p>"
        )
    body.append(
        f'<p class="note">Means over ok bouts with 95% bootstrap CIs ({resamples} resamples, '
        f"seed {seed}); no interval means n &lt; 2. Charts pool every arena and task per "
        "contender; the tables below keep them apart. Hover a mark for its numbers.</p>"
    )
    body += [
        "<h2>Overview</h2>",
        f"<figure>{chart_pareto(points, q, multi)}<figcaption>Each dot is a contender, "
        f"whiskers are 95% CIs. Gray is the baseline; the orange line links contenders no one "
        f"else beats on both {esc(q.label)} and cost.</figcaption></figure>",
        f"<figure>{chart_bars(points, [COST[0], COST[1]], 'Cost and tokens per bout', multi)}"
        "<figcaption>Mean per ok bout, whiskers 95% CI.</figcaption></figure>",
        f"<figure>{chart_per_arena(cells, q)}"
        f"<figcaption>Mean {esc(q.label)} per ok bout in each arena.</figcaption></figure>",
        f"<figure>{chart_bars(points, [COST[4], COST[2], COST[3]], 'Client resources', multi)}"
        "<figcaption>Peak RSS of the client harness, wall time and turns per ok bout."
        "</figcaption></figure>",
    ]
    for (arena, task, model), group in _groups(cells).items():
        body.append(f"<h2>{esc(arena)} · {esc(task)} · <code>{esc(model)}</code></h2>")
        body.append(_group_html(group))
    body += [
        "<h2>Bouts that did not finish ok</h2>",
        _failed_html(cells, rnd),
        "<h2>How to reproduce</h2>",
        _md_to_html(_repro(ctx)),
    ]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="icon" href="data:,">'
        f"<title>{esc(ctx.title)} · {esc(ctx.round)}</title>"
        f"<style>{_asset('report.css')}</style></head>"
        f'<body><main class="wrap">{"".join(body)}</main>'
        f"<script>{_asset('report.js')}</script></body></html>\n"
    )


def _md_to_html(md: str) -> str:
    """Enough markdown for the reproduce snippet: one fenced block, then paragraphs."""
    out, code, buf = [], False, []
    for line in md.splitlines():
        if line.startswith("```"):
            if code:
                out.append(f"<pre>{html.escape(chr(10).join(buf))}</pre>")
                buf = []
            code = not code
            continue
        if code:
            buf.append(line)
        elif line.strip():
            text = html.escape(line)
            out.append("<p>" + re.sub(r"`([^`]+)`", r"<code>\1</code>", text) + "</p>")
    return "".join(out)
