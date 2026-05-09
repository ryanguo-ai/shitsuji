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
        self._tree.tag_configure("deleted", background="#fde8e8", foreground="#9b1c1c")

        self._tree.bind("<Double-1>", self._start_edit)

        # ── Bottom toolbar ── #
        toolbar = tk.Frame(self, bg="#ecf0f1", pady=6, padx=10)
        toolbar.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Button(
            toolbar, text="🗑  Delete Selected Tags",
            command=self._delete_selected,
        ).pack(side=tk.LEFT)

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
    # Inline editing (value column only)                                   #
    # ------------------------------------------------------------------ #

    def _start_edit(self, event):
        item = self._tree.identify_row(event.y)
        col  = self._tree.identify_column(event.x)
        if not item or col != "#2" or item in self._deleted:
            return

        bbox = self._tree.bbox(item, col)
        if not bbox:
            return
        x, y, w, h = bbox

        # Strip the «multiple values» prefix so the user starts with a blank slate
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
            new_val  = var.get().strip()
            tag_name = self._tree.item(item, "values")[0]
            self._tree.item(item, values=(tag_name, new_val), tags=("edited",))
            self._edited[item] = new_val

        def cancel(_=None):
            done[0] = True
            entry.destroy()

        entry.bind("<Return>",   commit)
        entry.bind("<Tab>",      commit)
        entry.bind("<Escape>",   cancel)
        entry.bind("<FocusOut>", commit)

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
        updates = {
            self._tree.item(i, "values")[0].lower(): val
            for i, val in self._edited.items()
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
