"""Network egress for bout and judge containers.

`allowlist` mode puts the agent container on an internal-only podman network. A small proxy
container (same runner image, `/opt/skillordeal/egress-proxy.mjs`) sits on both that network
and the default one and tunnels only to allowed hosts. Its decisions end up in the record, so
a skill that tries to phone home shows up as `egress.denied`.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from typing import Any

from skillordeal.config import NetworkConfig, NetworkMode

PROXY_PORT = 3128


def _podman(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["podman", *args], capture_output=True, text=True, check=False)


class EgressError(RuntimeError):
    pass


class Egress:
    """Context manager: starts the proxy on enter, removes it on exit."""

    def __init__(self, name: str, image: str, cfg: NetworkConfig, *, enabled: bool = True):
        self.name = f"{name}-egress"
        self.image = image
        self.cfg = cfg
        self.active = enabled and cfg.mode == NetworkMode.allowlist
        self._summary: dict[str, Any] | None = None

    def __enter__(self) -> Egress:
        if not self.active:
            return self
        if _podman("network", "exists", self.cfg.internal_network).returncode != 0:
            made = _podman("network", "create", "--internal", self.cfg.internal_network)
            if made.returncode != 0 and "already exists" not in made.stderr:
                raise EgressError(f"cannot create network: {made.stderr.strip()}")
        _podman("rm", "-f", self.name)
        proc = _podman(
            "run", "-d", "--name", self.name,
            "--network", "podman", "--network", self.cfg.internal_network,
            "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
            "--memory", "128m", "--pids-limit", "64",
            "-e", f"ALLOW={','.join(self.cfg.allow)}", "-e", f"PORT={PROXY_PORT}",
            "--entrypoint", "node", self.image, "/opt/skillordeal/egress-proxy.mjs",
        )  # fmt: skip
        if proc.returncode != 0:
            raise EgressError(f"egress proxy failed to start: {proc.stderr.strip()}")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if '"kind":"ready"' in _podman("logs", self.name).stdout:
                return self
            time.sleep(0.2)
        self.__exit__(None, None, None)
        raise EgressError("egress proxy did not become ready")

    def __exit__(self, *_: object) -> None:
        if self.active and self._summary is None:
            self._summary = self._collect()
            _podman("rm", "-f", self.name)

    @property
    def podman_args(self) -> list[str]:
        if not self.active:
            return [] if self.cfg.mode == NetworkMode.open else ["--network", "none"]
        proxy = f"http://{self.name}:{PROXY_PORT}"
        return [
            "--network", self.cfg.internal_network,
            "-e", f"HTTPS_PROXY={proxy}", "-e", f"HTTP_PROXY={proxy}",
            "-e", "NO_PROXY=localhost,127.0.0.1",
        ]  # fmt: skip

    def _collect(self) -> dict[str, Any]:
        allowed: Counter[str] = Counter()
        denied: Counter[str] = Counter()
        for line in _podman("logs", self.name).stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("kind") in {"connect", "http"}:
                target = f"{ev.get('host')}:{ev.get('port', '')}".rstrip(":")
                (allowed if ev.get("allowed") else denied)[target] += 1
        return {"mode": self.cfg.mode.value, "allowed": dict(allowed), "denied": dict(denied)}

    def summary(self) -> dict[str, Any]:
        if not self.active:
            return {
                "mode": self.cfg.mode.value
                if self.cfg.mode != NetworkMode.allowlist
                else "disabled"
            }
        return self._summary or self._collect()
