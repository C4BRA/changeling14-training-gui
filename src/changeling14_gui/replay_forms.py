"""Windows for the replay jobs: import, record, build dataset, train on replays.

Same contract as forms.py: wait() returns (overrides, extra arguments) or
None if cancelled; the checks live in replays.py.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import replays
from .forms import Check, _Tooltip, _tree, _Window

RECORD_NOTE = "A game window opens and must stay visible; one job at a time."
DELETE_TIP = "Off: the inbox keeps its .zip files. On: each one is deleted once its copy in the library is checked."
HOLDOUT_TIP = "Round ids whose pictures are only used for testing, e.g. 115836. Empty: about 1 in 5 rounds."
DATASET_COLUMNS = (("dataset", "Dataset", 200), ("frames", "Frames", 65), ("train", "Train", 65),
                   ("test", "Test", 65), ("held", "Test rounds", 110), ("built", "Built", 120))
REPLAY_COLUMNS = (("replay", "Replay", 300), ("build", "Build", 170), ("length", "Length", 80),
                  ("recorded", "Recorded", 90))


def _row(form, row: int, label: str, var: tk.StringVar, width: int = 8, tip: str = "") -> ttk.Entry:
    ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
    entry = ttk.Entry(form, textvariable=var, width=width)
    entry.grid(row=row, column=1, sticky="w", padx=(6, 0), pady=2)
    if tip:
        _Tooltip(entry, tip)
    return entry


class _ReplayWindow(_Window):
    def _folder_row(self, row: int, label: str, var: tk.StringVar, title: str) -> None:
        ttk.Label(self.body, text=label).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
        line = ttk.Frame(self.body)
        line.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
        line.columnconfigure(0, weight=1)
        ttk.Entry(line, textvariable=var).grid(row=0, column=0, sticky="ew")

        def browse():
            current = Path(var.get().strip()).expanduser() if var.get().strip() else None
            picked = self._ask_folder(title, current if current and current.is_dir() else None)
            if picked:
                var.set(str(picked))
        ttk.Button(line, text="Browse…", command=browse).grid(row=0, column=1, padx=(8, 0))
        var.trace_add("write", lambda *_a: self.refresh())

    def _show2(self, ok_message: tuple[bool, str], note: str = "") -> None:
        self._show(Check(*ok_message), note)

    def refresh(self) -> None:
        raise NotImplementedError


class ImportReplaysWindow(_ReplayWindow):
    title = "Import replays"

    def __init__(self, parent, cfg, job, busy=frozenset()):
        super().__init__(parent, cfg, job, busy)
        self.top.resizable(True, False)
        self.inbox = tk.StringVar(value=str(cfg.inbox))
        self.library_dir = tk.StringVar(value=str(cfg.paths["replay_library"]))
        self._folder_row(0, "Inbox (replay .zip files)", self.inbox, "Choose the replay inbox")
        self._folder_row(2, "Library", self.library_dir, "Choose the replay library")
        self.remove = tk.BooleanVar(value=False)
        box = ttk.Checkbutton(self.body, text="Delete from inbox after import", variable=self.remove)
        box.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        _Tooltip(box, DELETE_TIP)
        self._footer(5, "Import")
        self.refresh()

    def refresh(self) -> None:
        if not hasattr(self, "ok_btn"):
            return
        inbox = Path(self.inbox.get().strip()).expanduser()
        zips = replays.inbox_zips(inbox)
        if not self.inbox.get().strip() or not inbox.is_dir():
            self._show2((False, f"{inbox} is not a folder."))
        elif not self.library_dir.get().strip():
            self._show2((False, "Choose a library folder."))
        elif not zips:
            self._show2((False, f"No replay .zip files in {inbox}."))
        else:
            self._show2((True, f"Copies {len(zips)} replay(s) into the library, grouped by the build that plays "
                               "them. Replays already there are skipped."))

    def ok(self) -> None:
        self.refresh()
        if self.ok_btn.instate(["disabled"]):
            return
        overrides = {"inbox": Path(self.inbox.get().strip()).expanduser(),
                     "replay_library": Path(self.library_dir.get().strip()).expanduser()}
        self.result = (overrides, ["--remove-from-inbox"] if self.remove.get() else [])
        self.top.destroy()


class RecordReplaysWindow(_ReplayWindow):
    title = "Record replays"

    def __init__(self, parent, cfg, job, busy=frozenset()):
        super().__init__(parent, cfg, job, busy)
        self.items = replays.library_replays(cfg)
        body = self.body
        ttk.Label(body, text="Replay").grid(row=0, column=0, sticky="w")
        self.tree = _tree(body, REPLAY_COLUMNS, height=min(8, max(3, len(self.items))))
        self.tree.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        for i, r in enumerate(self.items):
            self.tree.insert("", "end", iid=str(i), values=(r.name, r.build, replays.duration_text(r.duration),
                                                            f"{r.recorded:,}" if r.recorded else "–"))
        first_new = next((i for i, r in enumerate(self.items) if not r.recorded), 0)
        if self.items:
            self.tree.selection_set(str(first_new))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.refresh())

        self.source = tk.StringVar(value=str(cfg.paths.get("wizden", Path.home())))
        self._folder_row(2, "Source checkout (the codebase the replay came from)", self.source,
                         "Choose the SS14 checkout")
        form = ttk.Frame(body)
        form.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.interval, self.players, self.max_pictures = tk.StringVar(value="10"), tk.StringVar(value="2"), tk.StringVar()
        _row(form, 0, "Interval (s)", self.interval, tip="Seconds of replay between moments.")
        _row(form, 1, "Players", self.players, tip="Players followed at each moment.")
        _row(form, 2, "Max pictures", self.max_pictures, tip="Stop after this many pictures. Empty: the whole replay.")
        for var in (self.interval, self.players, self.max_pictures):
            var.trace_add("write", lambda *_a: self.refresh())
        self._footer(5, "Record")
        self.refresh()

    def selected(self) -> replays.Replay | None:
        sel = self.tree.selection()
        return self.items[int(sel[0])] if sel and int(sel[0]) < len(self.items) else None

    def refresh(self) -> None:
        if not hasattr(self, "ok_btn"):
            return
        self._show2(replays.check_record(self.selected(), self.source.get(), self.interval.get(),
                                         self.players.get(), self.max_pictures.get()), RECORD_NOTE)

    def ok(self) -> None:
        replay = self.selected()
        if not replays.check_record(replay, self.source.get(), self.interval.get(), self.players.get(),
                                    self.max_pictures.get())[0]:
            return
        overrides = {"replay": replay.file, "wizden": Path(self.source.get().strip()).expanduser()}
        self.result = (overrides, replays.record_args(self.interval.get(), self.players.get(),
                                                      self.max_pictures.get()))
        self.top.destroy()


class BuildDatasetWindow(_ReplayWindow):
    title = "Build dataset"

    def __init__(self, parent, cfg, job, busy=frozenset()):
        super().__init__(parent, cfg, job, busy)
        self.top.resizable(True, False)
        self.rounds = replays.recording_rounds(cfg)
        recorded = ", ".join(self.rounds) or "none yet"
        ttk.Label(self.body, text=f"Recorded rounds: {recorded}").grid(row=0, column=0, columnspan=2, sticky="w")
        form = ttk.Frame(self.body)
        form.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.holdout = tk.StringVar()
        _row(form, 0, "Held-out round ids", self.holdout, width=24, tip=HOLDOUT_TIP)
        self.holdout.trace_add("write", lambda *_a: self.refresh())
        self.name = tk.StringVar(value=cfg.paths["replay_dataset"].name)
        _row(form, 1, "Output name", self.name, width=24,
             tip=f"replay-dataset is the original folder; any other name goes in {replays.datasets_dir(cfg)}.")
        self.name.trace_add("write", lambda *_a: self.refresh())
        self._footer(2, "Build")
        self.refresh()

    def out(self) -> Path:
        return replays.dataset_path(self.cfg, self.name.get())

    def check(self) -> tuple[bool, str]:
        ok, message = replays.check_holdout(self.holdout.get(), self.rounds)
        if not ok:
            return ok, message
        problem = replays.check_dataset_name(self.name.get())
        if problem:
            return False, problem
        out_ok, out_message = replays.check_dataset_out(str(self.out()))
        return (out_ok, f"{message} {out_message}") if out_ok else (False, out_message)

    def refresh(self) -> None:
        if not hasattr(self, "ok_btn"):
            return
        self._show2(self.check(), "Train on replays lets you pick which dataset to train on.")

    def ok(self) -> None:
        if not self.check()[0]:
            return
        self.result = ({"replay_dataset": self.out()},
                       replays.holdout_args(self.holdout.get()))
        self.top.destroy()


class TrainReplaysWindow(_ReplayWindow):
    title = "Train on replays"

    def __init__(self, parent, cfg, job, busy=frozenset()):
        super().__init__(parent, cfg, job, busy)
        self.models = replays.text_models(cfg)
        body = self.body
        ttk.Label(body, text="Start from (sprite models trained with text)").grid(row=0, column=0, sticky="w")
        self.start_list = tk.Listbox(body, height=min(6, max(3, len(self.models))), width=60,
                                     activestyle="none", exportselection=False)
        self.start_list.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        for m in self.models:
            self.start_list.insert("end", m.name)
        if not self.models:
            self.start_list.insert("end", "No sprite model with text.npz in the models folder")
        self.start_list.bind("<<ListboxSelect>>", lambda _e: self.on_start())
        ttk.Label(body, text="Dataset").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.datasets = replays.replay_datasets(cfg)
        self.ds_tree = _tree(body, DATASET_COLUMNS, height=min(5, max(2, len(self.datasets))))
        self.ds_tree.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.ds_tree.bind("<<TreeviewSelect>>", lambda _e: self.refresh())
        ttk.Button(body, text="Browse…", command=self.add_dataset).grid(row=3, column=1, sticky="n", padx=(8, 0),
                                                                             pady=(4, 0))
        self._fill_datasets(None)     # newest first, so the newest is preselected
        form = ttk.Frame(body)
        form.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.name, self.steps, self.batch, self.minutes = (tk.StringVar(), tk.StringVar(value="6000"),
                                                           tk.StringVar(value="8"), tk.StringVar())
        self.name_touched = False
        name_entry = _row(form, 0, "New model name", self.name, width=36,
                          tip="A folder in the models folder; an existing one is resumed.")
        name_entry.bind("<Key>", lambda _e: setattr(self, "name_touched", True))
        _row(form, 1, "Steps", self.steps)
        _row(form, 2, "Batch", self.batch, tip="480x480 crops per step.")
        _row(form, 3, "Stop after (minutes)", self.minutes, tip="Empty: no time limit.")
        for var in (self.name, self.steps, self.batch, self.minutes):
            var.trace_add("write", lambda *_a: self.refresh())
        self._footer(5, "Train")
        if self.models:
            self.start_list.selection_set(0)
        self.on_start()

    def selected(self) -> Path | None:
        sel = self.start_list.curselection()
        return self.models[sel[0]] if sel and sel[0] < len(self.models) else None

    def _fill_datasets(self, select: Path | None) -> None:
        self.ds_tree.delete(*self.ds_tree.get_children())
        for i, ds in enumerate(self.datasets):
            self.ds_tree.insert("", "end", iid=str(i), values=ds.describe())
        index = next((i for i, d in enumerate(self.datasets) if d.path == select), 0 if self.datasets else None)
        if index is not None:
            self.ds_tree.selection_set(str(index))

    def dataset(self) -> Path | None:
        sel = self.ds_tree.selection()
        return self.datasets[int(sel[0])].path if sel and int(sel[0]) < len(self.datasets) else None

    def add_dataset(self) -> None:
        picked = self._ask_folder("Choose a replay dataset", self.cfg.paths["replay_dataset"].parent)
        if picked is None:
            return
        if not replays.is_dataset(picked):
            self._show2((False, f"{picked} is not a replay dataset (no dataset.json)."))
            return
        self.datasets = replays.replay_datasets(self.cfg, [d.path for d in self.datasets] + [picked])
        self._fill_datasets(picked)
        self.refresh()

    def on_start(self) -> None:
        start = self.selected()
        if start and not self.name_touched:
            self.name.set(replays.default_replay_model(start))
        self.refresh()

    def check(self) -> tuple[bool, str]:
        return replays.check_replay_train(self.selected(), self.name.get(), self.dataset(),
                                          self.steps.get(), self.batch.get(), self.minutes.get())

    def refresh(self) -> None:
        if not hasattr(self, "ok_btn"):
            return
        self._show2(self.check())

    def ok(self) -> None:
        if not self.check()[0]:
            return
        start = self.selected()
        overrides = {"replay_model": self.name.get().strip(), "start_model": start, "models": start.parent,
                     "replay_dataset": self.dataset()}
        self.result = (overrides, replays.replay_train_args(self.steps.get(), self.batch.get(), self.minutes.get()))
        self.top.destroy()


REPLAY_FORMS = {"import": ImportReplaysWindow, "record": RecordReplaysWindow,
                "dataset": BuildDatasetWindow, "replay-train": TrainReplaysWindow}
