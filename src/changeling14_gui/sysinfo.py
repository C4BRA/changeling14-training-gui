"""Disk, memory and CPU for the footer. Standard library and built-in tools only."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GB = 1024 ** 3


@dataclass(frozen=True)
class Snapshot:
    disk_free: int | None
    disk_total: int | None
    mem_used: int | None
    mem_total: int | None
    load1: float | None
    cpus: int
    app_rss: int | None

    def describe(self) -> str:
        parts = []
        if self.disk_free is not None:
            parts.append(f"Disk {self.disk_free / GB:.0f} GB free")
        if self.mem_used is not None and self.mem_total:
            parts.append(f"Memory {self.mem_used / GB:.1f} / {self.mem_total / GB:.0f} GB")
        if self.load1 is not None:
            parts.append(f"CPU load {self.load1:.1f} ({self.cpus} cores)")
        if self.app_rss is not None:
            parts.append(f"This app {self.app_rss / 1024 ** 2:.0f} MB")
        return "   ·   ".join(parts)


def _disk(path: Path) -> tuple[int | None, int | None]:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return None, None
    return usage.free, usage.total


def _run(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _memory_mac() -> tuple[int | None, int | None]:
    total = _run(["sysctl", "-n", "hw.memsize"])
    vm = _run(["vm_stat"])
    if not total or not vm:
        return None, None
    page = 4096
    counts = {}
    for line in vm.splitlines():
        if "page size of" in line:
            page = int(line.split("page size of")[1].split()[0])
        elif ":" in line:
            key, _, value = line.partition(":")
            value = value.strip().rstrip(".")
            if value.isdigit():
                counts[key.strip()] = int(value)
    # Activity Monitor's "Memory Used": app memory + wired + compressed.
    used_pages = (counts.get("Anonymous pages", counts.get("Pages active", 0))
                  - counts.get("Pages purgeable", 0)
                  + counts.get("Pages wired down", 0)
                  + counts.get("Pages occupied by compressor", 0))
    return max(0, used_pages) * page, int(total)


def _memory_linux() -> tuple[int | None, int | None]:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0]) * 1024
        return info["MemTotal"] - info["MemAvailable"], info["MemTotal"]
    except (OSError, KeyError, ValueError):
        return None, None


def _app_rss() -> int | None:
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError):
            return None
    out = _run(["ps", "-o", "rss=", "-p", str(os.getpid())])
    try:
        return int(out.strip()) * 1024 if out else None
    except ValueError:
        return None


def snapshot(disk_path: Path) -> Snapshot:
    free, total = _disk(disk_path)
    if sys.platform == "darwin":
        used, mem_total = _memory_mac()
    else:
        used, mem_total = _memory_linux()
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = None
    return Snapshot(free, total, used, mem_total, load1, os.cpu_count() or 1, _app_rss())
