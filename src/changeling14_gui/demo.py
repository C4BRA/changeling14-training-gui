"""A fake training run that writes a run folder exactly as the pipeline will.

Use it to try the app before changeling14-vision has real steps:

    python -m changeling14_gui.demo --run-dir runs/demo --seconds 60

Frames are 5x5 tiles of 32 px. Grey squares are "objects"; the outline
drawn on each is the "prediction": green when right, red when wrong, and
the boxes settle onto the objects as training goes on.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
from pathlib import Path

from .png import Canvas
from .runfolder import STATUS_FILE, WATCHSET_DIR, write_json_atomic

TILE = 32
GRID = 5
FLOORS = [(58, 62, 70), (64, 68, 76)]
OBJECT_COLORS = [(200, 170, 60), (90, 160, 210), (190, 80, 80), (120, 190, 110), (170, 120, 200)]


def make_scene(rng: random.Random) -> list[tuple[int, int, int, int, tuple]]:
    objects = []
    for _ in range(rng.randint(3, 6)):
        size = rng.choice([12, 16, 20, 24])
        x = rng.randint(0, GRID * TILE - size)
        y = rng.randint(0, GRID * TILE - size)
        objects.append((x, y, size, size, rng.choice(OBJECT_COLORS)))
    return objects


def render(objects, quality: float, rng: random.Random) -> Canvas:
    canvas = Canvas(GRID * TILE, GRID * TILE)
    for ty in range(GRID):
        for tx in range(GRID):
            canvas.fill_rect(tx * TILE, ty * TILE, TILE, TILE, FLOORS[(tx + ty) % 2])
    for x, y, w, h, color in objects:
        canvas.fill_rect(x, y, w, h, color)
    for x, y, w, h, _ in objects:
        error = int((1 - quality) * 14)
        dx, dy = rng.randint(-error, error), rng.randint(-error, error)
        right = rng.random() < 0.2 + 0.75 * quality
        canvas.outline(x + dx - 2, y + dy - 2, w + 4, h + 4, (80, 230, 110) if right else (240, 70, 70))
    return canvas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", default=os.environ.get("C14_RUN_DIR", "."))
    parser.add_argument("--seconds", type=float, default=60.0, help="total run time")
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--checkpoints", type=int, default=8)
    parser.add_argument("--frames", type=int, default=12, help="watch-set frames per checkpoint")
    parser.add_argument("--seed", type=int, default=14)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    scene_rng = random.Random(args.seed)
    scenes = [make_scene(scene_rng) for _ in range(args.frames)]

    stopping = False

    def on_term(signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, on_term)

    every = max(1, args.steps // args.checkpoints)
    delay = args.seconds / args.steps
    start = time.time()
    print(f"demo: {args.steps} steps, watch set every {every} steps, {args.frames} frames", flush=True)
    for step in range(1, args.steps + 1):
        if stopping:
            print("demo: stop requested, saving and exiting", flush=True)
            return 143
        time.sleep(delay)
        loss = 2.5 * (0.97 ** step) + 0.05 * random.random()
        if step % 10 == 0:
            print(f"step {step}/{args.steps}  loss {loss:.3f}", flush=True)
        if step % every == 0 or step == args.steps:
            name = f"step-{step:06d}"
            out = run_dir / WATCHSET_DIR / name
            out.mkdir(parents=True, exist_ok=True)
            quality = step / args.steps
            for i, objects in enumerate(scenes):
                render(objects, quality, random.Random(step * 1000 + i)).save(out / f"frame-{i:02d}.png")
            (out / "meta.json").write_text(json.dumps({"step": step, "time": time.time(), "loss": loss}))
            print(f"checkpoint {name}: watch set saved", flush=True)
        elapsed = time.time() - start
        write_json_atomic(run_dir / STATUS_FILE, {
            "stage": "train",
            "step": step,
            "total": args.steps,
            "message": f"loss {loss:.3f}",
            "eta_seconds": elapsed / step * (args.steps - step),
            "updated_at": time.time(),
        })
    print("demo: done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
