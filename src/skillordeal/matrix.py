"""Expand a locked trial into bouts with deterministic IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skillordeal.yamlio import sha256_obj


@dataclass(frozen=True)
class BoutKey:
    contender: str
    arena: str
    task: str
    model: str  # ModelSpec.slug
    rep: int
    bout_id: str

    def label(self) -> str:
        return f"{self.contender} × {self.arena} × {self.task} × {self.model} #{self.rep}"


def bout_id(
    lock: dict[str, Any], contender: str, arena: str, task: str, model: dict[str, Any], rep: int
) -> str:
    """Hash of exactly the inputs this bout depends on.

    Adding a contender to a trial doesn't change the other bouts' IDs, so rounds resume.
    """
    c = lock["contenders"][contender]
    a = lock["arenas"][arena]
    t = lock["tasks"][task]
    common = {
        "task": t,
        "model": model,
        "image": lock["image"].get("id"),
        "cli": lock["image"].get("cli_version"),
        "invocation": lock["invocation"],
        "auth_mode": lock["runtime"]["auth_mode"],
        "findings_schema": lock["findings_schema_sha256"],
        "rep": rep,
    }
    if "run_hash" in c and "run_hash" in a:
        # run_hash covers only run-relevant config, so editing notes or ground truth
        # keeps finished bouts valid. Limits change outcomes, so they are part of the ID.
        inputs = {
            **common,
            "contender": c["run_hash"],
            "arena": a["run_hash"],
            "limits": lock["runtime"].get("limits"),
        }
    else:  # locks written before run_hash existed keep their original IDs
        inputs = {
            **common,
            "contender": {k: c.get(k) for k in ("kind", "sha", "tree_hash", "config_hash")},
            "arena": {k: a.get(k) for k in ("sha", "config_hash", "groundtruth")},
        }
    return "b-" + sha256_obj(inputs)[:16]


def expand(lock: dict[str, Any]) -> list[BoutKey]:
    out = []
    for m in lock["models"]:
        slug = m["id"] + (f"@{m['effort']}" if m.get("effort") else "")
        for task in lock["tasks"]:
            for arena in lock["arenas"]:
                for contender in lock["contenders"]:
                    for rep in range(1, lock["reps"] + 1):
                        out.append(
                            BoutKey(
                                contender,
                                arena,
                                task,
                                slug,
                                rep,
                                bout_id(lock, contender, arena, task, m, rep),
                            )
                        )
    return out


def shard(bouts: list[BoutKey], index: int, total: int) -> list[BoutKey]:
    """Round-robin shard (1-based index) so each shard gets a mix of contenders."""
    if not 1 <= index <= total:
        raise ValueError(f"shard {index}/{total} out of range")
    return [b for i, b in enumerate(bouts) if i % total == index - 1]
