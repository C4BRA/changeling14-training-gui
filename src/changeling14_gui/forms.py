"""Windows that open before a job starts (the job's `form` in jobs.toml).

Each window's wait() returns (overrides, extra arguments) for the run, or
None if cancelled. The checks live in plain functions so they can be
tested without Tk.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk

from . import codebases
from .config import Config, JobSpec
from .library import (SIZES, Catalogue, Library, Model, checkpoint_step, find_catalogue_root,
                      find_model_root, is_catalogue, is_model)

ERROR = "#b00020"
FormResult = tuple[dict[str, Path | str], list[str]]


@dataclass(frozen=True)
class Check:
    ok: bool
    message: str


def _pct(value) -> str:
    return f"{value:.1%}" if isinstance(value, (int, float)) else "–"


def model_row(model: Model, busy: set[Path]) -> tuple[str, ...]:
    progress = f"{model.step or 0:,} / {model.steps:,}"
    name = model.path.name + ("  (in queue)" if model.path in busy else "")
    return (name, model.catalogue.name if model.catalogue else "?", model.size, progress,
            _pct(model.scores.get("unseen_text_top5")), _pct(model.scores.get("unseen_category_top1")))


MODEL_COLUMNS = (("model", "Model", 220), ("catalogue", "Catalogue", 200), ("size", "Size", 50),
                 ("step", "Step", 110), ("naming", "Naming top-5", 90), ("category", "Category", 70))
CATALOGUE_COLUMNS = (("catalogue", "Catalogue", 230), ("codebase", "Codebase", 140), ("commit", "Commit", 80),
                     ("images", "Images", 70), ("built", "Built", 130))


def catalogue_row(cat: Catalogue) -> tuple[str, ...]:
    images = f"{cat.images:,}" if cat.images is not None else "?"
    return (cat.path.name, cat.codebase, cat.commit or "?", images, cat.built.replace("T", " ").rstrip("Z")[:16])


def _tree(master, columns, height: int) -> ttk.Treeview:
    tree = ttk.Treeview(master, columns=[c[0] for c in columns], show="headings", height=height,
                        selectmode="browse")
    for key, title, width in columns:
        tree.heading(key, text=title, anchor="w")
        tree.column(key, width=width, anchor="w", stretch=key in ("model", "catalogue"))
    return tree


# Checks ------------------------------------------------------------------

def check_continue(model: Model | None, steps: str, minutes: str, fresh: bool) -> Check:
    if model is None:
        return Check(False, "Choose a model to continue.")
    if model.catalogue is None or not is_catalogue(model.catalogue):
        return Check(False, f"This model's catalogue ({model.catalogue or 'unknown'}) is missing. "
                            "Rebuild it with Build sprite catalogue, or start a new model.")
    bad = _check_numbers(steps, minutes)
    if bad:
        return bad
    if not fresh and model.step is not None and model.step >= int(steps):
        return Check(False, f"This model is already at step {model.step:,}. Raise Steps to keep training.")
    if fresh:
        return Check(True, f"Starts {model.path.name} over from step 0 on {model.catalogue.name}. "
                           "Its old checkpoints stay in checkpoints/.")
    return Check(True, f"Continues {model.path.name} from step {model.step or 0:,} on {model.catalogue.name}.")


def check_new(catalogue: Path | None, out: str, size: str, steps: str, minutes: str) -> Check:
    if catalogue is None:
        return Check(False, "Choose the catalogue to train on.")
    if not is_catalogue(catalogue):
        return Check(False, f"{catalogue} is not a sprite catalogue (no manifest.json).")
    if size not in SIZES:
        return Check(False, f"Size must be one of {', '.join(SIZES)}.")
    if not out.strip():
        return Check(False, "Choose a folder for the new model.")
    path = Path(out.strip()).expanduser()
    if is_model(path):
        return Check(False, f"{path.name} is already a model. Pick it under Continue a model, "
                            "or give the new one another name.")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        return Check(False, f"{path} already has other files in it. Pick an empty or new folder.")
    bad = _check_numbers(steps, minutes)
    if bad:
        return bad
    return Check(True, f"A new {size} model in {path.name}, trained on {catalogue.name}.")


def _check_numbers(steps: str, minutes: str) -> Check | None:
    try:
        if int(steps) <= 0:
            raise ValueError
    except ValueError:
        return Check(False, "Steps must be a whole number above 0.")
    if minutes.strip():
        try:
            if float(minutes) <= 0:
                raise ValueError
        except ValueError:
            return Check(False, "Stop after must be a number of minutes, or empty for no limit.")
    return None


def check_model(model: Model | None, needs_data: bool) -> Check:
    if model is None:
        return Check(False, "Choose a model.")
    if not model.has_latest and not model.checkpoints:
        return Check(False, f"{model.path.name} has no checkpoints yet. Train it first.")
    if needs_data and (model.catalogue is None or not is_catalogue(model.catalogue)):
        return Check(False, f"This model's catalogue ({model.catalogue or 'unknown'}) is missing; "
                            "rebuild it with Build sprite catalogue.")
    return Check(True, f"Uses {model.path.name}" + (f" on {model.catalogue.name}." if needs_data else "."))


BATCHES = (128, 256, 512)
OPTIMIZERS = {"AdamW": "adamw", "Schedule-free": "schedulefree"}
RICH_TEXT_TIP = "New models only: also describes items by suffix, folders and a checked list of components"


def rescale_steps(steps: str, old_batch: int, new_batch: int) -> str:
    """Keep steps x batch (pictures seen) the same when the batch changes."""
    try:
        return str(max(1, round(int(steps) * old_batch / new_batch)))
    except ValueError:
        return steps


def train_args(steps: str, minutes: str, amp: bool, fresh: bool, text: bool = True,
               batch: int | None = None, optimizer: str | None = None, rich_text: bool = False) -> list[str]:
    """Arguments after --out/--data/--size. batch, optimizer and rich_text are
    for new models only; a continued model keeps its own."""
    args = ["--steps", str(int(steps))]
    if batch:
        args += ["--batch", str(batch)]
    if optimizer:
        args += ["--optimizer", optimizer]
    if rich_text:
        args.append("--rich-text")
    if minutes.strip():
        args += ["--minutes", minutes.strip()]
    if amp:
        args.append("--amp")
    if fresh:
        args.append("--fresh")
    if not text:
        args.append("--no-text")
    return args


# Windows -----------------------------------------------------------------

def _set_enabled(widget, on: bool) -> None:
    """Enable or grey out a frame's widgets, nested ones included."""
    for child in widget.winfo_children():
        try:
            child.state(["!disabled"] if on else ["disabled"])
        except (AttributeError, tk.TclError):
            pass
        _set_enabled(child, on)


class _Tooltip:
    """A small note that appears while the pointer is over a widget."""

    def __init__(self, widget, text: str):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event=None) -> None:
        if self.tip:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, background="#ffffe0", foreground="#222", relief="solid",
                 borderwidth=1, wraplength=320, justify="left", padx=6, pady=3).pack()

    def hide(self, _event=None) -> None:
        if self.tip:
            self.tip.destroy()
            self.tip = None


class _Window:
    title = ""

    def __init__(self, parent: tk.Tk, cfg: Config, job: JobSpec, busy: set[Path] = frozenset()):
        self.cfg, self.job, self.busy = cfg, job, set(busy)
        self.library = Library(cfg)
        self.result: FormResult | None = None
        top = self.top = tk.Toplevel(parent)
        top.title(self.title or job.label)
        top.transient(parent)
        top.protocol("WM_DELETE_WINDOW", self.cancel)
        top.bind("<Escape>", lambda _e: self.cancel())
        self.body = ttk.Frame(top, padding=12)
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(0, weight=1)

    def _footer(self, row: int, ok_text: str) -> None:
        self.message = ttk.Label(self.body, text="", wraplength=640, justify="left")
        self.message.grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.note = ttk.Label(self.body, text="", wraplength=640, justify="left", foreground="#666")
        self.note.grid(row=row + 1, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(self.body)
        buttons.grid(row=row + 2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="left")
        self.ok_btn = ttk.Button(buttons, text=ok_text, command=self.ok, default="active")
        self.ok_btn.pack(side="left", padx=(8, 0))
        self.top.bind("<Return>", lambda _e: self.ok())

    def _show(self, check: Check, note: str = "") -> None:
        self.message.configure(text=check.message, foreground="" if check.ok else ERROR)
        self.ok_btn.state(["!disabled"] if check.ok else ["disabled"])
        self.note.configure(text=note)

    def _ask_folder(self, title: str, start: Path | None) -> Path | None:
        start = start if start and start.is_dir() else Path.home()
        picked = filedialog.askdirectory(parent=self.top, title=title, initialdir=str(start), mustexist=True)
        return Path(picked) if picked else None

    def _fill_models(self, tree: ttk.Treeview, models: list[Model], select: Path | None = None) -> None:
        tree.delete(*tree.get_children())
        for i, model in enumerate(models):
            tree.insert("", "end", iid=str(i), values=model_row(model, self.busy))
        index = next((i for i, m in enumerate(models) if m.path == select), 0 if models else None)
        if index is not None:
            tree.selection_set(str(index))
            tree.see(str(index))

    def _add_model(self, tree: ttk.Treeview) -> Path | None:
        picked = self._ask_folder("Choose a model folder", self.cfg.paths.get("models"))
        if picked is None:
            return None
        root = find_model_root(picked)
        if root is None:
            self._show(Check(False, f"{picked} is not a model folder (no config.json)."))
            return None
        self.library.remember("models", root)
        return root

    def wait(self) -> FormResult | None:
        self.top.grab_set()
        self.top.focus_set()
        self.top.wait_window()
        return self.result

    def ok(self) -> None:
        raise NotImplementedError

    def cancel(self) -> None:
        self.result = None
        self.top.destroy()


class SpriteCatalogueWindow(_Window):
    """Pick the codebase and output folder for a sprite catalogue build."""

    title = "Build sprite catalogue"

    def __init__(self, parent, cfg, job, busy=frozenset()):
        super().__init__(parent, cfg, job, busy)
        self.top.resizable(True, False)
        self.checkouts = codebases.find_checkouts(cfg)
        self.repo: Path | None = None
        self.out_touched = False  # the user typed or browsed an output folder
        body = self.body

        ttk.Label(body, text="Codebase to build from").grid(row=0, column=0, columnspan=2, sticky="w")
        self.repo_list = tk.Listbox(body, height=min(8, max(3, len(self.checkouts))), width=64,
                                    activestyle="none", exportselection=False)
        self.repo_list.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        for path in self.checkouts:
            self.repo_list.insert("end", f"{path.name}    {path}")
        if not self.checkouts:
            self.repo_list.insert("end", "No SS14 checkouts found; choose a folder")
        self.repo_list.bind("<<ListboxSelect>>", self.on_list_select)
        self.repo_list.bind("<Double-Button-1>", lambda _e: self.ok())
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
        self._footer(5, "Build")

        if self.checkouts:
            self.repo_list.selection_set(0)
            self.set_repo(self.checkouts[0])
        else:
            self.refresh()

    def on_list_select(self, _event=None) -> None:
        sel = self.repo_list.curselection()
        if sel and sel[0] < len(self.checkouts):
            self.set_repo(self.checkouts[sel[0]])

    def choose_repo(self) -> None:
        picked = self._ask_folder("Choose an SS14 codebase",
                                  self.repo or (self.checkouts[0].parent if self.checkouts else None))
        if picked is None:
            return
        # A folder inside a checkout (Resources, Resources/Textures, ...) means that checkout.
        root = codebases.checkout_root(picked) or picked
        self.repo_list.selection_clear(0, "end")
        if root in self.checkouts:
            self.repo_list.selection_set(self.checkouts.index(root))
        self.set_repo(root)

    def choose_out(self) -> None:
        current = self._out()
        picked = self._ask_folder("Choose where the catalogue goes", current.parent if current else self.cfg.runs.parent)
        if picked:
            self.out_touched = True
            self.out_var.set(str(picked))

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
        self._show(Check(check.ok, check.message),
                   "Train a model on it with Train → New model." if check.ok else "")

    def ok(self) -> None:
        out = self._out()
        if not codebases.check_choice(self.repo, out).ok:
            return
        self.result = ({"wizden": self.repo, "sprite_dataset": out.resolve()}, [])
        self.top.destroy()


class TrainWindow(_Window):
    """Continue a model, or start a new one on a chosen catalogue, with the
    training options."""

    title = "Train"

    def __init__(self, parent, cfg, job, busy=frozenset()):
        super().__init__(parent, cfg, job, busy)
        self.models = self.library.models()
        self.catalogues = self.library.catalogues()
        self.out_touched = False
        body = self.body
        self.mode = tk.StringVar(value="continue" if self.models else "new")

        ttk.Radiobutton(body, text="Continue a model", variable=self.mode, value="continue",
                        command=self.on_mode).grid(row=0, column=0, sticky="w")
        cont = self.cont = ttk.Frame(body)
        cont.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(20, 0), pady=(4, 8))
        cont.columnconfigure(0, weight=1)
        self.model_tree = _tree(cont, MODEL_COLUMNS, height=min(6, max(2, len(self.models))))
        self.model_tree.grid(row=0, column=0, sticky="ew")
        self.model_tree.bind("<<TreeviewSelect>>", lambda _e: self.on_model())
        ttk.Button(cont, text="Add model…", command=self.add_model).grid(row=0, column=1, sticky="n", padx=(8, 0))

        ttk.Radiobutton(body, text="New model", variable=self.mode, value="new",
                        command=self.on_mode).grid(row=2, column=0, sticky="w")
        new = self.new = ttk.Frame(body)
        new.grid(row=3, column=0, columnspan=2, sticky="ew", padx=(20, 0), pady=(4, 8))
        new.columnconfigure(0, weight=1)
        self.cat_tree = _tree(new, CATALOGUE_COLUMNS, height=min(6, max(2, len(self.catalogues))))
        self.cat_tree.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.cat_tree.bind("<<TreeviewSelect>>", lambda _e: self.on_catalogue())
        ttk.Button(new, text="Add catalogue…", command=self.add_catalogue).grid(row=0, column=4, sticky="n",
                                                                                padx=(8, 0))
        sizes = ttk.Frame(new)
        sizes.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        sizes.columnconfigure(3, weight=1)
        ttk.Label(sizes, text="Size").grid(row=0, column=0, sticky="w")
        self.size = tk.StringVar(value="tiny")
        size_box = ttk.Combobox(sizes, textvariable=self.size, values=SIZES, state="readonly", width=7)
        size_box.grid(row=0, column=1, padx=(6, 16))
        size_box.bind("<<ComboboxSelected>>", lambda _e: self.on_catalogue())
        ttk.Label(sizes, text="Model folder").grid(row=0, column=2, sticky="w")
        self.out_var = tk.StringVar()
        out_entry = ttk.Entry(sizes, textvariable=self.out_var)
        out_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        out_entry.bind("<Key>", lambda _e: setattr(self, "out_touched", True))
        ttk.Label(sizes, text="Batch").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.batch = tk.StringVar(value=str(BATCHES[0]))
        self.last_batch = BATCHES[0]
        batch_box = ttk.Combobox(sizes, textvariable=self.batch, values=[str(b) for b in BATCHES],
                                 state="readonly", width=7)
        batch_box.grid(row=1, column=1, padx=(6, 16), pady=(6, 0))
        batch_box.bind("<<ComboboxSelected>>", lambda _e: self.on_batch())
        ttk.Label(sizes, text="Optimiser").grid(row=1, column=2, sticky="w", pady=(6, 0))
        options = ttk.Frame(sizes)
        options.grid(row=1, column=3, sticky="w", padx=(6, 0), pady=(6, 0))
        self.optimizer = tk.StringVar(value=next(iter(OPTIMIZERS)))
        ttk.Combobox(options, textvariable=self.optimizer, values=list(OPTIMIZERS), state="readonly",
                     width=13).pack(side="left")
        self.rich_text = tk.BooleanVar(value=False)
        rich = ttk.Checkbutton(options, text="Richer text", variable=self.rich_text)
        rich.pack(side="left", padx=(16, 0))
        _Tooltip(rich, RICH_TEXT_TIP)

        opts = ttk.LabelFrame(body, text="Options", padding=8)
        opts.grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Label(opts, text="Steps").grid(row=0, column=0, sticky="w")
        self.steps = tk.StringVar(value="20000")
        ttk.Spinbox(opts, from_=100, to=1_000_000, increment=1000, textvariable=self.steps,
                    width=9).grid(row=0, column=1, padx=(6, 16))
        ttk.Label(opts, text="Stop after").grid(row=0, column=2, sticky="w")
        self.minutes = tk.StringVar()
        ttk.Entry(opts, textvariable=self.minutes, width=6).grid(row=0, column=3, padx=(6, 2))
        ttk.Label(opts, text="minutes (empty: no limit)").grid(row=0, column=4, sticky="w", padx=(0, 16))
        self.amp = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Half precision (faster on Apple GPUs)", variable=self.amp).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.fresh = tk.BooleanVar(value=False)
        self.fresh_box = ttk.Checkbutton(opts, text="Start over from step 0", variable=self.fresh)
        self.fresh_box.grid(row=1, column=3, columnspan=2, sticky="w", pady=(6, 0))
        for var in (self.steps, self.minutes, self.out_var, self.mode):
            var.trace_add("write", lambda *_a: self.refresh())
        self.fresh.trace_add("write", lambda *_a: self.refresh())

        self._footer(5, "Train")
        self._fill_models(self.model_tree, self.models, cfg.paths.get("model_dir"))
        self._fill_catalogues(cfg.paths.get("sprite_dataset"))
        self.on_mode()

    # Selection -----------------------------------------------------------

    def _selected(self, tree, items):
        sel = tree.selection()
        return items[int(sel[0])] if sel and int(sel[0]) < len(items) else None

    def _fill_catalogues(self, select: Path | None) -> None:
        self.cat_tree.delete(*self.cat_tree.get_children())
        for i, cat in enumerate(self.catalogues):
            self.cat_tree.insert("", "end", iid=str(i), values=catalogue_row(cat))
        index = next((i for i, c in enumerate(self.catalogues) if c.path == select), 0 if self.catalogues else None)
        if index is not None:
            self.cat_tree.selection_set(str(index))

    def on_mode(self) -> None:
        cont = self.mode.get() == "continue"
        for frame, on in ((self.cont, cont), (self.new, not cont)):
            _set_enabled(frame, on)
        self.fresh_box.state(["!disabled"] if cont else ["disabled"])
        if cont:
            self.on_model()
        else:
            # A new model starts from the default length at the chosen batch.
            self.steps.set(rescale_steps("20000", BATCHES[0], int(self.batch.get())))
            self.on_catalogue()

    def on_model(self) -> None:
        model = self._selected(self.model_tree, self.models)
        if model and self.mode.get() == "continue":
            self.steps.set(str(model.steps))
            self.amp.set(model.amp)
        self.refresh()

    def on_catalogue(self) -> None:
        cat = self._selected(self.cat_tree, self.catalogues)
        if self.mode.get() == "new":
            self.steps.set(self.steps.get() or "20000")
            if cat and not self.out_touched:
                self.out_var.set(str(self.library.new_model_path(cat.path, self.size.get())))
        self.refresh()

    def on_batch(self) -> None:
        new = int(self.batch.get())
        self.steps.set(rescale_steps(self.steps.get(), self.last_batch, new))
        self.last_batch = new

    def add_model(self) -> None:
        root = self._add_model(self.model_tree)
        if root:
            self.models = self.library.models()
            self.mode.set("continue")
            self._fill_models(self.model_tree, self.models, root)
            self.on_mode()

    def add_catalogue(self) -> None:
        picked = self._ask_folder("Choose a sprite catalogue", self.cfg.paths.get("sprite_dataset", Path.home()).parent)
        if picked is None:
            return
        root = find_catalogue_root(picked)
        if root is None:
            self._show(Check(False, f"{picked} is not a sprite catalogue (no manifest.json). "
                                    "Build one with Build sprite catalogue."))
            return
        self.library.remember("catalogues", root)
        self.catalogues = self.library.catalogues()
        self.mode.set("new")
        self._fill_catalogues(root)
        self.on_mode()

    # Result --------------------------------------------------------------

    def check(self) -> Check:
        if self.mode.get() == "continue":
            return check_continue(self._selected(self.model_tree, self.models), self.steps.get(),
                                  self.minutes.get(), self.fresh.get())
        cat = self._selected(self.cat_tree, self.catalogues)
        return check_new(cat.path if cat else None, self.out_var.get(), self.size.get(),
                         self.steps.get(), self.minutes.get())

    def refresh(self) -> None:
        if not hasattr(self, "ok_btn"):
            return
        check = self.check()
        note = ""
        model = self._selected(self.model_tree, self.models)
        if check.ok and self.mode.get() == "continue" and model and model.path in self.busy:
            note = "This model already has a Train job running or queued; this one waits for it."
        self._show(check, note)

    def ok(self) -> None:
        if not self.check().ok:
            return
        steps, minutes, amp = self.steps.get(), self.minutes.get(), self.amp.get()
        if self.mode.get() == "continue":
            model = self._selected(self.model_tree, self.models)
            overrides = {"model_dir": model.path, "sprite_dataset": model.catalogue, "size": model.size}
            extra = train_args(steps, minutes, amp, self.fresh.get(), model.text)
        else:
            cat = self._selected(self.cat_tree, self.catalogues)
            out = Path(self.out_var.get().strip()).expanduser()
            overrides = {"model_dir": out, "sprite_dataset": cat.path, "size": self.size.get()}
            extra = train_args(steps, minutes, amp, False, batch=int(self.batch.get()),
                               optimizer=OPTIMIZERS[self.optimizer.get()], rich_text=self.rich_text.get())
        self.result = (overrides, extra)
        self.top.destroy()


class ModelWindow(_Window):
    """Pick a model and checkpoint for Benchmark or Export."""

    def __init__(self, parent, cfg, job, busy=frozenset()):
        super().__init__(parent, cfg, job, busy)
        self.needs_data = "{sprite_dataset}" in job.command
        self.models = self.library.models()
        body = self.body

        ttk.Label(body, text="Model").grid(row=0, column=0, sticky="w")
        self.tree = _tree(body, MODEL_COLUMNS, height=min(8, max(3, len(self.models))))
        self.tree.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_model())
        ttk.Button(body, text="Add model…", command=self.add_model).grid(row=1, column=1, sticky="n", padx=(8, 0))

        row = ttk.Frame(body)
        row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(row, text="Checkpoint").pack(side="left")
        self.ckpt = tk.StringVar()
        self.ckpt_box = ttk.Combobox(row, textvariable=self.ckpt, state="readonly", width=30)
        self.ckpt_box.pack(side="left", padx=6)
        self.ckpt_paths: list[Path | None] = []
        self._footer(3, job.label)
        self._fill_models(self.tree, self.models, cfg.paths.get("model_dir"))
        self.on_model()

    def selected(self) -> Model | None:
        sel = self.tree.selection()
        return self.models[int(sel[0])] if sel and int(sel[0]) < len(self.models) else None

    def on_model(self) -> None:
        model = self.selected()
        labels, self.ckpt_paths = [], []
        if model:
            if model.has_latest:
                labels.append(f"Latest (step {model.step or 0:,})")
                self.ckpt_paths.append(None)
            for path in reversed(model.checkpoints):
                step = checkpoint_step(path)
                labels.append(f"Step {step:,}" if step is not None else path.name)
                self.ckpt_paths.append(path)
        self.ckpt_box.configure(values=labels)
        self.ckpt.set(labels[0] if labels else "")
        self._show(check_model(model, self.needs_data))

    def add_model(self) -> None:
        root = self._add_model(self.tree)
        if root:
            self.models = self.library.models()
            self._fill_models(self.tree, self.models, root)
            self.on_model()

    def ok(self) -> None:
        model = self.selected()
        if not check_model(model, self.needs_data).ok:
            return
        overrides = {"model_dir": model.path, "size": model.size}
        if model.catalogue:
            overrides["sprite_dataset"] = model.catalogue
        index = self.ckpt_box.current()
        path = self.ckpt_paths[index] if 0 <= index < len(self.ckpt_paths) else None
        self.result = (overrides, ["--checkpoint", str(path)] if path else [])
        self.top.destroy()


FORMS = {"sprites": SpriteCatalogueWindow, "train": TrainWindow, "model": ModelWindow}
