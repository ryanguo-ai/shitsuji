"""
Lyrics viewer/editor panel — opened when double-clicking a LYRICS tag row.
"""

import tkinter as tk
from tkinter import ttk, messagebox

from mutagen.flac import FLAC

from music.settings_panel import load_settings, save_settings


class LyricsPanel(tk.Toplevel):
    """Modeless window that shows and allows editing of a LYRICS tag."""

    def __init__(self, parent: tk.Widget, file_path: str, lyrics: str, on_save):
        super().__init__(parent)
        self._file_path = file_path
        self._on_save = on_save
        self._settings = load_settings()

        self.title("Lyrics")
        self.configure(bg="#f5f5f5")
        self.minsize(480, 360)

        self._build(lyrics)
        self._apply_geometry()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ #
    # UI                                                                   #
    # ------------------------------------------------------------------ #

    def _build(self, lyrics: str):
        # ── Header ── #
        header = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        header.pack(fill=tk.X)

        tk.Label(
            header, text="🎵  Lyrics",
            font=("Segoe UI", 12, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        tk.Label(
            header, text=self._file_path,
            font=("Segoe UI", 8),
            fg="#bdc3c7", bg="#2c3e50",
            anchor="e",
        ).pack(side=tk.RIGHT)

        # ── Text area ── #
        text_frame = tk.Frame(self, bg="#f5f5f5")
        text_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 4))

        vsb = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._text = tk.Text(
            text_frame,
            font=("Segoe UI", 10),
            wrap=tk.WORD,
            relief=tk.SOLID, bd=1,
            bg="white", fg="#2c3e50",
            insertbackground="#2c3e50",
            yscrollcommand=vsb.set,
            padx=8, pady=8,
        )
        self._text.pack(fill=tk.BOTH, expand=True)
        vsb.configure(command=self._text.yview)

        self._text.insert("1.0", lyrics)

        # ── Buttons ── #
        btn_frame = tk.Frame(self, bg="#f5f5f5", padx=12, pady=(0, 10))
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="Save", command=self._save).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_frame, text="Close", command=self._close).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ #
    # Geometry                                                             #
    # ------------------------------------------------------------------ #

    def _apply_geometry(self):
        saved = self._settings.get("lyrics_geometry")
        if saved:
            self.geometry(saved)
        else:
            self.update_idletasks()
            w = max(self.winfo_reqwidth(), 520)
            h = max(self.winfo_reqheight(), 420)
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
            self.geometry(f"{w}x{h}+{x}+{y}")

    def _persist_geometry(self):
        self._settings["lyrics_geometry"] = self.geometry()
        save_settings(self._settings)

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _close(self):
        self._persist_geometry()
        self.destroy()

    def _save(self):
        new_lyrics = self._text.get("1.0", tk.END).rstrip("\n")
        try:
            flac = FLAC(self._file_path)
            flac["lyrics"] = [new_lyrics]
            flac.save()
            self._on_save(new_lyrics)
            self._persist_geometry()
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
