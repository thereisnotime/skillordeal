"""Finding-level detail for the report: which issues and problems each contender found.

`report` works from per-bout rows (summary/bouts.csv). The charts and tables that answer
"which vulnerabilities does this skill catch, and which does nobody catch" need the rows under
them: findings.jsonl, the final verdicts, the arena's ground-truth issue list and the tool
calls in each bout's record.json. This module loads those once, tolerating any of them being
absent, and derives the per-arena views plus the "Key takeaways" bullets of RESULTS.md.

Everything here is a pure function of files on disk and sorts explicitly, so the output is
deterministic.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skillordeal.report import (
    BASELINE,
    QUALITY,
    Cell,
    Metric,
    _num,
    bootstrap,
    bootstrap_diff,
    fmt_est,
    fmt_value,
)
from skillordeal.rounddata import Round, findings_from_bouts, read_jsonl, read_yaml

SEVERITIES = ("critical", "high", "medium", "low", "info")


@dataclass(frozen=True)
class Issue:
    id: str
    title: str
    severity: str  # one of SEVERITIES, or "" when the ground truth file isn't readable
    cwe: tuple[str, ...] = ()

    @property
    def rank(self) -> int:
        return SEVERITIES.index(self.severity) if self.severity in SEVERITIES else len(SEVERITIES)


@dataclass
class Detail:
    """Finding rows (ok bouts only) with their verdicts, ground-truth issues and tool calls."""

    findings: list[dict[str, Any]] = field(default_factory=list)
    issues: dict[str, list[Issue]] = field(default_factory=dict)  # arena -> issues
    records: dict[str, dict[str, Any]] = field(default_factory=dict)  # bout_id -> record.json

    def arena_findings(self, arena: str) -> list[dict[str, Any]]:
        return [f for f in self.findings if f.get("arena") == arena]


# --- loading -----------------------------------------------------------------------------


def _record(rnd: Round, bout_id: str) -> dict[str, Any]:
    try:
        data = json.loads((rnd.bouts / bout_id / "record.json").read_text())
    except OSError, json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _issue(raw: dict[str, Any]) -> Issue:
    cwe = raw.get("cwe") or []
    cwe = [cwe] if isinstance(cwe, str) else cwe
    sev = str(raw.get("severity") or "").lower()
    return Issue(
        id=str(raw["id"]),
        title=str(raw.get("title") or ""),
        severity=sev if sev in SEVERITIES else "",
        cwe=tuple(str(c) for c in cwe),
    )


def _groundtruth_issues(trial_file: Path) -> dict[str, list[Issue]]:
    """arena -> issues, read leniently: a broken trial or ground truth file means none."""
    try:
        from skillordeal.config import load_trial

        lt = load_trial(trial_file)
    except Exception:  # the report must not fail on config it only reads for labels
        return {}
    out: dict[str, list[Issue]] = {}
    for a in lt.arenas:
        if not a.groundtruth or a.id not in lt.arena_files:
            continue
        try:
            gt = read_yaml((lt.arena_files[a.id].parent / a.groundtruth).resolve())
        except Exception:
            continue
        issues = [_issue(i) for i in gt.get("issues") or [] if isinstance(i, dict) and i.get("id")]
        if issues:
            out[a.id] = issues
    return out


def _final(
    v: dict[str, Any] | None, gt: dict[str, Any] | None, judge: dict[str, Any] | None
) -> tuple[str, Any, str]:
    """(verdict, issue_id, source): verdicts.jsonl when scored, else ground truth, else judge."""
    if v and v.get("verdict"):
        return str(v["verdict"]), v.get("issue_id"), str(v.get("source") or "")
    if gt and gt.get("verdict") in ("tp", "dup", "fp"):
        return str(gt["verdict"]), gt.get("issue_id"), "gt"
    jv = (judge or {}).get("verdict")
    if jv in ("valid", "invalid"):
        return ("tp" if jv == "valid" else "fp"), None, "judge"
    if gt and gt.get("verdict"):
        return str(gt["verdict"]), gt.get("issue_id"), "gt"
    return "unknown", None, ""


def load_detail(trial_file: Path, rnd: Round, cells: list[Cell]) -> Detail:
    ok = {str(b.get("bout_id")): c for c in cells for b in c.ok}
    rows = read_jsonl(rnd.scores / "findings.jsonl")
    if not rows:
        try:
            rows = findings_from_bouts(rnd)
        except OSError, ValueError:  # a broken record.json: no finding detail, not a crash
            rows = []
    verdicts = {r.get("finding_id"): r for r in read_jsonl(rnd.scores / "verdicts.jsonl")}
    gt = {r.get("finding_id"): r for r in read_jsonl(rnd.scores / "gt_matches.jsonl")}
    judge = {r.get("finding_hash"): r for r in read_jsonl(rnd.scores / "judge.jsonl")}
    findings = []
    for r in rows:
        bid = str(r.get("bout_id") or "")
        if bid not in ok:
            continue
        fid = r.get("finding_id")
        g, j = gt.get(fid), judge.get(r.get("finding_hash"))
        verdict, issue_id, source = _final(verdicts.get(fid), g, j)
        c = ok[bid]
        findings.append(
            {
                **r,
                "contender": c.contender,
                "arena": c.arena,
                "task": c.task,
                "model": c.model,
                "verdict": verdict,
                "issue_id": issue_id or (g or {}).get("issue_id"),
                "source": source,
                "gt": (g or {}).get("verdict"),
                "judge": (j or {}).get("verdict"),
            }
        )
    findings.sort(key=_finding_order)
    issues = _groundtruth_issues(trial_file)
    # Arenas scored against ground truth whose yaml we couldn't read: the matched ids at least.
    for f in findings:
        a = f["arena"]
        if f.get("gt") and f.get("issue_id") and a not in issues:
            issues.setdefault(f"?{a}", [])
            known = {i.id for i in issues[f"?{a}"]}
            if f["issue_id"] not in known:
                issues[f"?{a}"].append(Issue(str(f["issue_id"]), "", ""))
    for key in [k for k in issues if k.startswith("?")]:
        issues[key[1:]] = sorted(issues.pop(key), key=lambda i: i.id)
    records = {}
    for c in cells:
        for b in c.bouts:
            bid = str(b.get("bout_id") or "")
            rec = _record(rnd, bid)
            records[bid] = {
                k: rec.get(k) for k in ("tool_calls", "status", "error", "invalid_reasons")
            }
    return Detail(findings=findings, issues=issues, records=records)


def _finding_order(f: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(f.get("arena") or ""),
        str(f.get("contender") or ""),
        _num(f.get("rep")),
        str(f.get("finding_id") or ""),
    )


# --- per-arena views ---------------------------------------------------------------------


def contenders_with_ok(cells: list[Cell]) -> list[str]:
    names = {c.contender for c in cells if c.ok}
    return sorted(names, key=lambda n: (n != BASELINE, n))


def ok_bouts(cells: list[Cell], contender: str) -> list[str]:
    return [str(b.get("bout_id")) for c in cells if c.contender == contender for b in c.ok]


def rep_label(b: dict[str, Any]) -> str:
    rep = _num(b.get("rep"))
    return f"#{int(rep)}" if not math.isnan(rep) else str(b.get("bout_id"))


def _labelled(cells: list[Cell], ids: set[str]) -> list[tuple[str, str]]:
    """(label, bout id) for these bouts, in rep order."""
    rows = [b for c in cells for b in c.bouts if str(b.get("bout_id")) in ids]
    rows.sort(key=lambda b: (_num(b.get("rep")), str(b.get("bout_id"))))
    return [(rep_label(b), str(b.get("bout_id"))) for b in rows]


@dataclass
class CoverageRow:
    """One ground-truth issue (or one cluster) against every contender with ok bouts."""

    key: str  # issue id or cluster id
    label: str  # what the chart shows
    severity: str
    hits: dict[str, int]  # contender -> ok bouts that found it
    reps: dict[str, int]  # contender -> ok bouts
    bouts: dict[str, list[tuple[str, str]]]  # contender -> (label "#rep", bout id) that found it
    title: str = ""
    cwe: str = ""

    @property
    def found_by(self) -> list[str]:
        return [c for c, k in self.hits.items() if k > 0]


def coverage(cells: list[Cell], detail: Detail, arena: str) -> list[CoverageRow]:
    """Issue x contender: how many ok bouts of each contender had a tp for the issue.

    Issues are sorted by severity, then by how many bouts found them (most first), then id.
    Empty when the arena has no ground truth, or when none of its findings was matched against
    it (score didn't run), since an all-empty grid would read as "nobody found anything".
    """
    issues = detail.issues.get(arena) or []
    names = contenders_with_ok(cells)
    matched = any(
        f.get("gt") or f.get("source") in ("gt", "human") for f in detail.arena_findings(arena)
    )
    if not issues or not names or not matched:
        return []
    reps = {n: len(ok_bouts(cells, n)) for n in names}
    found: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for f in detail.arena_findings(arena):
        if f.get("verdict") == "tp" and f.get("issue_id"):
            found[str(f["issue_id"])][f["contender"]].add(str(f["bout_id"]))
    prefix = f"{arena}-"
    rows = []
    for i in issues:
        by = found.get(i.id, {})
        rows.append(
            CoverageRow(
                key=i.id,
                label=i.id.removeprefix(prefix),
                severity=i.severity,
                hits={n: len(by.get(n, ())) for n in names},
                reps=reps,
                bouts={n: _labelled(cells, set(by.get(n, ()))) for n in names},
                title=i.title,
                cwe=", ".join(i.cwe),
            )
        )
    rank = {i.id: i.rank for i in issues}
    rows.sort(key=lambda r: (rank[r.key], -sum(r.hits.values()), r.key))
    return rows


def _cluster_label(members: list[dict[str, Any]]) -> tuple[str, str, str]:
    """(label, title, cwe) for a cluster: its most common file:line, CWE and title."""

    def top(values: list[str]) -> str:
        counts = Counter(v for v in values if v)
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if counts else ""

    file = top([str(f.get("file") or "") for f in members])
    starts = sorted(
        int(x) for f in members if not math.isnan(x := _num(f.get("line_start"))) and x > 0
    )
    line = f":{starts[len(starts) // 2]}" if starts else ""
    cwe = top([str(f.get("cwe") or "") for f in members])
    title = top([str(f.get("title") or "") for f in members])
    short = file.rsplit("/", 1)[-1] if len(file) > 28 else file
    return f"{short}{line}" + (f" {cwe}" if cwe else ""), title, cwe


def clusters(cells: list[Cell], detail: Detail, arena: str) -> list[CoverageRow]:
    """Cluster x contender (same shape as coverage): one row per distinct problem reported.

    Sorted by how many contenders found it (most first), then by bouts, then label.
    """
    names = contenders_with_ok(cells)
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in detail.arena_findings(arena):
        if f.get("cluster_id"):
            members[str(f["cluster_id"])].append(f)
    if not members or not names:
        return []
    reps = {n: len(ok_bouts(cells, n)) for n in names}
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    rows = []
    for cid, fs in members.items():
        by: dict[str, set[str]] = defaultdict(set)
        for f in fs:
            by[f["contender"]].add(str(f["bout_id"]))
        label, title, cwe = _cluster_label(fs)
        sevs = sorted({str(f.get("severity") or "") for f in fs}, key=lambda s: sev_rank.get(s, 9))
        rows.append(
            CoverageRow(
                key=cid,
                label=label,
                severity=sevs[0] if sevs else "",
                hits={n: len(by.get(n, ())) for n in names},
                reps=reps,
                bouts={n: _labelled(cells, set(by.get(n, ()))) for n in names},
                title=title,
                cwe=cwe,
            )
        )
    rows.sort(key=lambda r: (-len(r.found_by), -sum(r.hits.values()), r.label, r.key))
    return rows


@dataclass
class Agreement:
    contender: str
    everyone: int  # clusters every contender with ok bouts found
    some: int  # found by this contender and some, not all, others
    only: int  # found by this contender alone
    only_rows: list[CoverageRow]


def agreement(rows: list[CoverageRow]) -> list[Agreement]:
    if not rows:
        return []
    names = list(rows[0].hits)
    out = []
    for n in names:
        mine = [r for r in rows if r.hits.get(n)]
        every = [r for r in mine if len(r.found_by) == len(names)]
        only = [r for r in mine if r.found_by == [n]]
        out.append(Agreement(n, len(every), len(mine) - len(every) - len(only), len(only), only))
    return out


def severity_counts(detail: Detail, arena: str) -> dict[str, Counter[str]]:
    """contender -> severity -> findings (summed over ok bouts)."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for f in detail.arena_findings(arena):
        sev = str(f.get("severity") or "").lower()
        out[f["contender"]][sev if sev in SEVERITIES else "other"] += 1
    return out


def cwe_counts(detail: Detail, arena: str) -> dict[str, Counter[str]]:
    """CWE -> contender -> findings (summed over ok bouts); findings without a CWE skipped."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for f in detail.arena_findings(arena):
        if f.get("cwe"):
            out[str(f["cwe"])][f["contender"]] += 1
    return out


def tool_calls(detail: Detail, bout_ids: list[str]) -> list[dict[str, float]]:
    """Per bout: tool name -> calls, from record.json (bouts without the field are skipped)."""
    out = []
    for bid in bout_ids:
        calls = (detail.records.get(bid) or {}).get("tool_calls")
        if isinstance(calls, dict) and calls:
            out.append({str(k): float(v) for k, v in calls.items() if isinstance(v, (int, float))})
    return out


# --- takeaways ---------------------------------------------------------------------------


def quality_metric(cells: list[Cell]) -> Metric | None:
    """TP where the arena has ground truth, else judge-valid, else None (same as the charts)."""
    for key in ("tp", "judge_valid"):
        m = next(x for x in QUALITY if x.key == key)
        if any(not math.isnan(v) for c in cells for v in c.values(m)):
            return m
    return None


def _links(bouts: list[dict[str, Any]], *, arena: bool = False) -> str:
    """Markdown links to these bouts labelled by rep (and arena when asked), in order."""
    rows = sorted(
        bouts,
        key=lambda b: (str(b.get("arena") or ""), _num(b.get("rep")), str(b.get("bout_id"))),
    )
    return " ".join(
        f"[{(str(b.get('arena')) + ' ') if arena else ''}{rep_label(b)}](bouts/{b.get('bout_id')}/)"
        for b in rows
    )


def _names(items: list[str], limit: int = 6) -> str:
    shown = [f"`{x}`" for x in items[:limit]]
    if len(items) > limit:
        shown.append(f"{len(items) - limit} more")
    return ", ".join(shown)


@dataclass
class _Pooled:
    contender: str
    bouts: list[dict[str, Any]]

    def values(self, m: Metric) -> list[float]:
        return [_num(b.get(m.column)) * m.scale for b in self.bouts]


def _pooled(cells: list[Cell]) -> list[_Pooled]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cells:
        by[c.contender] += c.ok
    return [_Pooled(k, v) for k, v in sorted(by.items(), key=lambda kv: (kv[0] != BASELINE, kv[0]))]


def takeaways(cells: list[Cell], detail: Detail | None, *, seed: int, resamples: int) -> list[str]:
    """Short factual bullets (markdown) straight from the numbers. No interpretation."""
    out: list[str] = []
    total = sum(len(c.bouts) for c in cells)
    ok = sum(len(c.ok) for c in cells)
    by_contender: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cells:
        by_contender[c.contender] += c.bouts
    if ok == total:
        out.append(f"All {total} bouts finished `ok`.")
    else:
        parts = []
        for name, bouts in sorted(by_contender.items()):
            bad = [b for b in bouts if b.get("status") != "ok"]
            if not bad:
                continue
            st = Counter(str(b.get("status")) for b in bad)
            why = ", ".join(f"{v} {k}" for k, v in sorted(st.items()))
            n_ok = len(bouts) - len(bad)
            what = "no bout finished ok" if n_ok == 0 else f"{n_ok} of {len(bouts)} bouts ok"
            multi = len({str(b.get("arena")) for b in bad}) > 1
            parts.append(f"`{name}`: {what} ({why}; bouts {_links(bad, arena=multi)})")
        out.append(
            f"{ok} of {total} bouts finished `ok`; the rest are left out of every mean and "
            f"chart. " + "; ".join(parts) + "."
        )

    by_arena: dict[str, list[Cell]] = defaultdict(list)
    for c in cells:
        by_arena[c.arena].append(c)
    for arena, acells in sorted(by_arena.items()):
        out += _arena_takeaways(arena, acells, detail, seed=seed, resamples=resamples)

    inj = [(c.contender, c.arena, c.injected) for c in cells if not math.isnan(c.injected)]
    if inj:
        low = sorted({n for n, _, x in inj if x <= 0})
        if low:
            out.append(
                f"First-turn prompt tokens were not above the baseline's for {_names(low)}, "
                "so the skill may not have loaded."
            )
        else:
            lo, hi = min(x for *_, x in inj), max(x for *_, x in inj)
            out.append(
                f"Every skill contender's first-turn prompt was larger than the baseline's "
                f"(+{lo:,.0f} to +{hi:,.0f} tokens), as expected when the skill loads."
            )
    return out


def _arena_takeaways(
    arena: str, cells: list[Cell], detail: Detail | None, *, seed: int, resamples: int
) -> list[str]:
    out: list[str] = []
    q = quality_metric(cells)
    pooled = [p for p in _pooled(cells) if p.bouts]
    base = next((p for p in pooled if p.contender == BASELINE), None)
    if q is not None and pooled:
        ests = {p.contender: bootstrap(p.values(q), seed=seed, resamples=resamples) for p in pooled}
        ests = {k: e for k, e in ests.items() if e.ok}
        if ests:
            best = max(e.mean for e in ests.values())
            top = sorted(k for k, e in ests.items() if abs(e.mean - best) < 1e-9)
            line = f"**{arena}**: highest mean {q.label} per bout: " + ", ".join(
                f"`{k}` {fmt_est(ests[k], q.fmt)} (n={ests[k].n}; bouts "
                f"{_links(_bouts_of(pooled, k))})"
                for k in top
            )
            if base is not None and BASELINE in ests and BASELINE not in top:
                line += f"; baseline {fmt_est(ests[BASELINE], q.fmt)} (n={ests[BASELINE].n})"
            out.append(line + ".")
        if base is not None:
            out.append(_delta_line(arena, q, pooled, base, seed=seed, resamples=resamples))
    if q is not None:
        out += _cost_lines(arena, cells, q)
    if detail is not None:
        rows = coverage(cells, detail, arena)
        if rows:
            out.append(_coverage_line(arena, rows, detail))
        crow = clusters(cells, detail, arena)
        if crow:
            out.append(_unique_line(arena, crow))
    return [x for x in out if x]


def _bouts_of(pooled: list[_Pooled], name: str) -> list[dict[str, Any]]:
    return next((p.bouts for p in pooled if p.contender == name), [])


def _delta_line(
    arena: str, q: Metric, pooled: list[_Pooled], base: _Pooled, *, seed: int, resamples: int
) -> str:
    above, below, tested = [], [], 0
    for p in pooled:
        if p is base:
            continue
        d = bootstrap_diff(p.values(q), base.values(q), seed=seed, resamples=resamples)
        if not d.ok or math.isnan(d.lo):
            continue
        tested += 1
        if d.lo > 0:
            above.append(f"`{p.contender}` {fmt_est(d, q.fmt, True)}")
        elif d.hi < 0:
            below.append(f"`{p.contender}` {fmt_est(d, q.fmt, True)}")
    if not tested:
        return ""
    reps = sorted({len(p.bouts) for p in pooled})
    n = f"n={reps[0]}" if len(reps) == 1 else f"n={reps[0]} to {reps[-1]}"
    if not above and not below:
        return (
            f"**{arena}**: none of the {tested} contenders differs from the baseline on mean "
            f"{q.label} with a 95% CI that excludes 0 ({n} ok bouts each)."
        )
    parts = []
    if above:
        parts.append("above the baseline: " + ", ".join(above))
    if below:
        parts.append("below the baseline: " + ", ".join(below))
    return (
        f"**{arena}**: Δ {q.label} vs baseline with a 95% CI excluding 0 ({n} ok bouts each), "
        + "; ".join(parts)
        + f". The other {tested - len(above) - len(below)} contenders' CIs include 0."
    )


def _cost_lines(arena: str, cells: list[Cell], q: Metric) -> list[str]:
    per: dict[str, tuple[float, float, int]] = {}
    for p in _pooled(cells):
        pairs = [
            (t, x)
            for t, x in zip(p.values(q), [_num(b.get("cost_usd")) for b in p.bouts], strict=True)
            if not (math.isnan(t) or math.isnan(x))
        ]
        tp = sum(t for t, _ in pairs)
        if pairs and tp > 0:
            per[p.contender] = (sum(x for _, x in pairs) / tp, tp, len(pairs))
    if len(per) < 2:
        return []
    order = sorted(per.items(), key=lambda kv: (kv[1][0], kv[0]))
    (lo_n, lo), (hi_n, hi) = order[0], order[-1]
    line = (
        f"**{arena}**: lowest cost per {q.label}: `{lo_n}` ${fmt_value(lo[0], 'usd')} "
        f"({lo[1]:.0f} {q.label} over {lo[2]} bouts); highest: `{hi_n}` "
        f"${fmt_value(hi[0], 'usd')} ({hi[1]:.0f} over {hi[2]} bouts)"
    )
    if BASELINE in per and BASELINE not in (lo_n, hi_n):
        line += f"; baseline ${fmt_value(per[BASELINE][0], 'usd')}"
    return [line + "."]


def _coverage_line(arena: str, rows: list[CoverageRow], detail: Detail) -> str:
    n = len(rows)
    nobody = [r.label for r in rows if not r.found_by]
    names = list(rows[0].hits)
    always = [r.label for r in rows if all(r.hits[c] == r.reps[c] > 0 for c in names)]
    single = [(r.label, r.found_by[0], r.hits[r.found_by[0]]) for r in rows if len(r.found_by) == 1]
    found = n - len(nobody)
    line = f"**{arena}**: {found} of {n} ground-truth issues were found by at least one bout"
    if nobody:
        line += f"; no contender found {len(nobody)}: {_names(nobody, 12)}"
    if always:
        line += f". {len(always)} were found in every ok bout of every contender"
    if single:
        line += ". Found by one contender only: " + ", ".join(
            f"`{label}` (`{who}`, {k} bout{'s' if k != 1 else ''})" for label, who, k in single
        )
    return line + "."


def _unique_line(arena: str, rows: list[CoverageRow]) -> str:
    ag = [a for a in agreement(rows) if a.only]
    total = len(rows)
    if not ag:
        return (
            f"**{arena}**: every one of the {total} distinct problems (finding clusters) was "
            "reported by at least two contenders."
        )
    ag.sort(key=lambda a: (-a.only, a.contender))
    parts = [f"`{a.contender}` {a.only}" for a in ag]
    k = sum(a.only for a in ag)
    return (
        f"**{arena}**: {k} of {total} distinct problems (finding clusters) "
        f"{'was' if k == 1 else 'were'} reported by a single contender: " + ", ".join(parts) + "."
    )


# --- tables (rendered as markdown by report.py and as HTML by report_html.py) -------------


@dataclass(frozen=True)
class Link:
    text: str
    href: str


# A table cell: plain text, or a run of text and links shown side by side.
TCell = str | list[str | Link]


@dataclass
class Table:
    title: str  # <details> summary / html caption
    head: list[str]
    rows: list[list[TCell]]
    numeric: tuple[int, ...] = ()  # right-aligned column indexes
    sort: list[list[float | str | None]] = field(default_factory=list)  # html sort keys


SEP = ";"  # separator between contenders in a "found by" cell

# Above this many bouts behind one row the row shows counts instead of one link per bout.
LINKS_UP_TO = 12


def _who(row: CoverageRow) -> list[str | Link]:
    """Who found it: 'everyone' or per contender k/n, with bout links while short."""
    names = list(row.hits)
    if all(row.hits[n] == row.reps[n] > 0 for n in names):
        return ["every contender, every ok bout"]
    found = [n for n in names if row.hits[n]]
    links = sum(row.hits[n] for n in found) <= LINKS_UP_TO
    out: list[str | Link] = []
    for i, n in enumerate(found):
        if i:
            out.append(SEP)
        out.append(f"{n} {row.hits[n]}/{row.reps[n]}")
        if links:
            out += [Link(label, f"bouts/{b}/") for label, b in row.bouts[n]]
    return out or ["nobody"]


def join_parts(parts: list[str], kinds: list[str | Link]) -> str:
    """Join rendered parts with spaces, except before a separator."""
    out = ""
    for text, kind in zip(parts, kinds, strict=True):
        out += text if (not out or kind is SEP) else " " + text
    return out


def coverage_table(rows: list[CoverageRow], *, what: str, title: str) -> Table:
    names = list(rows[0].hits) if rows else []
    body: list[list[TCell]] = []
    sort: list[list[float | str | None]] = []
    for r in rows:
        k = len(r.found_by)
        body.append([f"`{r.label}`", r.title, r.severity or "n/a", r.cwe, f"{k}/{len(names)}",
                     _who(r)])  # fmt: skip
        sev = SEVERITIES.index(r.severity) if r.severity in SEVERITIES else 9
        sort.append([r.label, r.title, sev, r.cwe, k, sum(r.hits.values())])
    head = [what, "title", "severity", "CWE", "contenders", "found by (ok bouts that found it)"]
    return Table(title, head, body, numeric=(4,), sort=sort)


def severity_table(cells: list[Cell], detail: Detail, arena: str) -> Table | None:
    counts = severity_counts(detail, arena)
    names = contenders_with_ok(cells)
    sevs = [s for s in (*SEVERITIES, "other") if any(counts[n][s] for n in names)]
    if not sevs or not names:
        return None
    body: list[list[TCell]] = []
    sort: list[list[float | str | None]] = []
    for n in names:
        k = len(ok_bouts(cells, n))
        vals = [counts[n][s] / k for s in sevs]
        body.append([n, str(k), *(fmt_value(v, "num") for v in vals)])
        sort.append([n, k, *vals])
    head = ["contender", "ok bouts", *sevs]
    numeric = tuple(range(1, len(head)))
    return Table("Findings per bout by severity", head, body, numeric=numeric, sort=sort)


def cwe_table(cells: list[Cell], detail: Detail, arena: str) -> Table | None:
    counts = cwe_counts(detail, arena)
    if not counts:
        return None
    names = contenders_with_ok(cells)
    order = sorted(counts.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    body: list[list[TCell]] = []
    sort: list[list[float | str | None]] = []
    for cwe, by in order:
        total = sum(by.values())
        who = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))
        body.append(
            [cwe, str(total), f"{len(by)}/{len(names)}", ", ".join(f"{n} {v}" for n, v in who)]
        )
        sort.append([cwe, total, len(by), None])
    return Table(
        f"Findings by CWE ({len(order)} CWEs)",
        ["CWE", "findings", "contenders", "findings per contender (all ok bouts)"],
        body,
        numeric=(1, 2),
        sort=sort,
    )


def tools_table(cells: list[Cell], detail: Detail) -> Table | None:
    per: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cells:
        per[c.contender] += c.ok
    rows = []
    tools: Counter[str] = Counter()
    for name in sorted(per, key=lambda n: (n != BASELINE, n)):
        calls = tool_calls(detail, [str(b.get("bout_id")) for b in per[name]])
        if not calls:
            continue
        turns = [x for x in (_num(b.get("turns")) for b in per[name]) if not math.isnan(x)]
        for c in calls:
            tools.update(c)
        rows.append((name, calls, sum(turns) / len(turns) if turns else math.nan))
    if not rows:
        return None
    names = sorted(tools, key=lambda t: (-tools[t], t))
    body: list[list[TCell]] = []
    sort: list[list[float | str | None]] = []
    for name, calls, turns in rows:
        means = [sum(c.get(t, 0.0) for c in calls) / len(calls) for t in names]
        body.append(
            [name, str(len(calls)), fmt_value(turns, "num"), *(fmt_value(m, "num") for m in means)]
        )
        sort.append([name, len(calls), turns, *means])
    head = ["contender", "bouts", "turns", *names]
    return Table(
        "Tool calls per bout (mean over ok bouts, all arenas)",
        head,
        body,
        numeric=tuple(range(1, len(head))),
        sort=sort,
    )


def arena_tables(cells: list[Cell], detail: Detail, arena: str) -> list[Table]:
    out: list[Table] = []
    cov = coverage(cells, detail, arena)
    if cov:
        nobody = sum(1 for r in cov if not r.found_by)
        title = f"Ground-truth issues: who found what ({len(cov)} issues, {nobody} found by nobody)"
        out.append(coverage_table(cov, what="issue", title=title))
    cl = clusters(cells, detail, arena)
    if cl:
        single = sum(1 for r in cl if len(r.found_by) == 1)
        title = (
            f"Distinct problems (finding clusters): who reported what ({len(cl)} problems, "
            f"{single} reported by one contender only)"
        )
        out.append(coverage_table(cl, what="problem", title=title))
    for t in (cwe_table(cells, detail, arena), severity_table(cells, detail, arena)):
        if t is not None:
            out.append(t)
    return out
