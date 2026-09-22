"""Fetch pinned git sources into a local cache and export trees without .git."""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def cache_path(cache_dir: Path, repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "--", repo.split("://")[-1].removesuffix(".git"))
    return cache_dir / "git" / slug


def ensure_repo(cache_dir: Path, repo: str) -> Path:
    """Bare, blobless mirror in the cache. Cheap to keep around, fetches only what's needed."""
    dest = cache_path(cache_dir, repo)
    if not (dest / "HEAD").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--bare", "--filter=blob:none", "--quiet", repo, str(dest))
    return dest


def resolve(cache_dir: Path, repo: str, ref: str | None) -> str:
    """Resolve a branch, tag or sha to a full commit sha."""
    dest = ensure_repo(cache_dir, repo)
    if ref and SHA_RE.match(ref):
        try:
            return _git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=dest)
        except GitError:
            _git("fetch", "--quiet", "origin", ref, cwd=dest)
            return _git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=dest)
    _git("fetch", "--quiet", "--tags", "--force", "origin", "+refs/heads/*:refs/heads/*", cwd=dest)
    target = ref or "HEAD"
    return _git("rev-parse", "--verify", f"{target}^{{commit}}", cwd=dest)


def export(cache_dir: Path, repo: str, sha: str, subpath: str, dest: Path) -> Path:
    """Write the tree at sha:subpath into dest (no .git). Returns dest."""
    src = ensure_repo(cache_dir, repo)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    sub = subpath.strip("/")
    with tempfile.TemporaryDirectory() as td:
        tar = Path(td) / "t.tar"
        args = ["archive", "--format=tar", "-o", str(tar), sha]
        if sub:
            args.append(sub)
        _git(*args, cwd=src)
        with tarfile.open(tar) as tf:
            tf.extractall(td + "/x", filter="data")
        root = Path(td) / "x" / sub if sub else Path(td) / "x"
        if root.is_file():  # a single prompt file
            shutil.copy2(root, dest / root.name)
        else:
            shutil.copytree(root, dest, dirs_exist_ok=True, symlinks=True)
    return dest
