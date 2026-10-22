"""Find SS14 checkouts to build a sprite catalogue from, and where it goes.

Plain functions, no Tk, so the sprite catalogue window stays thin and
this part can be tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config

NEEDS = ("Resources/Textures", "Resources/Prototypes/Entities")


def is_checkout(path: Path) -> bool:
    return all((path / part).is_dir() for part in NEEDS)


def checkout_root(path: Path) -> Path | None:
    """The checkout holding `path`, which may be the checkout itself or a
    folder inside it such as Resources/Textures."""
    path = path.expanduser()
    for candidate in (path, *path.parents):
        if is_checkout(candidate):
            return candidate
    return None


def find_checkouts(cfg: Config) -> list[Path]:
    """SS14 checkouts next to the configured Wizden one, Wizden first."""
    wizden = cfg.paths.get("wizden")
    found = []
    if wizden and is_checkout(wizden):
        found.append(wizden)
    for parent in {p.parent for p in (wizden, cfg.paths.get("sprite_dataset")) if p}:
        try:
            children = sorted(parent.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and not child.name.startswith(".") and child not in found and is_checkout(child):
                found.append(child)
    return found


def default_out(repo: Path, cfg: Config) -> Path:
    """Wizden builds into sprite_dataset; any other codebase gets its own
    folder beside it, so one build never replaces another's catalogue."""
    base = cfg.paths.get("sprite_dataset", cfg.runs.parent / "ss14-sprite-dataset")
    wizden = cfg.paths.get("wizden")
    if wizden and repo.resolve() == wizden.resolve():
        return base
    slug = re.sub(r"[^a-z0-9]+", "-", repo.name.lower()).strip("-") or "codebase"
    return base.with_name(f"{base.name}-{slug}")


@dataclass(frozen=True)
class Check:
    ok: bool
    message: str


def check_choice(repo: Path | None, out: Path | None) -> Check:
    """Whether the sprites step will accept this codebase and output folder."""
    if repo is None:
        return Check(False, "Choose a codebase.")
    if not is_checkout(repo):
        missing = [part for part in NEEDS if not (repo / part).is_dir()]
        return Check(False, f"{repo} has no {' or '.join(missing)}. The catalogue links sprites to "
                            "entities, so it needs an SS14 checkout.")
    if out is None or not str(out).strip():
        return Check(False, "Choose an output folder.")
    out_r, repo_r = out.expanduser().resolve(), repo.resolve()
    if out_r == repo_r or repo_r in out_r.parents:
        return Check(False, "The output folder is inside the codebase. Pick one outside it.")
    try:
        filled = out_r.is_dir() and any(out_r.iterdir())
    except OSError:
        filled = False
    if out_r.exists() and not out_r.is_dir():
        return Check(False, f"{out_r} is a file, not a folder.")
    if filled and not (out_r / "manifest.json").exists():
        return Check(False, f"{out_r} has other files in it and isn't an earlier catalogue. Pick an empty folder.")
    if filled:
        return Check(True, f"The catalogue already in {out_r.name} will be rebuilt.")
    return Check(True, f"A new catalogue will be written to {out_r}.")
