"""The Tkinter window.

The app never loads a model or a dataset. It starts jobs as separate
programs and reads their run folders, redrawing only what changed.
"""

from __future__ import annotations

import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk

from . import cards, sysinfo
from .config import Config, JobSpec
from .forms import FORMS
from .runfolder import Checkpoint, RunFolder, list_runs
from .runner import Runner

FAST_TICK_MS = 1000   # a job is running or queued
SLOW_TICK_MS = 4000   # nothing happening
FOOTER_EVERY_S = 5.0
STATE_MARK = {"running": "▶", "queued": "…", "done": "✓", "failed": "✗", "stopped": "■",
              "cancelled": "–", "lost": "?"}


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    seconds = int(max(0, seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def open_in_file_browser(path: Path) -> None:
    opener = "open" if sys.platform == "darwin" else "explorer" if sys.platform == "win32" else "xdg-open"
    try:
        subprocess.Popen([opener, str(path)])
    except OSError:
        pass


class WatchSetView(ttk.Frame):
    """Grid of watch-set frames for one checkpoint, with a timeline slider.

    A checkpoint with predictions.json is drawn as cards (real name and a
    plain verdict, click for the model's top guesses) under a one-sentence
    summary and a trend line. Older checkpoints are shown as their pictures.
    """

    CARD_TEXT_H = 38

    def __init__(self, master):
        super().__init__(master, padding=6)
        self.checkpoints: list[Checkpoint] = []
        self.images: list[tk.PhotoImage] = []
        self.shown: tuple | None = None
        self._resize_job = None

        controls = ttk.Frame(self)
        controls.pack(fill="x")
        ttk.Label(controls, text="Checkpoint").pack(side="left")
        self.slider = tk.Scale(controls, from_=0, to=0, orient="horizontal", showvalue=False,
                               command=lambda _v: self.redraw())
        self.slider.pack(side="left", fill="x", expand=True, padx=6)
        self.label = ttk.Label(controls, text="no watch set yet", width=28)
        self.label.pack(side="left")
        ttk.Label(controls, text="Zoom").pack(side="left", padx=(12, 2))
        self.zoom = tk.IntVar(value=1)
        ttk.Spinbox(controls, from_=1, to=4, width=3, textvariable=self.zoom,
                    command=self.redraw, state="readonly").pack(side="left")
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Follow latest", variable=self.follow).pack(side="left", padx=8)

        head = ttk.Frame(self)
        head.pack(fill="x", pady=(6, 0))
        self.summary = ttk.Label(head, text="", wraplength=560, justify="left")
        self.summary.pack(side="left", fill="x", expand=True, anchor="w")
        self.trend = tk.Canvas(head, width=240, height=54, highlightthickness=0, background="#1e2025")
        self.trend.bind("<Configure>", lambda _e: self._draw_trend())

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas = tk.Canvas(body, highlightthickness=0, background="#1e2025")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120) or -1 * (e.delta > 0) or 1, "units"))
        self.name_font = tkfont.Font(family="Helvetica", size=12, weight="bold")
        self.verdict_font = tkfont.Font(family="Helvetica", size=11)

    def set_checkpoints(self, checkpoints: list[Checkpoint]) -> None:
        old_count = len(self.checkpoints)
        at_end = self.slider.get() >= old_count - 1
        self.checkpoints = checkpoints
        self.slider.configure(to=max(0, len(checkpoints) - 1))
        if checkpoints and (self.follow.get() or at_end or old_count == 0):
            self.slider.set(len(checkpoints) - 1)
        self.redraw()

    def _on_resize(self, _event):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, lambda: self.redraw(force=True))

    def redraw(self, force: bool = False) -> None:
        if not self.checkpoints:
            self.canvas.delete("all")
            self.images = []
            self.shown = None
            self.label.configure(text="no watch set yet")
            self.summary.configure(text="")
            self.trend.pack_forget()
            return
        index = min(int(self.slider.get()), len(self.checkpoints) - 1)
        cp = self.checkpoints[index]
        zoom = max(1, min(4, int(self.zoom.get() or 1)))
        width = self.canvas.winfo_width()
        rows = cp.rows
        key = (cp.path, cp.images, len(rows), zoom, width, len(self.checkpoints))
        step = f"step {cp.step:,}" if cp.step is not None else cp.name
        self.label.configure(text=f"{index + 1} / {len(self.checkpoints)} · {step}")
        if key == self.shown and not force:
            return
        self.shown = key
        previous = next((c for c in reversed(self.checkpoints[:index]) if c.rows), None)
        self.summary.configure(text=cards.summary(cp, previous))
        if len(cards.trend(self.checkpoints)) >= 2:
            self.trend.pack(side="right", padx=(8, 0))
            self._draw_trend(cp.step)
        else:
            self.trend.pack_forget()

        self.canvas.delete("all")
        by_image = {r["image"]: r for r in rows}
        images = []
        for path in cp.images:
            try:
                img = tk.PhotoImage(file=str(path))
            except tk.TclError:
                continue  # half-written file; it will be there next poll
            # Integer zoom is nearest-neighbour, so pixel art stays crisp.
            images.append((img.zoom(zoom) if zoom > 1 else img, by_image.get(path.name)))
        self.images = [i for i, _ in images]
        if not images:
            return
        pad = 10
        text_h = self.CARD_TEXT_H if rows else 0
        cell_w = max(i.width() for i, _ in images) + pad
        cell_h = max(i.height() for i, _ in images) + text_h + pad
        cols = max(1, (max(width, cell_w) - pad) // cell_w)
        for n, (img, row) in enumerate(images):
            r, c = divmod(n, cols)
            x, y = pad + c * cell_w, pad + r * cell_h
            tag = f"card{n}"
            self.canvas.create_image(x, y, image=img, anchor="nw", tags=tag)
            if row is None:
                continue
            info = cards.card(row)
            w = img.width()
            self.canvas.create_rectangle(x - 2, y - 2, x + w + 1, y + img.height() + 1,
                                         outline=info.colour, width=3, tags=tag)
            self.canvas.create_text(x, y + img.height() + 6, anchor="nw", fill="#e6e6e6", font=self.name_font,
                                    text=self._fit(info.title, self.name_font, w), tags=tag)
            self.canvas.create_text(x, y + img.height() + 22, anchor="nw", fill=info.colour,
                                    font=self.verdict_font, text=self._fit(info.verdict, self.verdict_font, w),
                                    tags=tag)
            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, i=info: self._details(i))
            self.canvas.tag_bind(tag, "<Enter>", lambda _e: self.canvas.configure(cursor="hand2"))
            self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.configure(cursor=""))
        rows_n = (len(images) + cols - 1) // cols
        self.canvas.configure(scrollregion=(0, 0, cols * cell_w + pad, rows_n * cell_h + pad))

    @staticmethod
    def _fit(text: str, font: tkfont.Font, width: int) -> str:
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _draw_trend(self, current_step: int | None = None) -> None:
        points = cards.trend(self.checkpoints)
        c = self.trend
        c.delete("all")
        if len(points) < 2:
            return
        w, h = int(c.winfo_width()) or 240, int(c.winfo_height()) or 54
        top_pad, bottom = 14, h - 4
        steps = [p[0] for p in points]
        peak = max(max(p[2] for p in points), 0.01)
        lo, hi = min(steps), max(steps) or 1

        def xy(step, value):
            return (4 + (w - 8) * (step - lo) / max(1, hi - lo), bottom - (bottom - top_pad) * value / peak)

        for series, colour in ((2, cards.CLOSE), (1, cards.GOOD)):
            coords = [v for p in points for v in xy(p[0], p[series])]
            c.create_line(*coords, fill=colour, width=2)
        if current_step is not None and lo <= current_step <= hi:
            x = xy(current_step, 0)[0]
            c.create_line(x, top_pad, x, bottom, fill="#777", dash=(2, 2))
        last = points[-1]
        c.create_text(4, 1, anchor="nw", fill="#bbb", font=("Helvetica", 10),
                      text=f"All unseen sprites: right {last[1]:.1%} · top 5 {last[2]:.1%}")

    def _details(self, info: cards.Card) -> None:
        row = info.row
        top = tk.Toplevel(self)
        top.title(info.title)
        top.transient(self.winfo_toplevel())
        body = ttk.Frame(top, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=info.title, font=self.name_font).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text=info.verdict, foreground=info.colour).grid(row=1, column=0, columnspan=3, sticky="w")
        names = row.get("names") or []
        if len(names) > 1:
            also = ", ".join(cards.display_name(n) for n in names[1:4]) + ("…" if len(names) > 4 else "")
            ttk.Label(body, text=f"Also counts as right: {also}", foreground="#666",
                      wraplength=360).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="The model's top guesses").grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 4))
        wanted = {n.lower() for n in names}
        guesses = row.get("guesses") or []
        best = max([g.get("score", 0) for g in guesses] + [1e-9])
        for k, g in enumerate(guesses):
            right = str(g.get("name", "")).lower() in wanted
            ttk.Label(body, text=f"{k + 1}.").grid(row=4 + k, column=0, sticky="w")
            ttk.Label(body, text=cards.display_name(g.get("name")),
                      foreground=cards.GOOD if right else "").grid(row=4 + k, column=1, sticky="w", padx=6)
            bar = tk.Canvas(body, width=120, height=10, highlightthickness=0)
            bar.grid(row=4 + k, column=2, sticky="w")
            bar.create_rectangle(0, 0, 120 * g.get("score", 0) / best, 10,
                                 fill=cards.GOOD if right else "#8a8f99", width=0)
        detail = [f"Kind: {row.get('group', '?')}", f"Sprite: {row.get('sheet', '?')} / {row.get('state', '?')}"]
        if guesses:
            detail.append("Match scores: " + ", ".join(f"{g.get('score', 0):.2f}" for g in guesses))
        ttk.Label(body, text="\n".join(detail), foreground="#666", justify="left").grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(body, text="Close", command=top.destroy).grid(row=11, column=2, sticky="e", pady=(10, 0))
        top.bind("<Escape>", lambda _e: top.destroy())


class App:
    def __init__(self, root: tk.Tk, cfg: Config):
        self.root = root
        self.cfg = cfg
        self.runner = Runner(cfg)
        self.runs: list[RunFolder] = []
        self.selected: RunFolder | None = None
        self.selected_sig: tuple | None = None
        self.runs_sig = None
        self.last_footer = 0.0

        root.title("Changeling14 Training")
        root.geometry("1100x720")
        root.minsize(820, 520)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build()
        self.refresh_runs(select_newest=True)
        self.tick()

    # Layout --------------------------------------------------------------

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        side = ttk.Frame(outer)
        side.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        jobs = ttk.LabelFrame(side, text="Jobs", padding=6)
        jobs.pack(fill="x")
        self.hint = ttk.Label(side, text="", wraplength=230, foreground="#666")
        for job in self.cfg.jobs:
            btn = ttk.Button(jobs, text=job.label, command=lambda j=job: self.start_job(j))
            btn.pack(fill="x", pady=2)
            if not job.enabled:
                btn.state(["disabled"])
            hint = job.description + ("" if job.enabled else " (not available yet)")
            btn.bind("<Enter>", lambda _e, h=hint: self.hint.configure(text=h))
            btn.bind("<Leave>", lambda _e: self.hint.configure(text=""))
        ttk.Button(jobs, text="Open replay inbox", command=self.open_inbox).pack(fill="x", pady=(8, 2))
        self.hint.pack(fill="x", pady=4)

        queue = ttk.LabelFrame(side, text="Running and queued", padding=6)
        queue.pack(fill="both", expand=True, pady=(4, 0))
        self.queue_list = tk.Listbox(queue, height=8, width=32, activestyle="none")
        self.queue_list.pack(fill="both", expand=True)
        self.queue_list.bind("<<ListboxSelect>>", self.on_queue_select)
        ttk.Button(queue, text="Stop / remove", command=self.stop_selected).pack(fill="x", pady=(6, 0))
        self.queue_runs = []

        main = ttk.Frame(outer)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Run").pack(side="left")
        self.run_var = tk.StringVar()
        self.run_box = ttk.Combobox(top, textvariable=self.run_var, state="readonly", width=48)
        self.run_box.pack(side="left", padx=6)
        self.run_box.bind("<<ComboboxSelected>>", self.on_run_select)
        ttk.Button(top, text="Open folder", command=self.open_run_folder).pack(side="left")
        self.state_label = ttk.Label(top, text="")
        self.state_label.pack(side="left", padx=10)

        prog = ttk.Frame(main, padding=(0, 8))
        prog.grid(row=1, column=0, sticky="ew")
        prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=1000)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.status_label = ttk.Label(prog, text="")
        self.status_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.eta_label = ttk.Label(prog, text="")
        self.eta_label.grid(row=1, column=1, sticky="e", pady=(4, 0))

        tabs = ttk.Notebook(main)
        tabs.grid(row=2, column=0, sticky="nsew")
        self.watchset = WatchSetView(tabs)
        tabs.add(self.watchset, text="Watch set")
        log_frame = ttk.Frame(tabs, padding=6)
        self.log = tk.Text(log_frame, wrap="none", font=("Menlo", 11) if sys.platform == "darwin" else ("monospace", 10),
                           state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        tabs.add(log_frame, text="Log")

        self.footer = ttk.Label(self.root, text="", padding=(8, 2, 8, 6), foreground="#555")
        self.footer.pack(fill="x", side="bottom")

    # Actions -------------------------------------------------------------

    def start_job(self, job: JobSpec) -> None:
        overrides, extra = None, None
        if job.form:
            form = FORMS.get(job.form)
            if form is None:
                messagebox.showerror("Could not start job", f"{job.label}: unknown form {job.form!r}",
                                     parent=self.root)
                return
            chosen = form(self.root, self.cfg, job, self.busy_models()).wait()
            if chosen is None:
                return
            overrides, extra = chosen
        try:
            run = self.runner.submit(job, overrides, extra)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Could not start job", str(exc), parent=self.root)
            return
        self.refresh_runs()
        if run in self.runner.active:
            # Queued jobs don't take over the view from the one that's running.
            self.select_run(run.folder.path)
        self.refresh_queue()
        self.tick(reschedule=False)

    def busy_models(self) -> set[Path]:
        """Model folders (--out) of the jobs running or queued now."""
        busy = set()
        for run in self.runner.active + self.runner.pending:
            command = run.folder.run_info().get("command") or []
            if "--out" in command[:-1]:
                busy.add(Path(command[command.index("--out") + 1]))
        return busy

    def stop_selected(self) -> None:
        sel = self.queue_list.curselection()
        if not sel:
            return
        run = self.queue_runs[sel[0]]
        if run in self.runner.active and not messagebox.askyesno(
                "Stop job", f"Stop {run.job.label}? It gets a few seconds to save, then it is ended.",
                parent=self.root):
            return
        self.runner.stop(run)
        self.refresh_queue()

    def open_inbox(self) -> None:
        self.cfg.inbox.mkdir(parents=True, exist_ok=True)
        open_in_file_browser(self.cfg.inbox)

    def open_run_folder(self) -> None:
        if self.selected:
            open_in_file_browser(self.selected.path)

    def on_queue_select(self, _event=None) -> None:
        sel = self.queue_list.curselection()
        if sel:
            self.select_run(self.queue_runs[sel[0]].folder.path)

    def on_run_select(self, _event=None) -> None:
        index = self.run_box.current()
        if 0 <= index < len(self.runs):
            self.select_run(self.runs[index].path)

    def select_run(self, path: Path) -> None:
        self.selected = RunFolder(path)
        self.selected_sig = None
        for i, run in enumerate(self.runs):
            if run.path == path:
                self.run_box.current(i)
                break
        self.watchset.checkpoints = []
        self.refresh_selected()

    def on_close(self) -> None:
        if self.runner.active or self.runner.pending:
            answer = messagebox.askyesnocancel(
                "Jobs still running",
                "Running jobs keep going after the app closes, and you can reopen the app to watch them.\n\n"
                "Stop them before closing? Queued jobs that haven't started are cancelled either way.",
                parent=self.root)
            if answer is None:
                return
            for run in list(self.runner.pending):
                self.runner.stop(run)
            if answer:
                self.runner.stop_all()
        self.root.destroy()

    # Refresh -------------------------------------------------------------

    def tick(self, reschedule: bool = True) -> None:
        changed = self.runner.poll()
        self.refresh_runs()
        if changed:
            self.refresh_queue()
        self.refresh_selected()
        now = time.time()
        if now - self.last_footer >= FOOTER_EVERY_S:
            self.last_footer = now
            self.footer.configure(text=sysinfo.snapshot(self.cfg.runs).describe())
        if reschedule:
            busy = self.runner.active or self.runner.pending or (
                self.selected is not None and self.selected.state() == "running")
            self.root.after(FAST_TICK_MS if busy else SLOW_TICK_MS, self.tick)

    def refresh_runs(self, select_newest: bool = False) -> None:
        try:
            sig = self.cfg.runs.stat().st_mtime_ns
        except OSError:
            sig = None
        if sig == self.runs_sig and not select_newest:
            return
        self.runs_sig = sig
        self.runs = list_runs(self.cfg.runs)
        self.run_box.configure(values=[r.name for r in self.runs])
        if select_newest and self.runs:
            self.select_run(self.runs[0].path)
        elif self.selected:
            for i, run in enumerate(self.runs):
                if run.path == self.selected.path:
                    self.run_box.current(i)
        self.refresh_queue()

    def refresh_queue(self) -> None:
        self.queue_runs = list(self.runner.active) + list(self.runner.pending)
        self.queue_list.delete(0, "end")
        for run in self.queue_runs:
            mark = "▶" if run in self.runner.active else "…"
            if run.stop_sent_at:
                mark = "■"
            self.queue_list.insert("end", f"{mark} {run.job.label}  ({run.folder.name[:15]})")
        if not self.queue_runs:
            self.queue_list.insert("end", "Nothing running")

    def refresh_selected(self) -> None:
        run = self.selected
        if run is None:
            self.state_label.configure(text=f"No runs yet in {self.cfg.runs}")
            return
        state = run.state()
        self.state_label.configure(text=f"{STATE_MARK.get(state, '')} {state}")
        status = run.status()
        if state == "running":
            self.eta_label.configure(text=f"About {format_duration(run.eta_seconds())} left")
        else:
            info = run.run_info()
            started, ended = info.get("started_at"), info.get("ended_at")
            took = f"took {format_duration(ended - started)}" if started and ended else ""
            self.eta_label.configure(text=took)

        sig = run.signature()
        if sig == self.selected_sig:
            return
        self.selected_sig = sig

        frac = status.fraction
        self.progress.configure(value=int((frac or (1.0 if state == "done" else 0.0)) * 1000))
        parts = []
        if status.step is not None:
            parts.append(f"Step {status.step:,}" + (f" / {status.total:,}" if status.total else ""))
        if status.stage:
            parts.append(status.stage)
        if status.message:
            parts.append(status.message)
        self.status_label.configure(text="  ·  ".join(parts))

        self._set_log(run.console_tail())
        self.watchset.set_checkpoints(run.checkpoints())

    def _set_log(self, text: str) -> None:
        at_bottom = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", text)
        self.log.configure(state="disabled")
        if at_bottom:
            self.log.see("end")


def run_app(cfg: Config) -> None:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("aqua" if sys.platform == "darwin" else "clam")
    except tk.TclError:
        pass
    App(root, cfg)
    root.mainloop()
