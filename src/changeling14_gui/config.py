"""Load the jobs config (which buttons exist and what they run)."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

USER_CONFIG = Path("~/.changeling14/gui.toml")


@dataclass(frozen=True)
class JobSpec:
    id: str
    label: str
    command: tuple[str, ...]
    heavy: bool = True
    description: str = ""
    form: str = ""  # a window to fill in before the job starts, e.g. "sprites"

    @property
    def enabled(self) -> bool:
        return bool(self.command)


@dataclass(frozen=True)
class Config:
    runs: Path
    inbox: Path
    jobs: tuple[JobSpec, ...] = field(default_factory=tuple)
    source: str = "<builtin>"
    paths: dict[str, Path] = field(default_factory=dict)  # every [paths] entry, for placeholders

    def job(self, job_id: str) -> JobSpec:
        for job in self.jobs:
            if job.id == job_id:
                return job
        raise KeyError(job_id)


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path)))


def parse(data: dict, source: str = "<builtin>") -> Config:
    paths = data.get("paths", {})
    jobs = []
    seen = set()
    for raw in data.get("jobs", []):
        job_id = str(raw["id"])
        if job_id in seen:
            raise ValueError(f"{source}: duplicate job id {job_id!r}")
        seen.add(job_id)
        jobs.append(
            JobSpec(
                id=job_id,
                label=str(raw.get("label", job_id)),
                command=tuple(str(part) for part in raw.get("command", [])),
                heavy=bool(raw.get("heavy", True)),
                description=str(raw.get("description", "")),
                form=str(raw.get("form", "")),
            )
        )
    all_paths = {
        "runs": "~/Desktop/SS13-14/changeling14-runs",
        "inbox": "~/Desktop/SS13-14/replay-inbox",
        **{str(k): str(v) for k, v in paths.items()},
    }
    expanded = {k: _expand(v) for k, v in all_paths.items()}
    return Config(
        runs=expanded["runs"],
        inbox=expanded["inbox"],
        jobs=tuple(jobs),
        source=source,
        paths=expanded,
    )


def find_config_path(explicit: str | None = None) -> Path | None:
    """--config, then $C14_GUI_CONFIG, then ~/.changeling14/gui.toml."""
    for candidate in (explicit, os.environ.get("C14_GUI_CONFIG")):
        if candidate:
            return _expand(candidate)
    user = _expand(str(USER_CONFIG))
    return user if user.exists() else None


def load(explicit: str | None = None) -> Config:
    path = find_config_path(explicit)
    if path is None:
        text = resources.files("changeling14_gui").joinpath("jobs.toml").read_text()
        return parse(tomllib.loads(text))
    with open(path, "rb") as f:
        return parse(tomllib.load(f), source=str(path))


def expand_command(job: JobSpec, cfg: Config, run_dir: Path,
                   overrides: dict[str, Path] | None = None) -> list[str]:
    """Fill in the placeholders; `overrides` replaces [paths] entries for this run only."""
    values = {k: str(v) for k, v in cfg.paths.items()}
    values.update({k: str(v) for k, v in (overrides or {}).items()})
    values.update(python=sys.executable, run_dir=str(run_dir), runs=str(cfg.runs), inbox=str(cfg.inbox))
    return [part.format(**values) for part in job.command]
