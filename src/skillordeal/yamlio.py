"""YAML and canonical-JSON helpers."""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML(typ="safe", pure=True)
    y.default_flow_style = False
    y.width = 4096
    y.sort_base_mapping_type_on_output = False
    return y


def load_yaml(text: str) -> Any:
    return _yaml().load(text) or {}


def dump_yaml(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_obj(data: Any) -> str:
    return sha256_text(canonical_json(data))
