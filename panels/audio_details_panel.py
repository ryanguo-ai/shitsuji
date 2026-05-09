"""
Audio Details Panel — shows cover art and tags for a selected audio file.
"""

import io
import tkinter as tk
from tkinter import ttk

from mutagen.flac import FLAC
from PIL import Image, ImageTk


class AudioDetailsPanel(tk.Frame):

    def __init__(self, master):
        super().__init__(master, bg="#f0f0f0", width=280)
        self._cover_photo = None  # keep reference to avoid GC
        self._build()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build(self):
        tk.Label(
            self, text="File Details",
            font=("Segoe UI", 11, "bold"),
            bg="#f0f0f0", fg="#2c3e50", anchor="w", padx=10, pady=8,
        ).pack(fill=tk.X)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Cover art
        self._cover_label = tk.Label(
            self, bg="#f0f0f0",
            text="No cover art", font=("Segoe UI", 9, "italic"), fg="#7f8c8d",
        )
        self._cover_label.pack(pady=(12, 8))

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Tags table
        tag_frame = tk.Frame(self, bg="#f0f0f0")
        tag_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._tag_tree = ttk.Treeview(
            tag_frame, columns=("tag", "value"), show="headings",
            selectmode="none",
        )
        self._tag_tree.heading("tag", text="Tag", anchor=tk.W)
        self._tag_tree.heading("value", text="Value", anchor=tk.W)
        self._tag_tree.column("tag", width=90, stretch=False)
        self._tag_tree.column("value", width=160, stretch=True)

        tag_vsb = ttk.Scrollbar(tag_frame, orient=tk.VERTICAL, command=self._tag_tree.yview)
        self._tag_tree.configure(yscrollcommand=tag_vsb.set)
        tag_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tag_tree.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def show_flac(self, path: str):
        """Populate the panel with cover art and tags from a FLAC file."""
        self.clear()
        try:
            flac = FLAC(path)
        except Exception:
            return

        # Cover art
        pictures = flac.pictures
        cover_pic = next(
            (p for p in pictures if p.type == 3),  # type 3 = Front Cover
            pictures[0] if pictures else None,
        )
        if cover_pic:
            try:
                img = Image.open(io.BytesIO(cover_pic.data))
                img.thumbnail((240, 240), Image.LANCZOS)
                self._cover_photo = ImageTk.PhotoImage(img)
                self._cover_label.configure(image=self._cover_photo, text="")
            except Exception:
                self._cover_label.configure(image="", text="(cover unreadable)")
        else:
            self._cover_label.configure(image="", text="No cover art")

        # Tags
        tags = flac.tags or {}
        for key, values in sorted(tags.items()):
            display_val = " / ".join(values) if isinstance(values, list) else values
            self._tag_tree.insert("", "end", values=(key.upper(), display_val))

    def clear(self):
        """Reset the panel to its empty state."""
        self._cover_label.configure(image="", text="No cover art")
        self._cover_photo = None
        self._tag_tree.delete(*self._tag_tree.get_children())
