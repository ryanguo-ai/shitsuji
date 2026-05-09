"""
Settings dialog — edit and persist application settings.
"""

import json
import pathlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from panels.database import DB_PATH

SETTINGS_PATH = pathlib.Path.home() / ".shitsuji" / "settings.json"

DEFAULTS: dict = {
    "foobar_path": r"C:\_soft\foobar2000_2.25.8\foobar2000.exe",
}


def load_settings() -> dict:
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULTS)


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        messagebox.showerror("Save failed", str(exc))


class SettingsDialog(tk.Toplevel):
    """Modal settings window. Calls ``on_save(settings)`` when the user saves."""

    def __init__(self, parent: tk.Widget, settings: dict, on_save):
        super().__init__(parent)
        self._settings = dict(settings)
        self._on_save = on_save

        self.title("Settings")
        self.resizable(False, False)
        self.grab_set()
        self.configure(bg="#f5f5f5")

        self._build()
        self._center()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build(self):
        # ── foobar2000 path ── #
        frm = tk.Frame(self, bg="#f5f5f5", padx=16, pady=16)
        frm.pack(fill=tk.BOTH, expand=True)

        tk.Label(frm, text="foobar2000 path:", font=("Segoe UI", 9),
                 bg="#f5f5f5").grid(row=0, column=0, sticky=tk.W, pady=(0, 6))

        self._foobar_var = tk.StringVar(value=self._settings.get("foobar_path", ""))
        tk.Entry(
            frm, textvariable=self._foobar_var,
            font=("Segoe UI", 9), relief=tk.SOLID, bd=1, width=44,
        ).grid(row=0, column=1, sticky=tk.EW, padx=(8, 4), pady=(0, 6))

        ttk.Button(frm, text="…", width=3, command=self._browse_foobar).grid(
            row=0, column=2, pady=(0, 6))

        frm.columnconfigure(1, weight=1)

        # ── File paths (read-only info) ── #
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16)

        info_frm = tk.Frame(self, bg="#f5f5f5", padx=16, pady=8)
        info_frm.pack(fill=tk.X)

        for label, path in (("Settings file:", SETTINGS_PATH), ("Database file:", DB_PATH)):
            row = tk.Frame(info_frm, bg="#f5f5f5")
            row.pack(fill=tk.X, pady=1)
            tk.Label(
                row, text=label, width=13, anchor="w",
                font=("Segoe UI", 8), fg="#7f8c8d", bg="#f5f5f5",
            ).pack(side=tk.LEFT)
            tk.Label(
                row, text=str(path),
                font=("Segoe UI", 8), fg="#555555", bg="#f5f5f5",
            ).pack(side=tk.LEFT, padx=(4, 0))

        # ── Action buttons ── #
        btn_frm = tk.Frame(self, bg="#f5f5f5", padx=16, pady=(0, 12))
        btn_frm.pack(fill=tk.X)

        ttk.Button(btn_frm, text="Save", command=self._save).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_frm, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)

    def _center(self):
        """Center the dialog on the screen using its actual required size."""
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------ #
    # Handlers                                                             #
    # ------------------------------------------------------------------ #

    def _browse_foobar(self):
        path = filedialog.askopenfilename(
            title="Select foobar2000.exe",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
            initialfile=self._foobar_var.get(),
        )
        if path:
            self._foobar_var.set(path)

    def _save(self):
        self._settings["foobar_path"] = self._foobar_var.get().strip()
        save_settings(self._settings)
        self._on_save(self._settings)
        self.destroy()
