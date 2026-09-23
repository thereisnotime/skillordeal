"""Prompt for pipeline verifier stages: the previous stage's findings, as data, to re-check."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from skillordeal.config import Arena
from skillordeal.yamlio import sha256_text

VERIFY_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "verify.md"
# Free-text finding fields that could name a contender, so they go through the blinder.
BLIND_TEXT = ("title", "description", "evidence", "recommendation")
FINDING_FIELDS = (
    "title",
    "category",
    "cwe",
    "severity",
    "confidence",
    "file",
    "line_start",
    "line_end",
    "description",
    "evidence",
    "recommendation",
)


def verify_template() -> str:
    return VERIFY_PROMPT_PATH.read_text()


def verify_prompt_sha(template: str | None = None) -> str:
    return sha256_text(verify_template() if template is None else template)


def _fence(body: str) -> str:
    """A backtick fence longer than any run inside the body, so the data can't close it."""
    longest = max((len(m) for m in re.findall(r"`+", body)), default=0)
    tick = "`" * max(3, longest + 1)
    return f"{tick}json\n{body}\n{tick}"


def render_verify_prompt(
    template: str,
    arena: Arena,
    findings: list[dict[str, Any]],
    blind: Callable[[Any], Any],
) -> str:
    """Only the schema's finding fields go in; the earlier summary and coverage stay behind."""
    items = []
    for f in findings:
        item = {}
        for k in FINDING_FIELDS:
            v = f.get(k)
            if v is not None and v != "":
                item[k] = blind(v) if k in BLIND_TEXT else v
        items.append(item)
    body = json.dumps({"findings": items}, indent=2, ensure_ascii=False)
    text = template.replace("{language}", arena.language).replace("{count}", str(len(items)))
    return text.replace("{findings}", _fence(body))  # last, so finding text is never substituted
