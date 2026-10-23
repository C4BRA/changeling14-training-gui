"""Sprite catalogues and models on disk, for the Train, Benchmark and Export
windows.

Both lists are read from the folders themselves, so a deleted folder simply
drops off. Folders added through "Add…" are remembered in
~/.changeling14/library.json. No torch here: a model is described from its
config.json and metrics.jsonl.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

STORE = Path("~/.changeling14/library.json")
SIZES = ("nano", "tiny", "small", "base")
MODEL_PREFIX = "sprite-pretrain"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class Catalogue:
    path: Path
    repo: Path | None = None
    commit: str = ""
    built: str = ""        # ISO time from manifest.json
    images: int | None = None

    @property
    def codebase(self) -> str:
        return self.repo.name if self.repo else "?"


@dataclass(frozen=True)
class Model:
    path: Path
    catalogue: Path | None
    size: str
    steps: int
    step: int | None = None             # last checkpoint
    scores: dict = field(default_factory=dict)   # last checkpoint that was scored
    checkpoints: tuple[Path, ...] = ()
    text: bool = True
    amp: bool = False

    @property
    def has_latest(self) -> bool:
        return (self.path / "latest.pt").is_file()

    @property
    def duplicates_report(self) -> Path | None:
        """duplicates.html, which training writes when it groups identical pictures."""
        path = self.path / "duplicates.html"
        return path if path.is_file() else None


def is_catalogue(path: Path) -> bool:
    return (path / "manifest.json").is_file() and (path / "catalogue" / "images.jsonl").is_file()


def is_model(path: Path) -> bool:
    """A sprite (stage 1) model folder. Replay models, whose config.json
    names a "start" model and a "dataset", belong to Train on replays."""
    cfg = _read_json(path / "config.json")
    return (path / "config.json").is_file() and not ("start" in cfg and "dataset" in cfg)


def read_catalogue(path: Path) -> Catalogue:
    manifest = _read_json(path / "manifest.json")
    report = _read_json(path / "report.json")
    source = manifest.get("source") or report.get("source") or {}
    images = (report.get("images") or {}).get("written")
    return Catalogue(path=path, repo=Path(source["repo"]) if source.get("repo") else None,
                     commit=str(source.get("commit") or "")[:8], built=str(manifest.get("generated_at", "")),
                     images=images if isinstance(images, int) else None)


def read_model(path: Path) -> Model:
    cfg = _read_json(path / "config.json")
    step, scores = None, {}
    try:
        with open(path / "metrics.jsonl", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                step = row.get("step", step)
                if row.get("unseen_category_top1") is not None:
                    scores = row
    except OSError:
        pass
    checkpoints = tuple(sorted((path / "checkpoints").glob("step-*.pt")))
    return Model(path=path, catalogue=Path(cfg["data"]) if cfg.get("data") else None,
                 size=str(cfg.get("size", "tiny")), steps=int(cfg.get("steps", 20000)),
                 step=step, scores=scores, checkpoints=checkpoints,
                 text=cfg.get("text_model", "x") is not None, amp=bool(cfg.get("amp", False)))


def checkpoint_step(path: Path) -> int | None:
    match = re.fullmatch(r"step-(\d+)\.pt", path.name)
    return int(match.group(1)) if match else None


class Library:
    def __init__(self, cfg: Config, store: Path = STORE):
        self.cfg = cfg
        self.store = store.expanduser()

    # Remembered folders --------------------------------------------------

    def _remembered(self) -> dict:
        data = _read_json(self.store)
        return {kind: [Path(p) for p in data.get(kind, []) if isinstance(p, str)]
                for kind in ("catalogues", "models")}

    def remember(self, kind: str, path: Path) -> None:
        data = {k: [str(p) for p in v] for k, v in self._remembered().items()}
        if str(path) not in data[kind]:
            data[kind].append(str(path))
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_name(self.store.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.store)

    # Discovery -----------------------------------------------------------

    def _scan(self, roots: list[Path], extra: list[Path], test) -> list[Path]:
        found: list[Path] = []

        def add(path: Path) -> None:
            path = path.expanduser()
            if path not in found and test(path):
                found.append(path)

        for root in roots:
            add(root)
            try:
                children = sorted(root.parent.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for child in children:
                if child.is_dir() and not child.name.startswith("."):
                    add(child)
        for path in extra:
            add(path)
        return found

    def catalogues(self) -> list[Catalogue]:
        """The default catalogue first, then its neighbours, then added ones."""
        roots = [p for p in (self.cfg.paths.get("sprite_dataset"),) if p]
        return [read_catalogue(p) for p in self._scan(roots, self._remembered()["catalogues"], is_catalogue)]

    def models(self) -> list[Model]:
        """Models in the models folder, then added ones."""
        found: list[Path] = []
        models = self.cfg.paths.get("models")
        default = self.cfg.paths.get("model_dir")
        for path in ([default] if default else []):
            if is_model(path):
                found.append(path)
        if models:
            try:
                for child in sorted(models.iterdir(), key=lambda p: p.name.lower()):
                    if child.is_dir() and child not in found and is_model(child):
                        found.append(child)
            except OSError:
                pass
        for path in self._remembered()["models"]:
            if path not in found and is_model(path):
                found.append(path)
        return [read_model(p) for p in found]

    def new_model_path(self, catalogue: Path, size: str) -> Path:
        """sprite-pretrain-<size> for the default catalogue, else
        sprite-pretrain-<size>-<catalogue suffix>; -2, -3... if that is taken."""
        models = self.cfg.paths.get("models", self.cfg.runs.parent / "changeling14-models")
        default = self.cfg.paths.get("sprite_dataset")
        name = f"{MODEL_PREFIX}-{size}"
        if not (default and catalogue.resolve() == default.resolve()):
            stem = catalogue.name
            if default and stem.startswith(default.name + "-"):
                stem = stem[len(default.name) + 1:]
            name += "-" + (re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "catalogue")
        path, n = models / name, 1
        while path.exists():
            n += 1
            path = models / f"{name}-{n}"
        return path


def find_catalogue_root(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if is_catalogue(candidate):
            return candidate
    return None


def find_model_root(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if is_model(candidate):
            return candidate
    return None
