"""Content hashing for directories, so a lock can detect any edit to a skill or arena."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: Path) -> str:
    """sha256 over sorted (relative path, exec bit, file hash) entries.

    Symlinks hash their target text.
    """
    h = hashlib.sha256()
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                entries.append((rel, "l", hashlib.sha256(os.readlink(p).encode()).hexdigest()))
            else:
                mode = "x" if os.access(p, os.X_OK) else "f"
                entries.append((rel, mode, file_sha256(p)))
    for rel, mode, digest in sorted(entries):
        h.update(f"{rel}\0{mode}\0{digest}\n".encode())
    return h.hexdigest()
