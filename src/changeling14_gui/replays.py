"""The replay library, recordings and replay datasets on disk, for the replay
job windows. Plain functions, no Tk; the pipeline itself is
`changeling14-vision replays ...`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config

LIBRARY_INDEX = "replays.jsonl"


@dataclass(frozen=True)
class Replay:
    file: str              # path inside the library, e.g. wizards-94087a918a2f/leviathan-...zip
    build: str
    duration: float | None
    round_id: str
    map: str
    recorded: int          # pictures already recorded for it (0: none)

    @property
    def name(self) -> str:
        return Path(self.file).name

    @property
    def stem(self) -> str:
        return Path(self.file).stem


def _round_id(name: str) -> str:
    match = re.search(r"round_(\d+)", name)
    return match.group(1) if match else ""


def recorded_pictures(recordings: Path, stem: str) -> int:
    frames = recordings / stem / "frames"
    try:
        return sum(1 for _ in frames.glob("*.png"))
    except OSError:
        return 0


def library_replays(cfg: Config) -> list[Replay]:
    """Replays in the library, from its replays.jsonl, oldest first."""
    library = cfg.paths["replay_library"]
    recordings = cfg.paths["replay_recordings"]
    found = []
    try:
        lines = (library / LIBRARY_INDEX).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        file = str(row.get("file") or "")
        if not file or not (library / file).is_file():
            continue
        duration = row.get("duration")
        found.append(Replay(file=file, build=str(row.get("build", "?")),
                            duration=float(duration) if isinstance(duration, (int, float)) else None,
                            round_id=str(row.get("roundId") or _round_id(file)), map=str(row.get("map") or ""),
                            recorded=recorded_pictures(recordings, Path(file).stem)))
    return found


def inbox_zips(inbox: Path) -> list[Path]:
    try:
        return sorted(p for p in inbox.glob("*.zip") if p.is_file())
    except OSError:
        return []


def recording_rounds(cfg: Config) -> list[str]:
    """Round ids of library replays that have pictures recorded."""
    return sorted({r.round_id for r in library_replays(cfg) if r.recorded and r.round_id})


def text_models(cfg: Config) -> list[Path]:
    """Sprite models a replay model can start from: folders with text.npz."""
    models = cfg.paths.get("models")
    try:
        return sorted((p for p in models.iterdir() if (p / "text.npz").is_file() and (p / "config.json").is_file()),
                      key=lambda p: p.name.lower()) if models else []
    except OSError:
        return []


def default_replay_model(start: Path) -> str:
    return f"replay-{start.name}"


def duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


# Arguments -----------------------------------------------------------------

def record_args(interval: str, players: str, max_pictures: str) -> list[str]:
    args = ["--interval", str(float(interval)).rstrip("0").rstrip("."), "--players", str(int(players))]
    if max_pictures.strip():
        args += ["--max-pictures", str(int(max_pictures))]
    return args


def holdout_args(holdout: str) -> list[str]:
    ids = [p for p in re.split(r"[\s,]+", holdout.strip()) if p]
    return ["--holdout", ",".join(ids)] if ids else []


def replay_train_args(steps: str, batch: str, minutes: str) -> list[str]:
    args = ["--steps", str(int(steps)), "--batch", str(int(batch))]
    if minutes.strip():
        args += ["--minutes", minutes.strip()]
    return args


# Checks --------------------------------------------------------------------

def _positive(value: str, what: str, number=int, optional: bool = False) -> str | None:
    if optional and not value.strip():
        return None
    try:
        if number(value) <= 0:
            raise ValueError
    except ValueError:
        return f"{what} must be a number above 0" + (", or empty." if optional else ".")
    return None


def check_record(replay: Replay | None, source: str, interval: str, players: str, max_pictures: str) -> tuple[bool, str]:
    if replay is None:
        return False, "Choose a replay from the library (Import replays adds them)."
    src = Path(source.strip()).expanduser()
    if not source.strip() or not (src / "RobustToolbox").is_dir() or not (src / ".git").exists():
        return False, f"{src} is not an SS14 git checkout (it needs .git and RobustToolbox)."
    for problem in (_positive(interval, "Interval", float), _positive(players, "Players"),
                    _positive(max_pictures, "Max pictures", optional=True)):
        if problem:
            return False, problem
    if replay.recorded:
        return True, f"{replay.name} already has {replay.recorded:,} pictures; recording again replaces them."
    return True, f"Records {replay.name} ({duration_text(replay.duration)})."


def check_holdout(holdout: str, rounds: list[str]) -> tuple[bool, str]:
    ids = [p for p in re.split(r"[\s,]+", holdout.strip()) if p]
    if not rounds:
        return False, "No recorded replays yet. Run Record replays first."
    bad = [i for i in ids if not i.isdigit()]
    if bad:
        return False, f"Round ids are numbers: {', '.join(bad)}."
    unknown = [i for i in ids if i not in rounds]
    if unknown:
        return False, f"No recording for round {', '.join(unknown)}. Recorded: {', '.join(rounds)}."
    if ids and set(ids) >= set(rounds):
        return False, "Every recorded round would be held out, leaving nothing to train on."
    held = f"rounds {', '.join(ids)} held out for testing" if ids else "about 1 in 5 rounds held out automatically"
    return True, f"Builds a dataset from {len(rounds)} recorded round(s), {held}."


def check_replay_train(start: Path | None, name: str, dataset: Path, steps: str, batch: str,
                       minutes: str) -> tuple[bool, str]:
    if start is None:
        return False, "Choose the sprite model to start from (one trained with text)."
    if not (dataset / "dataset.json").is_file():
        return False, f"No replay dataset at {dataset}. Run Build dataset first."
    if not name.strip() or "/" in name or name.strip() in (".", ".."):
        return False, "Give the new model a folder name (no slashes)."
    for problem in (_positive(steps, "Steps"), _positive(batch, "Batch"),
                    _positive(minutes, "Stop after", float, optional=True)):
        if problem:
            return False, problem
    out = start.parent / name.strip()
    if out.resolve() == start.resolve():
        return False, "The new model needs its own folder, not the starting model's."
    if out.exists():
        return True, f"{name.strip()} exists, so training resumes it."
    return True, f"A new replay model {name.strip()}, starting from {start.name}."
