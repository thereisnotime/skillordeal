"""Bundled JSON schemas."""

import json
from functools import cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).parent
FINDINGS_SCHEMA_PATH = SCHEMA_DIR / "findings.schema.json"
RECORD_SCHEMA_PATH = SCHEMA_DIR / "record.schema.json"


@cache
def load(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text())
