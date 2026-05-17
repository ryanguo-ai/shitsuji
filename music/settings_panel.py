"""
Settings dialog — edit and persist application settings.
"""

import json
import pathlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from music.database import DB_PATH

SETTINGS_PATH = pathlib.Path.home() / ".shitsuji" / "settings.json"

MUSIC_LIB_PARTITIONS = ["POP", "CPOP", "JPOP", "OST", "Instrumental", "OTHER"]
_DEFAULT_MUSIC_LIB_PATH = r"C:\_MUSIC_LIB"

DEFAULTS: dict = {
    "foobar_path": r"C:\_soft\foobar2000_2.25.8\foobar2000.exe",
    "music_lib_paths": {p: _DEFAULT_MUSIC_LIB_PATH for p in MUSIC_LIB_PARTITIONS},
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
        outer = tk.Frame(self, bg="#f5f5f5", padx=16, pady=16)
        outer.pack(fill=tk.BOTH, expand=True)

        # ── foobar2000 path ── #
        tk.Label(outer, text="foobar2000 path:", font=("Segoe UI", 9, "bold"),
                 bg="#f5f5f5", anchor="w").grid(row=0, column=0, sticky=tk.W, pady=(0, 4))

        self._foobar_var = tk.StringVar(value=self._settings.get("foobar_path", ""))
        tk.Entry(
            outer, textvariable=self._foobar_var,
            font=("Segoe UI", 9), relief=tk.SOLID, bd=1, width=46,
        ).grid(row=0, column=1, sticky=tk.EW, padx=(8, 4), pady=(0, 4))
        ttk.Button(outer, text="…", width=3, command=self._browse_foobar).grid(
            row=0, column=2, pady=(0, 4))

        # ── Music library folder paths ── #
        ttk.Separator(outer, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=3, sticky=tk.EW, pady=(8, 10))

        tk.Label(outer, text="Music library folder paths:",
                 font=("Segoe UI", 9, "bold"), bg="#f5f5f5", anchor="w").grid(
            row=2, column=0, columnspan=3, sticky=tk.W, pady=(0, 6))

        saved_paths: dict = self._settings.get("music_lib_paths", {})
        self._lib_vars: dict[str, tk.StringVar] = {}

        for idx, partition in enumerate(MUSIC_LIB_PARTITIONS):
            row = 3 + idx
            default = saved_paths.get(partition, _DEFAULT_MUSIC_LIB_PATH)
            var = tk.StringVar(value=default)
            self._lib_vars[partition] = var

            tk.Label(outer, text=f"{partition}:", font=("Segoe UI", 9),
                     bg="#f5f5f5", anchor="e", width=12).grid(
                row=row, column=0, sticky=tk.E, pady=2)
            tk.Entry(outer, textvariable=var, font=("Segoe UI", 9),
                     relief=tk.SOLID, bd=1).grid(
                row=row, column=1, sticky=tk.EW, padx=(8, 4), pady=2)
            ttk.Button(
                outer, text="…", width=3,
                command=lambda v=var: self._browse_lib(v),
            ).grid(row=row, column=2, pady=2)

        outer.columnconfigure(1, weight=1)

        # ── File paths (read-only info) ── #
        sep_row = 3 + len(MUSIC_LIB_PARTITIONS)
        ttk.Separator(outer, orient=tk.HORIZONTAL).grid(
            row=sep_row, column=0, columnspan=3, sticky=tk.EW, pady=(10, 6))

        for i, (label, path) in enumerate(
            (("Settings file:", SETTINGS_PATH), ("Database file:", DB_PATH))
        ):
            info_row = sep_row + 1 + i
            tk.Label(outer, text=label, font=("Segoe UI", 8), fg="#7f8c8d",
                     bg="#f5f5f5", anchor="e", width=12).grid(
                row=info_row, column=0, sticky=tk.E, pady=1)
            tk.Label(outer, text=str(path), font=("Segoe UI", 8), fg="#555555",
                     bg="#f5f5f5", anchor="w").grid(
                row=info_row, column=1, columnspan=2, sticky=tk.W, padx=(8, 0), pady=1)

        # ── Action buttons ── #
        btn_frm = tk.Frame(self, bg="#f5f5f5", padx=16, pady=(0, 12))
        btn_frm.pack(fill=tk.X)
        ttk.Button(btn_frm, text="Save",   command=self._save   ).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_frm, text="Cancel", command=self.destroy  ).pack(side=tk.RIGHT)

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

    def _browse_lib(self, var: tk.StringVar):
        folder = filedialog.askdirectory(
            title="Select music library folder",
            initialdir=var.get() or _DEFAULT_MUSIC_LIB_PATH,
        )
        if folder:
            var.set(folder)

    def _save(self):
        self._settings["foobar_path"] = self._foobar_var.get().strip()
        self._settings["music_lib_paths"] = {
            p: var.get().strip() for p, var in self._lib_vars.items()
        }
        save_settings(self._settings)
        self._on_save(self._settings)
        self.destroy()
