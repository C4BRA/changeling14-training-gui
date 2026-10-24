"""Run one job's command and record how it ended in run.json.

The app starts this as its own process group, so the job keeps running
and still records its exit if the app is closed.

    python -m changeling14_gui.supervise RUN_DIR
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .runfolder import CONSOLE_FILE, RUN_FILE, RunFolder, write_json_atomic


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m changeling14_gui.supervise RUN_DIR", file=sys.stderr)
        return 2
    run_dir = Path(args[0])
    run = RunFolder(run_dir)
    info = run.run_info()
    command = info.get("command") or []
    if not command:
        print(f"{run_dir}: run.json has no command", file=sys.stderr)
        return 2

    stop_requested = False

    def on_term(signum, _frame):
        # The child is in our process group and got the same signal; wait for it.
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    env = dict(os.environ, C14_RUN_DIR=str(run_dir), PYTHONUNBUFFERED="1")
    with open(run_dir / CONSOLE_FILE, "ab", buffering=0) as log:
        log.write(f"$ {' '.join(command)}\n".encode())
        try:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=run_dir, env=env)
        except OSError as exc:
            log.write(f"could not start: {exc}\n".encode())
            info.update(state="failed", exit_code=None, ended_at=time.time(), error=str(exc))
            write_json_atomic(run_dir / RUN_FILE, info)
            return 1

        info.update(state="running", pid=os.getpid(), child_pid=child.pid, started_at=time.time())
        write_json_atomic(run_dir / RUN_FILE, info)

        while True:
            try:
                code = child.wait()
                break
            except InterruptedError:
                continue
        log.write(f"\n[exit code {code}]\n".encode())

    if stop_requested or code in (143, -15):
        # 143 is how the pipeline reports a clean stop (it leaves its old output as it was).
        state = "stopped"
    else:
        state = "done" if code == 0 else "failed"
    info.update(state=state, exit_code=code, ended_at=time.time())
    write_json_atomic(run_dir / RUN_FILE, info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
