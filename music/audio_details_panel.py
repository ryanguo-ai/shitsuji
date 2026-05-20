"""
Audio Details Panel — shows cover art and tags for a selected audio file.
Double-click any tag row to edit the tag name or value; Save Tags writes to disk.
"""

import ctypes
import io
import tkinter as tk
from tkinter import ttk, messagebox

from mutagen.flac import FLAC
from PIL import Image, ImageTk

from music.lyrics_panel import LyricsPanel

# FLAC picture type IDs
_PIC_FRONT = 3
_PIC_BACK = 4


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class _PasteJsonDialog(tk.Toplevel):
    """Side-by-side review dialog for pasting tags from JSON.

    Shows columns: Apply | Tag | Current Value | New Value (editable).
    Rows whose current ≠ new are highlighted; rows with no change are
    unchecked by default. Press *Apply* to commit; *Cancel* to abort.
    """

    def __init__(self, parent, current: dict, new_tags: dict):
        """
        Parameters
        ----------
        current : dict[tag → (iid, value)]
        new_tags : dict[tag → value]
        """
        super().__init__(parent)
        self.title("Paste Tags from JSON — Review")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.minsize(620, 360)
        self.resizable(True, True)

        self.confirmed = False
        self.result: dict[str, str] = {}   # tag → new value (only applied rows)

        # Build merged row list, sorted: changed first, then additions, then same.
        all_tags = sorted(set(current) | set(new_tags))
        self._rows: list[dict] = []
        for tag in all_tags:
            if tag not in new_tags:
                continue   # JSON didn't mention it → don't propose any change
            old_val = current.get(tag, ("", ""))[1] if tag in current else ""
            new_val = new_tags[tag]
            status = (
                "new" if tag not in current
                else ("same" if old_val == new_val else "changed")
            )
            self._rows.append({
                "tag": tag,
                "old": old_val,
                "new": new_val,
                "status": status,
                "apply_var": tk.BooleanVar(value=(status != "same")),
                "value_var": tk.StringVar(value=new_val),
            })

        # Sort: changed → new → same
        order = {"changed": 0, "new": 1, "same": 2}
        self._rows.sort(key=lambda r: (order[r["status"]], r["tag"]))

        self._build()
        self._center()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Header
        hdr = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hdr, text="📋  Paste Tags from JSON",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        n_changed = sum(1 for r in self._rows if r["status"] == "changed")
        n_new     = sum(1 for r in self._rows if r["status"] == "new")
        n_same    = sum(1 for r in self._rows if r["status"] == "same")
        summ = tk.Frame(self, bg="#eaf2fb", padx=12, pady=6)
        summ.grid(row=1, column=0, sticky="ew")
        tk.Label(
            summ,
            text=(
                f"{n_changed} changed   •   {n_new} new   •   {n_same} unchanged."
                "   Uncheck rows to skip them. Edit the New Value column to adjust."
            ),
            font=("Segoe UI", 9), fg="#1a5276", bg="#eaf2fb",
        ).pack(anchor="w")

        # Scrollable table area
        outer = tk.Frame(self, bg="#f5f5f5")
        outer.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 4))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        # Column headings (manual, since each row contains an Entry widget)
        head = tk.Frame(outer, bg="#d6eaf8")
        head.grid(row=0, column=0, sticky="ew")
        for c, w in enumerate((60, 160, 200, 200)):
            head.columnconfigure(c, weight=(0 if c == 0 else 1), minsize=w)
        for c, text in enumerate(("Apply", "Tag", "Current Value", "New Value")):
            tk.Label(
                head, text=text, font=("Segoe UI", 9, "bold"),
                bg="#d6eaf8", fg="#1a5276", anchor="w", padx=6, pady=4,
            ).grid(row=0, column=c, sticky="ew")

        canvas = tk.Canvas(outer, bg="#ffffff", highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        vsb.grid  (row=1, column=1, sticky="ns")

        inner = tk.Frame(canvas, bg="#ffffff")
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner)

        def _on_canvas(e):
            canvas.itemconfigure(inner_id, width=e.width)
        canvas.bind("<Configure>", _on_canvas)

        for c, w in enumerate((60, 160, 200, 200)):
            inner.columnconfigure(c, weight=(0 if c == 0 else 1), minsize=w)

        bg_for = {
            "changed": "#fefce8",
            "new":     "#eafaf1",
            "same":    "#ffffff",
        }
        fg_for = {
            "changed": "#7d6608",
            "new":     "#196f3d",
            "same":    "#666666",
        }

        for i, r in enumerate(self._rows):
            bg = bg_for[r["status"]]
            tk.Checkbutton(
                inner, variable=r["apply_var"], bg=bg, activebackground=bg,
            ).grid(row=i, column=0, sticky="w", padx=4, pady=1)
            tk.Label(
                inner, text=r["tag"], font=("Segoe UI", 9, "bold"),
                bg=bg, fg=fg_for[r["status"]], anchor="w", padx=4,
            ).grid(row=i, column=1, sticky="ew", pady=1)
            tk.Label(
                inner, text=r["old"], font=("Segoe UI", 9),
                bg=bg, fg="#555555", anchor="w", padx=4,
                wraplength=320, justify="left",
            ).grid(row=i, column=2, sticky="ew", pady=1)
            ttk.Entry(
                inner, textvariable=r["value_var"], font=("Segoe UI", 9),
            ).grid(row=i, column=3, sticky="ew", padx=4, pady=1)

        # Footer buttons
        btn = tk.Frame(self, bg="#f5f5f5", pady=8, padx=12)
        btn.grid(row=3, column=0, sticky="ew")

        def _toggle_all(val: bool):
            for r in self._rows:
                r["apply_var"].set(val)

        ttk.Button(btn, text="Select All",   command=lambda: _toggle_all(True)
                   ).pack(side=tk.LEFT)
        ttk.Button(btn, text="Deselect All", command=lambda: _toggle_all(False)
                   ).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Button(btn, text="Cancel", command=self.destroy
                   ).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn, text="Apply",  command=self._on_apply
                   ).pack(side=tk.RIGHT)

    def _on_apply(self):
        out: dict[str, str] = {}
        for r in self._rows:
            if r["apply_var"].get():
                out[r["tag"]] = r["value_var"].get()
        self.result = out
        self.confirmed = True
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w  = max(self.winfo_reqwidth(),  680)
        h  = max(self.winfo_reqheight(), 420)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class AudioDetailsPanel(tk.Frame):

    def __init__(self, master, on_after_save=None, title: str = "File Details"):
        super().__init__(master, bg="#f0f0f0", width=280)
        self._cover_photo = None
        self._cover_image_data: bytes | None = None   # raw bytes of current cover art
        self._current_path: str | None = None
        self._dirty = False
        self._deleted_items: set = set()   # item IDs marked for deletion
        self._on_after_save = on_after_save  # optional callable(path: str)
        self._title = title
        self._build()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build(self):
        tk.Label(
            self, text=self._title,
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
        self._cover_label.bind("<Button-3>", self._on_cover_right_click)

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
            selectmode="none",
        )
        self._tag_tree.heading("tag", text="Tag", anchor=tk.W)
        self._tag_tree.heading("value", text="Value", anchor=tk.W)
        self._tag_tree.column("tag", width=90, stretch=False)
        self._tag_tree.column("value", width=160, stretch=True)

        self._tag_tree.tag_configure("deleted",
            background="#fde8e8", foreground="#aaaaaa",
        )

        # Cell-level selection: track the active (item, col_idx) and draw a
        # highlight border on top of it using four thin frames so the cell
        # text underneath stays visible. The Treeview itself has selectmode
        # disabled so individual cells, not whole rows, appear selected.
        self._active_cell: tuple[str, int] | None = None
        self._cell_hl_frames = [
            tk.Frame(self._tag_tree, bg="#2563eb", bd=0) for _ in range(4)
        ]

        def _on_yscroll(first, last):
            tag_vsb.set(first, last)
            self._redraw_cell_highlight()

        tag_vsb = ttk.Scrollbar(tag_frame, orient=tk.VERTICAL, command=self._tag_tree.yview)
        self._tag_tree.configure(yscrollcommand=_on_yscroll)
        tag_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tag_tree.pack(fill=tk.BOTH, expand=True)

        self._tag_tree.bind("<Button-1>",     self._on_cell_click)
        self._tag_tree.bind("<Double-1>",     self._start_edit)
        self._tag_tree.bind("<Delete>",       self._mark_deleted)
        self._tag_tree.bind("<Control-c>",    self._copy_active_cell)
        self._tag_tree.bind("<Control-C>",    self._copy_active_cell)
        self._tag_tree.bind("<Control-v>",    self._paste_active_cell)
        self._tag_tree.bind("<Control-V>",    self._paste_active_cell)
        self._tag_tree.bind("<Configure>",    lambda _e: self._redraw_cell_highlight(), add=True)

        # Save / Copy / Add Tag buttons
        btn_frame = tk.Frame(self, bg="#f0f0f0")
        btn_frame.pack(fill=tk.X, padx=4, pady=4)
        self._save_btn = ttk.Button(
            btn_frame, text="Save Tags",
            command=self._save_tags, state="disabled",
        )
        self._save_btn.pack(side=tk.RIGHT)
        ttk.Button(
            btn_frame, text="Copy JSON",
            command=self._copy_tags_json,
        ).pack(side=tk.RIGHT, padx=(0, 4))
        ttk.Button(
            btn_frame, text="Paste JSON",
            command=self._paste_tags_json,
        ).pack(side=tk.RIGHT, padx=(0, 4))
        ttk.Button(
            btn_frame, text="+ Add Tag",
            command=self._add_tag_dialog,
        ).pack(side=tk.LEFT)

    # ------------------------------------------------------------------ #
    # Inline editing                                                       #
    # ------------------------------------------------------------------ #

    def _on_cell_click(self, event):
        # Clear any row-level focus/selection that Tk may have applied so that
        # only the cell-level highlight is visible.
        try:
            self._tag_tree.selection_remove(*self._tag_tree.selection())
        except tk.TclError:
            pass
        self._tag_tree.focus("")

        item = self._tag_tree.identify_row(event.y)
        col  = self._tag_tree.identify_column(event.x)
        if not item or col not in ("#1", "#2"):
            self._active_cell = None
            self._redraw_cell_highlight()
            return "break"
        col_idx = int(col[1:]) - 1
        self._active_cell = (item, col_idx)
        self._tag_tree.focus_set()
        self._redraw_cell_highlight()
        return "break"

    def _redraw_cell_highlight(self):
        if not self._active_cell:
            for f in self._cell_hl_frames:
                f.place_forget()
            return
        item, col_idx = self._active_cell
        col = f"#{col_idx + 1}"
        bbox = self._tag_tree.bbox(item, col)
        if not bbox:
            for f in self._cell_hl_frames:
                f.place_forget()
            return
        x, y, w, h = bbox
        t = 2
        top, bottom, left, right = self._cell_hl_frames
        top.place   (x=x,         y=y,         width=w, height=t)
        bottom.place(x=x,         y=y + h - t, width=w, height=t)
        left.place  (x=x,         y=y,         width=t, height=h)
        right.place (x=x + w - t, y=y,         width=t, height=h)
        for f in self._cell_hl_frames:
            f.lift()

    def _copy_active_cell(self, _event=None):
        if not self._active_cell:
            return "break"
        item, col_idx = self._active_cell
        text = str(self._tag_tree.item(item, "values")[col_idx])
        self.clipboard_clear()
        self.clipboard_append(text)
        return "break"

    def _paste_active_cell(self, _event=None):
        if not self._active_cell:
            return "break"
        item, col_idx = self._active_cell
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"
        vals = list(self._tag_tree.item(item, "values"))
        vals[col_idx] = text.strip()
        self._tag_tree.item(item, values=vals)
        self._mark_dirty()
        self._redraw_cell_highlight()
        return "break"

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

    def _mark_deleted(self, _event=None):
        """Mark the active cell's row for deletion (red/grey). Actual removal on Save."""
        if not self._active_cell:
            return
        item = self._active_cell[0]
        if item in self._deleted_items:
            self._deleted_items.discard(item)
            self._tag_tree.item(item, tags=())
        else:
            self._deleted_items.add(item)
            self._tag_tree.item(item, tags=("deleted",))
        if self._deleted_items:
            self._mark_dirty()
        self._redraw_cell_highlight()

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

    def _copy_tags_json(self):
        """Copy all (non-deleted) tag rows as JSON to clipboard."""
        import json
        items = self._tag_tree.get_children()
        data = {
            self._tag_tree.item(iid, "values")[0]: self._tag_tree.item(iid, "values")[1]
            for iid in items
            if iid not in self._deleted_items
        }
        self.clipboard_clear()
        self.clipboard_append(json.dumps(data, ensure_ascii=False, indent=2))

    def _paste_tags_json(self):
        """Paste tags from a clipboard JSON object, with side-by-side review.

        The clipboard must contain a JSON object whose keys are tag names and
        whose values are the new tag values (string or list of strings). A
        dialog lets the user see current-vs-new values, edit the proposed
        values, and selectively apply rows before they touch the tag tree.
        """
        import json
        if not self._current_path:
            messagebox.showinfo(
                "Paste JSON",
                "Load a file first before pasting tags.",
                parent=self,
            )
            return
        try:
            raw = self.clipboard_get()
        except tk.TclError:
            messagebox.showerror("Paste JSON", "Clipboard is empty.", parent=self)
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            messagebox.showerror(
                "Paste JSON",
                f"Clipboard does not contain valid JSON:\n{exc}",
                parent=self,
            )
            return
        if not isinstance(payload, dict) or not payload:
            messagebox.showerror(
                "Paste JSON",
                "Clipboard JSON must be a non-empty object of tag → value.",
                parent=self,
            )
            return

        # Normalise: tag name upper-cased, values flattened to a single string.
        new_tags: dict[str, str] = {}
        for k, v in payload.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if isinstance(v, list):
                v = " / ".join(str(x) for x in v)
            elif v is None:
                v = ""
            else:
                v = str(v)
            new_tags[k.strip().upper()] = v

        current: dict[str, tuple[str, str]] = {}   # tag → (iid, current_value)
        for iid in self._tag_tree.get_children():
            if iid in self._deleted_items:
                continue
            tag, val = self._tag_tree.item(iid, "values")
            current[str(tag).upper()] = (iid, str(val))

        dlg = _PasteJsonDialog(self.winfo_toplevel(), current, new_tags)
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        applied = 0
        for tag, new_val in dlg.result.items():
            if tag in current:
                iid, _ = current[tag]
                self._tag_tree.item(iid, values=(tag, new_val))
            else:
                self._tag_tree.insert("", "end", values=(tag, new_val))
            applied += 1

        if applied:
            self._mark_dirty()

    # ------------------------------------------------------------------ #
    # Add new tag                                                          #
    # ------------------------------------------------------------------ #

    # Common FLAC tag names offered in the dropdown
    _COMMON_TAGS = [
        "TITLE", "ARTIST", "ALBUM", "ALBUMARTIST", "DATE", "YEAR",
        "TRACKNUMBER", "TOTALTRACKS", "DISCNUMBER", "TOTALDISCS",
        "GENRE", "COMMENT", "COMPOSER", "CONDUCTOR", "LYRICIST",
        "LYRICS", "DESCRIPTION", "LABEL", "ISRC", "BARCODE",
        "REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_ALBUM_GAIN",
    ]

    def _add_tag_dialog(self):
        """Open a small dialog to enter a new tag name + value."""
        if not self._current_path:
            return

        dlg = tk.Toplevel(self)
        dlg.title("Add Tag")
        dlg.configure(bg="#f5f5f5")
        dlg.resizable(False, False)
        dlg.grab_set()

        pad = {"padx": 10, "pady": 6}

        tk.Label(dlg, text="Tag name:", font=("Segoe UI", 9),
                 bg="#f5f5f5", anchor="w").grid(row=0, column=0, sticky="w", **pad)
        name_var = tk.StringVar(value="ALBUM")
        name_cb = ttk.Combobox(dlg, textvariable=name_var,
                               values=self._COMMON_TAGS, width=22)
        name_cb.grid(row=0, column=1, sticky="ew", **pad)

        tk.Label(dlg, text="Value:", font=("Segoe UI", 9),
                 bg="#f5f5f5", anchor="w").grid(row=1, column=0, sticky="w", **pad)
        val_var = tk.StringVar()
        val_entry = ttk.Entry(dlg, textvariable=val_var, width=24)
        val_entry.grid(row=1, column=1, sticky="ew", **pad)

        # Error label (hidden until needed)
        err_var = tk.StringVar()
        err_lbl = tk.Label(dlg, textvariable=err_var,
                           font=("Segoe UI", 8), fg="#c0392b", bg="#f5f5f5")
        err_lbl.grid(row=2, column=0, columnspan=2, sticky="w", padx=10)

        btn_row = tk.Frame(dlg, bg="#f5f5f5")
        btn_row.grid(row=3, column=0, columnspan=2, sticky="e", padx=8, pady=(0, 8))

        def commit(_=None):
            tag  = name_var.get().strip().upper()
            val  = val_var.get().strip()
            if not tag:
                err_var.set("Tag name is required.")
                name_cb.focus_set()
                return
            # Check for duplicate tag name
            existing = [
                self._tag_tree.item(iid, "values")[0].upper()
                for iid in self._tag_tree.get_children()
                if iid not in self._deleted_items
            ]
            if tag in existing:
                err_var.set(f'Tag "{tag}" already exists — edit it in the list.')
                return
            self._tag_tree.insert("", "end", values=(tag, val))
            self._mark_dirty()
            dlg.destroy()

        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Add",    command=commit).pack(side=tk.RIGHT)

        # Enter in either field commits
        name_cb.bind("<Return>", commit)
        val_entry.bind("<Return>", commit)
        # Tab from name → value
        name_cb.bind("<Tab>", lambda _: (val_entry.focus_set(), "break"))

        dlg.columnconfigure(1, weight=1)
        dlg.update_idletasks()
        # Centre over the panel
        x = self.winfo_rootx() + (self.winfo_width()  - dlg.winfo_reqwidth())  // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")

        val_entry.focus_set()

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
                if iid in self._deleted_items:
                    continue          # skip tags marked for deletion
                key, val = self._tag_tree.item(iid, "values")
                if key:
                    flac[key.lower()] = [val]
            flac.save()
            if self._on_after_save:
                self._on_after_save(self._current_path)
            # Remove the deleted rows from the tree now that save succeeded
            for iid in self._deleted_items:
                self._tag_tree.delete(iid)
            if self._active_cell and self._active_cell[0] in self._deleted_items:
                self._active_cell = None
            self._deleted_items.clear()
            self._dirty = False
            self._save_btn.configure(state="disabled")
            self._redraw_cell_highlight()
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
                self._cover_image_data = cover_pic.data
            except Exception:
                self._cover_label.configure(image="", text="(cover unreadable)")
                self._cover_image_data = None
        else:
            self._cover_label.configure(image="", text="No cover art")
            self._cover_image_data = None

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
        self._deleted_items.clear()
        self._save_btn.configure(state="disabled")
        self._cover_label.configure(image="", text="No cover art")
        self._cover_photo = None
        self._cover_image_data = None
        self._img_count_var.set("")
        self._img_dims_var.set("")
        self._tag_tree.delete(*self._tag_tree.get_children())
        self._active_cell = None
        self._redraw_cell_highlight()

    # ------------------------------------------------------------------ #
    # Cover art context menu                                               #
    # ------------------------------------------------------------------ #

    def _on_cover_right_click(self, event) -> None:
        if not self._cover_image_data:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="📋  Copy Image to Clipboard",
            command=self._copy_cover_to_clipboard,
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _copy_cover_to_clipboard(self) -> None:
        """Copy the full-resolution cover art to the Windows clipboard (CF_DIB)."""
        if not self._cover_image_data:
            return
        try:
            img = Image.open(io.BytesIO(self._cover_image_data)).convert("RGB")

            # Encode as BMP and strip the 14-byte BITMAPFILEHEADER;
            # the clipboard expects a raw BITMAPINFOHEADER + pixel data (CF_DIB).
            buf = io.BytesIO()
            img.save(buf, "BMP")
            dib = buf.getvalue()[14:]

            GMEM_MOVEABLE = 0x0002
            CF_DIB        = 8

            k32 = ctypes.windll.kernel32
            u32 = ctypes.windll.user32

            # Declare correct arg/return types so 64-bit handles are not truncated.
            k32.GlobalAlloc.restype  = ctypes.c_void_p
            k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            k32.GlobalLock.restype   = ctypes.c_void_p
            k32.GlobalLock.argtypes  = [ctypes.c_void_p]
            k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            u32.SetClipboardData.restype  = ctypes.c_void_p
            u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

            u32.OpenClipboard(0)
            try:
                u32.EmptyClipboard()
                h = k32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
                p = k32.GlobalLock(h)
                ctypes.memmove(p, dib, len(dib))
                k32.GlobalUnlock(h)
                u32.SetClipboardData(CF_DIB, h)
            finally:
                u32.CloseClipboard()
        except Exception as exc:
            messagebox.showerror("Copy failed", str(exc), parent=self)
