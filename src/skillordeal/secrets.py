"""Credential resolution and scrubbing. Secret values never reach argv, logs or git."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from skillordeal.config import AuthConfig, AuthMode

REDACTED = "[REDACTED]"
PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
]


class AuthError(RuntimeError):
    pass


@dataclass
class Credentials:
    mode: AuthMode
    # Env the podman process gets; the container is told only the NAMES.
    env: dict[str, str] = field(default_factory=dict)
    credentials_file: Path | None = None

    @property
    def secret_values(self) -> list[str]:
        vals = [v for v in self.env.values() if len(v) >= 8]
        if self.credentials_file and self.credentials_file.exists():
            vals.extend(
                re.findall(
                    r'"[A-Za-z]*[Tt]oken"\s*:\s*"([^"]{8,})"', self.credentials_file.read_text()
                )
            )
        return vals


def resolve_credentials(auth: AuthConfig, environ: dict[str, str] | None = None) -> Credentials:
    """Find the secret for auth.mode.

    SKILLORDEAL_TOKEN_ENV / SKILLORDEAL_ENV_FILE override token_env / env_file, so a
    personal setup (e.g. WORK_CLAUDE_CODE_OAUTH_TOKEN in ~/.config/secrets/claude.env)
    never has to be written into a committed trial file.
    """
    env = dict(os.environ if environ is None else environ)
    auth = auth.model_copy(
        update={
            k: env[e]
            for k, e in (
                ("token_env", "SKILLORDEAL_TOKEN_ENV"),
                ("env_file", "SKILLORDEAL_ENV_FILE"),
            )
            if env.get(e)
        }
    )
    if auth.env_file:
        f = Path(auth.env_file).expanduser()
        if not f.exists():
            raise AuthError(f"auth.env_file {f} does not exist")
        env = {**{k: v for k, v in dotenv_values(f).items() if v}, **env}

    if auth.mode == AuthMode.credentials_file:
        f = Path(auth.credentials_file).expanduser()
        if not f.is_file():
            raise AuthError(f"credentials file {f} not found")
        return Credentials(mode=auth.mode, credentials_file=f)

    name = auth.token_env or auth.default_token_env
    value = env.get(name)
    if not value:
        where = f" (also looked in {auth.env_file})" if auth.env_file else ""
        raise AuthError(f"{name} is not set{where}")
    return Credentials(mode=auth.mode, env={auth.container_env: value})


class Scrubber:
    def __init__(self, secrets: list[str]):
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, REDACTED)
        for p in PATTERNS:
            text = p.sub(lambda m: (m.group(1) if m.groups() else "") + REDACTED, text)
        return text
