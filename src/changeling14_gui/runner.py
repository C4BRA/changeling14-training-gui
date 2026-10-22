"""Job queue: heavy jobs run one at a time, the rest wait their turn.

No threads. The GUI calls poll() on a timer; poll() is a handful of
cheap system calls.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config, JobSpec, expand_command
from .runfolder import RUN_FILE, RunFolder, list_runs, pid_alive, write_json_atomic

KILL_AFTER_SECONDS = 15.0


@dataclass(eq=False)
class Run:
    folder: RunFolder
    job: JobSpec
    proc: subprocess.Popen | None = None
    pid: int | None = None  # process group of a job started by an earlier app session
    stop_sent_at: float | None = None
    killed: bool = False

    @property
    def pgid(self) -> int | None:
        return self.proc.pid if self.proc else self.pid


@dataclass
class Runner:
    cfg: Config
    pending: list[Run] = field(default_factory=list)
    active: list[Run] = field(default_factory=list)

    def __post_init__(self):
        self._adopt_existing()

    # Startup -------------------------------------------------------------

    def _adopt_existing(self) -> None:
        """Pick up jobs a previous app session left running; drop stale queue entries."""
        for folder in list_runs(self.cfg.runs):
            info = folder.run_info()
            state = info.get("state")
            if state == "queued":
                info.update(state="cancelled", ended_at=time.time())
                write_json_atomic(folder.path / RUN_FILE, info)
            elif state == "running" and pid_alive(info.get("pid")):
                job = self._job_for(info)
                self.active.append(Run(folder, job, pid=int(info["pid"])))

    def _job_for(self, info: dict) -> JobSpec:
        try:
            return self.cfg.job(info.get("job", ""))
        except KeyError:
            return JobSpec(id=info.get("job", "?"), label=info.get("label", "?"),
                           command=tuple(info.get("command", [])), heavy=bool(info.get("heavy", True)))

    # Queue ---------------------------------------------------------------

    def submit(self, job: JobSpec, overrides: dict[str, Path | str] | None = None,
               extra: list[str] | None = None) -> Run:
        if not job.enabled:
            raise ValueError(f"job {job.id!r} has no command yet")
        self.cfg.runs.mkdir(parents=True, exist_ok=True)
        run_dir = self._new_run_dir(job)
        info = {
            "job": job.id,
            "label": job.label,
            "heavy": job.heavy,
            "command": expand_command(job, self.cfg, run_dir, overrides, extra),
            "state": "queued",
            "queued_at": time.time(),
        }
        write_json_atomic(run_dir / RUN_FILE, info)
        run = Run(RunFolder(run_dir), job)
        self.pending.append(run)
        self.poll()
        return run

    def _new_run_dir(self, job: JobSpec) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for n in range(1000):
            suffix = f"-{n}" if n else ""
            path = self.cfg.runs / f"{stamp}-{job.id}{suffix}"
            try:
                path.mkdir()
                return path
            except FileExistsError:
                continue
        raise RuntimeError("could not make a run folder")

    def heavy_busy(self) -> bool:
        return any(r.job.heavy for r in self.active)

    def poll(self) -> bool:
        """Advance the queue. Returns True if anything changed."""
        changed = False
        for run in list(self.active):
            if self._finished(run):
                self.active.remove(run)
                changed = True
            elif run.stop_sent_at and not run.killed and time.time() - run.stop_sent_at > KILL_AFTER_SECONDS:
                self._signal(run, signal.SIGKILL)
                run.killed = True
        for run in list(self.pending):
            if run.job.heavy and self.heavy_busy():
                continue
            self.pending.remove(run)
            self._launch(run)
            self.active.append(run)
            changed = True
        return changed

    def _finished(self, run: Run) -> bool:
        if run.proc is not None:
            if run.proc.poll() is None:
                return False
        elif pid_alive(run.pid):
            return False
        if run.killed:
            # The supervisor was killed before it could record the stop.
            info = run.folder.run_info()
            if info.get("state") == "running":
                info.update(state="stopped", ended_at=time.time())
                write_json_atomic(run.folder.path / RUN_FILE, info)
        return True

    def _launch(self, run: Run) -> None:
        run.proc = subprocess.Popen(
            [sys.executable, "-m", "changeling14_gui.supervise", str(run.folder.path)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [_package_root(), os.environ.get("PYTHONPATH")]))),
        )

    def stop(self, run: Run) -> None:
        if run in self.pending:
            self.pending.remove(run)
            info = run.folder.run_info()
            info.update(state="cancelled", ended_at=time.time())
            write_json_atomic(run.folder.path / RUN_FILE, info)
        elif run in self.active and run.stop_sent_at is None:
            run.stop_sent_at = time.time()
            self._signal(run, signal.SIGTERM)

    def stop_all(self) -> None:
        for run in list(self.pending) + list(self.active):
            self.stop(run)

    def _signal(self, run: Run, sig: int) -> None:
        if run.pgid is None:
            return
        try:
            os.killpg(run.pgid, sig)
        except ProcessLookupError:
            pass

    def find(self, path: Path) -> Run | None:
        for run in self.pending + self.active:
            if run.folder.path == path:
                return run
        return None


def _package_root() -> str:
    # src/ when running from a checkout without installing.
    return str(Path(__file__).resolve().parent.parent)
