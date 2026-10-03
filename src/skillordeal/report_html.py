"""report.html: one self-contained page. Charts are inlined SVG, tables sort in place.

The charts are the same files `report` writes to charts/ (see report_charts); their colors are
rewritten to CSS variables so they follow the page into dark mode. Each mark carries an SVG
<title> for a native hover tooltip; every charted value is also in a table on the page.
"""

from __future__ import annotations

import html
import math
import re
from collections import Counter
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from skillordeal.report import (
    BASELINE,
    COST,
    QUALITY,
    Cell,
    Context,
    Est,
    _bout_links,
    _groups,
    _has,
    fmt_est,
    fmt_value,
    injection_flag,
)
from skillordeal.report_charts import ChartFile, inline_svg
from skillordeal.report_detail import Link, join_parts
from skillordeal.rounddata import Round

if TYPE_CHECKING:
    from skillordeal.report_detail import Detail, Table, TCell

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


def _figure(c: ChartFile) -> str:
    return f"<figure>{inline_svg(c)}<figcaption>{html.escape(c.caption)}</figcaption></figure>"


def _inline_md(text: str) -> str:
    """The inline markdown the takeaways use: `code`, **bold** and [text](href)."""
    s = html.escape(text, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    return re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>',
        s,
    )


def _cell_html(v: TCell) -> str:
    if isinstance(v, str):
        return _inline_md(v)
    parts = [
        f'<a href="{html.escape(p.href)}">{html.escape(p.text)}</a>'
        if isinstance(p, Link)
        else _inline_md(p)
        for p in v
    ]
    return join_parts(parts, v)


def table_html(t: Table) -> str:
    rows = []
    for i, row in enumerate(t.rows):
        keys = t.sort[i] if i < len(t.sort) else []
        tds = []
        for j, v in enumerate(row):
            key = keys[j] if j < len(keys) else None
            tds.append(_td(_cell_html(v), key, "num" if j in t.numeric else ""))
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return _table(t.head, rows, t.title)


def _drilldown_html(cells: list[Cell], detail: Detail, arena: str) -> str:
    """Per contender: every finding of its ok bouts with verdicts, linked to the bout."""
    findings = detail.arena_findings(arena)
    if not findings:
        return ""
    by: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        by.setdefault(str(f["contender"]), []).append(f)
    reps = {str(b.get("bout_id")): b.get("rep") for c in cells for b in c.bouts}
    out = []
    for name in sorted(by, key=lambda n: (n != BASELINE, n)):
        fs = by[name]
        verdicts = Counter(str(f.get("verdict")) for f in fs)
        summary = ", ".join(f"{v} {k}" for k, v in sorted(verdicts.items()))
        bouts = len({f["bout_id"] for f in fs})
        rows = []
        for f in fs:
            bid = str(f.get("bout_id"))
            rep = reps.get(bid)
            loc = f"{f.get('file') or '?'}:{f.get('line_start') or '?'}"
            end = f.get("line_end")
            if end and end != f.get("line_start"):
                loc += f"-{end}"
            verdict = str(f.get("verdict") or "")
            src = f" ({f['source']})" if f.get("source") else ""
            link = f'<a href="bouts/{html.escape(bid)}/">#{html.escape(str(rep))}</a>'
            rows.append(
                "<tr>"
                + _td(link, _rep(rep))
                + _td(html.escape(str(f.get("severity") or "")), _sev(f.get("severity")))
                + _td(html.escape(str(f.get("cwe") or "")), str(f.get("cwe") or ""))
                + _td(f"<code>{html.escape(loc)}</code>", loc)
                + _td(html.escape(str(f.get("title") or "")), str(f.get("title") or ""))
                + _td(html.escape(verdict + src), verdict)
                + _td(html.escape(str(f.get("issue_id") or "")), str(f.get("issue_id") or ""))
                + _td(html.escape(str(f.get("judge") or "")), str(f.get("judge") or ""))
                + "</tr>"
            )
        table = _table(
            ["bout", "severity", "CWE", "location", "title", "verdict", "issue", "judge"],
            rows,
            f"{len(fs)} findings from {bouts} ok bouts",
        )
        out.append(
            f"<details><summary><strong>{html.escape(name)}</strong>: {len(fs)} findings "
            f"({html.escape(summary)})</summary>{table}</details>"
        )
    return "".join(out)


def _rep(rep: Any) -> float | str:
    try:
        return float(rep)
    except TypeError, ValueError:
        return str(rep)


def _sev(sev: Any) -> int:
    from skillordeal.report_detail import SEVERITIES

    return SEVERITIES.index(sev) if sev in SEVERITIES else len(SEVERITIES)


def render_html(
    ctx: Context,
    cells: list[Cell],
    rnd: Round,
    resamples: int,
    seed: int,
    charts: list[ChartFile] | None = None,
    detail: Detail | None = None,
) -> str:
    from skillordeal.report import _repro
    from skillordeal.report_detail import arena_tables, takeaways, tools_table

    esc = html.escape
    lk, img = ctx.lock, ctx.lock.get("image") or {}
    charts = charts or []
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
    bullets = takeaways(cells, detail, seed=seed, resamples=resamples)
    if bullets:
        body += [
            "<h2>Key takeaways</h2>",
            '<ul class="takeaways">' + "".join(f"<li>{_inline_md(b)}</li>" for b in bullets),
            "</ul>",
        ]
    body.append(
        f'<p class="note">Means over ok bouts with 95% bootstrap CIs ({resamples} resamples, '
        f"seed {seed}); no interval means n &lt; 2. Arena charts pool each contender over "
        "tasks; the tables keep them apart. Hover a mark for its numbers.</p>"
    )
    groups = _groups(cells)
    for arena in sorted({c.arena for c in cells}):
        body.append(f'<h2 id="arena-{esc(arena)}">{esc(arena)}</h2>')
        body += map(_figure, [c for c in charts if c.arena == arena])
        for (a, task, model), group in groups.items():
            if a == arena:
                body.append(f"<h3>Tables · {esc(task)} · <code>{esc(model)}</code></h3>")
                body.append(_group_html(group))
        if detail is not None:
            acells = [c for c in cells if c.arena == arena]
            tables = arena_tables(acells, detail, arena)
            if tables:
                body.append("<h3>What each contender found</h3>")
                body += map(table_html, tables)
            drill = _drilldown_html(acells, detail, arena)
            if drill:
                body += [
                    "<h3>Findings by contender</h3>",
                    '<p class="note">Every finding of the ok bouts, with its final verdict '
                    "(and where it came from), the ground-truth issue it matched and the judge's "
                    "verdict. Open a contender to see its list.</p>",
                    f'<div class="drill">{drill}</div>',
                ]
    overview = [c for c in charts if c.arena is None]
    tools = tools_table(cells, detail) if detail is not None else None
    if overview or tools:
        body += ["<h2>All arenas</h2>", *map(_figure, overview)]
        if tools:
            body.append(table_html(tools))
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
