"""Start the Changeling14 training app.

    changeling14-gui [--config FILE] [--runs DIR]
    python -m changeling14_gui
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from . import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="changeling14-gui", description="Changeling14 training app")
    parser.add_argument("--config", help="jobs config (default: ~/.changeling14/gui.toml, else built-in)")
    parser.add_argument("--runs", help="runs folder (overrides the config)")
    parser.add_argument("--list-jobs", action="store_true", help="print the jobs and exit")
    args = parser.parse_args(argv)

    cfg = config.load(args.config)
    if args.runs:
        cfg = dataclasses.replace(cfg, runs=Path(args.runs).expanduser())

    if args.list_jobs:
        print(f"config: {cfg.source}\nruns:   {cfg.runs}\ninbox:  {cfg.inbox}")
        for job in cfg.jobs:
            state = "ready" if job.enabled else "not available yet"
            print(f"  {job.id:16} {job.label:28} {'heavy' if job.heavy else 'light':6} {state}")
        return 0

    try:
        from .app import run_app
    except ImportError as exc:
        print(f"Tkinter is not available ({exc}).\n"
              "On a Mac with Homebrew Python: brew install python-tk", file=sys.stderr)
        return 1
    run_app(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
