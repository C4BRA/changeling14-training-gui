"""A frame that scrolls when its content is taller than the space it gets.

Put widgets in `.inner`. While everything fits, the content is stretched to
the visible height (so a list or log set to expand still fills the space);
when it doesn't fit, a scrollbar appears and the wheel or trackpad scrolls it.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk


class ScrollFrame(ttk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self._layout())
        self.canvas.bind("<Configure>", lambda _e: self._layout())
        # The wheel scrolls whichever scroll frame the pointer is over.
        for widget in (self.canvas, self.inner):
            widget.bind("<Enter>", lambda _e: self._bind_wheel(True), add="+")
            widget.bind("<Leave>", lambda _e: self._bind_wheel(False), add="+")

    def needs_scroll(self) -> bool:
        return self.inner.winfo_reqheight() > max(1, self.canvas.winfo_height())

    def _layout(self) -> None:
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        want_w, want_h = self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()
        # Never narrower than the content asks; as tall as the view while the content fits.
        if int(self.canvas.cget("width")) != want_w:
            self.canvas.configure(width=want_w)
        self.canvas.itemconfigure(self._window, width=max(width, want_w), height=max(height, want_h))
        self.canvas.configure(scrollregion=(0, 0, max(width, want_w), max(height, want_h)))
        if want_h > height > 1:
            self.bar.grid(row=0, column=1, sticky="ns")
        else:
            self.bar.grid_remove()
            self.canvas.yview_moveto(0)

    def _bind_wheel(self, on: bool) -> None:
        events = ("<MouseWheel>",) if sys.platform in ("darwin", "win32") else ("<Button-4>", "<Button-5>")
        for event in events:
            if on:
                self.canvas.bind_all(event, self._wheel)
            else:
                self.canvas.unbind_all(event)

    def _wheel(self, event) -> str | None:
        if not self.needs_scroll():
            return None
        if getattr(event, "num", None) in (4, 5):
            step = -1 if event.num == 4 else 1
        elif sys.platform == "darwin":
            step = -int(event.delta) or (-1 if event.delta > 0 else 1)   # trackpad deltas are small
        else:
            step = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(step, "units")
        return "break"
