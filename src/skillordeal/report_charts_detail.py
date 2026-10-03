"""Detail charts: what each contender found, per-bout spread, efficiency and run health.

Same contract as report_charts (transparent background, colors that read on GitHub light and
dark, deterministic bytes, a hover title per mark, skipped when the data isn't there). The
extra colors are validated with the dataviz validator against #ffffff and #0d1117:

- SHARE_ALPHA: series-1 blue at three opacities for "how often" (coverage grids). Opacity,
  not fixed light-to-dark steps, so the faint end recedes toward the surface on both themes;
  the blended steps pass the ordinal checks on #ffffff and on #0d1117.
- SEV_COLORS: a 4-step opaque blue ramp for severity (no opacity ramp of 4 steps passes on
  both surfaces).
- TOOLS: the first five categorical slots in fixed order, plus MUTED for "other".
- STATUS_COLORS: the reserved status steps; ok recedes as MUTED so failures stand out.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from skillordeal.report import BASELINE, Cell, _num, fmt_value
from skillordeal.report_charts import (
    INK,
    INK2,
    MUTED,
    OTHER_ALPHA,
    S1,
    S2,
    WIDTH,
    Chart,
    ChartFile,
    _plt,
    _slug,
    _style,
)
from skillordeal.report_detail import (
    SEVERITIES,
    CoverageRow,
    Detail,
    agreement,
    clusters,
    coverage,
    quality_metric,
    severity_counts,
    tool_calls,
)

SHARE_ALPHA = (0.6, 0.8, 1.0)  # under half, half or more, every ok bout
RAMP4 = ("#86b6ef", "#4a8fe6", "#2563b5", "#184f95")  # low -> critical
EMPTY = "#8b8b8b"  # drawn at EMPTY_ALPHA: an empty cell that still reads as part of the grid
EMPTY_ALPHA = 0.14
# Categorical slots 1-5 (dark steps; pass adjacent CVD/normal checks on both surfaces).
TOOL_SLOTS = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181")
TOOL_ORDER = ("Read", "Grep", "Glob", "Bash", "Skill")
STATUS_COLORS = {
    "invalid": "#fab219",  # warning
    "schema_violation": "#ec835a",  # serious
    "timeout": "#d03b3b",  # critical
    "limit_exceeded": "#d03b3b",
    "error": "#d03b3b",
}
STATUS_ORDER = ("ok", "invalid", "schema_violation", "timeout", "limit_exceeded", "error")
SEV_COLORS = dict(zip(("critical", "high", "medium", "low"), reversed(RAMP4), strict=True))


def _rows_n(n_ok: int, n: int) -> str:
    return f"n = {n_ok} ok bouts of {n}"


def _n_cells(cells: list[Cell]) -> str:
    return _rows_n(sum(len(c.ok) for c in cells), sum(len(c.bouts) for c in cells))


def _share_alpha(k: int, n: int) -> float | None:
    """Opacity of a grid cell found in k of n ok bouts; None when never found."""
    if k <= 0 or n <= 0:
        return None
    if k >= n:
        return SHARE_ALPHA[2]
    return SHARE_ALPHA[1] if k / n >= 0.5 else SHARE_ALPHA[0]


def _legend_patches(ax: Any, items: list[tuple[str, str, float]], **kw: Any) -> None:
    import matplotlib.patches as mpatches

    handles = [mpatches.Patch(color=c, alpha=a, label=lab) for lab, c, a in items]
    ax.legend(handles=handles, fontsize=8, handlelength=1, handleheight=1, frameon=False, **kw)


# --- which issues / problems each contender found ----------------------------------------


def _matrix(
    name: str,
    arena: str,
    rows: list[CoverageRow],
    *,
    grouped: bool,
    title: str,
    label: str,
    what: str,
    alt: str,
    caption: str,
) -> ChartFile:
    """Rows x contenders grid, cells shaded by the share of ok bouts that found the row."""
    import matplotlib.patches as mpatches

    names = list(rows[0].hits)
    layout: list[tuple[str, CoverageRow | None]] = []  # (label, row); row None = group header
    if grouped:
        for sev in [*SEVERITIES, ""]:
            group = [r for r in rows if r.severity == sev]
            if group:
                layout.append(((sev or "unrated").upper() + f" ({len(group)})", None))
                layout += [(r.label, r) for r in group]
    else:
        layout = [(r.label, r) for r in rows]
    plt, ch = _plt(), Chart(name)
    ncol, nrow = len(names), len(layout) + 2  # + gap + "found in any bout" row
    fig, ax = plt.subplots(figsize=(WIDTH, 0.27 * nrow + 2.6))
    ax.grid(False)
    for side in ("left", "bottom", "top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    for y, (label, r) in enumerate(layout):
        if r is None:
            ax.text(-0.15, y + 0.62, label, ha="right", va="center", fontsize=7.5,
                    color=INK, fontweight="bold")  # fmt: skip
            continue
        for x, n in enumerate(names):
            k, reps = r.hits[n], r.reps[n]
            alpha = _share_alpha(k, reps)
            rect = mpatches.Rectangle(
                (x + 0.05, y + 0.08),
                0.9,
                0.84,
                facecolor=S1 if alpha else EMPTY,
                alpha=alpha or EMPTY_ALPHA,
                linewidth=0,
            )
            ax.add_patch(rect)
            tip = f"{n} · {r.label}: found in {k} of {reps} ok bouts"
            if r.title:
                tip += f"\n{r.title}"
            ch.tip(rect, tip)
        who = len(r.found_by)
        ax.text(ncol + 0.15, y + 0.5, f"{who}/{ncol}", ha="left", va="center", fontsize=8,
                color=INK2 if who else S2, fontweight="normal" if who else "bold")  # fmt: skip
    # Union over bouts per contender, under the grid.
    y = len(layout) + 1
    for x, n in enumerate(names):
        got = sum(1 for _, r in layout if r is not None and r.hits[n])
        ax.text(x + 0.5, y + 0.5, str(got), ha="center", va="center", fontsize=8.5, color=INK)
    ax.set_xlim(0, ncol)
    ax.set_ylim(nrow, 0)
    ax.set_yticks(
        [i + 0.5 for i in range(len(layout))] + [y + 0.5],
        [lab if r is not None else "" for lab, r in layout] + [f"{what} found in any bout"],
    )
    ax.xaxis.tick_top()
    ax.set_xticks([i + 0.5 for i in range(ncol)])
    ax.set_xticklabels(names, rotation=40, ha="left", rotation_mode="anchor", fontsize=8.5)
    ax.text(ncol + 0.15, -0.25, "contenders\nthat found it", ha="left", va="bottom",
            fontsize=7.5, color=INK2)  # fmt: skip
    ax.set_title(title, pad=12)
    _legend_patches(
        ax,
        [
            ("every ok bout", S1, SHARE_ALPHA[2]),
            ("half or more", S1, SHARE_ALPHA[1]),
            ("under half", S1, SHARE_ALPHA[0]),
            ("never", EMPTY, EMPTY_ALPHA * 2),
        ],
        loc="upper left",
        bbox_to_anchor=(0.0, 0.0),
        ncol=4,
    )
    fig.tight_layout()
    return ChartFile(name, arena, label, caption, ch.svg(fig, alt))


def chart_coverage(arena: str, cells: list[Cell], detail: Detail) -> ChartFile | None:
    rows = coverage(cells, detail, arena)
    if not rows:
        return None
    nobody = sum(1 for r in rows if not r.found_by)
    return _matrix(
        f"coverage-{_slug(arena)}.svg",
        arena,
        rows,
        grouped=True,
        title=f"{arena}: ground-truth issues found, by contender",
        label=f"Ground-truth coverage, {arena}",
        what="issues",
        alt=f"Grid of ground-truth issues by contender in {arena}, shaded by how often found",
        caption=(
            "Each row is a known issue (grouped by severity), each column a contender; darker "
            "means found in more of its ok bouts. The right column counts contenders that "
            f"found the issue at least once ({nobody} of {len(rows)} issues: none). "
            f"{_n_cells(cells)}."
        ),
    )


def chart_problems(arena: str, cells: list[Cell], detail: Detail) -> ChartFile | None:
    """Arenas without ground truth: the same grid over finding clusters."""
    if detail.issues.get(arena):
        return None
    rows = clusters(cells, detail, arena)
    if not rows:
        return None
    single = sum(1 for r in rows if len(r.found_by) == 1)
    return _matrix(
        f"problems-{_slug(arena)}.svg",
        arena,
        rows,
        grouped=False,
        title=f"{arena}: distinct problems reported, by contender",
        label=f"Problems reported, {arena}",
        what="problems",
        alt=f"Grid of finding clusters by contender in {arena}, shaded by how often reported",
        caption=(
            "No ground truth here, so rows are clusters of findings about the same code "
            "(file, lines ±5, CWE), labelled by their most common location and CWE. Darker "
            "means reported in more ok bouts; rows near the top are consensus, rows with 1 in "
            f"the right column are one contender's alone ({single} of {len(rows)}). "
            f"{_n_cells(cells)}."
        ),
    )


def chart_agreement(arena: str, cells: list[Cell], detail: Detail) -> ChartFile | None:
    rows = clusters(cells, detail, arena)
    ag = agreement(rows)
    if not ag or len(ag) < 2:
        return None
    segs = [
        ("found by every contender", MUTED, 1.0, "everyone"),
        ("by some others too", S1, 1.0, "some"),
        ("only this contender", S2, 1.0, "only"),
    ]
    name = f"agreement-{_slug(arena)}.svg"
    plt, ch = _plt(), Chart(name)
    fig, ax = plt.subplots(figsize=(WIDTH, 0.34 * len(ag) + 1.5))
    xmax = max(a.everyone + a.some + a.only for a in ag)
    gap = xmax * 0.004
    for y, a in enumerate(ag):
        left = 0.0
        for label, color, alpha, attr in segs:
            v = getattr(a, attr)
            if v:
                bar = ax.barh([y], [max(v - gap, v * 0.5)], left=left, height=0.55,
                              color=color, alpha=alpha, zorder=2)  # fmt: skip
                tip = f"{a.contender}: {v} problem{'s' if v != 1 else ''} {label}"
                if attr == "only":
                    tip += "\n" + "\n".join(f"- {r.label}: {r.title}" for r in a.only_rows)
                ch.tip(bar.patches[0], tip)
            left += v
        ax.annotate(str(int(left)), (left, y), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8, color=INK2)  # fmt: skip
    _legend_patches(
        ax,
        [(lab, c, al) for lab, c, al, attr in segs if any(getattr(a, attr) for a in ag)],
        loc="lower left",
        bbox_to_anchor=(0.0, 1.0),
        ncol=3,
        borderaxespad=0.2,
    )
    ax.set_yticks(range(len(ag)), [a.contender for a in ag])
    ax.set_ylim(len(ag) - 0.5, -0.5)
    ax.set_xlim(0, xmax * 1.1)
    _style(ax, grid_axis="x")
    ax.grid(False)
    ax.set_xlabel("distinct problems (finding clusters) reported in any ok bout")
    ax.set_title(f"{arena}: shared vs unique findings", pad=24)
    fig.tight_layout()
    return ChartFile(
        name,
        arena,
        f"Shared vs unique findings, {arena}",
        f"Distinct problems each contender reported across its bouts, split by how many other "
        f"contenders reported them too; orange is what only this contender found "
        f"({len(rows)} problems in all). {_n_cells(cells)}.",
        ch.svg(fig, f"Stacked bars of shared and unique problems per contender in {arena}"),
    )


# --- per-bout spread ---------------------------------------------------------------------


def _by_contender(cells: list[Cell]) -> list[tuple[str, list[dict[str, Any]], int]]:
    """(contender, ok bouts, all bouts), baseline first; contenders with no ok bout dropped."""
    by: dict[str, list[Cell]] = defaultdict(list)
    for c in cells:
        by[c.contender].append(c)
    out = []
    for name in sorted(by, key=lambda n: (n != BASELINE, n)):
        ok = [b for c in by[name] for b in c.ok]
        if ok:
            out.append(
                (
                    name,
                    sorted(ok, key=lambda b: _num(b.get("rep"))),
                    sum(len(c.bouts) for c in by[name]),
                )
            )
    return out


def _strip(ax: Any, ch: Chart, rows: list[tuple[str, list[float]]], label: str, fmt: str) -> None:
    """One dot per bout, jittered by rep along y, with the mean as a dark tick."""
    base = next((v for n, v in rows if n == BASELINE), None)
    if base:
        got = [x for x in base if not math.isnan(x)]
        if got:
            ax.axvline(sum(got) / len(got), color=MUTED, lw=1, alpha=0.8, zorder=1)
    for y, (name, vals) in enumerate(rows):
        color = MUTED if name == BASELINE else S1
        n = len(vals)
        offs = [0.0] if n == 1 else [-0.22 + 0.44 * i / (n - 1) for i in range(n)]
        for i, (v, dy) in enumerate(zip(vals, offs, strict=True)):
            if math.isnan(v):
                continue
            dot = ax.scatter([v], [y + dy], s=26, color=color, alpha=0.8, zorder=3, linewidths=0)
            ch.tip(dot, f"{name}, bout {i + 1} of {n}: {label} {fmt_value(v, fmt)}")
        got = [v for v in vals if not math.isnan(v)]
        if got:
            m = sum(got) / len(got)
            tick = ax.plot([m, m], [y - 0.34, y + 0.34], color=INK, lw=2, zorder=4,
                           solid_capstyle="butt")[0]  # fmt: skip
            ch.tip(tick, f"{name}: mean {label} {fmt_value(m, fmt)} (n={len(got)})")
    ax.set_ylim(len(rows) - 0.5, -0.5)
    _style(ax, grid_axis="x")
    ax.grid(False)  # the only vertical line is the baseline's mean
    ax.margins(x=0.12)


def chart_reps(arena: str, cells: list[Cell], multi: bool) -> ChartFile | None:
    del multi  # contenders are pooled over models only when there is one model
    data = _by_contender(cells)
    if not data:
        return None
    q = quality_metric(cells)
    panels: list[tuple[str, str, str, float]] = []  # (column, label, fmt, scale)
    if q is not None:
        panels.append((q.column, q.label, q.fmt, 1.0))
    panels += [
        ("findings", "findings", "num", 1.0),
        ("cost_usd", "cost $", "usd", 1.0),
        ("duration_s", "wall minutes", "num", 1 / 60),
    ]
    panels = [
        p for p in panels if any(not math.isnan(_num(b.get(p[0]))) for _, ok, _ in data for b in ok)
    ]
    if len(panels) < 2:
        return None
    name = f"reps-{_slug(arena)}.svg"
    plt, ch = _plt(), Chart(name)
    fig, axes = plt.subplots(
        1, len(panels), squeeze=False, sharey=True, figsize=(WIDTH, 0.3 * len(data) + 1.5)
    )
    for ax, (col, label, fmt, scale) in zip(axes[0], panels, strict=True):
        rows = [(n, [_num(b.get(col)) * scale for b in ok]) for n, ok, _ in data]
        _strip(ax, ch, rows, label, fmt)
        ax.set_title(label, fontsize=9)
        ax.xaxis.set_major_locator(plt.MaxNLocator(4, integer=col in ("tp", "findings")))
    axes[0, 0].set_yticks(range(len(data)), [n for n, _, _ in data])
    fig.suptitle(f"{arena}: every bout, not just the mean", x=0.01, ha="left",
                 fontsize=10, fontweight="bold", color=INK)  # fmt: skip
    fig.tight_layout()
    n_ok = sum(len(ok) for _, ok, _ in data)
    n_all = sum(n for _, _, n in data)
    return ChartFile(
        name,
        arena,
        f"Per-bout spread, {arena}",
        "Each dot is one ok bout, the dark tick is the mean and the gray line is the "
        "baseline's mean; dots far apart mean the contender is inconsistent from run to run. "
        f"{_rows_n(n_ok, n_all)}.",
        ch.svg(fig, f"Strip plots of per-bout quality, findings, cost and time in {arena}"),
    )


def chart_efficiency(arena: str, cells: list[Cell]) -> ChartFile | None:
    q = quality_metric(cells)
    data = _by_contender(cells)
    if q is None or not data:
        return None
    panels = [("cost_usd", f"$ per {q.label}", "usd", 1.0),
              ("duration_s", f"wall seconds per {q.label}", "sec", 1.0)]  # fmt: skip
    name = f"efficiency-{_slug(arena)}.svg"
    built = []
    for col, label, fmt, scale in panels:
        rows, pooled = [], {}
        for n, ok, _ in data:
            pairs = [(_num(b.get(col)) * scale, _num(b.get(q.column))) for b in ok]
            pairs = [(x, t) for x, t in pairs if not (math.isnan(x) or math.isnan(t))]
            rows.append((n, [x / t if t > 0 else math.nan for x, t in pairs]))
            if sum(t for _, t in pairs) > 0:
                pooled[n] = sum(x for x, _ in pairs) / sum(t for _, t in pairs)
        if pooled:
            built.append((label, fmt, rows, pooled))
    if not built:
        return None
    plt, ch = _plt(), Chart(name)
    fig, axes = plt.subplots(
        1, len(built), squeeze=False, sharey=True, figsize=(WIDTH, 0.3 * len(data) + 1.5)
    )
    for ax, (label, fmt, rows, pooled) in zip(axes[0], built, strict=True):
        _strip(ax, ch, rows, label, fmt)
        for y, (n, vals) in enumerate(rows):
            if n in pooled:
                right = max([pooled[n], *(v for v in vals if not math.isnan(v))])
                ax.annotate(fmt_value(pooled[n], fmt), (right, y), xytext=(7, 0),
                            textcoords="offset points", va="center", fontsize=7.5,
                            color=INK2)  # fmt: skip
        ax.set_title(f"{label} (lower is better)", fontsize=9)
        ax.xaxis.set_major_locator(plt.MaxNLocator(5))
    axes[0, 0].set_yticks(range(len(data)), [n for n, _, _ in data])
    fig.suptitle(f"{arena}: what one {q.label} costs", x=0.01, ha="left",
                 fontsize=10, fontweight="bold", color=INK)  # fmt: skip
    fig.tight_layout()
    return ChartFile(
        name,
        arena,
        f"Efficiency, {arena}",
        f"Dollars and wall-clock seconds per {q.label}: dots are single bouts (bouts with no "
        f"{q.label} are left out), the tick and number are the pooled ratio (total over "
        f"total). {_n_cells(cells)}.",
        ch.svg(fig, f"Cost and time per {q.label} per contender in {arena}"),
    )


# --- findings by severity ------------------------------------------------------------------


def chart_severity(arena: str, cells: list[Cell], detail: Detail) -> ChartFile | None:
    counts = severity_counts(detail, arena)
    data = [(n, ok) for n, ok, _ in _by_contender(cells)]
    if not counts or not data:
        return None
    sevs = [s for s in (*SEVERITIES, "other") if any(counts[n][s] for n, _ in data)]
    if not sevs:
        return None
    color = {**SEV_COLORS, "info": MUTED, "other": MUTED}
    alpha = {"info": 1.0, "other": OTHER_ALPHA}
    name = f"severity-{_slug(arena)}.svg"
    plt, ch = _plt(), Chart(name)
    fig, ax = plt.subplots(figsize=(WIDTH, 0.34 * len(data) + 1.5))
    means = [(n, {s: counts[n][s] / len(ok) for s in sevs}) for n, ok in data]
    xmax = max(sum(m.values()) for _, m in means) or 1.0
    gap = xmax * 0.004
    for y, (n, m) in enumerate(means):
        left = 0.0
        for s in sevs:
            v = m[s]
            if v > 0:
                bar = ax.barh([y], [max(v - gap, v * 0.5)], left=left, height=0.55,
                              color=color[s], alpha=alpha.get(s, 1.0), zorder=2)  # fmt: skip
                ch.tip(bar.patches[0], f"{n}: {fmt_value(v, 'num')} {s} findings per bout")
            left += v
        ax.annotate(fmt_value(left, "num"), (left, y), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8, color=INK2)  # fmt: skip
    _legend_patches(
        ax,
        [(s, color[s], alpha.get(s, 1.0)) for s in sevs],
        loc="lower left",
        bbox_to_anchor=(0.0, 1.0),
        ncol=len(sevs),
        borderaxespad=0.2,
    )
    ax.set_yticks(range(len(means)), [n for n, _ in means])
    ax.set_ylim(len(means) - 0.5, -0.5)
    ax.set_xlim(0, xmax * 1.1)
    _style(ax, grid_axis="x")
    ax.grid(False)
    ax.set_xlabel("mean findings per ok bout, by the severity the agent assigned")
    ax.set_title(f"{arena}: findings by severity", pad=24)
    fig.tight_layout()
    return ChartFile(
        name,
        arena,
        f"Findings by severity, {arena}",
        "Mean findings per bout stacked by self-reported severity, darkest is critical; a "
        "contender that rates the same issues higher shows more dark. "
        f"{_n_cells(cells)}.",
        ch.svg(fig, f"Stacked bars of findings per contender by severity in {arena}"),
    )


# --- round-wide: tools, skill loading, bout status -------------------------------------------


def chart_tools(cells: list[Cell], detail: Detail) -> ChartFile | None:
    data = []
    for n, ok, _ in _by_contender(cells):
        calls = tool_calls(detail, [str(b.get("bout_id")) for b in ok])
        if calls:
            turns = [x for x in (_num(b.get("turns")) for b in ok) if not math.isnan(x)]
            data.append((n, calls, sum(turns) / len(turns) if turns else math.nan))
    if not data:
        return None
    seen = Counter[str]()
    for _, calls, _ in data:
        for c in calls:
            seen.update(c)
    named = [t for t in TOOL_ORDER if seen[t]]
    others = sorted(t for t in seen if t not in named)
    segs = [(t, TOOL_SLOTS[TOOL_ORDER.index(t)], 1.0) for t in named]
    if others:
        label = "other (" + ", ".join(others[:3]) + (", …" if len(others) > 3 else "") + ")"
        segs.append((label, MUTED, OTHER_ALPHA))
    name = "tool-calls.svg"
    plt, ch = _plt(), Chart(name)
    fig, ax = plt.subplots(figsize=(WIDTH, 0.34 * len(data) + 1.5))
    rows = []
    for n, calls, turns in data:
        means = [sum(c.get(t, 0.0) for c in calls) / len(calls) for t in named]
        if others:
            means.append(sum(c.get(t, 0.0) for c in calls for t in others) / len(calls))
        rows.append((n, means, turns, len(calls)))
    xmax = max(sum(m) for _, m, _, _ in rows) or 1.0
    gap = xmax * 0.004
    for y, (n, means, turns, k) in enumerate(rows):
        left = 0.0
        for v, (seg, color, alpha) in zip(means, segs, strict=True):
            if v > 0:
                bar = ax.barh([y], [max(v - gap, v * 0.5)], left=left, height=0.55,
                              color=color, alpha=alpha, zorder=2)  # fmt: skip
                ch.tip(bar.patches[0], f"{n}: {fmt_value(v, 'num')} {seg} calls per bout (n={k})")
            left += v
        text = f"{left:.0f} calls" + (f" · {turns:.0f} turns" if not math.isnan(turns) else "")
        ax.annotate(text, (left, y), xytext=(5, 0), textcoords="offset points", va="center",
                    fontsize=8, color=INK2)  # fmt: skip
    _legend_patches(
        ax,
        [(s, c, a) for s, c, a in segs],
        loc="lower left",
        bbox_to_anchor=(0.0, 1.0),
        ncol=len(segs),
        borderaxespad=0.2,
    )
    ax.set_yticks(range(len(rows)), [n for n, *_ in rows])
    ax.set_ylim(len(rows) - 0.5, -0.5)
    ax.set_xlim(0, xmax * 1.25)
    _style(ax, grid_axis="x")
    ax.grid(False)
    ax.set_xlabel("mean tool calls per ok bout, pooled over arenas")
    ax.set_title("How each contender worked: tool calls and turns", pad=24)
    fig.tight_layout()
    return ChartFile(
        name,
        None,
        "Tool calls and turns",
        "Mean tool calls per bout by tool (from each bout's record.json), with mean agent "
        f"turns at the end of the bar, pooled over arenas. {_n_cells(cells)}.",
        ch.svg(fig, "Stacked bars of tool calls per bout by tool, per contender"),
    )


def chart_skill_load(by_arena: dict[str, list[Cell]]) -> ChartFile | None:
    panels = []
    for arena, cells in by_arena.items():
        pooled: dict[str, list[float]] = defaultdict(list)
        for c in cells:
            if c.contender != BASELINE and not math.isnan(c.injected):
                pooled[c.contender].append(c.injected)
        base = [
            x
            for c in cells
            if c.contender == BASELINE
            for x in (_num(b.get("first_turn_prompt_tokens")) for b in c.ok)
            if not math.isnan(x)
        ]
        if pooled:
            rows = [(n, sum(v) / len(v)) for n, v in sorted(pooled.items())]
            panels.append((arena, rows, sum(base) / len(base) if base else math.nan))
    if not panels:
        return None
    names = sorted({n for _, rows, _ in panels for n, _ in rows})
    name = "skill-load.svg"
    plt, ch = _plt(), Chart(name)
    fig, axes = plt.subplots(
        1, len(panels), squeeze=False, sharey=True, figsize=(WIDTH, 0.3 * len(names) + 1.6)
    )
    flagged = set()
    for ax, (arena, rows, base) in zip(axes[0], panels, strict=True):
        vals = dict(rows)
        ax.axvline(0, color=MUTED, lw=1.5, zorder=1)
        for y, n in enumerate(names):
            if n not in vals:
                continue
            v = vals[n]
            bad = v <= 0
            if bad:
                flagged.add(n)
            bar = ax.barh([y], [v], height=0.55, color=S2 if bad else S1, zorder=2)
            tip = f"{n} in {arena}: {v:+,.0f} first-turn prompt tokens vs baseline"
            ch.tip(bar.patches[0], tip + (" (skill may not have loaded)" if bad else ""))
            ax.annotate(f"{v:+,.0f}" + ("  ⚠ not loaded?" if bad else ""), (max(v, 0), y),
                        xytext=(5, 0), textcoords="offset points", va="center", fontsize=8,
                        color=INK2)  # fmt: skip
        lo = min(0.0, *vals.values())
        hi = max(0.0, *vals.values())
        ax.set_xlim(lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.3 + 1)
        ax.xaxis.set_major_formatter(lambda v, _: f"{v / 1e3:+g}k" if v else "0")
        ax.xaxis.set_major_locator(plt.MaxNLocator(4))
        sub = f"baseline {base:,.0f} tokens" if not math.isnan(base) else "no baseline bouts"
        ax.set_title(f"{arena} ({sub})", fontsize=9)
        _style(ax, grid_axis="x")
        ax.set_ylim(len(names) - 0.5, -0.5)
    axes[0, 0].set_yticks(range(len(names)), names)
    fig.suptitle("Did the skill load? First-turn prompt tokens minus the baseline's", x=0.01,
                 ha="left", fontsize=10, fontweight="bold", color=INK)  # fmt: skip
    fig.tight_layout()
    note = f"{len(flagged)} flagged (orange)" if flagged else "none at or below zero"
    return ChartFile(
        name,
        None,
        "Skill loading check",
        "How many more tokens each contender's first prompt carried than the baseline's "
        f"(mean over ok bouts). A loaded skill adds tokens; zero or less is flagged: {note}. "
        f"{_n_cells([c for cs in by_arena.values() for c in cs])}.",
        ch.svg(fig, "Bars of first-turn prompt tokens minus the baseline's, per contender"),
    )


def chart_status(cells: list[Cell]) -> ChartFile | None:
    by: dict[str, Counter[str]] = defaultdict(Counter)
    for c in cells:
        for b in c.bouts:
            by[c.contender][str(b.get("status") or "unknown")] += 1
    if all(set(v) <= {"ok"} for v in by.values()):
        return None  # every bout ok: nothing to show that the takeaways don't already say
    statuses = [s for s in STATUS_ORDER if any(v[s] for v in by.values())]
    statuses += sorted({s for v in by.values() for s in v} - set(STATUS_ORDER))
    color = {"ok": MUTED, **STATUS_COLORS}
    names = sorted(by, key=lambda n: (n != BASELINE, n))
    name = "bout-status.svg"
    plt, ch = _plt(), Chart(name)
    fig, ax = plt.subplots(figsize=(WIDTH, 0.3 * len(names) + 1.5))
    xmax = max(sum(v.values()) for v in by.values())
    gap = xmax * 0.004
    for y, n in enumerate(names):
        left = 0.0
        for s in statuses:
            v = by[n][s]
            if v:
                bar = ax.barh([y], [max(v - gap, v * 0.5)], left=left, height=0.55,
                              color=color.get(s, MUTED),
                              alpha=OTHER_ALPHA if s == "ok" else 1.0, zorder=2)  # fmt: skip
                ch.tip(bar.patches[0], f"{n}: {v} bout{'s' if v != 1 else ''} {s}")
            left += v
        total = sum(by[n].values())
        txt = f"{by[n]['ok']}/{total} ok"
        ax.annotate(txt, (left, y), xytext=(5, 0), textcoords="offset points", va="center",
                    fontsize=8, color=INK if by[n]["ok"] < total else INK2,
                    fontweight="bold" if by[n]["ok"] < total else "normal")  # fmt: skip
    _legend_patches(
        ax,
        [(s, color.get(s, MUTED), OTHER_ALPHA if s == "ok" else 1.0) for s in statuses],
        loc="lower left",
        bbox_to_anchor=(0.0, 1.0),
        ncol=len(statuses),
        borderaxespad=0.2,
    )
    ax.set_yticks(range(len(names)), names)
    ax.set_ylim(len(names) - 0.5, -0.5)
    ax.set_xlim(0, xmax * 1.18)
    ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    _style(ax, grid_axis="x")
    ax.grid(False)
    ax.set_xlabel("bouts, all arenas")
    ax.set_title("Bout status by contender", pad=24)
    fig.tight_layout()
    bad = sum(v for c in by.values() for s, v in c.items() if s != "ok")
    return ChartFile(
        name,
        None,
        "Bout status",
        f"How every bout ended; colored segments are the {bad} that did not finish ok and are "
        f"left out of every mean (reasons in the table at the end). {_n_cells(cells)}.",
        ch.svg(fig, "Stacked bars of bout status per contender"),
    )


def arena_charts(arena: str, cells: list[Cell], detail: Detail | None, multi: bool) -> list[Any]:
    """Detail charts for one arena, in display order (None where the data isn't there)."""
    out: list[Any] = [chart_reps(arena, cells, multi)]
    if detail is not None:
        out += [
            chart_coverage(arena, cells, detail),
            chart_problems(arena, cells, detail),
            chart_agreement(arena, cells, detail),
            chart_severity(arena, cells, detail),
        ]
    else:
        out += [None] * 4
    out.append(chart_efficiency(arena, cells))
    return out


def round_charts(by_arena: dict[str, list[Cell]], detail: Detail | None) -> list[Any]:
    cells = [c for cs in by_arena.values() for c in cs]
    out: list[Any] = [chart_status(cells), chart_skill_load(by_arena)]
    if detail is not None:
        out.append(chart_tools(cells, detail))
    return out
