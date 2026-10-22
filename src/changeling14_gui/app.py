"""The Tkinter window.

The app never loads a model or a dataset. It starts jobs as separate
programs and reads their run folders, redrawing only what changed.
"""

from __future__ import annotations

import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import codebases, sysinfo
from .config import Config, JobSpec
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
    """Grid of watch-set frames for one checkpoint, with a timeline slider."""

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
        self.zoom = tk.IntVar(value=2)
        ttk.Spinbox(controls, from_=1, to=4, width=3, textvariable=self.zoom,
                    command=self.redraw, state="readonly").pack(side="left")
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Follow latest", variable=self.follow).pack(side="left", padx=8)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas = tk.Canvas(body, highlightthickness=0, background="#1e2025")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120) or -1 * (e.delta > 0) or 1, "units"))

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
            return
        index = min(int(self.slider.get()), len(self.checkpoints) - 1)
        cp = self.checkpoints[index]
        zoom = max(1, min(4, int(self.zoom.get() or 1)))
        width = self.canvas.winfo_width()
        key = (cp.path, cp.images, zoom, width)
        step = f"step {cp.step}" if cp.step is not None else cp.name
        self.label.configure(text=f"{index + 1} / {len(self.checkpoints)} · {step}")
        if key == self.shown and not force:
            return
        self.shown = key

        self.canvas.delete("all")
        images = []
        for path in cp.images:
            try:
                img = tk.PhotoImage(file=str(path))
            except tk.TclError:
                continue  # half-written file; it will be there next poll
            # Integer zoom is nearest-neighbour, so pixel art stays crisp.
            images.append(img.zoom(zoom) if zoom > 1 else img)
        self.images = images
        if not images:
            return
        pad = 8
        cell_w = max(i.width() for i in images) + pad
        cell_h = max(i.height() for i in images) + pad
        cols = max(1, (max(width, cell_w) - pad) // cell_w)
        for n, img in enumerate(images):
            r, c = divmod(n, cols)
            self.canvas.create_image(pad + c * cell_w, pad + r * cell_h, image=img, anchor="nw")
        rows = (len(images) + cols - 1) // cols
        self.canvas.configure(scrollregion=(0, 0, cols * cell_w + pad, rows * cell_h + pad))


class SpriteCatalogueWindow:
    """Pick the codebase and output folder for a sprite catalogue build.

    After wait() returns, `result` is the {wizden, sprite_dataset}
    overrides for the run, or None if cancelled.
    """

    def __init__(self, parent: tk.Tk, cfg: Config):
        self.cfg = cfg
        self.result: dict[str, Path] | None = None
        self.checkouts = codebases.find_checkouts(cfg)
        self.repo: Path | None = None
        self.out_touched = False  # the user typed or browsed an output folder

        top = self.top = tk.Toplevel(parent)
        top.title("Build sprite catalogue")
        top.transient(parent)
        top.resizable(True, False)
        top.protocol("WM_DELETE_WINDOW", self.cancel)
        body = ttk.Frame(top, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text="Codebase to build from").grid(row=0, column=0, columnspan=2, sticky="w")
        self.repo_list = tk.Listbox(body, height=min(8, max(3, len(self.checkouts))), width=64,
                                    activestyle="none", exportselection=False)
        self.repo_list.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        for path in self.checkouts:
            self.repo_list.insert("end", f"{path.name}    {path}")
        if not self.checkouts:
            self.repo_list.insert("end", "No SS14 checkouts found; choose a folder")
        self.repo_list.bind("<<ListboxSelect>>", self.on_list_select)
        self.repo_list.bind("<Double-Button-1>", lambda _e: self.build())
        ttk.Button(body, text="Choose folder…", command=self.choose_repo).grid(row=1, column=1, sticky="n",
                                                                              padx=(8, 0), pady=(4, 0))
        self.repo_label = ttk.Label(body, text="", foreground="#666")
        self.repo_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 10))

        ttk.Label(body, text="Output folder").grid(row=3, column=0, columnspan=2, sticky="w")
        self.out_var = tk.StringVar()
        out_entry = ttk.Entry(body, textvariable=self.out_var)
        out_entry.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        out_entry.bind("<Key>", lambda _e: setattr(self, "out_touched", True))
        self.out_var.trace_add("write", lambda *_a: self.refresh())
        ttk.Button(body, text="Browse…", command=self.choose_out).grid(row=4, column=1, padx=(8, 0), pady=(4, 0))

        self.message = ttk.Label(body, text="", wraplength=520, justify="left")
        self.message.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.note = ttk.Label(body, text="", wraplength=520, justify="left", foreground="#666")
        self.note.grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="left")
        self.build_btn = ttk.Button(buttons, text="Build", command=self.build, default="active")
        self.build_btn.pack(side="left", padx=(8, 0))
        top.bind("<Return>", lambda _e: self.build())
        top.bind("<Escape>", lambda _e: self.cancel())

        if self.checkouts:
            self.repo_list.selection_set(0)
            self.set_repo(self.checkouts[0])
        else:
            self.refresh()

    def wait(self) -> dict[str, Path] | None:
        self.top.grab_set()
        self.top.focus_set()
        self.top.wait_window()
        return self.result

    def on_list_select(self, _event=None) -> None:
        sel = self.repo_list.curselection()
        if sel and sel[0] < len(self.checkouts):
            self.set_repo(self.checkouts[sel[0]])

    def choose_repo(self) -> None:
        start = self.repo or (self.checkouts[0].parent if self.checkouts else Path.home())
        picked = filedialog.askdirectory(parent=self.top, title="Choose an SS14 codebase", initialdir=str(start),
                                         mustexist=True)
        if not picked:
            return
        path = Path(picked)
        # A folder inside a checkout (Resources, Resources/Textures, ...) means that checkout.
        root = codebases.checkout_root(path) or path
        self.repo_list.selection_clear(0, "end")
        if root in self.checkouts:
            self.repo_list.selection_set(self.checkouts.index(root))
        self.set_repo(root)

    def choose_out(self) -> None:
        current = Path(self.out_var.get()).expanduser() if self.out_var.get().strip() else None
        start = current.parent if current else self.cfg.runs.parent
        picked = filedialog.askdirectory(parent=self.top, title="Choose where the catalogue goes",
                                         initialdir=str(start if start.is_dir() else Path.home()))
        if picked:
            self.out_touched = True
            self.out_var.set(picked)

    def set_repo(self, repo: Path) -> None:
        self.repo = repo
        self.repo_label.configure(text=str(repo))
        if not self.out_touched:
            self.out_var.set(str(codebases.default_out(repo, self.cfg)))
        self.refresh()

    def _out(self) -> Path | None:
        text = self.out_var.get().strip()
        return Path(text).expanduser() if text else None

    def refresh(self) -> None:
        check = codebases.check_choice(self.repo, self._out())
        self.message.configure(text=check.message, foreground="" if check.ok else "#b00020")
        self.build_btn.state(["!disabled"] if check.ok else ["disabled"])
        out, dataset = self._out(), self.cfg.paths.get("sprite_dataset")
        if check.ok and out and dataset and out.resolve() != dataset.resolve():
            self.note.configure(text=f"Train and Benchmark read {dataset}. To train on this catalogue, "
                                     "point sprite_dataset at it in ~/.changeling14/gui.toml.")
        else:
            self.note.configure(text="")

    def build(self) -> None:
        out = self._out()
        if not codebases.check_choice(self.repo, out).ok:
            return
        self.result = {"wizden": self.repo, "sprite_dataset": out.resolve()}
        self.top.destroy()

    def cancel(self) -> None:
        self.result = None
        self.top.destroy()


FORMS = {"sprites": SpriteCatalogueWindow}


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
        overrides = None
        if job.form:
            form = FORMS.get(job.form)
            if form is None:
                messagebox.showerror("Could not start job", f"{job.label}: unknown form {job.form!r}",
                                     parent=self.root)
                return
            overrides = form(self.root, self.cfg).wait()
            if overrides is None:
                return
        try:
            run = self.runner.submit(job, overrides)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not start job", str(exc), parent=self.root)
            return
        self.refresh_runs()
        if run in self.runner.active:
            # Queued jobs don't take over the view from the one that's running.
            self.select_run(run.folder.path)
        self.refresh_queue()
        self.tick(reschedule=False)

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
