"""Read run folders. See docs/run-folder.md for the contract.

The app writes only run.json and console.log. Everything else in a run
folder belongs to the pipeline and is read, never changed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

RUN_FILE = "run.json"
STATUS_FILE = "status.json"
CONSOLE_FILE = "console.log"
WATCHSET_DIR = "watchset"


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # Missing, or caught mid-write by the pipeline; try again next poll.
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass(frozen=True)
class Status:
    stage: str = ""
    step: int | None = None
    total: int | None = None
    message: str = ""
    eta_seconds: float | None = None

    @property
    def fraction(self) -> float | None:
        if self.step is None or not self.total:
            return None
        return max(0.0, min(1.0, self.step / self.total))


@dataclass(frozen=True)
class Checkpoint:
    name: str
    path: Path
    step: int | None
    images: tuple[Path, ...]


class RunFolder:
    def __init__(self, path: Path):
        self.path = Path(path)

    @property
    def name(self) -> str:
        return self.path.name

    def run_info(self) -> dict:
        return _read_json(self.path / RUN_FILE) or {}

    def state(self) -> str:
        """queued, running, done, failed, stopped, or lost (process gone)."""
        info = self.run_info()
        state = info.get("state", "unknown")
        if state == "running" and not pid_alive(info.get("pid")):
            return "lost"
        return state

    def status(self) -> Status:
        data = _read_json(self.path / STATUS_FILE) or {}

        def num(key, cast):
            value = data.get(key)
            try:
                return None if value is None else cast(value)
            except (TypeError, ValueError):
                return None

        return Status(
            stage=str(data.get("stage", "")),
            step=num("step", int),
            total=num("total", int),
            message=str(data.get("message", "")),
            eta_seconds=num("eta_seconds", float),
        )

    def eta_seconds(self, now: float | None = None) -> float | None:
        """Pipeline's own estimate if given, else a straight-line guess."""
        status = self.status()
        if status.eta_seconds is not None:
            return status.eta_seconds
        started = self.run_info().get("started_at")
        frac = status.fraction
        if not started or not frac or frac >= 1:
            return None
        elapsed = (now or time.time()) - float(started)
        return elapsed * (1 - frac) / frac

    def checkpoints(self) -> list[Checkpoint]:
        root = self.path / WATCHSET_DIR
        if not root.is_dir():
            return []
        found = []
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            images = tuple(sorted(p for p in entry.iterdir() if p.suffix.lower() in (".png", ".gif")))
            if not images:
                continue
            meta = _read_json(entry / "meta.json") or {}
            step = meta.get("step")
            found.append(Checkpoint(entry.name, entry, int(step) if isinstance(step, int) else None, images))
        # Order by training step when known, then by folder name.
        found.sort(key=lambda c: (c.step is None, c.step or 0, c.name))
        return found

    def console_tail(self, max_bytes: int = 64 * 1024) -> str:
        path = self.path / CONSOLE_FILE
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                data = f.read()
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace")

    def signature(self) -> tuple:
        """Cheap change detector: mtimes and sizes of the files the app shows."""
        sig = []
        for name in (RUN_FILE, STATUS_FILE, CONSOLE_FILE, WATCHSET_DIR):
            try:
                st = (self.path / name).stat()
                sig.append((st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append(None)
        return tuple(sig)


def list_runs(runs_root: Path) -> list[RunFolder]:
    """Newest first. A run folder is any directory with a run.json."""
    if not runs_root.is_dir():
        return []
    runs = [RunFolder(p) for p in runs_root.iterdir() if (p / RUN_FILE).is_file()]
    runs.sort(key=lambda r: r.name, reverse=True)
    return runs
