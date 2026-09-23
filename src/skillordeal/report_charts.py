"""Standalone SVG charts for a round, shared by RESULTS.md (as files) and report.html (inline).

GitHub shows committed SVGs referenced from markdown, but it can't give them CSS variables,
so every chart has a transparent background and a palette that reads on both GitHub's light
(#ffffff) and dark (#0d1117) surfaces: mid-luminance series colors, neutral grey text, and
grid/axis lines drawn with opacity. report.html rewrites those colors to its own variables.

Output is deterministic: fixed hash salt, no dates or creator metadata, ids prefixed from the
chart's file name, bootstraps on the report seed.
"""

from __future__ import annotations

import html
import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillordeal.report import (
    BASELINE,
    COST,
    QUALITY,
    Cell,
    Est,
    Metric,
    _num,
    bootstrap,
    bootstrap_diff,
    fmt_est,
    fmt_value,
)

# Palette: dataviz reference dark-mode steps, validated (all pairs) against #ffffff and #0d1117.
S1 = "#3987e5"  # blue: contenders, tp / valid
S2 = "#d95926"  # orange: Pareto frontier, fp / invalid
S3 = "#199e70"  # aqua: dup
MUTED = "#898781"  # baseline, unknown / other
INK = "#767676"  # titles
INK2 = "#777777"  # labels, ticks
GRID = "#8b8b8b"  # drawn at GRID_ALPHA
AXIS = "#8c8c8c"  # drawn at AXIS_ALPHA
GRID_ALPHA = 0.3
AXIS_ALPHA = 0.6
FONT_STACK = 'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'
WIDTH = 7.6  # inches; every figure shares it so text scales alike

CPU = Metric("cpu_s", "CPU s", "cpu_s", "sec")
RSS = COST[4]


@dataclass
class ChartFile:
    name: str  # file name in charts/
    arena: str | None  # None: round-wide
    title: str
    caption: str  # what to read from it, and the n behind it
    svg: str  # full standalone document


# --- matplotlib -> svg ---------------------------------------------------------------------


class Chart:
    """Collects hover titles for one figure and serializes it deterministically."""

    def __init__(self, name: str) -> None:
        self.prefix = re.sub(r"[^A-Za-z0-9]+", "-", name.removesuffix(".svg")).strip("-") + "-"
        self.tips: dict[str, str] = {}

    def tip(self, artist: Any, text: str) -> None:
        gid = f"{self.prefix}tip{len(self.tips)}"
        artist.set_gid(gid)
        self.tips[gid] = text

    def svg(self, fig: Any, alt: str) -> str:
        import matplotlib.pyplot as plt

        buf = io.StringIO()
        fig.savefig(buf, format="svg", transparent=True, metadata={"Date": None, "Creator": None})
        plt.close(fig)
        s = buf.getvalue()
        s = re.sub(r"\s*<metadata>.*?</metadata>", "", s, flags=re.S)
        s = re.sub(r'id="([^"]+)"', lambda m: f'id="{self._id(m.group(1))}"', s)
        s = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{self._id(m.group(1))})", s)
        s = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{self._id(m.group(1))}"', s)
        s = s.replace("font-family: 'DejaVu Sans'", f"font-family: {FONT_STACK.replace('"', "'")}")
        for gid, text in self.tips.items():
            s = s.replace(
                f'<g id="{gid}">', f'<g id="{gid}" class="tip"><title>{html.escape(text)}</title>'
            )
        a = html.escape(alt)
        s = re.sub(r"(<svg [^>]*?)>", rf'\1 role="img" aria-label="{a}">', s, count=1)
        return s.replace("<defs>", f"<title>{a}</title>\n <defs>", 1)

    def _id(self, raw: str) -> str:
        return raw if raw.startswith(self.prefix) else f"{self.prefix}{raw}"


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
            "axes.edgecolor": (*_rgb(AXIS), AXIS_ALPHA),
            "axes.linewidth": 1,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.titlecolor": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.alpha": GRID_ALPHA,
            "grid.linewidth": 1,
            "xtick.color": INK2,
            "ytick.color": INK2,
            "xtick.labelcolor": INK2,
            "ytick.labelcolor": INK2,
            "legend.frameon": False,
            "legend.labelcolor": INK2,
        }
    )
    return plt


def _rgb(hexv: str) -> tuple[float, float, float]:
    return tuple(int(hexv[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _style(ax: Any, *, grid_axis: str) -> None:
    ax.grid(False)
    ax.grid(True, axis=grid_axis)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def _err(e: Est) -> list[list[float]]:
    if math.isnan(e.lo):
        return [[0.0], [0.0]]
    return [[max(0.0, e.mean - e.lo)], [max(0.0, e.hi - e.mean)]]


def _whisker(ax: Any, x: float, y: float, e: Est, *, horizontal: bool, color: str = INK2) -> None:
    if math.isnan(e.lo):
        return
    kw = {"xerr": _err(e)} if horizontal else {"yerr": _err(e)}
    ax.errorbar(
        x, y, **kw, fmt="none", color=color, ecolor=color, elinewidth=1, capsize=2.5, zorder=3
    )


# --- data --------------------------------------------------------------------------------------


@dataclass
class Point:
    """One contender (and model) pooled over tasks, in one arena or over the round."""

    contender: str
    model: str
    cells: list[Cell]

    @property
    def ok(self) -> list[dict[str, Any]]:
        return [b for c in self.cells for b in c.ok]

    @property
    def n_ok(self) -> int:
        return len(self.ok)

    @property
    def n(self) -> int:
        return sum(len(c.bouts) for c in self.cells)

    def values(self, column: str, scale: float = 1.0) -> list[float]:
        return [_num(b.get(column)) * scale for b in self.ok]

    def name(self, multi_model: bool) -> str:
        return f"{self.contender} · {self.model}" if multi_model else self.contender


def _points(cells: list[Cell]) -> list[Point]:
    by: dict[tuple[str, str], list[Cell]] = defaultdict(list)
    for c in cells:
        by[(c.contender, c.model)].append(c)
    pts = [Point(k[0], k[1], v) for k, v in by.items()]
    pts.sort(key=lambda p: (p.contender != BASELINE, p.contender, p.model))
    return pts


def _finite(xs: list[float]) -> bool:
    return any(not math.isnan(x) for x in xs)


def _quality(cells: list[Cell]) -> Metric | None:
    """TP where the arena has ground truth, else judge-valid, else nothing to chart."""
    for key in ("tp", "judge_valid"):
        m = next(x for x in QUALITY if x.key == key)
        if any(_finite(c.values(m)) for c in cells):
            return m
    return None


# (column, label, color) per verdict scheme, in stack order
SCHEMES: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "final verdict",
        [
            ("final_tp", "tp", S1),
            ("final_dup", "dup", S3),
            ("final_fp", "fp", S2),
            ("final_unknown", "unknown", MUTED),
        ],
    ),
    (
        "ground truth",
        [("tp", "tp", S1), ("dup", "dup", S3), ("fp", "fp", S2), ("unknown", "unknown", MUTED)],
    ),
    (
        "judge",
        [
            ("judge_valid", "valid", S1),
            ("judge_invalid", "invalid", S2),
            ("judge_unverifiable", "unverifiable", MUTED),
        ],
    ),
]
OTHER_ALPHA = 0.45  # "other" (findings no verdict column accounts for): MUTED, washed out


def verdict_scheme(rows: list[dict[str, Any]]) -> tuple[str, list[tuple[str, str, str]]] | None:
    """The first verdict scheme with a value in any of these ok bouts."""
    for label, parts in SCHEMES:
        if any(not math.isnan(_num(r.get(parts[0][0]))) for r in rows):
            return label, [p for p in parts if any(p[0] in r for r in rows)]
    return None


def _nlabel(pts: list[Point]) -> str:
    ok, n = sum(p.n_ok for p in pts), sum(p.n for p in pts)
    return f"n = {ok} ok bouts of {n}"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "arena"


# --- charts --------------------------------------------------------------------------------


def _frontier(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Points nobody else beats on both axes (cheaper-or-equal and better-or-equal)."""
    front = [p for p in pts if not any(o != p and o[0] <= p[0] and o[1] >= p[1] for o in pts)]
    return sorted(set(front))


def chart_quality_cost(
    arena: str, cells: list[Cell], multi: bool, seed: int, resamples: int
) -> ChartFile | None:
    q = _quality(cells)
    if q is None:
        return None
    rows = []
    for p in _points(cells):
        y = bootstrap(p.values(q.column), seed=seed, resamples=resamples)
        x = bootstrap(p.values("cost_usd"), seed=seed, resamples=resamples)
        if x.ok and y.ok:
            rows.append((p, x, y))
    if not rows:
        return None
    name = f"quality-vs-cost-{_slug(arena)}.svg"
    plt, ch = _plt(), Chart(name)
    fig, ax = plt.subplots(figsize=(WIDTH, 4.2))
    _style(ax, grid_axis="both")
    front = _frontier([(x.mean, y.mean) for _, x, y in rows])
    if len(front) > 1:
        ax.plot(
            *zip(*front, strict=True),
            color=S2,
            lw=2,
            solid_capstyle="round",
            zorder=2,
            label="Pareto frontier",
        )
    side = {id(r[0]): i % 2 for i, r in enumerate(sorted(rows, key=lambda r: r[1].mean))}
    seen: set[str] = set()
    for p, x, y in rows:
        base = p.contender == BASELINE
        color = MUTED if base else S1
        kind = "baseline" if base else "contender"
        ax.errorbar(
            x.mean,
            y.mean,
            xerr=_err(x),
            yerr=_err(y),
            fmt="none",
            color=color,
            ecolor=color,
            elinewidth=1,
            alpha=0.5,
            zorder=3,
        )
        dot = ax.scatter(
            [x.mean],
            [y.mean],
            s=70 if base else 60,
            color=color,
            zorder=4,
            marker="D" if base else "o",
            label=None if kind in seen else kind,
        )
        seen.add(kind)
        ch.tip(
            dot,
            f"{p.name(multi)}\n{q.label} {fmt_est(y, q.fmt)}\n"
            f"cost ${fmt_est(x, 'usd')} per bout\nn = {p.n_ok}/{p.n} bouts",
        )
        ax.annotate(
            p.name(multi),
            (x.mean, y.mean),
            textcoords="offset points",
            xytext=(7, 6) if side[id(p)] == 0 else (7, -13),
            fontsize=8.5,
            color=INK2,
        )
    ax.set_xlabel("mean cost per bout, $ (lower is better)")
    ax.set_ylabel(f"mean {q.label} per bout (higher is better)")
    ax.set_title(f"{arena}: {q.label} against cost")
    ax.margins(x=0.25, y=0.2)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    pts = [r[0] for r in rows]
    line = (
        f"the orange line joins contenders no one beats on both {q.label} and cost, "
        if len(front) > 1
        else ""
    )
    return ChartFile(
        name,
        arena,
        f"Quality vs cost, {arena}",
        f"Up and to the left is better; {line}whiskers are 95% CIs, gray is the baseline. "
        f"{_nlabel(pts)}.",
        ch.svg(fig, f"Scatter of mean {q.label} against mean cost per bout in {arena}"),
    )


def _hbars(
    ax: Any, ch: Chart, names: list[str], ests: list[Est], m: Metric, colors: list[str]
) -> None:
    ys = list(range(len(names)))
    for y, e, color, name in zip(ys, ests, colors, names, strict=True):
        if not e.ok:
            ax.text(0, y, "  n/a", va="center", fontsize=8, color=INK2)
            continue
        bar = ax.barh([y], [e.mean], height=0.55, color=color, zorder=2)
        ch.tip(bar.patches[0], f"{name}: {m.label} {fmt_est(e, m.fmt)} (n={e.n})")
        _whisker(ax, e.mean, y, e, horizontal=True)
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
    ax.set_ylim(len(names) - 0.5, -0.5)
    ax.margins(x=0.22)
    ax.set_xlim(left=0)
    _style(ax, grid_axis="x")


def _short(v: float) -> str:
    """Compact axis tick: 250k, 1.5M."""
    if abs(v) >= 1e6:
        return f"{v / 1e6:g}M"
    return f"{v / 1e3:g}k" if abs(v) >= 1e3 else f"{v:g}"


def _colors(pts: list[Point]) -> list[str]:
    return [MUTED if p.contender == BASELINE else S1 for p in pts]


def chart_recall(
    by_arena: dict[str, list[Cell]], multi: bool, seed: int, resamples: int
) -> ChartFile | None:
    recall = next(m for m in QUALITY if m.key == "recall")
    panels = []
    for arena, cells in by_arena.items():
        pts = _points(cells)
        ests = [bootstrap(p.values(recall.column), seed=seed, resamples=resamples) for p in pts]
        if any(e.ok for e in ests):
            panels.append((arena, pts, ests))
    if not panels:
        return None
    name = "recall-by-contender.svg"
    plt, ch = _plt(), Chart(name)
    rows_n = max(len(p[1]) for p in panels)
    fig, axes = plt.subplots(
        len(panels),
        1,
        figsize=(WIDTH, (0.34 * rows_n + 0.9) * len(panels)),
        squeeze=False,
        sharex=True,
    )
    for ax, (arena, pts, ests) in zip(axes[:, 0], panels, strict=True):
        _hbars(ax, ch, [p.name(multi) for p in pts], ests, recall, _colors(pts))
        ax.set_xlim(0, 1.12)  # recall is a share: keep the whole scale in view
        ax.set_title(arena)
    axes[-1, 0].set_xlabel("mean recall per bout (share of ground-truth issues found)")
    fig.tight_layout()
    all_pts = [p for _, pts, _ in panels for p in pts]
    return ChartFile(
        name,
        None,
        "Recall by contender",
        "Share of each arena's ground-truth issues a bout found, mean with 95% CI; gray is "
        f"the baseline. {_nlabel(all_pts)}.",
        ch.svg(fig, "Horizontal bars of mean recall per contender, one panel per arena"),
    )


def chart_breakdown(arena: str, cells: list[Cell], multi: bool) -> ChartFile | None:
    pts = [p for p in _points(cells) if p.n_ok]
    rows = [b for p in pts for b in p.ok]
    scheme = verdict_scheme(rows)
    if scheme is None or not rows:
        return None
    label, parts = scheme
    segs = [*parts, ("", "other", MUTED)]
    data: list[tuple[Point, list[float]]] = []  # per contender: mean per segment, other last
    for p in pts:
        means: list[float] = []
        for col, _, _ in parts:
            got = [x for x in p.values(col) if not math.isnan(x)]
            means.append(sum(got) / len(got) if got else 0.0)
        found = [x for x in p.values("findings") if not math.isnan(x)]
        total = sum(found) / len(found) if found else sum(means)
        data.append((p, [*means, max(0.0, total - sum(means))]))
    used = [i for i in range(len(segs)) if any(m[i] > 1e-9 for _, m in data)]
    xmax = max((sum(m) for _, m in data), default=0.0)
    if xmax <= 0:
        return None
    name = f"findings-breakdown-{_slug(arena)}.svg"
    plt, ch = _plt(), Chart(name)
    fig, ax = plt.subplots(figsize=(WIDTH, 0.36 * len(data) + 1.6))
    gap = xmax * 0.004
    for y, (p, means) in enumerate(data):
        left = 0.0
        for v, (_, seg, color) in zip(means, segs, strict=True):
            if v > 0:
                alpha = OTHER_ALPHA if seg == "other" else 1.0
                bar = ax.barh(
                    [y],
                    [max(v - gap, v * 0.5)],
                    left=left,
                    height=0.55,
                    color=color,
                    alpha=alpha,
                    zorder=2,
                )
                ch.tip(
                    bar.patches[0],
                    f"{p.name(multi)}: {seg} {fmt_value(v, 'num')} per bout (n={p.n_ok})",
                )
            left += v
        ax.annotate(
            fmt_value(left, "num"),
            (left, y),
            xytext=(5, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=INK2,
        )
    for i in used:
        _, seg, color = segs[i]
        ax.barh([0], [0], color=color, alpha=OTHER_ALPHA if seg == "other" else 1.0, label=seg)
    ax.set_yticks(range(len(data)), [p.name(multi) for p, _ in data])
    ax.set_ylim(len(data) - 0.5, -0.5)
    ax.set_xlim(0, xmax * 1.12)
    _style(ax, grid_axis="x")
    ax.grid(False)  # the washed-out "other" segment would show gridlines through it
    ax.set_xlabel(f"mean findings per ok bout, split by {label}")
    ax.set_title(f"{arena}: signal vs noise in findings")
    ax.legend(
        loc="lower right",
        bbox_to_anchor=(1.0, 1.0),
        ncol=len(used),
        fontsize=8,
        handlelength=1,
        handleheight=1,
        borderaxespad=0.2,
    )
    fig.tight_layout()
    return ChartFile(
        name,
        arena,
        f"Findings breakdown, {arena}",
        f"Mean findings per bout split by {label}; the blue share is signal, the rest is noise "
        f"or undecided. {_nlabel(pts)}.",
        ch.svg(fig, f"Stacked bars of findings per contender by {label} in {arena}"),
    )


def chart_cost_tokens(
    by_arena: dict[str, list[Cell]], multi: bool, seed: int, resamples: int
) -> ChartFile | None:
    tokens, cost = COST[1], COST[0]
    panels = []
    for arena, cells in by_arena.items():
        pts = _points(cells)
        te = [bootstrap(p.values(tokens.column), seed=seed, resamples=resamples) for p in pts]
        ce = [bootstrap(p.values(cost.column), seed=seed, resamples=resamples) for p in pts]
        if any(e.ok for e in te + ce):
            panels.append((arena, pts, te, ce))
    if not panels:
        return None
    has_t = any(e.ok for _, _, te, _ in panels for e in te)
    has_c = any(e.ok for _, _, _, ce in panels for e in ce)
    cols = [k for k, on in (("tokens", has_t), ("cost", has_c)) if on]
    name = "cost-tokens.svg"
    plt, ch = _plt(), Chart(name)
    rows_n = max(len(p[1]) for p in panels)
    fig, axes = plt.subplots(
        len(panels),
        len(cols),
        squeeze=False,
        sharey="row",
        figsize=(WIDTH, (0.34 * rows_n + 1.0) * len(panels)),
    )
    for r, (arena, pts, te, ce) in enumerate(panels):
        names = [p.name(multi) for p in pts]
        for ax, col in zip(axes[r], cols, strict=True):
            if col == "tokens":
                _hbars(ax, ch, names, te, tokens, _colors(pts))
                ax.set_title(f"{arena}: tokens per bout")
                ax.xaxis.set_major_formatter(lambda v, _: _short(v))
                ax.xaxis.set_major_locator(plt.MaxNLocator(4))
                continue
            _style(ax, grid_axis="x")
            for y, (e, color, nm) in enumerate(zip(ce, _colors(pts), names, strict=True)):
                if not e.ok:
                    continue
                _whisker(ax, e.mean, y, e, horizontal=True, color=color)
                dot = ax.scatter([e.mean], [y], s=60, color=color, zorder=4)
                ch.tip(dot, f"{nm}: cost ${fmt_est(e, 'usd')} per bout (n={e.n})")
                hi = e.hi if not math.isnan(e.hi) else e.mean
                ax.annotate(
                    fmt_value(e.mean, "usd"),
                    (hi, y),
                    xytext=(7, 0),
                    textcoords="offset points",
                    va="center",
                    fontsize=8,
                    color=INK2,
                )
            ax.set_yticks(range(len(names)), names)
            ax.set_ylim(len(names) - 0.5, -0.5)
            top = max((e.hi if not math.isnan(e.hi) else e.mean) for e in ce if e.ok)
            ax.set_xlim(0, (top or 1.0) * 1.3)
            ax.set_title("cost $ per bout")
    fig.tight_layout()
    all_pts = [p for _, pts, _, _ in panels for p in pts]
    return ChartFile(
        name,
        None,
        "Tokens and cost",
        "Mean tokens (bars) and cost (dots) per ok bout, one row per arena, whiskers 95% CI; "
        f"gray is the baseline. {_nlabel(all_pts)}.",
        ch.svg(fig, "Tokens and cost per bout per contender, one row per arena"),
    )


def chart_resources(cells: list[Cell], multi: bool, seed: int, resamples: int) -> ChartFile | None:
    pts = _points(cells)
    metrics = []
    for m in (RSS, CPU):
        ests = [bootstrap(p.values(m.column, m.scale), seed=seed, resamples=resamples) for p in pts]
        if any(e.ok for e in ests):
            metrics.append((m, ests))
    if not metrics:
        return None
    name = "resources.svg"
    plt, ch = _plt(), Chart(name)
    fig, axes = plt.subplots(
        1, len(metrics), squeeze=False, sharey=True, figsize=(WIDTH, 0.34 * len(pts) + 1.4)
    )
    names = [p.name(multi) for p in pts]
    for ax, (m, ests) in zip(axes[0], metrics, strict=True):
        _hbars(ax, ch, names, ests, m, _colors(pts))
        ax.set_title({"rss_peak_mb": "peak RSS, MB", "cpu_s": "CPU seconds"}[m.key])
    fig.tight_layout()
    what = " and ".join({"rss_peak_mb": "peak RSS", "cpu_s": "CPU time"}[m.key] for m, _ in metrics)
    return ChartFile(
        name,
        None,
        "Client resources",
        f"{what[0].upper()}{what[1:]} of the client harness per ok bout, pooled over arenas, "
        f"whiskers 95% CI (not model-side compute). {_nlabel(pts)}.",
        ch.svg(fig, f"{what[0].upper()}{what[1:]} per contender"),
    )


def chart_delta(
    arena: str, cells: list[Cell], multi: bool, seed: int, resamples: int
) -> ChartFile | None:
    q = _quality(cells)
    if q is None:
        return None
    pts = _points(cells)
    base = {p.model: p for p in pts if p.contender == BASELINE}
    rows = []
    for p in pts:
        b = base.get(p.model)
        if p.contender == BASELINE or b is None:
            continue
        d = bootstrap_diff(p.values(q.column), b.values(q.column), seed=seed, resamples=resamples)
        if d.ok:
            rows.append((p, b, d))
    if not rows:
        return None
    name = f"delta-vs-baseline-{_slug(arena)}.svg"
    plt, ch = _plt(), Chart(name)
    fig, ax = plt.subplots(figsize=(WIDTH, 0.36 * len(rows) + 1.4))
    _style(ax, grid_axis="x")
    ax.axvline(0, color=MUTED, lw=1.5, zorder=1)
    for y, (p, _, d) in enumerate(rows):
        _whisker(ax, d.mean, y, d, horizontal=True, color=S1)
        dot = ax.scatter([d.mean], [y], s=60, color=S1, zorder=4)
        ch.tip(dot, f"{p.name(multi)}: Δ {q.label} {fmt_est(d, 'num', True)} (n={d.n})")
        hi = d.hi if not math.isnan(d.hi) else d.mean
        ax.annotate(
            fmt_est(d, "num", True).split(" [")[0],
            (hi, y),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=INK2,
        )
    ax.set_yticks(range(len(rows)), [p.name(multi) for p, _, _ in rows])
    ax.set_ylim(len(rows) - 0.5, -0.5)
    lo = min(0.0, *(d.lo if not math.isnan(d.lo) else d.mean for _, _, d in rows))
    hi = max(0.0, *(d.hi if not math.isnan(d.hi) else d.mean for _, _, d in rows))
    pad = (hi - lo) * 0.25 or 0.5
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_xlabel(f"Δ mean {q.label} per bout, contender minus baseline")
    ax.set_title(f"{arena}: {q.label} against baseline")
    fig.tight_layout()
    pts_used = [p for p, _, _ in rows] + list({id(b): b for _, b, _ in rows}.values())
    return ChartFile(
        name,
        arena,
        f"Δ vs baseline, {arena}",
        f"Right of the line beats the baseline; a CI that crosses zero isn't a clear "
        f"difference. {_nlabel(pts_used)}.",
        ch.svg(fig, f"Dot plot of the difference in {q.label} from baseline in {arena}"),
    )


# --- entry points --------------------------------------------------------------------------


def build_charts(cells: list[Cell], *, seed: int, resamples: int) -> list[ChartFile]:
    """Every chart the data supports, in display order. Arena charts first, arena by arena."""
    by_arena: dict[str, list[Cell]] = defaultdict(list)
    for c in cells:
        by_arena[c.arena].append(c)
    multi = len({c.model for c in cells}) > 1
    out: list[ChartFile | None] = []
    for arena, cs in sorted(by_arena.items()):
        out += [
            chart_quality_cost(arena, cs, multi, seed, resamples),
            chart_breakdown(arena, cs, multi),
            chart_delta(arena, cs, multi, seed, resamples),
        ]
    ordered = dict(sorted(by_arena.items()))
    out += [
        chart_recall(ordered, multi, seed, resamples),
        chart_cost_tokens(ordered, multi, seed, resamples),
        chart_resources(cells, multi, seed, resamples),
    ]
    return [c for c in out if c is not None]


def write_charts(charts: list[ChartFile], root: Path) -> list[Path]:
    """Write into root/charts/, removing SVGs from earlier runs that this run didn't produce."""
    d = root / "charts"
    d.mkdir(parents=True, exist_ok=True)
    keep = {c.name for c in charts}
    for old in d.glob("*.svg"):
        if old.name not in keep:
            old.unlink()
    paths = []
    for c in charts:
        (d / c.name).write_text(c.svg)
        paths.append(d / c.name)
    return paths


# report.html: file colors -> page CSS variables
_HTML_COLORS = {
    S1: "var(--series-1)",
    S2: "var(--series-2)",
    S3: "var(--series-3)",
    MUTED: "var(--muted)",
    INK: "var(--ink)",
    INK2: "var(--ink-2)",
}


def inline_svg(c: ChartFile) -> str:
    """The chart as an element for report.html: theme variables, no fixed size, no prolog."""
    s = c.svg[c.svg.index("<svg") :]
    s = re.sub(rf"stroke: {GRID}; stroke-opacity: {GRID_ALPHA}", "stroke: var(--grid)", s)
    s = re.sub(rf"stroke: {AXIS}; stroke-opacity: {AXIS_ALPHA}", "stroke: var(--axis)", s)
    for hexv, var in _HTML_COLORS.items():
        s = s.replace(hexv, var)
    s = re.sub(r"font-family: [^;\"]+", "font-family: var(--font)", s)
    return re.sub(r'<svg ([^>]*?)width="[^"]+" height="[^"]+"', r"<svg \1", s, count=1)
