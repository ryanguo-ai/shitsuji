"""
Entry point — builds the tabbed application window and starts the event loop.
"""

import tkinter as tk
from tkinter import ttk

from panels.folder_scanner import ScanTab
from panels.search_panel import SearchTab
from panels.settings_panel import SettingsDialog


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Shitsuji")

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        if screen_w > 0 and screen_h > 0:
            win_w, win_h = screen_w // 2, screen_h // 2
        else:
            screen_w, screen_h = 1280, 720
            win_w, win_h = 640, 360
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self.minsize(700, 450)

        self._build_toolbar()

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True)

        self._scan_tab = ScanTab(notebook)
        notebook.add(SearchTab(notebook), text="  Search  ")
        notebook.add(self._scan_tab, text="  Scan  ")

    # ------------------------------------------------------------------ #
    # Toolbar                                                              #
    # ------------------------------------------------------------------ #

    def _build_toolbar(self):
        toolbar = tk.Frame(self, bg="#2c3e50", pady=6, padx=12)
        toolbar.pack(fill=tk.X)

        tk.Label(
            toolbar, text="Shitsuji",
            font=("Segoe UI", 13, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        tk.Button(
            toolbar, text="⚙", font=("Segoe UI", 13),
            fg="white", bg="#2c3e50",
            activeforeground="#ecf0f1", activebackground="#34495e",
            relief=tk.FLAT, bd=0, cursor="hand2",
            command=self._open_settings,
        ).pack(side=tk.RIGHT)

    def _open_settings(self):
        SettingsDialog(
            self,
            self._scan_tab._settings,
            on_save=lambda updated: self._scan_tab._settings.update(updated),
        )


if __name__ == "__main__":
    App().mainloop()
