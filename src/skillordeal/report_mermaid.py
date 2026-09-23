"""Mermaid diagrams for RESULTS.md: GitHub renders ```mermaid blocks natively.

Kept to plain `flowchart LR` so any recent GitHub mermaid handles it. Node ids are generated
(`n0`, `n1`, ...) so none is a keyword like `end` or starts an `o`/`x` edge, and every label is
quoted with `"` escaped.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from skillordeal.report import Cell, _num
from skillordeal.report_charts import verdict_scheme
from skillordeal.rounddata import Round

STATUSES = ("ok", "invalid", "schema_violation", "error", "timeout", "limit_exceeded")


class Flow:
    def __init__(self) -> None:
        self.lines = ["flowchart LR"]
        self._n = 0

    def node(self, label: str) -> str:
        nid = f"n{self._n}"
        self._n += 1
        self.lines.append(f'    {nid}["{label_text(label)}"]')
        return nid

    def edge(self, a: str, b: str, label: str | None = None) -> None:
        mid = f'|"{label_text(label)}"|' if label else ""
        self.lines.append(f"    {a} -->{mid} {b}")

    def block(self) -> str:
        return "\n".join(["```mermaid", *self.lines, "```"])


def label_text(s: str) -> str:
    return str(s).replace('"', "#quot;").replace("\n", " ")


def _count(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 1e-9 else f"{x:.1f}"


def _sum(xs: Any) -> float:
    return sum(x for x in xs if not math.isnan(x))


def planned_bouts(lock: dict[str, Any], seen: int) -> int:
    try:
        from skillordeal.matrix import expand

        return max(len(expand(lock)), seen)
    except KeyError, TypeError, ValueError:
        return seen


def glance(cells: list[Cell], lock: dict[str, Any]) -> str:
    """Planned bouts -> status counts -> findings in ok bouts -> verdict totals."""
    rows = [b for c in cells for b in c.bouts]
    ok = [b for c in cells for b in c.ok]
    statuses = Counter(str(b.get("status") or "unknown") for b in rows)
    planned = planned_bouts(lock, len(rows))
    f = Flow()
    root = f.node(f"{planned} bouts planned")
    ok_id = f.node(f"ok: {statuses.get('ok', 0)}")
    f.edge(root, ok_id)
    order = [s for s in STATUSES if s != "ok"] + sorted(set(statuses) - set(STATUSES))
    for s in order:
        if statuses.get(s):
            f.edge(root, f.node(f"{s}: {statuses[s]}"))
    if planned > len(rows):
        f.edge(root, f.node(f"not run: {planned - len(rows)}"))
    total = _sum(_num(b.get("findings")) for b in ok)
    fid = f.node(f"{_count(total)} findings")
    f.edge(ok_id, fid)
    scheme = verdict_scheme(ok)
    if scheme:
        _, parts = scheme
        decided = 0.0
        for col, label, _ in parts:
            v = _sum(_num(b.get(col)) for b in ok)
            decided += v
            f.edge(fid, f.node(f"{label}: {_count(v)}"))
        if total - decided > 1e-9:
            f.edge(fid, f.node(f"other: {_count(total - decided)}"))
    return f.block()


def _record(rnd: Round, bout_id: str) -> dict[str, Any]:
    p = rnd.bouts / bout_id / "record.json"
    try:
        return json.loads(p.read_text())
    except OSError, json.JSONDecodeError:
        return {}


def pipelines(cells: list[Cell], rnd: Round) -> str | None:
    """Findings per stage for each pipeline contender, summed over its ok bouts."""
    by: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for c in cells:
        for b in c.ok:
            rec = _record(rnd, str(b.get("bout_id") or ""))
            if rec.get("stages") or not math.isnan(_num(b.get("findings_before_verify"))):
                by.setdefault(c.contender, []).append((b, rec))
    if not by:
        return None
    f = Flow()
    for contender, bouts in sorted(by.items()):
        stages: dict[int, tuple[str, float]] = {}
        for _, rec in bouts:
            for s in rec.get("stages") or []:
                n, name = int(s.get("n") or 0), str(s.get("contender") or "?")
                got = _num(s.get("findings_count"))
                acc = stages.get(n, (name, 0.0))[1]
                stages[n] = (name, acc + (0.0 if math.isnan(got) else got))
        if not stages:  # no stage detail in the records: fall back to summary columns
            before = _sum(_num(b.get("findings_before_verify")) for b, _ in bouts)
            after = _sum(_num(b.get("findings")) for b, _ in bouts)
            stages = {1: ("finder", before), 2: ("after verify", after)}
        head = f.node(f"{contender} ({len(bouts)} ok bouts)")
        prev = head
        for i, (n, (name, count)) in enumerate(sorted(stages.items())):
            word = "findings" if i == 0 else "kept"
            nid = f.node(f"stage {n}, {name}: {_count(count)} {word}")
            f.edge(prev, nid, None if i == 0 else "verify")
            prev = nid
    return f.block()
