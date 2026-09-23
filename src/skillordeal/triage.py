"""Shortlist contenders from a skills-collection checkout.

Input is skills-collection's `skills-list.json` (repo_dir -> [{name, description, path, words,
...}]); skill files live in `<collection>/skills/<path>`. Optional siblings: `inventory.json`
(repo URLs), `repos-meta.json` (stars) and `commits.json` (the commit that was synced).

Every step is static and explainable: keyword hits on name + description, near-duplicate folding
by normalized SKILL.md hash, a score whose parts are written into the notes, and a regex risk
pre-scan. Nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from skillordeal.config import Contender
from skillordeal.yamlio import dump_yaml

DEFAULT_KEYWORDS = (
    "security",
    "vulnerab",
    "audit",
    "code review",
    "code-review",
    "owasp",
    "cwe",
    "sast",
    "pentest",
    "threat model",
    "exploit",
    "injection",
    "xss",
    "secure coding",
)

MAX_SCAN_BYTES = 1_000_000
GITHUB_HOSTS = {
    "github.com",
    "www.github.com",
    "raw.githubusercontent.com",
    "gist.github.com",
    "gist.githubusercontent.com",
    "docs.github.com",
    "api.github.com",
    "objects.githubusercontent.com",
}
RISK_PATTERNS: dict[str, re.Pattern[str]] = {
    "network-fetch": re.compile(r"\b(curl|wget)\b"),
    "netcat": re.compile(r"\bnc\s+-?\w"),
    "dev-tcp": re.compile(r"/dev/(tcp|udp)/"),
    "base64-decode": re.compile(r"base64\s+(-d\b|--decode\b|-D\b)|b64decode|atob\("),
    "eval": re.compile(r"\beval\b"),
}
INJECTION_PHRASES = (
    "ignore previous",
    "ignore all previous",
    "ignore the previous",
    "disregard previous",
    "system prompt",
    "do not tell the user",
    "don't tell the user",
    "without telling the user",
    "do not mention this",
)
URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")

# Optional second opinion on a candidate (e.g. an LLM classifier). Called with the candidate and
# its SKILL.md text; returns a note to append, or None. Not wired to anything yet, on purpose:
# triage has to stay free and deterministic by default.
# TODO: add `--classifier MODULE:FUNC` once there is a classifier worth paying for.
Classifier = Callable[["Candidate", str], str | None]


class TriageError(RuntimeError):
    pass


@dataclass
class Candidate:
    repo_dir: str
    name: str
    description: str
    path: str  # relative to <collection>/skills
    remote_path: str
    words: int
    keyword_hits: list[str]
    has_references: bool = False
    stars: int | None = None
    content_hash: str = ""
    risks: list[str] = field(default_factory=list)
    risk: str = "none"
    duplicates: list[str] = field(default_factory=list)
    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)
    extra_notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.repo_dir}/{self.remote_path or self.path}"


@dataclass
class TriageResult:
    candidates: list[Candidate]
    contenders: list[dict[str, Any]]
    yaml: str
    matched: int
    duplicates: int
    too_short: int


# --- pieces ----------------------------------------------------------------------------


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    low = text.lower()
    return [k for k in keywords if k.lower() in low]


def normalized_hash(text: str) -> str:
    """Hash of SKILL.md without its frontmatter, lowercased, whitespace collapsed.

    Forks that only rename the skill or reflow the text land on the same hash.
    """
    body = re.sub(r"^\ufeff?---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    norm = re.sub(r"\s+", " ", body.lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()


CODE_SUFFIXES = {".sh", ".bash", ".zsh", ".py", ".js", ".cjs", ".mjs", ".ts", ".rb", ".pl", ".ps1"}


def _is_code(p: Path, rel: str) -> bool:
    if rel.startswith("scripts/") or p.suffix.lower() in CODE_SUFFIXES:
        return True
    try:
        return p.read_bytes()[:2] == b"#!"
    except OSError:
        return False


def scan_risks(skill_dir: Path) -> list[str]:
    """Static red flags in a skill directory, as `[code|doc] kind: file:line` strings.

    `[code]` means the hit is in something executable (scripts/, *.sh, *.py, a shebang);
    `[doc]` means prose, which security skills legitimately fill with curl and eval examples.
    """
    flags: list[str] = []
    scripts = [p for p in (skill_dir / "scripts").rglob("*") if p.is_file()]
    if scripts:
        names = ", ".join(sorted(p.relative_to(skill_dir).as_posix() for p in scripts)[:5])
        more = f" (+{len(scripts) - 5} more)" if len(scripts) > 5 else ""
        flags.append(f"[code] scripts: {len(scripts)} file(s): {names}{more}")
    for p in sorted(skill_dir.rglob("*")):
        if not p.is_file() or p.is_symlink() or p.stat().st_size > MAX_SCAN_BYTES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError, OSError:
            continue
        rel = p.relative_to(skill_dir).as_posix()
        where = "[code]" if _is_code(p, rel) else "[doc]"
        seen: set[str] = set()
        for n, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            for kind, rx in RISK_PATTERNS.items():
                if kind not in seen and rx.search(line):
                    seen.add(kind)
                    flags.append(f"{where} {kind}: {rel}:{n}")
            for phrase in INJECTION_PHRASES:
                if f"inj:{phrase}" not in seen and phrase in low:
                    seen.add(f"inj:{phrase}")
                    flags.append(f"{where} prompt-injection phrase {phrase!r}: {rel}:{n}")
            for host in URL_RE.findall(line):
                host = host.lower().rstrip(".")
                if host in GITHUB_HOSTS or host.endswith(".github.io"):
                    continue
                if f"url:{host}" not in seen:
                    seen.add(f"url:{host}")
                    flags.append(f"{where} non-github url {host}: {rel}:{n}")
    return flags


def risk_level(flags: list[str]) -> str:
    """How loud the pre-scan is, from the flags alone.

    high: code that fetches, decodes or evals, or a prompt-injection phrase in SKILL.md or code.
    medium: ships scripts or other code hits. low: only mentions in reference prose.
    """
    if not flags:
        return "none"
    injection = any(
        "prompt-injection" in f and (f.startswith("[code]") or ": SKILL.md:" in f) for f in flags
    )
    if injection or any(
        f.startswith("[code]") and k in f
        for f in flags
        for k in ("network-fetch", "netcat", "dev-tcp", "base64-decode", "eval")
    ):
        return "high"
    if any(f.startswith("[code]") for f in flags):
        return "medium"
    return "low"


def score(c: Candidate) -> tuple[float, dict[str, float]]:
    """Transparent ranking. Each part is reported in the notes, so the order can be argued with."""
    parts = {
        "keywords": 3.0 * len(c.keyword_hits),
        "length": round(min(c.words, 3000) / 1000, 2),  # up to 3 for a long, substantive skill
        "references": 2.0 if c.has_references else 0.0,
        "stars": round(math.log10((c.stars or 0) + 1), 2),  # 10k stars = 4
    }
    return round(sum(parts.values()), 2), parts


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-._")
    return s or "skill"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _repo_urls(root: Path) -> dict[str, str]:
    inv = _load_json(root / "inventory.json") or []
    out = {}
    for row in inv if isinstance(inv, list) else []:
        if isinstance(row, dict) and row.get("dir") and row.get("url"):
            out[row["dir"]] = str(row["url"]).removesuffix(".git").rstrip("/")
    return out


def _github_url(repo_dir: str, urls: dict[str, str]) -> str | None:
    url = urls.get(repo_dir)
    if url:
        host = urlsplit(url).hostname or ""
        return url if host == "github.com" else None
    owner, sep, repo = repo_dir.partition("--")
    return f"https://github.com/{owner}/{repo}" if sep and owner and repo else None


# --- main ------------------------------------------------------------------------------


def run_triage(
    skills_list: Path,
    *,
    keywords: list[str],
    min_words: int = 200,
    top: int = 0,
    pin_synced: bool = False,
    classifier: Classifier | None = None,
) -> TriageResult:
    if not skills_list.exists():
        raise TriageError(f"{skills_list} not found")
    root = skills_list.parent
    skills_root = root / "skills"
    data = json.loads(skills_list.read_text())
    if not isinstance(data, dict):
        raise TriageError(f"{skills_list}: expected an object of repo_dir -> [skills]")
    meta = _load_json(root / "repos-meta.json") or {}
    synced = _load_json(root / "commits.json") or {}
    urls = _repo_urls(root)

    matched, too_short = 0, 0
    pool: list[Candidate] = []
    for repo_dir, skills in sorted(data.items()):
        for s in skills or []:
            if not isinstance(s, dict) or not s.get("path"):
                continue
            hits = keyword_hits(f"{s.get('name', '')} {s.get('description', '')}", keywords)
            if not hits:
                continue
            matched += 1
            if int(s.get("words") or 0) < min_words:
                too_short += 1
                continue
            skill_md = skills_root / s["path"]
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            c = Candidate(
                repo_dir=repo_dir,
                name=str(s.get("name") or skill_md.parent.name).strip().strip("'\"").strip()
                or skill_md.parent.name,
                description=str(s.get("description") or ""),
                path=s["path"],
                remote_path=str(s.get("remote_path") or ""),
                words=int(s.get("words") or 0),
                keyword_hits=hits,
                has_references=any(
                    (skill_md.parent / d).is_dir() for d in ("references", "reference")
                ),
                stars=(meta.get(repo_dir) or {}).get("stars") if isinstance(meta, dict) else None,
                content_hash=normalized_hash(text),
            )
            c.score, c.score_parts = score(c)
            if classifier:
                note = classifier(c, text)
                if note:
                    c.extra_notes.append(note)
            pool.append(c)

    # Near-duplicates: keep the best-scoring copy, remember the others.
    pool.sort(key=lambda c: (-c.score, -(c.stars or 0), c.key))
    kept: dict[str, Candidate] = {}
    folded = 0
    for c in pool:
        if c.content_hash in kept:
            kept[c.content_hash].duplicates.append(c.key)
            folded += 1
        else:
            kept[c.content_hash] = c
    cands = list(kept.values())
    if top:
        cands = cands[:top]
    for c in cands:
        c.risks = scan_risks((skills_root / c.path).parent)
        c.risk = risk_level(c.risks)

    contenders, used = [], set()
    for c in cands:
        entry = _contender(c, urls, synced if pin_synced else {}, skills_root)
        base, n = entry["id"], 2
        while entry["id"] in used:
            entry["id"] = f"{base}-{n}"
            n += 1
        used.add(entry["id"])
        Contender.model_validate(entry)  # fail here, not at lock time
        contenders.append(entry)

    return TriageResult(
        candidates=cands,
        contenders=contenders,
        yaml=render_yaml(cands, contenders, skills_list, keywords, min_words),
        matched=matched,
        duplicates=folded,
        too_short=too_short,
    )


def _contender(
    c: Candidate, urls: dict[str, str], synced: dict[str, str], skills_root: Path
) -> dict[str, Any]:
    owner = c.repo_dir.partition("--")[0]
    entry: dict[str, Any] = {"id": slugify(f"{owner}-{c.name}"), "kind": "skill"}
    url = _github_url(c.repo_dir, urls)
    subpath = str(Path(c.remote_path).parent) if c.remote_path else ""
    if url and c.remote_path:
        entry["repo"] = url
        if synced.get(c.repo_dir):
            entry["ref"] = synced[c.repo_dir]
        # no ref = the default branch; `skillordeal lock` pins it to a sha
        entry["subpath"] = "" if subpath == "." else subpath
    else:
        entry["path"] = str((skills_root / c.path).parent.resolve())
    entry["role"] = "finder"
    entry["notes"] = _notes(c)
    return entry


def _notes(c: Candidate) -> str:
    parts = ", ".join(f"{k} {v:g}" for k, v in c.score_parts.items())
    lines = [
        f"triage score {c.score:g} ({parts}); keywords: {', '.join(c.keyword_hits)}; "
        f"{c.words} words; stars {c.stars if c.stars is not None else 'unknown'}",
        f"source: skills-collection/skills/{c.path}",
    ]
    if c.duplicates:
        lines.append(f"near-duplicates folded in: {', '.join(c.duplicates)}")
    if c.risks:
        lines.append(f"risk pre-scan ({c.risk}): " + "; ".join(c.risks))
    else:
        lines.append("risk pre-scan: nothing flagged")
    lines += c.extra_notes
    return "\n".join(lines)


def render_yaml(
    cands: list[Candidate],
    contenders: list[dict[str, Any]],
    skills_list: Path,
    keywords: list[str],
    min_words: int,
) -> str:
    head = [
        "# Generated by `skillordeal triage`. Review every entry before copying it into a trial:",
        "# the risk pre-scan is a grep, not a verdict, and third-party skills are untrusted.",
        f"# source: {skills_list}",
        f"# keywords: {', '.join(keywords)}; min words: {min_words}",
        f"# {len(contenders)} candidates, best first",
        "contenders:",
    ]
    body = []
    for c, entry in zip(cands, contenders, strict=True):
        flag = f"  risk {c.risk} ({len(c.risks)} flags)" if c.risks else ""
        body.append(f"  # {c.score:g}  {c.repo_dir} :: {c.name}{flag}")
        item = dump_yaml([entry]).rstrip("\n").splitlines()
        body += [f"  {line}" for line in item]
    return "\n".join(head + body) + "\n"
