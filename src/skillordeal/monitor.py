"""Client-side resource monitoring for a running bout container.

Runs on the host, so nothing inside the container (including an untrusted skill) can
see or tamper with it. Two sources:

* cgroup v2 files of the container scope: memory.peak, cpu.stat, pids.peak, io.stat.
  The scope is removed when the container exits, so we keep the last values seen.
* /proc/<pid>/{status,stat,fd} for every pid in cgroup.procs, sampled each interval.

This measures the agent harness (CLI, node, tools it spawns), not model-side compute.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

CGROUP_ROOT = Path("/sys/fs/cgroup")
CLK_TCK = os.sysconf("SC_CLK_TCK")


def parse_kv(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def parse_io_stat(text: str) -> dict[str, int]:
    tot: dict[str, int] = defaultdict(int)
    for line in text.splitlines():
        for kv in line.split()[1:]:
            k, _, v = kv.partition("=")
            if v.isdigit():
                tot[k] += int(v)
    return dict(tot)


def parse_proc_status(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if k == "Name":
            out["comm"] = v
        elif k == "VmRSS":
            out["rss_kb"] = int(v.split()[0])
        elif k == "Threads":
            out["threads"] = int(v)
    return out


def parse_proc_stat_ticks(text: str) -> int:
    # comm may contain spaces/parens; fields after the last ')' are fixed.
    rest = text[text.rindex(")") + 2 :].split()
    utime, stime = int(rest[11]), int(rest[12])
    return utime + stime


def _read(p: Path) -> str | None:
    try:
        return p.read_text()
    except OSError:
        return None


@dataclass
class ProcStats:
    rss_kb_max: int = 0
    threads_max: int = 0
    fds_max: int = 0
    cpu_ticks_max: int = 0
    seen: int = 0


@dataclass
class Monitor:
    container: str
    out: TextIO
    interval: float = 1.0
    started: float = field(default_factory=time.monotonic)
    cgroup: Path | None = None
    last_cgroup: dict[str, Any] = field(default_factory=dict)
    samples: int = 0
    rss_series: list[int] = field(default_factory=list)
    threads_max: int = 0
    fds_max: int = 0
    per_comm: dict[str, ProcStats] = field(default_factory=lambda: defaultdict(ProcStats))
    pid_ticks: dict[int, tuple[str, int]] = field(default_factory=dict)
    error: str | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"monitor-{self.container}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _find_cgroup(self) -> Path | None:
        proc = subprocess.run(
            [
                "podman",
                "inspect",
                "--format",
                "{{.State.Running}}|{{.State.CgroupPath}}",
                self.container,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return None
        running, _, cg = proc.stdout.strip().partition("|")
        if running != "true" or not cg:
            return None
        path = CGROUP_ROOT / cg.lstrip("/")
        return path if path.exists() else None

    def _run(self) -> None:
        deadline = time.monotonic() + 60
        while not self._stop.is_set() and self.cgroup is None:
            self.cgroup = self._find_cgroup()
            if self.cgroup is None:
                if time.monotonic() > deadline:
                    self.error = "cgroup not found within 60s"
                    return
                self._stop.wait(0.2)
        while not self._stop.is_set():
            if not self.sample():
                break
            self._stop.wait(self.interval)

    def sample(self) -> bool:
        assert self.cgroup is not None
        procs_txt = _read(self.cgroup / "cgroup.procs")
        if procs_txt is None:
            return False  # scope gone: container exited
        cg: dict[str, Any] = {}
        for name in ("memory.peak", "memory.current", "pids.peak", "pids.current"):
            v = _read(self.cgroup / name)
            if v is not None and v.strip().isdigit():
                cg[name] = int(v)
        if (v := _read(self.cgroup / "cpu.stat")) is not None:
            cg["cpu.stat"] = parse_kv(v)
        if (v := _read(self.cgroup / "io.stat")) is not None:
            cg["io.stat"] = parse_io_stat(v)
        if (v := _read(self.cgroup / "memory.stat")) is not None:
            ms = parse_kv(v)
            cg["memory.stat"] = {k: ms[k] for k in ("anon", "file", "kernel", "sock") if k in ms}
        self.last_cgroup = cg

        t = round(time.monotonic() - self.started, 3)
        rss_total = threads_total = fds_total = 0
        for pid_s in procs_txt.split():
            pid = int(pid_s)
            base = Path("/proc") / pid_s
            st = _read(base / "status")
            stat = _read(base / "stat")
            if not st or not stat:
                continue
            info = parse_proc_status(st)
            try:
                fds = len(os.listdir(base / "fd"))
            except OSError:
                fds = -1
            ticks = parse_proc_stat_ticks(stat)
            comm = info.get("comm", "?")
            row = {
                "t": t,
                "pid": pid,
                "comm": comm,
                "rss_kb": info.get("rss_kb", 0),
                "threads": info.get("threads", 0),
                "fds": fds,
                "cpu_ticks": ticks,
            }
            self.out.write(json.dumps(row) + "\n")
            ps = self.per_comm[comm]
            ps.rss_kb_max = max(ps.rss_kb_max, row["rss_kb"])
            ps.threads_max = max(ps.threads_max, row["threads"])
            ps.fds_max = max(ps.fds_max, fds)
            self.pid_ticks[pid] = (comm, ticks)
            rss_total += row["rss_kb"]
            threads_total += row["threads"]
            fds_total += max(fds, 0)
        self.out.write(json.dumps({"t": t, "cgroup": cg}) + "\n")
        self.out.flush()
        self.samples += 1
        self.rss_series.append(rss_total)
        self.threads_max = max(self.threads_max, threads_total)
        self.fds_max = max(self.fds_max, fds_total)
        return True

    def summary(self) -> dict[str, Any]:
        if self.samples == 0:
            return {"available": False, "reason": self.error or "no samples (bout too short?)"}
        cpu = self.last_cgroup.get("cpu.stat", {})
        io = self.last_cgroup.get("io.stat", {})
        cpu_by_comm: dict[str, float] = defaultdict(float)
        for comm, ticks in self.pid_ticks.values():
            cpu_by_comm[comm] += ticks / CLK_TCK
        return {
            "available": True,
            "samples": self.samples,
            "interval_s": self.interval,
            "memory_peak_bytes": self.last_cgroup.get("memory.peak"),
            "rss_peak_kb": max(self.rss_series),
            "rss_mean_kb": round(sum(self.rss_series) / len(self.rss_series)),
            "threads_peak": self.threads_max,
            "fds_peak": self.fds_max,
            "pids_peak": self.last_cgroup.get("pids.peak"),
            "cpu_usage_s": cpu.get("usage_usec", 0) / 1e6,
            "cpu_user_s": cpu.get("user_usec", 0) / 1e6,
            "cpu_system_s": cpu.get("system_usec", 0) / 1e6,
            "cpu_throttled_s": cpu.get("throttled_usec", 0) / 1e6,
            "io_read_bytes": io.get("rbytes", 0),
            "io_write_bytes": io.get("wbytes", 0),
            "by_comm": {
                c: {
                    "rss_peak_kb": s.rss_kb_max,
                    "threads_peak": s.threads_max,
                    "fds_peak": s.fds_max,
                    "cpu_s": round(cpu_by_comm.get(c, 0.0), 3),
                }
                for c, s in sorted(self.per_comm.items())
            },
            "note": "client harness only; totals are the last values seen before the scope exited",
        }
