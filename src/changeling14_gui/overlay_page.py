"""The Live overlay page: pick a replay-trained model and run
`changeling14-vision replays overlay` on top of the SS14 window, with live
status (fps, ms per stage, engine, window found, where the game view is).

The overlay runs as a separate program (it needs its own macOS run loop); this
page starts it, reads the STATUS lines it prints, and stops it cleanly.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

from . import replays
from .config import Config

ENGINES = {"Neural Engine (Core ML)": "coreml", "GPU (PyTorch)": "torch"}
VIEWS = {"Auto (found from the game)": "auto", "Manual (my own box)": "manual"}
PERMISSION_HINT = ("macOS hasn't given screen recording to the app that started this one. Turn it on in System "
                   "Settings > Privacy & Security > Screen Recording for Terminal (or whichever app you launched "
                   "changeling14-gui from), quit and reopen that app, then start the overlay again.")
STAGES = ("capture", "resize", "model", "names")


@dataclass(frozen=True)
class OverlayModel:
    path: Path
    step: int | None
    name_top5: float | None
    neural_sizes: tuple[str, ...]          # exported Core ML input sizes, e.g. ("288x320",)

    def row(self) -> tuple[str, ...]:
        step = f"{self.step:,}" if self.step is not None else "?"
        score = f"{self.name_top5:.0%}" if isinstance(self.name_top5, (int, float)) else "–"
        ne = ", ".join(self.neural_sizes) if self.neural_sizes else "not yet (converts on first start)"
        return self.path.name, step, score, ne


def describe_models(cfg: Config) -> list[OverlayModel]:
    out = []
    for path in replays.replay_models(cfg):
        step = score = None
        try:
            for line in (path / "metrics.jsonl").read_text().splitlines():
                row = json.loads(line)
                step = row.get("step", step)
                if row.get("name_top5") is not None:
                    score = row["name_top5"]
        except (OSError, ValueError):
            pass
        sizes = tuple(p.stem.split("_", 1)[1] for p in sorted((path / "coreml").glob("detect_*x*.mlpackage")))
        out.append(OverlayModel(path, step, score, sizes))
    return out


def overlay_command(model: Path, engine: str, threshold: float, name_threshold: float, fps: float,
                    tile_px: str, labels: bool, floor: bool, pick: bool, view: str = "auto") -> list[str]:
    """pick drags a new game-view box, which makes the view manual."""
    cmd = [sys.executable, "-m", "changeling14_vision", "replays", "overlay", str(model), "--backend", engine,
           "--threshold", f"{threshold:g}", "--name-threshold", f"{name_threshold:g}", "--fps", f"{fps:g}",
           "--view", "manual" if pick else view]
    if tile_px.strip():
        cmd += ["--tile-px", f"{float(tile_px):g}"]
    if not labels:
        cmd.append("--no-labels")
    if floor:
        cmd.append("--floor")
    if pick:
        cmd.append("--pick")
    return cmd


def parse_status(line: str) -> dict | None:
    if not line.startswith("STATUS "):
        return None
    try:
        return json.loads(line[len("STATUS "):])
    except ValueError:
        return None


def status_text(s: dict) -> str:
    if not s.get("window", True):
        return "SS14 window not found or not on screen (open the game, un-minimise it)"
    ms = s.get("ms") or {}
    parts = ", ".join(f"{k} {ms[k]:.0f}" for k in STAGES if k in ms)
    tile = s.get("tile_px")
    return (f"{s.get('fps', 0):.1f} fps on {s.get('engine', '?')}  ·  ms: {parts}  ·  "
            f"{s.get('things', 0)} things  ·  tile {tile:.0f} px ({s.get('tile_source', '?')})"
            if tile else f"{s.get('fps', 0):.1f} fps on {s.get('engine', '?')}")


def view_text(s: dict) -> tuple[str, bool]:
    """The game-view line, and whether it needs attention (holding an old
    view, unsure, or found nothing to check against)."""
    v = s.get("view") or {}
    if v.get("mode") == "manual":
        return "Game view: manual (your box; Re-pick game view to change it)", False
    if v.get("mode") != "auto":
        return "", False
    x, y, w, h = v.get("rect_pt") or (0, 0, 0, 0)
    conf = v.get("confidence", 0)
    text = (f"Game view: auto, {w}x{h} pt at {x},{y}  ·  tile {v.get('tile_pt', 0):.0f} pt"
            + (f" (zoom {v['zoom']:g})" if v.get("zoom", 1) != 1 else "")
            + f"  ·  from {v.get('source', '?')}, {conf:.0%} sure  ·  {v.get('hud_dropped', 0)} HUD detections hidden")
    note = v.get("note") or ""
    if note:
        text += f"\n{note}"
    return text, conf < 0.6 or note.startswith("holding")


class OverlayPage(ttk.Frame):
    def __init__(self, master, cfg: Config):
        super().__init__(master, padding=10)
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.running_model: Path | None = None
        self.columnconfigure(0, weight=1)

        ttk.Label(self, text="Model (trained on replays)").grid(row=0, column=0, sticky="w")
        cols = (("model", "Model", 260), ("step", "Step", 80), ("score", "Naming top-5", 100),
                ("ne", "Neural Engine sizes", 260))
        self.tree = ttk.Treeview(self, columns=[c[0] for c in cols], show="headings", height=5, selectmode="browse")
        for key, title, width in cols:
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=width, anchor="w", stretch=key in ("model", "ne"))
        self.tree.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_model())
        side = ttk.Frame(self)
        side.grid(row=1, column=1, sticky="n", padx=(8, 0), pady=(4, 0))
        ttk.Button(side, text="Refresh", command=self.load_models).pack(fill="x")
        self.convert_btn = ttk.Button(side, text="Convert for Neural Engine", command=self.convert)
        self.convert_btn.pack(fill="x", pady=(4, 0))

        opts = ttk.LabelFrame(self, text="Settings", padding=8)
        opts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(opts, text="Engine").grid(row=0, column=0, sticky="w")
        self.engine = tk.StringVar(value=next(iter(ENGINES)))
        engine_box = ttk.Combobox(opts, textvariable=self.engine, values=list(ENGINES), state="readonly", width=24)
        engine_box.grid(row=0, column=1, sticky="w", padx=(6, 16))
        engine_box.bind("<<ComboboxSelected>>", lambda _e: self.restart_if_running())
        ttk.Label(opts, text="Game view").grid(row=2, column=2, sticky="w", padx=(0, 6), pady=(6, 0))
        self.view = tk.StringVar(value=next(iter(VIEWS)))
        view_box = ttk.Combobox(opts, textvariable=self.view, values=list(VIEWS), state="readonly", width=26)
        view_box.grid(row=2, column=3, columnspan=2, sticky="w", pady=(6, 0))
        view_box.bind("<<ComboboxSelected>>", lambda _e: self.restart_if_running())
        self.threshold, self.name_threshold = tk.DoubleVar(value=0.5), tk.DoubleVar(value=0.6)
        self.fps, self.tile = tk.StringVar(value="30"), tk.StringVar(value="")
        self.labels, self.floor = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        self._scale(opts, 0, 2, "Threshold", self.threshold)
        self._scale(opts, 1, 2, "Name threshold", self.name_threshold)
        ttk.Label(opts, text="Fps cap").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(opts, from_=1, to=60, textvariable=self.fps, width=6).grid(row=1, column=1, sticky="w", padx=6,
                                                                                pady=(6, 0))
        ttk.Label(opts, text="Tile size px (empty: auto)").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(opts, textvariable=self.tile, width=8).grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))
        checks = ttk.Frame(opts)
        checks.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(checks, text="Labels", variable=self.labels).pack(side="left")
        ttk.Checkbutton(checks, text="Floor tiles and gas", variable=self.floor).pack(side="left", padx=12)
        ttk.Label(opts, text="Settings apply when the overlay (re)starts; the overlay's own panel changes "
                             "threshold, tile size, labels, floor and auto/manual view while it runs. Auto finds the "
                             "game view from SS14's settings, the floor grid and your character, and hides "
                             "detections under the HUD.",
                  wraplength=760, justify="left",
                  foreground="#666").grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.start_btn = ttk.Button(buttons, text="Start overlay", command=self.toggle)
        self.start_btn.pack(side="left")
        ttk.Button(buttons, text="Re-pick game view (manual)", command=self.repick).pack(side="left", padx=(8, 0))

        stat = ttk.LabelFrame(self, text="Status", padding=8)
        stat.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        stat.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self.state_label = ttk.Label(stat, text="Stopped")
        self.state_label.grid(row=0, column=0, sticky="w")
        self.status_label = ttk.Label(stat, text="", foreground="#444")
        self.status_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.view_label = ttk.Label(stat, text="", foreground="#444", wraplength=760, justify="left")
        self.view_label.grid(row=5, column=0, sticky="w", pady=(2, 0))
        self.warn_label = ttk.Label(stat, text="", foreground="#b00020", wraplength=760, justify="left")
        self.warn_label.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.log = tk.Text(stat, height=8, wrap="word", state="disabled",
                           font=("Menlo", 10) if sys.platform == "darwin" else ("monospace", 9))
        self.view_label.grid_configure(row=2)
        self.warn_label.grid_configure(row=3)
        self.log.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        stat.rowconfigure(4, weight=1)

        self.load_models()
        self.after(200, self.poll)

    def _scale(self, parent, row, col, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=(6 if row else 0, 0))
        value = ttk.Label(parent, text=f"{var.get():.2f}", width=5)
        value.grid(row=row, column=col + 2, sticky="w", pady=(6 if row else 0, 0))
        ttk.Scale(parent, from_=0.1, to=0.95, variable=var, length=160,
                  command=lambda v: value.configure(text=f"{float(v):.2f}")).grid(row=row, column=col + 1, sticky="w",
                                                                                 pady=(6 if row else 0, 0))

    # Models ---------------------------------------------------------------

    def load_models(self) -> None:
        keep = self.selected()
        self.models = describe_models(self.cfg)
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.models):
            self.tree.insert("", "end", iid=str(i), values=m.row())
        index = next((i for i, m in enumerate(self.models) if keep and m.path == keep.path), 0 if self.models else None)
        if index is not None:
            self.tree.selection_set(str(index))
        self.on_model()

    def selected(self) -> OverlayModel | None:
        sel = self.tree.selection() if hasattr(self, "models") else ()
        return self.models[int(sel[0])] if sel and int(sel[0]) < len(self.models) else None

    def on_model(self) -> None:
        m = self.selected()
        self.start_btn.state(["!disabled"] if m else ["disabled"])
        self.convert_btn.state(["!disabled"] if m else ["disabled"])
        if not self.models:
            self.state_label.configure(text="No replay-trained model yet (Train on replays makes one).")
        if m and self.proc and self.running_model != m.path:
            self.restart_if_running()

    # Running --------------------------------------------------------------

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def toggle(self) -> None:
        self.stop() if self.running() else self.start()

    def start(self, pick: bool | None = None) -> None:
        m = self.selected()
        if m is None:
            return
        try:
            cmd = overlay_command(m.path, ENGINES[self.engine.get()], self.threshold.get(), self.name_threshold.get(),
                                  float(self.fps.get()), self.tile.get(), self.labels.get(), self.floor.get(),
                                  bool(pick), VIEWS[self.view.get()])
        except ValueError:
            self.warn_label.configure(text="Fps cap and tile size must be numbers (tile size may be empty).")
            return
        self.warn_label.configure(text="")
        self._log_clear()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                     start_new_session=True, env=dict(os.environ, PYTHONUNBUFFERED="1"))
        self.running_model = m.path
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self.start_btn.configure(text="Stop overlay")
        self.state_label.configure(text=f"Starting {m.path.name} on {self.engine.get()}…")

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.lines.put(line.rstrip("\n"))
        self.lines.put("\0exit")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            for sig, wait in ((signal.SIGINT, 4), (signal.SIGTERM, 3)):
                try:
                    os.killpg(proc.pid, sig)        # SIGINT is the overlay's own clean quit
                    proc.wait(timeout=wait)
                    break
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    continue
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.running_model = None
        self.start_btn.configure(text="Start overlay")
        self.state_label.configure(text="Stopped")

    def restart_if_running(self) -> None:
        if self.running():
            self.stop()
            self.start()

    def repick(self) -> None:
        if self.running():
            self.stop()
        self.view.set(list(VIEWS)[1])       # a hand-picked box replaces the automatic view
        self.start(pick=True)

    def convert(self) -> None:
        m = self.selected()
        if m is None:
            return
        size = getattr(self, "last_input", None) or "288x320"
        cmd = [sys.executable, "-m", "changeling14_vision", "replays", "coreml", str(m.path), "--size", size]
        self._log(f"converting {m.path.name} for the Neural Engine at {size}…")
        self.convert_btn.state(["disabled"])

        def work():
            res = subprocess.run(cmd, capture_output=True, text=True)
            for line in (res.stdout + res.stderr).splitlines():
                if line.startswith("coreml:"):
                    self.lines.put(line)
            self.lines.put("\0converted" if res.returncode == 0 else f"\0convert-failed {res.returncode}")
        threading.Thread(target=work, daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                line = self.lines.get_nowait()
                if line == "\0exit":
                    if self.proc is not None and self.proc.poll() is not None:
                        code = self.proc.returncode
                        self.proc, self.running_model = None, None
                        self.start_btn.configure(text="Start overlay")
                        self.state_label.configure(text=f"Overlay stopped (exit {code})")
                    continue
                if line.startswith("\0convert"):
                    self.convert_btn.state(["!disabled"])
                    self.load_models()
                    if line != "\0converted":
                        self._log(f"conversion failed ({line.split()[-1]})")
                    continue
                s = parse_status(line)
                if s is not None:
                    self.state_label.configure(text="Running" if s.get("window", True) else "Waiting for the game")
                    self.status_label.configure(text=status_text(s))
                    text, attention = view_text(s)
                    self.view_label.configure(text=text, foreground="#b06000" if attention else "#444")
                    if s.get("input"):
                        self.last_input = f"{s['input'][0]}x{s['input'][1]}"
                    continue
                if "Screen Recording permission" in line:
                    self.warn_label.configure(text=PERMISSION_HINT)
                if line.startswith("no SS14 client window found"):
                    self.warn_label.configure(text="No SS14 window found. Open the game (launcher, any server or the "
                                                   "main menu) and start the overlay again.")
                self._log(line)
        except queue.Empty:
            pass
        self.after(200, self.poll)

    def _log(self, line: str) -> None:
        if not line.strip() or any(s in line for s in ("has not been tested with coremltools", "scikit-learn version",
                                                       "Running MIL", "Converting PyTorch", "Tuple detected")):
            return
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.delete("1.0", "end-300l")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_clear(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
