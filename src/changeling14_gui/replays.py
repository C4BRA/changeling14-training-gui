"""The replay library, recordings and replay datasets on disk, for the replay
job windows. Plain functions, no Tk; the pipeline itself is
`changeling14-vision replays ...`.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config

LIBRARY_INDEX = "replays.jsonl"
DATASET_FILE = "dataset.json"


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


@dataclass(frozen=True)
class ReplayDataset:
    path: Path
    summary: dict
    built: float = 0.0     # dataset.json modification time

    @property
    def frames(self) -> int | None:
        return self.summary.get("frames")

    def describe(self) -> tuple[str, ...]:
        s = self.summary
        num = lambda k: f"{s[k]:,}" if isinstance(s.get(k), int) else "?"
        built = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.built)) if self.built else "?"
        return (self.path.name, num("frames"), num("train_frames"), num("test_frames"),
                ", ".join(s.get("test_rounds") or []) or "–", built)


def read_dataset_summary(path: Path) -> dict:
    """The "summary" object near the start of dataset.json, without reading
    the whole file (it holds every sprite of every frame, hundreds of MB)."""
    try:
        with open(path / DATASET_FILE, encoding="utf-8") as f:
            head = f.read(65536)
    except OSError:
        return {}
    start = head.find('"summary"')
    start = head.find("{", start) if start >= 0 else -1
    if start < 0:
        return {}
    depth = 0
    for i in range(start, len(head)):
        depth += {"{": 1, "}": -1}.get(head[i], 0)
        if depth == 0:
            try:
                return json.loads(head[start:i + 1])
            except ValueError:
                return {}
    return {}


def is_dataset(path: Path) -> bool:
    return (path / DATASET_FILE).is_file()


def datasets_dir(cfg: Config) -> Path:
    """Where named datasets go: replay-datasets/<name>."""
    return cfg.paths.get("replay_datasets", cfg.paths["replay_dataset"].parent / "replay-datasets")


def dataset_path(cfg: Config, name: str) -> Path:
    """Output name -> folder. The default name is the original replay-dataset
    folder, so existing datasets keep working; any other name goes under
    replay-datasets/."""
    name = name.strip()
    legacy = cfg.paths["replay_dataset"]
    return legacy if name == legacy.name else datasets_dir(cfg) / name


def replay_datasets(cfg: Config, extra: list[Path] = ()) -> list[ReplayDataset]:
    """The original replay-dataset, every replay-datasets/* and any extra
    folders, newest first."""
    found = [cfg.paths["replay_dataset"]]
    try:
        # Rebuilds leave .<name>.building / .<name>.previous beside a dataset.
        found += sorted(p for p in datasets_dir(cfg).iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        pass
    found += list(extra)
    seen, out = set(), []
    for p in found:
        if p in seen or not is_dataset(p):
            continue
        seen.add(p)
        out.append(ReplayDataset(p, read_dataset_summary(p), (p / DATASET_FILE).stat().st_mtime))
    return sorted(out, key=lambda d: -d.built)


def check_dataset_name(name: str) -> str | None:
    if not name.strip():
        return "Give the dataset a name."
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name.strip()):
        return "Use letters, digits, dots, dashes and underscores for the name."
    return None


def check_dataset_out(out: str) -> tuple[bool, str]:
    if not out.strip():
        return False, "Choose a folder for the dataset."
    path = Path(out.strip()).expanduser()
    if path.exists() and not path.is_dir():
        return False, f"{path} is a file, not a folder."
    if is_dataset(path):
        return True, f"Replaces the dataset in {path.name}."
    try:
        if path.is_dir() and any(path.iterdir()):
            return False, f"{path} has other files in it and isn't a replay dataset. Pick an empty or new folder."
    except OSError:
        pass
    return True, f"Writes a new dataset to {path}."


def is_replay_model(path: Path) -> bool:
    """A model trained on replays: its config.json names the dataset and start model."""
    try:
        cfg = json.loads((path / "config.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(cfg, dict) and "start" in cfg and "dataset" in cfg


def replay_models(cfg: Config) -> list[Path]:
    """Models trained on replays (Train on replays), newest first, for the live overlay."""
    models = cfg.paths.get("models")
    try:
        found = [p for p in models.iterdir() if (p / "latest.pt").is_file() and is_replay_model(p)] if models else []
    except OSError:
        return []
    return sorted(found, key=lambda p: -(p / "latest.pt").stat().st_mtime)


def overlay_args(floor: bool, pick: bool, threshold: str) -> list[str]:
    args = ["--threshold", f"{float(threshold):g}"]
    if floor:
        args.append("--floor")
    if pick:
        args.append("--pick")
    return args


def text_models(cfg: Config) -> list[Path]:
    """Sprite models a replay model can start from: stage-1 folders with text.npz."""
    models = cfg.paths.get("models")
    try:
        return sorted((p for p in models.iterdir() if (p / "text.npz").is_file() and (p / "config.json").is_file()
                       and not is_replay_model(p)), key=lambda p: p.name.lower()) if models else []
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


def replay_train_args(steps: str, batch: str, minutes: str, keep: str = "", allow_new_data: bool = False) -> list[str]:
    args = ["--steps", str(int(steps)), "--batch", str(int(batch))]
    if minutes.strip():
        args += ["--minutes", minutes.strip()]
    if keep.strip():
        args += ["--keep-checkpoints", str(int(keep))]
    if allow_new_data:
        args.append("--allow-new-data")
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


def check_replay_train(start: Path | None, name: str, dataset: Path | None, steps: str, batch: str,
                       minutes: str, keep: str = "5") -> tuple[bool, str]:
    if start is None:
        return False, "Choose the sprite model to start from (one trained with text)."
    if dataset is None or not is_dataset(dataset):
        return False, "Choose a replay dataset (Build dataset makes one)."
    if not name.strip() or "/" in name or name.strip() in (".", ".."):
        return False, "Give the new model a folder name (no slashes)."
    for problem in (_positive(steps, "Steps"), _positive(batch, "Batch"),
                    _positive(minutes, "Stop after", float, optional=True)):
        if problem:
            return False, problem
    try:
        if keep.strip() and int(keep) < 0:
            raise ValueError
    except ValueError:
        return False, "Keep checkpoints must be a whole number (0 keeps all)."
    out = start.parent / name.strip()
    if out.resolve() == start.resolve():
        return False, "The new model needs its own folder, not the starting model's."
    if out.exists():
        return True, (f"{name.strip()} exists, so training resumes it. It refuses if that model was trained on "
                      "another dataset or start model, unless you tick Resume on new data.")
    return True, f"A new replay model {name.strip()}, starting from {start.name}."
