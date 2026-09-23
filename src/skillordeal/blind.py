"""Redact contender, skill and bout identity from text an engine-side agent will read.

Used by the judge and by pipeline verifier stages, so neither can tell whose findings it got.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from skillordeal.lock import WRAPPER_PLUGIN

REDACTED = "[redacted]"
_IDS = re.compile(r"\bb-[0-9a-f]{16}(?::\d+)?\b")  # bout and finding ids


def _entries(lock: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for cid, c in (lock.get("contenders") or {}).items():
        yield cid, c
        for s in c.get("stage_locks") or []:  # pipeline stages, locked inside the pipeline
            yield s["id"], s


def blind_terms(lock: dict[str, Any]) -> list[str]:
    """Names that would give a contender away if a finding happened to mention them."""
    terms: set[str] = set()
    for cid, c in _entries(lock):
        if c.get("kind") == "baseline":
            continue
        terms.add(cid)
        for name in (c.get("skill_name"), c.get("plugin_name")):
            if name and name != WRAPPER_PLUGIN:
                terms.add(name)
                terms.add(name.split(":")[-1])
        for s in c.get("expected_skills") or []:
            terms.add(s.split(":")[-1])
    return sorted((t for t in terms if len(t) >= 3), key=len, reverse=True)


class Blinder:
    def __init__(self, terms: list[str]):
        pat = "|".join(re.escape(t) for t in terms)
        self._terms = re.compile(rf"(?<![\w-])(?:{pat})(?![\w-])", re.I) if pat else None

    def __call__(self, text: Any) -> Any:
        if not isinstance(text, str):
            return text
        text = _IDS.sub(REDACTED, text)
        return self._terms.sub(REDACTED, text) if self._terms else text
