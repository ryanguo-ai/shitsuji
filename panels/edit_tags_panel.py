"""
Edit Tags panel — view and batch-edit FLAC tags across one or more files.

Tag states:
  normal  — single consistent value across all files
  multi   — values differ; displayed as «multiple values» {v1};{v2}
  edited  — user has typed a new value (will overwrite all files on save)
  deleted — marked for removal from all files on save
"""

import tkinter as tk
from tkinter import ttk, messagebox

from mutagen.flac import FLAC

MULTI_PREFIX = "«multiple values»"


def _aggregate_tags(paths: list[str]) -> list[tuple[str, str]]:
    """Return sorted [(TAG_NAME, display_value)] across all files."""
    tag_values: dict[str, set] = {}

    for path in paths:
        try:
            flac = FLAC(path)
            for key, values in (flac.tags or {}).items():
                val = " / ".join(values) if isinstance(values, list) else str(values)
                tag_values.setdefault(key.upper(), set()).add(val)
        except Exception:
            pass

    result = []
    for tag in sorted(tag_values):
        value_set = tag_values[tag]
        if len(value_set) == 1:
            display = next(iter(value_set))
        else:
            display = f"{MULTI_PREFIX} " + ";".join(sorted(value_set))
        result.append((tag, display))

    return result


class EditTagsPanel(tk.Toplevel):
    """Modeless window for editing tags across one or more FLAC files."""

    def __init__(self, parent: tk.Widget, paths: list[str]):
        super().__init__(parent)
        self._paths = list(paths)
        self._deleted_keys: set = set()  # lowercase tag names removed from table
        self._edited:       dict = {}    # item ID → new value string

        n = len(paths)
        self.title(f"Edit Tags — {n} file{'s' if n > 1 else ''}")
        self.configure(bg="#f5f5f5")
        self.minsize(520, 400)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build()
        self._load_tags()
        self._center()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build(self):
        # ── Header ── #
        header = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        header.pack(fill=tk.X)

        tk.Label(
            header, text="🏷  Edit Tags",
            font=("Segoe UI", 12, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        n = len(self._paths)
        tk.Label(
            header, text=f"{n} file{'s' if n > 1 else ''}",
            font=("Segoe UI", 9), fg="#bdc3c7", bg="#2c3e50",
        ).pack(side=tk.RIGHT)

        # ── Tag table ── #
        tree_frame = tk.Frame(self, bg="#f5f5f5")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 0))

        self._tree = ttk.Treeview(
            tree_frame, columns=("tag", "value"), show="headings",
            selectmode="extended",
        )
        self._tree.heading("tag",   text="Tag",   anchor=tk.W)
        self._tree.heading("value", text="Value", anchor=tk.W)
        self._tree.column("tag",   width=130, stretch=False)
        self._tree.column("value", width=400, stretch=True)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.tag_configure("normal",  background="#ffffff")
        self._tree.tag_configure("multi",   background="#fffbe6", foreground="#856404")
        self._tree.tag_configure("edited",  background="#e8f4fd", foreground="#0c4a6e")

        self._tree.bind("<Double-1>",        self._start_edit)
        self._tree.bind("<Return>",          self._start_edit)
        self._tree.bind("<F2>",              self._start_edit)

        # ── Hint ── #
        tk.Label(
            self, text="Double-click or press F2 / Enter to edit a value.",
            font=("Segoe UI", 8), fg="#95a5a6", bg="#f5f5f5", anchor="w", padx=12,
        ).pack(fill=tk.X, pady=(2, 0))

        # ── Bottom toolbar ── #
        toolbar = tk.Frame(self, bg="#ecf0f1", pady=6, padx=10)
        toolbar.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Button(
            toolbar, text="🗑  Delete Selected Tags",
            command=self._delete_selected,
        ).pack(side=tk.LEFT)

        ttk.Button(
            toolbar, text="+ Add Tag",
            command=self._add_tag_dialog,
        ).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Button(toolbar, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="Save",   command=self._save  ).pack(side=tk.RIGHT, padx=(0, 4))

    # ------------------------------------------------------------------ #
    # Data loading                                                         #
    # ------------------------------------------------------------------ #

    def _load_tags(self):
        self._tree.delete(*self._tree.get_children())
        self._deleted_keys.clear()
        self._edited.clear()

        for tag_name, display_val in _aggregate_tags(self._paths):
            row_tag = "multi" if display_val.startswith(MULTI_PREFIX) else "normal"
            self._tree.insert("", "end", values=(tag_name, display_val), tags=(row_tag,))

    # ------------------------------------------------------------------ #
    # Inline editing (both tag name and value columns)                     #
    # ------------------------------------------------------------------ #

    def _start_edit(self, event=None):
        # For keyboard triggers, use the currently focused/selected row
        if event and event.type == tk.EventType.KeyPress:
            selected = self._tree.selection()
            if not selected:
                return
            item = selected[0]
            col  = "#2"   # default to value column for keyboard
        else:
            item = self._tree.identify_row(event.y)
            col  = self._tree.identify_column(event.x)
            if not item:
                return
            # Only allow editing col 1 (tag name) or col 2 (value)
            if col not in ("#1", "#2"):
                return

        bbox = self._tree.bbox(item, col)
        if not bbox:
            return
        x, y, w, h = bbox

        col_idx = 0 if col == "#1" else 1
        current = self._tree.item(item, "values")[col_idx]

        # Clear «multiple values» prefix when editing value column
        if col_idx == 1 and current.startswith(MULTI_PREFIX):
            current = ""

        var   = tk.StringVar(value=current)
        entry = tk.Entry(
            self._tree, textvariable=var,
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
            new_val = var.get().strip()
            vals = list(self._tree.item(item, "values"))
            vals[col_idx] = new_val
            self._tree.item(item, values=tuple(vals), tags=("edited",))
            # Track edited value (keyed by item; store full (tag, value) tuple)
            self._edited[item] = (vals[0], vals[1])

        def cancel(_=None):
            done[0] = True
            entry.destroy()

        def tab_next(_=None):
            commit()
            # Advance to next row
            children = self._tree.get_children()
            if item in children:
                idx = list(children).index(item)
                if idx + 1 < len(children):
                    nxt = children[idx + 1]
                    self._tree.selection_set(nxt)
                    self._tree.focus(nxt)
                    self.after(50, lambda: self._start_edit_row(nxt))

        entry.bind("<Return>",   commit)
        entry.bind("<Tab>",      tab_next)
        entry.bind("<Escape>",   cancel)
        entry.bind("<FocusOut>", commit)

    def _start_edit_row(self, item):
        """Open editor on value column of a specific row (used by Tab navigation)."""
        bbox = self._tree.bbox(item, "#2")
        if not bbox:
            return
        x, y, w, h = bbox
        current = self._tree.item(item, "values")[1]
        if current.startswith(MULTI_PREFIX):
            current = ""

        var   = tk.StringVar(value=current)
        entry = tk.Entry(
            self._tree, textvariable=var,
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
            new_val = var.get().strip()
            vals = list(self._tree.item(item, "values"))
            vals[1] = new_val
            self._tree.item(item, values=tuple(vals), tags=("edited",))
            self._edited[item] = (vals[0], vals[1])

        def cancel(_=None):
            done[0] = True
            entry.destroy()

        entry.bind("<Return>",   commit)
        entry.bind("<Escape>",   cancel)
        entry.bind("<FocusOut>", commit)

    # ------------------------------------------------------------------ #
    # Add new tag                                                          #
    # ------------------------------------------------------------------ #

    _COMMON_TAGS = [
        "TITLE", "ARTIST", "ALBUM", "ALBUMARTIST", "DATE", "YEAR",
        "TRACKNUMBER", "TOTALTRACKS", "DISCNUMBER", "TOTALDISCS",
        "GENRE", "COMMENT", "COMPOSER", "CONDUCTOR", "LYRICIST",
        "LYRICS", "DESCRIPTION", "LABEL", "ISRC", "BARCODE",
        "REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_ALBUM_GAIN",
    ]

    def _add_tag_dialog(self):
        """Open a small dialog to add a new tag to all files."""
        existing_tags = {
            self._tree.item(iid, "values")[0].upper()
            for iid in self._tree.get_children()
        }
        default_tag = "ALBUM" if "ALBUM" not in existing_tags else ""

        dlg = tk.Toplevel(self)
        dlg.title("Add Tag")
        dlg.configure(bg="#f5f5f5")
        dlg.resizable(False, False)
        dlg.grab_set()

        pad = {"padx": 10, "pady": 6}

        tk.Label(dlg, text="Tag name:", font=("Segoe UI", 9),
                 bg="#f5f5f5", anchor="w").grid(row=0, column=0, sticky="w", **pad)
        name_var = tk.StringVar(value=default_tag)
        name_cb = ttk.Combobox(dlg, textvariable=name_var,
                               values=self._COMMON_TAGS, width=22)
        name_cb.grid(row=0, column=1, sticky="ew", **pad)

        tk.Label(dlg, text="Value:", font=("Segoe UI", 9),
                 bg="#f5f5f5", anchor="w").grid(row=1, column=0, sticky="w", **pad)
        val_var = tk.StringVar()
        val_entry = ttk.Entry(dlg, textvariable=val_var, width=24)
        val_entry.grid(row=1, column=1, sticky="ew", **pad)

        err_var = tk.StringVar()
        tk.Label(dlg, textvariable=err_var,
                 font=("Segoe UI", 8), fg="#c0392b", bg="#f5f5f5").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=10)

        btn_row = tk.Frame(dlg, bg="#f5f5f5")
        btn_row.grid(row=3, column=0, columnspan=2, sticky="e", padx=8, pady=(0, 8))

        def commit(_=None):
            tag = name_var.get().strip().upper()
            val = val_var.get().strip()
            if not tag:
                err_var.set("Tag name is required.")
                name_cb.focus_set()
                return
            if tag in existing_tags:
                err_var.set(f'Tag "{tag}" already exists — edit it in the list.')
                return
            iid = self._tree.insert("", "end", values=(tag, val), tags=("edited",))
            self._edited[iid] = (tag, val)
            dlg.destroy()

        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Add",    command=commit).pack(side=tk.RIGHT)

        name_cb.bind("<Return>", commit)
        val_entry.bind("<Return>", commit)
        name_cb.bind("<Tab>", lambda _: (val_entry.focus_set(), "break"))

        dlg.columnconfigure(1, weight=1)
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width()  - dlg.winfo_reqwidth())  // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")

        name_cb.focus_set()

    # ------------------------------------------------------------------ #
    # Delete                                                               #
    # ------------------------------------------------------------------ #

    def _delete_selected(self):
        items = self._tree.selection()
        if not items:
            return
        for item in items:
            tag_name = self._tree.item(item, "values")[0].lower()
            self._deleted_keys.add(tag_name)
            self._edited.pop(item, None)
            self._tree.delete(item)

    # ------------------------------------------------------------------ #
    # Save                                                                 #
    # ------------------------------------------------------------------ #

    def _save(self):
        # _edited maps item_id → (tag_name, new_value)
        updates = {
            tag_name.lower(): val
            for tag_name, val in self._edited.values()
        }

        if not self._deleted_keys and not updates:
            self.destroy()
            return

        errors: list[str] = []
        for path in self._paths:
            try:
                flac = FLAC(path)
                for key in list(flac.keys()):
                    if key.lower() in self._deleted_keys:
                        del flac[key]
                for key, val in updates.items():
                    flac[key] = [val]
                flac.save()
            except Exception as exc:
                errors.append(f"{path}:\n  {exc}")

        if errors:
            messagebox.showerror(
                "Save errors",
                f"{len(errors)} file(s) could not be saved:\n\n" + "\n".join(errors[:5]),
                parent=self,
            )
        else:
            self.destroy()

    # ------------------------------------------------------------------ #
    # Geometry                                                             #
    # ------------------------------------------------------------------ #

    def _center(self):
        self.update_idletasks()
        w  = max(self.winfo_reqwidth(),  560)
        h  = max(self.winfo_reqheight(), 460)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
