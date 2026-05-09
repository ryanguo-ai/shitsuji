"""
Audio Details Panel — shows cover art and tags for a selected audio file.
Double-click any tag row to edit the tag name or value; Save Tags writes to disk.
"""

import io
import tkinter as tk
from tkinter import ttk, messagebox

from mutagen.flac import FLAC
from PIL import Image, ImageTk

from panels.lyrics_panel import LyricsPanel

# FLAC picture type IDs
_PIC_FRONT = 3
_PIC_BACK = 4


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class AudioDetailsPanel(tk.Frame):

    def __init__(self, master):
        super().__init__(master, bg="#f0f0f0", width=280)
        self._cover_photo = None  # keep reference to avoid GC
        self._current_path: str | None = None
        self._dirty = False
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
        self._cover_label.pack(pady=(12, 4))

        # Image count / dimension / size info
        img_info_frame = tk.Frame(self, bg="#f0f0f0")
        img_info_frame.pack(pady=(0, 6))

        self._img_count_var = tk.StringVar()
        self._img_dims_var = tk.StringVar()

        tk.Label(
            img_info_frame, textvariable=self._img_count_var,
            font=("Segoe UI", 8), fg="#7f8c8d", bg="#f0f0f0",
        ).pack()
        tk.Label(
            img_info_frame, textvariable=self._img_dims_var,
            font=("Segoe UI", 8), fg="#7f8c8d", bg="#f0f0f0",
        ).pack()

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Tags table
        tag_frame = tk.Frame(self, bg="#f0f0f0")
        tag_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))

        self._tag_tree = ttk.Treeview(
            tag_frame, columns=("tag", "value"), show="headings",
            selectmode="browse",
        )
        self._tag_tree.heading("tag", text="Tag", anchor=tk.W)
        self._tag_tree.heading("value", text="Value", anchor=tk.W)
        self._tag_tree.column("tag", width=90, stretch=False)
        self._tag_tree.column("value", width=160, stretch=True)

        tag_vsb = ttk.Scrollbar(tag_frame, orient=tk.VERTICAL, command=self._tag_tree.yview)
        self._tag_tree.configure(yscrollcommand=tag_vsb.set)
        tag_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tag_tree.pack(fill=tk.BOTH, expand=True)

        self._tag_tree.bind("<Double-1>", self._start_edit)

        # Save button
        btn_frame = tk.Frame(self, bg="#f0f0f0")
        btn_frame.pack(fill=tk.X, padx=4, pady=4)
        self._save_btn = ttk.Button(
            btn_frame, text="Save Tags",
            command=self._save_tags, state="disabled",
        )
        self._save_btn.pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ #
    # Inline editing                                                       #
    # ------------------------------------------------------------------ #

    def _start_edit(self, event):
        item = self._tag_tree.identify_row(event.y)
        col = self._tag_tree.identify_column(event.x)  # '#1' or '#2'
        if not item or col not in ("#1", "#2"):
            return

        tag_name = self._tag_tree.item(item, "values")[0]
        if tag_name.upper() == "LYRICS":
            self._open_lyrics(item)
            return

        bbox = self._tag_tree.bbox(item, col)
        if not bbox:
            return
        x, y, w, h = bbox

        col_idx = int(col[1:]) - 1  # '#1'→0, '#2'→1
        current = self._tag_tree.item(item, "values")[col_idx]

        var = tk.StringVar(value=current)
        entry = tk.Entry(
            self._tag_tree, textvariable=var,
            font=("Segoe UI", 9), relief=tk.SOLID, bd=1,
        )
        entry.place(x=x, y=y, width=w, height=h)
        entry.select_range(0, tk.END)
        entry.focus_set()

        done = [False]

        def commit(_=None):
            if done[0]:
                return
            done[0] = True
            entry.destroy()
            vals = list(self._tag_tree.item(item, "values"))
            vals[col_idx] = var.get().strip()
            self._tag_tree.item(item, values=vals)
            self._mark_dirty()

        def cancel(_=None):
            done[0] = True
            entry.destroy()

        entry.bind("<Return>", commit)
        entry.bind("<Tab>", commit)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", commit)

    def _open_lyrics(self, item):
        lyrics = self._tag_tree.item(item, "values")[1]

        def on_save(new_lyrics: str):
            self._tag_tree.item(item, values=("LYRICS", new_lyrics))
            self._mark_dirty()

        LyricsPanel(
            self.winfo_toplevel(),
            self._current_path or "",
            lyrics,
            on_save=on_save,
        )

    def _mark_dirty(self):
        self._dirty = True
        self._save_btn.configure(state="normal")

    # ------------------------------------------------------------------ #
    # Saving                                                               #
    # ------------------------------------------------------------------ #

    def _save_tags(self):
        if not self._current_path or not self._dirty:
            return
        try:
            flac = FLAC(self._current_path)
            flac.tags.clear()
            for iid in self._tag_tree.get_children():
                key, val = self._tag_tree.item(iid, "values")
                if key:
                    flac[key.lower()] = [val]
            flac.save()
            self._dirty = False
            self._save_btn.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def show_flac(self, path: str):
        """Populate the panel with cover art and tags from a FLAC file."""
        self.clear()
        self._current_path = path
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

        # Image info
        total = len(pictures)
        front = sum(1 for p in pictures if p.type == _PIC_FRONT)
        back  = sum(1 for p in pictures if p.type == _PIC_BACK)
        other = total - front - back

        parts = []
        if front: parts.append(f"{front} front")
        if back:  parts.append(f"{back} back")
        if other: parts.append(f"{other} other")
        count_str = f"{total} image{'s' if total != 1 else ''}"
        if parts:
            count_str += f"  ·  {'  ·  '.join(parts)}"
        self._img_count_var.set(count_str)

        if cover_pic:
            try:
                orig = Image.open(io.BytesIO(cover_pic.data))
                w, h = orig.size
                sz = _fmt_size(len(cover_pic.data))
                self._img_dims_var.set(f"{w} × {h} px  ·  {sz}")
            except Exception:
                self._img_dims_var.set("")
        else:
            self._img_dims_var.set("")

        # Tags
        tags = flac.tags or {}
        for key, values in sorted(tags.items()):
            display_val = " / ".join(values) if isinstance(values, list) else values
            self._tag_tree.insert("", "end", values=(key.upper(), display_val))

    def clear(self):
        """Reset the panel to its empty state."""
        self._current_path = None
        self._dirty = False
        self._save_btn.configure(state="disabled")
        self._cover_label.configure(image="", text="No cover art")
        self._cover_photo = None
        self._img_count_var.set("")
        self._img_dims_var.set("")
        self._tag_tree.delete(*self._tag_tree.get_children())
