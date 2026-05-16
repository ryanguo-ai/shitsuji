"""
Scan Folders panel — drag folders in, inspect their file type breakdown.

Rows are colour-coded:
  • Green  — folder contains at least one FLAC file
  • Red    — folder has no FLAC files

Right-click menu on folders with no FLAC files exposes a "Delete from Disk"
action in addition to the always-available "Remove from List" action.
"""

import os
import shutil
import tkinter as tk
from tkinter import ttk, messagebox

from tkinterdnd2 import DND_FILES

from panels.audio_menu import AUDIO_EXTENSIONS
from panels.logger import get_logger

_log = get_logger("scan_folders")

_FLAC_EXT        = {"FLAC"}
_MP3_EXT         = {"MP3"}
_OTHER_AUDIO_EXT = AUDIO_EXTENSIONS - _FLAC_EXT - _MP3_EXT


def _scan_folder_types(folder: str) -> dict:
    """Return file-type counts for top-level files inside *folder*.

    Keys: flac, mp3, other_audio, other, total
    """
    counts = {"flac": 0, "mp3": 0, "other_audio": 0, "other": 0, "total": 0}
    try:
        for entry in os.scandir(folder):
            if not entry.is_file(follow_symlinks=False):
                continue
            counts["total"] += 1
            ext = os.path.splitext(entry.name)[1].lstrip(".").upper()
            if ext in _FLAC_EXT:
                counts["flac"] += 1
            elif ext in _MP3_EXT:
                counts["mp3"] += 1
            elif ext in _OTHER_AUDIO_EXT:
                counts["other_audio"] += 1
            else:
                counts["other"] += 1
    except PermissionError:
        pass
    return counts


class ScanFoldersTab(tk.Frame):
    """Notebook tab: drag folders in, see per-folder file-type breakdown."""

    # (col_id, heading, width, anchor, stretch)
    _COLS = [
        ("folder",      "Folder Path",   420, tk.W,      True),
        ("flac",        "FLAC",           60, tk.CENTER, False),
        ("mp3",         "MP3",            60, tk.CENTER, False),
        ("other_audio", "Other Audio",    90, tk.CENTER, False),
        ("other",       "Other Files",    85, tk.CENTER, False),
        ("total",       "Total",          60, tk.CENTER, False),
    ]

    def __init__(self, master, on_scan_folders=None):
        """
        Parameters
        ----------
        on_scan_folders : callable(folders: list[str]) | None
            Called when the user chooses "Scan folders" from the right-click
            menu.  The host (App) should switch to the Scan tab and invoke
            ScanTab.scan_folders() with the supplied list.
        """
        super().__init__(master, bg="#f5f5f5")
        self._on_scan_folders = on_scan_folders
        self._sort_col: str | None = None
        self._sort_rev: bool = False
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── Header bar ── #
        hdr = tk.Frame(self, bg="#2c3e50", pady=12, padx=16)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr, text="📂  Scan Folders",
            font=("Segoe UI", 16, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # ── Toolbar row ── #
        toolbar = tk.Frame(self, bg="#f5f5f5", pady=8, padx=16)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="🔄  Refresh", command=self._refresh).pack(side=tk.LEFT)

        # ── Hint label ── #
        tk.Label(
            self,
            text=(
                "Drag folders here to inspect their file-type breakdown.  "
                "Green rows contain FLAC files; red rows do not."
            ),
            font=("Segoe UI", 9, "italic"),
            fg="#7f8c8d", bg="#f5f5f5",
            anchor="w", padx=16, pady=6,
        ).pack(fill=tk.X)

        # ── Treeview ── #
        tree_frame = tk.Frame(self, bg="#f5f5f5")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 4))

        col_ids = [c[0] for c in self._COLS]
        self._tree = ttk.Treeview(
            tree_frame, columns=col_ids,
            show="headings", selectmode="extended",
        )
        _cmd = lambda c: (lambda: self._sort_column(c))
        for cid, heading, width, anchor, stretch in self._COLS:
            self._tree.heading(cid, text=heading, anchor=anchor, command=_cmd(cid))
            self._tree.column(cid, width=width, anchor=anchor, stretch=stretch)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        # Row colour tags
        self._tree.tag_configure(
            "has_flac",
            background="#eafaf1", foreground="#1e8449",   # green — folder has FLAC
        )
        self._tree.tag_configure(
            "no_flac",
            background="#fdf2f8", foreground="#922b21",   # red — no FLAC
        )

        # Bindings
        self._tree.bind("<Button-3>",   self._on_right_click)
        self._tree.bind("<Delete>",     self._on_delete_key)
        self._tree.bind("<Control-a>",
                        lambda _: self._tree.selection_set(self._tree.get_children()))

        # Register as a DnD drop target
        self._tree.drop_target_register(DND_FILES)
        self._tree.dnd_bind("<<Drop>>", self._on_drop)

        # ── Status bar ── #
        bar = tk.Frame(self, bg="#bdc3c7", height=24)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self._status_var = tk.StringVar(value="Drop folders here to scan.")
        tk.Label(
            bar, textvariable=self._status_var,
            font=("Segoe UI", 9), bg="#bdc3c7",
            anchor="w", padx=8,
        ).pack(fill=tk.X)

    # ------------------------------------------------------------------ #
    # Column sorting                                                       #
    # ------------------------------------------------------------------ #

    _COL_LABELS = {
        "folder":      "Folder Path",
        "flac":        "FLAC",
        "mp3":         "MP3",
        "other_audio": "Other Audio",
        "other":       "Other Files",
        "total":       "Total",
    }
    _NUMERIC_COLS = {"flac", "mp3", "other_audio", "other", "total"}

    def _sort_column(self, col: str):
        """Sort rows by *col*, toggling direction on repeated clicks."""
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False

        if col in self._NUMERIC_COLS:
            key_fn = lambda iid: int(self._tree.set(iid, col) or 0)
        else:
            key_fn = lambda iid: self._tree.set(iid, col).lower()

        items = sorted(self._tree.get_children(), key=key_fn, reverse=self._sort_rev)
        for i, iid in enumerate(items):
            self._tree.move(iid, "", i)

        arrow = " ▲" if not self._sort_rev else " ▼"
        for c, label in self._COL_LABELS.items():
            self._tree.heading(c, text=label + (arrow if c == col else ""))

    # ------------------------------------------------------------------ #
    # Refresh                                                              #
    # ------------------------------------------------------------------ #

    def _refresh(self):
        """Re-scan every listed folder; remove rows whose folder no longer exists."""
        items = self._tree.get_children()
        if not items:
            self._status_var.set("Nothing to refresh.")
            return

        removed = 0
        updated = 0
        for iid in items:
            folder = self._tree.set(iid, "folder")
            if not os.path.isdir(folder):
                self._tree.delete(iid)
                removed += 1
                _log.info(f"Refresh: removed missing folder {folder}")
                continue

            counts = _scan_folder_types(folder)
            tag    = "has_flac" if counts["flac"] > 0 else "no_flac"
            self._tree.item(iid, tags=(tag,), values=(
                folder,
                counts["flac"],
                counts["mp3"],
                counts["other_audio"],
                counts["other"],
                counts["total"],
            ))
            updated += 1

        total = len(self._tree.get_children())
        parts = [f"Refreshed {updated} folder{'s' if updated != 1 else ''}"]
        if removed:
            parts.append(f"{removed} missing folder{'s' if removed != 1 else ''} removed")
        parts.append(f"{total} remaining")
        self._status_var.set(" — ".join(parts) + ".")
        _log.info(f"Refresh complete: {updated} updated, {removed} removed")

    # ------------------------------------------------------------------ #
    # DnD                                                                  #
    # ------------------------------------------------------------------ #

    def _on_drop(self, event):
        paths = self.tk.splitlist(event.data)
        added = 0
        skipped = 0
        for path in paths:
            path = path.strip()
            if os.path.isdir(path):
                if self._add_folder(path):
                    added += 1
                else:
                    skipped += 1

        total = len(self._tree.get_children())
        parts = [f"Added {added} folder{'s' if added != 1 else ''}"]
        if skipped:
            parts.append(f"{skipped} already in list")
        parts.append(f"{total} total")
        self._status_var.set(" — ".join(parts) + ".")
        _log.info(f"Dropped {added} folder(s), {skipped} duplicate(s), {total} total")

    def _add_folder(self, folder: str) -> bool:
        """Scan *folder* and append a row.  Returns False if already present."""
        for iid in self._tree.get_children():
            if os.path.normcase(self._tree.set(iid, "folder")) == os.path.normcase(folder):
                return False   # already listed

        counts = _scan_folder_types(folder)
        tag    = "has_flac" if counts["flac"] > 0 else "no_flac"

        self._tree.insert(
            "", "end", tags=(tag,),
            values=(
                folder,
                counts["flac"],
                counts["mp3"],
                counts["other_audio"],
                counts["other"],
                counts["total"],
            ),
        )
        return True

    # ------------------------------------------------------------------ #
    # Keyboard                                                             #
    # ------------------------------------------------------------------ #

    def _on_delete_key(self, _event=None):
        """Remove selected rows from the list (no disk changes)."""
        selected = self._tree.selection()
        if not selected:
            return
        for iid in selected:
            self._tree.delete(iid)
        n     = len(selected)
        total = len(self._tree.get_children())
        self._status_var.set(
            f"Removed {n} folder{'s' if n != 1 else ''} from list — {total} remaining."
        )

    # ------------------------------------------------------------------ #
    # Right-click menu                                                     #
    # ------------------------------------------------------------------ #

    def _on_right_click(self, event):
        item = self._tree.identify_row(event.y)
        if not item:
            return

        # Select clicked row if it isn't already part of the selection
        if item not in self._tree.selection():
            self._tree.selection_set(item)

        selected = self._tree.selection()
        n = len(selected)

        menu = tk.Menu(self, tearoff=0)

        # Scan selected folders in the Scan tab
        if self._on_scan_folders is not None:
            menu.add_command(
                label=f"🔍  Scan {n} Folder{'s' if n != 1 else ''} in Scan tab",
                command=lambda iids=selected: self._on_scan_folders(
                    [self._tree.set(iid, "folder") for iid in iids]
                ),
            )
            menu.add_separator()

        # Always available: remove from list
        menu.add_command(
            label=f"✖  Remove {n} Folder{'s' if n != 1 else ''} from List",
            accelerator="Delete",
            command=self._on_delete_key,
        )

        # Only for folders that contain no FLAC files: delete from disk
        no_flac_iids = [
            iid for iid in selected
            if self._tree.set(iid, "flac") == "0"
        ]
        if no_flac_iids:
            menu.add_separator()
            n_del = len(no_flac_iids)
            menu.add_command(
                label=f"🗑  Delete {n_del} Folder{'s' if n_del != 1 else ''} from Disk  (no FLAC)",
                command=lambda iids=no_flac_iids: self._delete_folders_from_disk(iids),
            )

        menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------ #
    # Delete folders from disk                                             #
    # ------------------------------------------------------------------ #

    def _delete_folders_from_disk(self, iids: list):
        """Confirm then permanently delete the given folders from disk."""
        folders = [self._tree.set(iid, "folder") for iid in iids]
        n = len(folders)
        preview = "\n".join(f"• {f}" for f in folders[:10])
        if n > 10:
            preview += f"\n…and {n - 10} more"

        confirmed = messagebox.askyesno(
            "Delete Folders from Disk",
            f"Permanently delete {n} folder{'s' if n != 1 else ''} and all their "
            f"contents from disk?\n\n{preview}\n\nThis cannot be undone.",
            icon="warning",
            parent=self,
        )
        if not confirmed:
            return

        deleted, errors = [], []
        for iid, folder in zip(iids, folders):
            try:
                shutil.rmtree(folder)
                deleted.append(iid)
                _log.info(f"Deleted folder from disk: {folder}")
            except OSError as exc:
                errors.append(f"{folder}: {exc}")
                _log.error(f"Delete folder failed: {folder} — {exc}")

        for iid in deleted:
            self._tree.delete(iid)

        total = len(self._tree.get_children())
        self._status_var.set(
            f"🗑  Deleted {len(deleted)} folder{'s' if len(deleted) != 1 else ''} from disk."
            + (f"  {len(errors)} error(s)." if errors else "")
            + f"  {total} remaining."
        )

        if errors:
            messagebox.showerror(
                "Delete Folders — errors",
                f"{len(deleted)} deleted, {len(errors)} failed:\n\n"
                + "\n".join(errors[:8]),
                parent=self,
            )
