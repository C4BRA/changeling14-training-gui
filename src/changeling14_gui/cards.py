"""Plain-language words for the watch set, from a checkpoint's predictions.json.

Each sprite becomes a card: its real name and a verdict (got it, close, or
what the model thought it was). Above the cards, one sentence sums the
checkpoint up against the one before. No Tk here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .runfolder import Checkpoint

GOOD, CLOSE, BAD = "#46be5a", "#dca028", "#d74646"


def display_name(name: str | None) -> str:
    """Entity names are often lower case ("security hud"); capitalise the first letter."""
    name = (name or "?").strip()
    return name[:1].upper() + name[1:]


@dataclass(frozen=True)
class Card:
    title: str
    verdict: str
    colour: str
    row: dict


def card(row: dict) -> Card:
    rank = row.get("rank")
    guesses = row.get("guesses") or []
    if rank == 1:
        verdict, colour = "✓ Got it", GOOD
    elif isinstance(rank, int) and rank > 1:
        verdict, colour = f"≈ Close: {_ordinal(rank)} guess", CLOSE
    elif guesses:
        verdict, colour = f"✗ Thought it was: {display_name(guesses[0].get('name'))}", BAD
    else:
        verdict, colour = "✗ No guess", BAD
    return Card(display_name(row.get("name")), verdict, colour, row)


def _ordinal(n: int) -> str:
    return f"{n}{'tsnrhtdd'[(n // 10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"


@dataclass(frozen=True)
class Tally:
    total: int
    right: int
    close: int


def tally(cp: Checkpoint) -> Tally | None:
    rows = cp.rows
    if not rows:
        return None
    ranks = [r.get("rank") for r in rows]
    return Tally(len(rows), sum(r == 1 for r in ranks), sum(isinstance(r, int) and r > 1 for r in ranks))


def summary(cp: Checkpoint, previous: Checkpoint | None = None) -> str:
    """One sentence for the checkpoint, e.g. "These are 24 sprites the model
    has never trained on. It named 3 right and had 7 more in its top 5, up
    from 1 and 4 at the last checkpoint." Empty for an old-style watch set."""
    t = tally(cp)
    if t is None:
        return ""
    by_group = all(r.get("kind") == "group" for r in cp.rows)
    text = f"These are {t.total} sprites the model has never trained on. "
    if t.right == t.close == 0:
        text += "It hasn't " + ("put any in the right group" if by_group else "named any of them") + " yet"
    else:
        did = "put {} in the right group" if by_group else "named {} right"
        text += f"It {did.format(t.right)} and had {t.close} more in its top 5"
    before = tally(previous) if previous else None
    if before is not None:
        text += f", {_change(t, before)}"
    return text + "."


def _change(now: Tally, before: Tally) -> str:
    if (now.right, now.close) == (before.right, before.close):
        return "no change since the last checkpoint"
    return f"{'up' if (now.right, now.close) > (before.right, before.close) else 'down'} from " \
           f"{before.right} and {before.close} at the last checkpoint"


def trend(checkpoints: list[Checkpoint]) -> list[tuple[int, float, float]]:
    """(step, named right, in top 5) per scored checkpoint, as fractions.

    From meta.json's scores over every unseen sprite when the step records
    them (thousands of sprites, so steadier than the watch set), else from
    the watch set itself."""
    points = []
    for cp in checkpoints:
        if cp.step is None:
            continue
        top1, top5 = cp.meta.get("unseen_text_top1"), cp.meta.get("unseen_text_top5")
        if not isinstance(top1, (int, float)):
            top1, top5 = cp.meta.get("unseen_category_top1"), cp.meta.get("unseen_category_top5")
        if not isinstance(top1, (int, float)):
            t = tally(cp)
            if t is None:
                continue
            top1, top5 = t.right / t.total, (t.right + t.close) / t.total
        points.append((cp.step, float(top1), float(top5) if isinstance(top5, (int, float)) else float(top1)))
    return points
