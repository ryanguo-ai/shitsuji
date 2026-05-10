"""
Folder Scanner UI — browse and list all files in a selected directory.
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

from mutagen.flac import FLAC
from tkinterdnd2 import DND_FILES

from panels.audio_details_panel import AudioDetailsPanel
from panels.audio_menu import AUDIO_EXTENSIONS, AudioMenuMixin
from panels.keyboard_selection import attach_keyboard_range_selection
from panels.settings_panel import load_settings, save_settings, MUSIC_LIB_PARTITIONS
from panels.logger import get_logger


def format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _read_flac_tags(file_path: str) -> tuple[str, str, str, str]:
    """Return (artist, title, album, bitrate) from a FLAC file's tags/info, or ('','','','') on failure."""
    try:
        flac = FLAC(file_path)
        tags = flac.tags or {}
        artist  = tags.get("artist",  [""])[0]
        title   = tags.get("title",   [""])[0]
        album   = tags.get("album",   [""])[0]
        bitrate = f"{round(flac.info.bitrate / 1000)} kbps" if flac.info.bitrate else ""
        return artist, title, album, bitrate
    except Exception:
        return "", "", "", ""


def _read_audio_tags(file_path: str) -> tuple[str, str, str, str]:
    """Return (artist, title, album, bitrate) for any mutagen-supported audio file."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio is None:
            return "", "", "", ""
        tags = audio.tags or {}
        artist  = tags.get("artist", [""])[0] if "artist" in tags else ""
        title   = tags.get("title",  [""])[0] if "title"  in tags else ""
        album   = tags.get("album",  [""])[0] if "album"  in tags else ""
        bitrate = ""
        info = getattr(audio, "info", None)
        if info and getattr(info, "bitrate", 0):
            bitrate = f"{round(info.bitrate / 1000)} kbps"
        return artist, title, album, bitrate
    except Exception:
        return "", "", "", ""


def _check_lib_ready(file_path: str) -> bool:
    """
    Return True if the file meets lib-ready criteria:
      1. Has non-empty ARTIST, TITLE and ALBUM tags
      2. Has at least one embedded image with both dimensions > 320 px
    """
    try:
        from PIL import Image
        import io
        flac = FLAC(file_path)
        tags = flac.tags or {}
        if not (tags.get("artist", [""])[0] and
                tags.get("title",  [""])[0] and
                tags.get("album",  [""])[0]):
            return False
        for pic in flac.pictures:
            img = Image.open(io.BytesIO(pic.data))
            w, h = img.size
            if w > 320 and h > 320:
                return True
        return False
    except Exception:
        return False


# Characters illegal in Windows file names
_ILLEGAL_CHARS = r'\/:*?"<>|'


def _sanitize_filename(name: str) -> str:
    """Replace Windows-illegal characters and strip leading/trailing spaces and dots."""
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "_")
    return name.strip(" .") or "_"


class _NormalizeResultWindow(tk.Toplevel):
    """Modal-ish result window listing renamed files and errors from a normalize run."""

    def __init__(self, parent, renamed: list, errors: list):
        super().__init__(parent)
        self.title("Normalize File Name — Results")
        self.configure(bg="#f5f5f5")
        self.minsize(700, 380)
        self.resizable(True, True)
        self._build(renamed, errors)
        self._center()

    def _build(self, renamed, errors):
        # ── Header ── #
        hdr = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        hdr.pack(fill=tk.X)
        n_ok  = len(renamed)
        n_err = len(errors)
        tk.Label(
            hdr,
            text=(
                f"✅ {n_ok} renamed"
                + (f"   ❌ {n_err} error{'s' if n_err != 1 else ''}" if n_err else "")
            ),
            font=("Segoe UI", 11, "bold"), fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # ── Table ── #
        frame = tk.Frame(self, bg="#f5f5f5")
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        cols = ("status", "original", "result")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tree.heading("status",   text="Status",        anchor=tk.W)
        tree.heading("original", text="Original Path", anchor=tk.W)
        tree.heading("result",   text="New Name / Error", anchor=tk.W)
        tree.column("status",   width=90,  stretch=False)
        tree.column("original", width=310, stretch=True)
        tree.column("result",   width=280, stretch=True)

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL,   command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        tree.pack(fill=tk.BOTH, expand=True)

        tree.tag_configure("ok",  background="#eafaf1", foreground="#1e8449")
        tree.tag_configure("err", background="#fdf2f2", foreground="#922b21")

        for old, new in renamed:
            tree.insert("", "end",
                        values=("✅ Renamed", old, os.path.basename(new)),
                        tags=("ok",))
        for path, msg in errors:
            tree.insert("", "end",
                        values=("❌ Error", path, msg),
                        tags=("err",))

        # ── Close button ── #
        ttk.Button(self, text="Close", command=self.destroy).pack(
            side=tk.BOTTOM, pady=(0, 10))

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  700)
        h = max(self.winfo_reqheight(), 380)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class _DeleteConfirmDialog(tk.Toplevel):
    """Modal confirmation dialog for 'Remove Lib Duplicates'.

    Shows two detailed lists:
      • Files that will be permanently deleted (MD5 confirmed in lib) —
        displays Artist, Title, Album, Type, Bitrate, Size, Scan path, Lib path.
      • Files that will be skipped (MD5 not found / file missing / error) —
        displays Artist, Title, Album, Type, Bitrate, Size, Full Path, Reason.

    Sets ``self.confirmed = True`` when the user clicks the delete button.
    """

    # Column definitions: (id, heading, width, anchor)
    _DEL_COLS = [
        ("artist",   "Artist",         130, tk.W),
        ("title",    "Title",          160, tk.W),
        ("album",    "Album",          120, tk.W),
        ("type",     "Type",            50, tk.W),
        ("bitrate",  "Bitrate",         65, tk.E),
        ("size",     "Size",            70, tk.E),
        ("scan",     "Scan File Path", 300, tk.W),
        ("lib",      "Library Path",   260, tk.W),
    ]
    _SKIP_COLS = [
        ("artist",  "Artist",         130, tk.W),
        ("title",   "Title",          160, tk.W),
        ("album",   "Album",          120, tk.W),
        ("type",    "Type",            50, tk.W),
        ("bitrate", "Bitrate",         65, tk.E),
        ("size",    "Size",            70, tk.E),
        ("path",    "Full Path",      300, tk.W),
        ("reason",  "Reason",         200, tk.W),
    ]

    def __init__(self, parent, to_delete: list, cant_delete: list):
        super().__init__(parent)
        self.confirmed = False
        self._to_delete   = to_delete    # (item_id, full_path, lib_row dict, scan_vals tuple)
        self._cant_delete = cant_delete  # (full_path, reason, scan_vals tuple)

        self.title("Remove Lib Duplicates — Confirm")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.minsize(960, 580)
        self.resizable(True, True)
        self._build()
        self._center()

    def _build(self):
        n_del  = len(self._to_delete)
        n_skip = len(self._cant_delete)

        # Use grid on the Toplevel so row weights control vertical expansion.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=3)   # delete table  → 3/5 of extra space
        self.rowconfigure(5, weight=2)   # skipped table → 2/5 of extra space

        # ── Row 0: Header ── #
        hdr = tk.Frame(self, bg="#c0392b", pady=8, padx=12)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hdr, text="🗑  Remove Lib Duplicates",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#c0392b",
        ).pack(side=tk.LEFT)

        # ── Row 1: Summary banner ── #
        summ = tk.Frame(self, bg="#fdf2f2", padx=12, pady=6)
        summ.grid(row=1, column=0, sticky="ew")
        tk.Label(
            summ,
            text=(
                f"{n_del} file{'s' if n_del != 1 else ''} will be PERMANENTLY DELETED"
                f"   ·   {n_skip} file{'s' if n_skip != 1 else ''} skipped"
                f" (not confirmed in lib)"
            ),
            font=("Segoe UI", 9, "bold"), fg="#c0392b", bg="#fdf2f2",
        ).pack(anchor="w")

        # ── Row 2: Delete section label ── #
        tk.Label(
            self,
            text=f"  Files to DELETE ({n_del})  — MD5 confirmed in library",
            font=("Segoe UI", 9, "bold"), fg="#c0392b", bg="#f5f5f5",
            anchor="w", pady=4,
        ).grid(row=2, column=0, sticky="ew")

        # ── Row 3: Delete table (expands) ── #
        del_frame = tk.Frame(self, bg="#f5f5f5")
        del_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 4))
        del_frame.columnconfigure(0, weight=1)
        del_frame.rowconfigure(0, weight=1)

        col_ids = [c[0] for c in self._DEL_COLS]
        del_tree = ttk.Treeview(del_frame, columns=col_ids, show="headings",
                                selectmode="none")
        for cid, heading, width, anchor in self._DEL_COLS:
            del_tree.heading(cid, text=heading, anchor=anchor)
            del_tree.column(cid, width=width, anchor=anchor,
                            stretch=(cid in ("scan", "lib")))
        del_tree.tag_configure("del", background="#fdf2f2")

        vsb1 = ttk.Scrollbar(del_frame, orient=tk.VERTICAL,   command=del_tree.yview)
        hsb1 = ttk.Scrollbar(del_frame, orient=tk.HORIZONTAL, command=del_tree.xview)
        del_tree.configure(yscrollcommand=vsb1.set, xscrollcommand=hsb1.set)
        del_tree.grid(row=0, column=0, sticky="nsew")
        vsb1.grid(row=0, column=1, sticky="ns")
        hsb1.grid(row=1, column=0, sticky="ew")

        for _, full_path, lib_row, sv in self._to_delete:
            lib_path = f"{lib_row['partition']} / {lib_row['rel_path']}"
            del_tree.insert("", "end", tags=("del",), values=(
                sv[4], sv[5], sv[6], sv[3], sv[7], sv[8],
                full_path, lib_path,
            ))

        # ── Row 4: Skipped section label ── #
        tk.Label(
            self,
            text=f"  Files SKIPPED ({n_skip})  — not confirmed in library",
            font=("Segoe UI", 9, "bold"), fg="#7f8c8d", bg="#f5f5f5",
            anchor="w", pady=4,
        ).grid(row=4, column=0, sticky="ew")

        # ── Row 5: Skipped table (expands) ── #
        skip_frame = tk.Frame(self, bg="#f5f5f5")
        skip_frame.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 4))
        skip_frame.columnconfigure(0, weight=1)
        skip_frame.rowconfigure(0, weight=1)

        skip_col_ids = [c[0] for c in self._SKIP_COLS]
        skip_tree = ttk.Treeview(skip_frame, columns=skip_col_ids, show="headings",
                                 selectmode="none")
        for cid, heading, width, anchor in self._SKIP_COLS:
            skip_tree.heading(cid, text=heading, anchor=anchor)
            skip_tree.column(cid, width=width, anchor=anchor,
                             stretch=(cid in ("path", "reason")))

        vsb2 = ttk.Scrollbar(skip_frame, orient=tk.VERTICAL,   command=skip_tree.yview)
        hsb2 = ttk.Scrollbar(skip_frame, orient=tk.HORIZONTAL, command=skip_tree.xview)
        skip_tree.configure(yscrollcommand=vsb2.set, xscrollcommand=hsb2.set)
        skip_tree.grid(row=0, column=0, sticky="nsew")
        vsb2.grid(row=0, column=1, sticky="ns")
        hsb2.grid(row=1, column=0, sticky="ew")

        for full_path, reason, sv in self._cant_delete:
            skip_tree.insert("", "end", values=(
                sv[4], sv[5], sv[6], sv[3], sv[7], sv[8],
                full_path, reason,
            ))

        # ── Row 6: Buttons ── #
        btn_frame = tk.Frame(self, bg="#f5f5f5", pady=8, padx=12)
        btn_frame.grid(row=6, column=0, sticky="ew")
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(
            side=tk.RIGHT, padx=(4, 0))
        del_label = (
            f"🗑  Delete {n_del} File{'s' if n_del != 1 else ''}"
            if n_del else "Nothing to Delete"
        )
        ttk.Button(
            btn_frame, text=del_label,
            command=self._confirm,
            state="normal" if n_del else "disabled",
        ).pack(side=tk.RIGHT)

    def _confirm(self):
        self.confirmed = True
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  720)
        h = max(self.winfo_reqheight(), 520)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class _DeleteFilesDialog(tk.Toplevel):
    """Modal confirmation dialog before permanently deleting files from disk.

    Displays Artist / Title / Album / Type / Size / Full Path for every
    selected file so the user can review before committing.

    Sets ``self.confirmed = True`` when the user clicks the delete button.
    """

    _COLS = [
        ("artist",  "Artist",    130, tk.W),
        ("title",   "Title",     160, tk.W),
        ("album",   "Album",     120, tk.W),
        ("type",    "Type",       50, tk.CENTER),
        ("size",    "Size",       70, tk.E),
        ("path",    "Full Path", 340, tk.W),
    ]

    def __init__(self, parent, rows: list[tuple]):
        """
        Parameters
        ----------
        rows : list of tuples
            Each tuple is the full ``values`` tuple from the scan Treeview row:
            (lib_ready, in_lib, full_path, file_type, artist, title, album,
             bitrate, size, modified)
        """
        super().__init__(parent)
        self.confirmed = False
        self._rows = rows

        self.title("Delete Files — Confirm")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.minsize(860, 420)
        self.resizable(True, True)
        self._build()
        self._center()

    # ------------------------------------------------------------------ #

    def _build(self):
        n = len(self._rows)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)   # table gets all extra vertical space

        # ── Header ── #
        hdr = tk.Frame(self, bg="#c0392b", pady=8, padx=12)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hdr, text="🗑  Delete Files",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#c0392b",
        ).pack(side=tk.LEFT)

        # ── Warning banner ── #
        warn = tk.Frame(self, bg="#fdf2f2", padx=12, pady=8)
        warn.grid(row=1, column=0, sticky="ew")
        tk.Label(
            warn,
            text=(
                f"⚠  {n} file{'s' if n != 1 else ''} will be PERMANENTLY deleted "
                "from disk.  This cannot be undone."
            ),
            font=("Segoe UI", 9, "bold"), fg="#c0392b", bg="#fdf2f2",
        ).pack(anchor="w")

        # ── File table ── #
        tbl_frame = tk.Frame(self, bg="#f5f5f5")
        tbl_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
        tbl_frame.columnconfigure(0, weight=1)
        tbl_frame.rowconfigure(0, weight=1)

        col_ids = [c[0] for c in self._COLS]
        tree = ttk.Treeview(tbl_frame, columns=col_ids, show="headings",
                            selectmode="none")
        for cid, heading, width, anchor in self._COLS:
            tree.heading(cid, text=heading, anchor=anchor)
            tree.column(cid, width=width, anchor=anchor, stretch=(cid == "path"))
        tree.tag_configure("row", background="#fdf2f2")

        vsb = ttk.Scrollbar(tbl_frame, orient=tk.VERTICAL,   command=tree.yview)
        hsb = ttk.Scrollbar(tbl_frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        for sv in self._rows:
            # sv = (lib_ready, in_lib, full_path, file_type,
            #        artist, title, album, bitrate, size, modified)
            tree.insert("", "end", tags=("row",),
                        values=(sv[4], sv[5], sv[6], sv[3], sv[8], sv[2]))

        # ── Buttons ── #
        btn_frame = tk.Frame(self, bg="#f5f5f5", pady=8, padx=12)
        btn_frame.grid(row=3, column=0, sticky="ew")

        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(
            side=tk.RIGHT, padx=(4, 0))
        ttk.Button(
            btn_frame,
            text=f"🗑  Delete {n} File{'s' if n != 1 else ''}",
            command=self._confirm,
        ).pack(side=tk.RIGHT)

    def _confirm(self):
        self.confirmed = True
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  860)
        h = max(self.winfo_reqheight(), 420)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class ScanTab(tk.Frame, AudioMenuMixin):

    def __init__(self, master, on_compare=None, on_search_artist=None):
        super().__init__(master, bg="#f5f5f5")
        self._settings = load_settings()
        self._on_compare = on_compare           # callable(src_path, lib_path) or None
        self._on_search_artist = on_search_artist  # callable(artist_name) or None
        self._sort_col: str | None = None   # currently sorted column id
        self._sort_rev: bool = False        # True → descending
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────── #
        top = tk.Frame(self, bg="#2c3e50", pady=12, padx=16)
        top.pack(fill=tk.X)

        tk.Label(
            top, text="📁  Folder Scanner",
            font=("Segoe UI", 16, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # ── Path entry row────────────────────────────────────────────── #
        row = tk.Frame(self, bg="#f5f5f5", pady=10, padx=16)
        row.pack(fill=tk.X)

        ttk.Button(row, text="Check Tracks",         command=self._check_tracks).pack(side=tk.LEFT)
        ttk.Button(row, text="Normalize File Name",   command=self._normalize_filenames).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row, text="Remove Lib Duplicates", command=self._remove_lib_duplicates).pack(side=tk.LEFT, padx=(8, 0))

        # ── Options row ───────────────────────────────────────────────── #
        opts = tk.Frame(self, bg="#f5f5f5", padx=16)
        opts.pack(fill=tk.X)

        self.recursive_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opts, text="Include subfolders (recursive)",
            variable=self.recursive_var,
            font=("Segoe UI", 9), bg="#f5f5f5",
        ).pack(side=tk.LEFT)

        self.show_hidden_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opts, text="Show hidden files",
            variable=self.show_hidden_var,
            font=("Segoe UI", 9), bg="#f5f5f5",
        ).pack(side=tk.LEFT, padx=(16, 0))

        # ── Status label ─────────────────────────────────────────────── #
        self.status_var = tk.StringVar(value="No folder selected.")
        tk.Label(
            self, textvariable=self.status_var,
            font=("Segoe UI", 9, "italic"),
            fg="#7f8c8d", bg="#f5f5f5", anchor="w", padx=16,
        ).pack(fill=tk.X)

        # ── Main content: file table + detail panel ───────────────────── #
        self._paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, bg="#d0d3d4",
            sashrelief=tk.FLAT, sashwidth=5,
        )
        self._paned.pack(fill=tk.BOTH, expand=True, padx=16, pady=6)

        # ── Left: file table ─────────────────────────────────────────── #
        tree_frame = tk.Frame(self._paned, bg="#f5f5f5")
        self._paned.add(tree_frame, stretch="always", minsize=400)

        columns = ("flibready", "finlib", "fpath", "ftype", "fartist", "ftitle", "falbum", "fbitrate", "fsize", "fmodified")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            selectmode="extended",
        )

        _cmd = lambda c: (lambda: self._sort_column(c))
        self.tree.heading("flibready",  text="Lib Ready",  anchor=tk.CENTER, command=_cmd("flibready"))
        self.tree.heading("finlib",     text="In Lib",     anchor=tk.CENTER, command=_cmd("finlib"))
        self.tree.heading("fpath",      text="Full Path",  anchor=tk.W,      command=_cmd("fpath"))
        self.tree.heading("ftype",      text="Type",       anchor=tk.W,      command=_cmd("ftype"))
        self.tree.heading("fartist",    text="Artist",     anchor=tk.W,      command=_cmd("fartist"))
        self.tree.heading("ftitle",     text="Title",      anchor=tk.W,      command=_cmd("ftitle"))
        self.tree.heading("falbum",     text="Album",      anchor=tk.W,      command=_cmd("falbum"))
        self.tree.heading("fbitrate",   text="Bitrate",    anchor=tk.E,      command=_cmd("fbitrate"))
        self.tree.heading("fsize",      text="Size",       anchor=tk.E,      command=_cmd("fsize"))
        self.tree.heading("fmodified",  text="Modified",   anchor=tk.W,      command=_cmd("fmodified"))

        self.tree.column("flibready",  width=70,  anchor=tk.CENTER, stretch=False)
        self.tree.column("finlib",     width=55,  anchor=tk.CENTER, stretch=False)
        self.tree.column("fpath",      width=270, stretch=True)
        self.tree.column("ftype",      width=55,  stretch=False)
        self.tree.column("fartist",    width=140, stretch=False)
        self.tree.column("ftitle",     width=170, stretch=False)
        self.tree.column("falbum",     width=155, stretch=False)
        self.tree.column("fbitrate",   width=75,  anchor=tk.E, stretch=False)
        self.tree.column("fsize",      width=65,  anchor=tk.E, stretch=False)
        self.tree.column("fmodified",  width=125, stretch=False)

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.tag_configure("odd",         background="#ffffff")
        self.tree.tag_configure("even",        background="#ecf0f1")
        self.tree.tag_configure("inlib_exact", background="#eafaf1", foreground="#1e8449")  # green  — MD5 match
        self.tree.tag_configure("inlib_diff",  background="#fefce8", foreground="#92400e")  # amber  — metadata match, different file
        self.tree.tag_configure("notlib",      background="#fdf2f8", foreground="#922b21")
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Button-3>", self._on_row_right_click)
        self.tree.bind("<Delete>", self._on_delete_key)
        self.tree.bind("<Control-a>", lambda _: self.tree.selection_set(self.tree.get_children()))
        self._kb_sel = attach_keyboard_range_selection(self.tree)

        self.tree.drop_target_register(DND_FILES)
        self.tree.dnd_bind("<<Drop>>", self._on_drop)

        # ── Right: detail panel ───────────────────────────────────────── #
        self._detail_panel = AudioDetailsPanel(self._paned)
        self._paned.add(self._detail_panel, stretch="never", minsize=240)

        self._paned.bind("<ButtonRelease-1>", self._on_sash_release)
        self.after(150, self._restore_sash)

        # ── Bottom status bar ─────────────────────────────────────────── #
        bar = tk.Frame(self, bg="#bdc3c7", height=24)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.footer_var = tk.StringVar(value="Ready.")
        tk.Label(
            bar, textvariable=self.footer_var,
            font=("Segoe UI", 9), bg="#bdc3c7",
            anchor="w", padx=8,
        ).pack(fill=tk.X)

    # ------------------------------------------------------------------ #
    # Column sorting                                                       #
    # ------------------------------------------------------------------ #

    # Map column id → index in the values tuple
    _COL_IDX = {
        "flibready": 0, "finlib": 1, "fpath": 2, "ftype": 3,
        "fartist": 4, "ftitle": 5, "falbum": 6,
        "fbitrate": 7, "fsize": 8, "fmodified": 9,
    }

    _COL_LABELS = {
        "flibready": "Lib Ready", "finlib": "In Lib", "fpath": "Full Path",
        "ftype": "Type", "fartist": "Artist", "ftitle": "Title",
        "falbum": "Album", "fbitrate": "Bitrate", "fsize": "Size",
        "fmodified": "Modified",
    }

    @staticmethod
    def _parse_size(val: str) -> float:
        """Convert a human-readable size string (e.g. '1.5 MB') to bytes."""
        units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}
        parts = val.strip().split()
        if len(parts) == 2:
            try:
                return float(parts[0]) * units.get(parts[1], 1)
            except ValueError:
                pass
        return 0.0

    @staticmethod
    def _parse_bitrate(val: str) -> float:
        """Convert a bitrate string (e.g. '1000 kbps') to a float."""
        parts = val.strip().split()
        if parts:
            try:
                return float(parts[0])
            except ValueError:
                pass
        return 0.0

    def _sort_column(self, col: str):
        """Sort treeview rows by *col*, toggling direction on repeated clicks."""
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False

        # Build sort key depending on column type
        if col == "fsize":
            key_fn = lambda iid: self._parse_size(self.tree.set(iid, col))
        elif col == "fbitrate":
            key_fn = lambda iid: self._parse_bitrate(self.tree.set(iid, col))
        else:
            key_fn = lambda iid: self.tree.set(iid, col).lower()

        items = sorted(self.tree.get_children(), key=key_fn, reverse=self._sort_rev)

        for i, iid in enumerate(items):
            self.tree.move(iid, "", i)

        # Re-stripe rows while preserving special in-lib highlight tags
        _special = {"inlib_exact", "inlib_diff", "notlib"}
        for i, iid in enumerate(self.tree.get_children()):
            current_tags = self.tree.item(iid, "tags")
            if current_tags and current_tags[0] in _special:
                continue  # leave colour-coded rows as-is
            self.tree.item(iid, tags=("odd" if i % 2 == 0 else "even",))

        # Update heading labels to show sort indicator; clear all others
        arrow = " ▲" if not self._sort_rev else " ▼"
        for c, label in self._COL_LABELS.items():
            self.tree.heading(c, text=label + (arrow if c == col else ""))

    # ------------------------------------------------------------------ #
    # Layout persistence                                                   #
    # ------------------------------------------------------------------ #

    def _on_sash_release(self, _event):
        try:
            x, _ = self._paned.sash_coord(0)
            self._settings["scan_sash"] = x
            save_settings(self._settings)
        except Exception:
            pass

    def _restore_sash(self):
        x = self._settings.get("scan_sash")
        if x is not None:
            try:
                self._paned.sash_place(0, int(x), 0)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _scan(self):
        folder = filedialog.askdirectory(title="Select a folder to scan")
        if not folder:
            return
        if not os.path.isdir(folder):
            messagebox.showerror("Invalid folder", f"'{folder}' is not a valid directory.")
            return

        self.tree.delete(*self.tree.get_children())
        self.status_var.set("Scanning…")
        self.update_idletasks()

        recursive = self.recursive_var.get()
        show_hidden = self.show_hidden_var.get()

        try:
            self._populate_list(folder, show_hidden, recursive)
        except PermissionError as exc:
            messagebox.showerror("Permission denied", str(exc))

        total = len(self.tree.get_children())
        self.status_var.set(f"Found {total} file{'s' if total != 1 else ''}  in  {folder}")
        self.footer_var.set(f"Scan complete — {total} file{'s' if total != 1 else ''} found.")

    def _check_tracks(self):
        """Refresh Lib Ready and In Lib columns for every row in the table."""
        from panels.database import get_track_info, compute_file_md5

        items = self.tree.get_children()
        if not items:
            self.status_var.set("No tracks to check.")
            return

        self.status_var.set("Loading library index…")
        self.update_idletasks()

        # Build two lookup structures from DB (single query):
        #   md5_set  — set of md5 digests present in lib (exact-match detection)
        #   aat_set  — set of (artist, title, album) tuples (metadata-match detection)
        md5_set: set[str]              = set()
        aat_set: set[tuple[str,str,str]] = set()
        for row in get_track_info():
            if row["file_md5"]:
                md5_set.add(row["file_md5"].strip().lower())
            aat_set.add((
                (row["artist"] or "").strip().lower(),
                (row["title"]  or "").strip().lower(),
                (row["album"]  or "").strip().lower(),
            ))

        found = ready = 0
        total = len(items)
        for i, item in enumerate(items):
            vals      = list(self.tree.item(item, "values"))
            full_path = vals[2]

            # ── Refresh tags from file ── #
            ext = os.path.splitext(full_path)[1].lstrip(".").upper()
            if ext in AUDIO_EXTENSIONS:
                a, t, al, br = _read_audio_tags(full_path)
                vals[4], vals[5], vals[6], vals[7] = a, t, al, br

            artist = (vals[4] or "").strip().lower()
            title  = (vals[5] or "").strip().lower()
            album  = (vals[6] or "").strip().lower()

            # ── Lib Ready ── #
            is_ready = _check_lib_ready(full_path)
            vals[0]  = "✅" if is_ready else "❌"
            if is_ready:
                ready += 1

            # ── In Lib — MD5-based matching ── #
            try:
                file_md5 = compute_file_md5(full_path).lower()
            except Exception:
                file_md5 = ""

            if file_md5 and file_md5 in md5_set:
                # Exact duplicate — same bytes already in lib
                vals[1]  = "🟢"
                row_tag  = "inlib_exact"
                found   += 1
            elif bool(artist and title) and (artist, title, album) in aat_set:
                # Metadata match but different file content (e.g. re-encode/remaster)
                vals[1] = "🟡"
                row_tag = "inlib_diff"
                found  += 1
            else:
                # Not in lib at all
                vals[1] = "⬛"
                row_tag = "odd" if i % 2 == 0 else "even"

            self.tree.item(item, values=vals, tags=(row_tag,))

            if (i + 1) % 10 == 0:
                self.status_var.set(f"Checking… {i + 1}/{total}")
                self.update_idletasks()

        self.status_var.set(
            f"Check complete — {ready}/{total} lib-ready · {found}/{total} in lib."
        )

    # ------------------------------------------------------------------ #
    # Remove Lib Duplicates                                                #
    # ------------------------------------------------------------------ #

    def _remove_lib_duplicates(self):
        """Verify scan files against library MD5s and delete confirmed duplicates.

        Processes selected rows if any are selected, otherwise all rows.
        Re-computes MD5 for each file and compares against the DB; only files
        with an exact MD5 match qualify for deletion.  Shows a confirmation
        dialog before touching anything on disk.
        """
        from panels.database import get_track_info, compute_file_md5

        selected = self.tree.selection()
        items    = selected if selected else self.tree.get_children()
        if not items:
            self.status_var.set("No tracks in list.")
            return

        self.status_var.set("Loading library MD5 index…")
        self.update_idletasks()

        # Build md5 → lib row lookup (single DB query)
        md5_to_lib: dict[str, object] = {}
        for row in get_track_info():
            if row["file_md5"]:
                md5_to_lib[row["file_md5"].strip().lower()] = row

        # Classify each scan row
        # to_delete  : (item_id, full_path, lib_row dict, scan_vals tuple)
        # cant_delete: (full_path, reason, scan_vals tuple)
        to_delete:   list[tuple] = []
        cant_delete: list[tuple] = []

        total = len(items)
        for i, item in enumerate(items):
            vals      = self.tree.item(item, "values")
            full_path = vals[2]

            if not os.path.isfile(full_path):
                cant_delete.append((full_path, "File not found on disk", vals))
                continue

            try:
                md5 = compute_file_md5(full_path).lower()
            except Exception as exc:
                cant_delete.append((full_path, f"MD5 error: {exc}", vals))
                continue

            if md5 in md5_to_lib:
                lib_row = md5_to_lib[md5]
                to_delete.append((item, full_path, lib_row, vals))
            else:
                cant_delete.append((full_path, "MD5 not found in library", vals))

            if (i + 1) % 5 == 0:
                self.status_var.set(f"Verifying MD5… {i + 1}/{total}")
                self.update_idletasks()

        self.status_var.set("Verification complete — review and confirm below.")

        if not to_delete and not cant_delete:
            messagebox.showinfo("Remove Lib Duplicates", "No items to process.")
            return

        # ── Show confirmation dialog (modal) ── #
        dlg = _DeleteConfirmDialog(self.winfo_toplevel(), to_delete, cant_delete)
        self.wait_window(dlg)

        if not dlg.confirmed:
            self.status_var.set("Deletion cancelled.")
            return

        # ── Execute deletions ── #
        deleted_items: list = []
        delete_errors: list[tuple[str, str]] = []

        for item, full_path, _lib_row, _sv in to_delete:
            try:
                os.remove(full_path)
                deleted_items.append(item)
            except OSError as exc:
                delete_errors.append((full_path, str(exc)))

        # Remove deleted rows from tree and re-stripe
        for item in deleted_items:
            self.tree.delete(item)

        _special = {"inlib_exact", "inlib_diff", "notlib"}
        for i, iid in enumerate(self.tree.get_children()):
            current_tags = self.tree.item(iid, "tags")
            if not (current_tags and current_tags[0] in _special):
                self.tree.item(iid, tags=("odd" if i % 2 == 0 else "even",))

        n_del = len(deleted_items)
        n_err = len(delete_errors)

        if delete_errors:
            messagebox.showerror(
                "Deletion Errors",
                f"{n_err} file(s) could not be deleted:\n\n"
                + "\n".join(f"• {p}\n  {e}" for p, e in delete_errors[:8]),
            )

        remaining = len(self.tree.get_children())
        self.status_var.set(
            f"Deleted {n_del} file{'s' if n_del != 1 else ''}"
            + (f", {n_err} deletion error{'s' if n_err != 1 else ''}" if n_err else "")
            + f" — {remaining} remaining."
        )
        self.footer_var.set(f"{remaining} file{'s' if remaining != 1 else ''} in list.")

    # ------------------------------------------------------------------ #
    # Normalize file names                                                 #
    # ------------------------------------------------------------------ #

    def _normalize_filenames(self):
        """Rename each listed file to '{Artist} - {Title}{ext}'.

        Processes selected rows when a selection exists, otherwise all rows.
        Opens a result window summarising successes and failures.
        """
        selected = self.tree.selection()
        items    = selected if selected else self.tree.get_children()
        if not items:
            self.status_var.set("No tracks to rename.")
            return

        renamed: list[tuple[str, str]] = []   # (old_path, new_path)
        errors:  list[tuple[str, str]] = []   # (path, reason)

        for item in items:
            vals      = list(self.tree.item(item, "values"))
            full_path = vals[2]
            artist    = (vals[4] or "").strip()
            title     = (vals[5] or "").strip()
            ext       = os.path.splitext(full_path)[1]   # includes "."

            if not artist or not title:
                errors.append((full_path,
                               "Missing artist or title — run Check Tracks first."))
                continue

            new_stem = _sanitize_filename(f"{artist} - {title}")
            new_name = new_stem + ext
            new_path = os.path.join(os.path.dirname(full_path), new_name)

            if os.path.normcase(new_path) == os.path.normcase(full_path):
                continue   # already correctly named — silently skip

            if os.path.exists(new_path):
                errors.append((full_path,
                               f"Target already exists: {new_name}"))
                continue

            try:
                os.rename(full_path, new_path)
                vals[2] = new_path
                self.tree.item(item, values=vals)
                renamed.append((full_path, new_path))
            except OSError as exc:
                errors.append((full_path, str(exc)))

        n_ok  = len(renamed)
        n_err = len(errors)

        if renamed or errors:
            _NormalizeResultWindow(self.winfo_toplevel(), renamed, errors)

        self.status_var.set(
            f"Rename complete — {n_ok} renamed"
            + (f", {n_err} error{'s' if n_err != 1 else ''}" if n_err else "")
            + ("." if n_ok or n_err else " (nothing to do).")
        )

    # ------------------------------------------------------------------ #
    # List population                                                      #
    # ------------------------------------------------------------------ #

    def _populate_list(self, folder, show_hidden, recursive):
        """Walk the folder and insert every file as a flat row."""
        walker = os.walk(folder) if recursive else self._single_level(folder)

        for dirpath, dirnames, filenames in walker:
            if not show_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                filenames = [f for f in filenames if not f.startswith(".")]

            for name in sorted(filenames, key=str.lower):
                self._append_file(os.path.join(dirpath, name))

    def _append_file(self, full_path: str):
        """Insert a single file row at the end of the tree."""
        try:
            stat = os.stat(full_path)
            size     = format_size(stat.st_size)
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size = modified = "—"

        ext       = os.path.splitext(full_path)[1].lstrip(".").upper()
        file_type = ext if ext else "File"
        artist, title, album, bitrate = _read_flac_tags(full_path) if ext == "FLAC" else ("", "", "", "")

        tag = "odd" if len(self.tree.get_children()) % 2 == 0 else "even"
        self.tree.insert(
            "", "end",
            values=("", "", full_path, file_type, artist, title, album, bitrate, size, modified),
            tags=(tag,),
        )

    @staticmethod
    def _single_level(folder):
        """Yield a single os.walk-compatible tuple for the top-level only."""
        try:
            entries = list(os.scandir(folder))
        except PermissionError:
            return
        dirnames = [e.name for e in entries if e.is_dir(follow_symlinks=False)]
        filenames = [e.name for e in entries if e.is_file(follow_symlinks=False)]
        yield folder, dirnames, filenames

    def _on_delete_key(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        for item in selected:
            self.tree.delete(item)
        # Re-stripe remaining rows
        for i, item in enumerate(self.tree.get_children()):
            self.tree.item(item, tags=("odd" if i % 2 else "even",))
        total = len(self.tree.get_children())
        self.footer_var.set(f"{total} file{'s' if total != 1 else ''} in list.")
        self.status_var.set(
            f"Removed {len(selected)} row{'s' if len(selected) != 1 else ''}. "
            f"{total} remaining."
        )

    def _delete_selected_files(self):
        """Confirm then permanently delete the selected files from disk."""
        selected = self.tree.selection()
        if not selected:
            return

        rows = [self.tree.item(iid, "values") for iid in selected]
        dlg = _DeleteFilesDialog(self, rows)
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        log = get_logger("scan_delete")
        deleted_iids, errors = [], []
        for iid, sv in zip(selected, rows):
            path = sv[2]
            try:
                os.remove(path)
                deleted_iids.append(iid)
                log.info(f"Deleted: {path}")
            except OSError as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
                log.error(f"Delete failed: {path} — {exc}")

        # Remove successfully deleted rows from the tree
        for iid in deleted_iids:
            self.tree.delete(iid)

        # Re-stripe
        for i, iid in enumerate(self.tree.get_children()):
            self.tree.item(iid, tags=("odd" if i % 2 else "even",))

        total = len(self.tree.get_children())
        self.footer_var.set(f"{total} file{'s' if total != 1 else ''} in list.")
        n_ok = len(deleted_iids)
        self.status_var.set(
            f"🗑  Deleted {n_ok} file{'s' if n_ok != 1 else ''} from disk."
            + (f"  {len(errors)} error(s)." if errors else "")
        )

        if errors:
            messagebox.showerror(
                "Delete — errors",
                f"{n_ok} deleted, {len(errors)} failed:\n\n" + "\n".join(errors[:10]),
            )

    def _on_drop(self, event):
        log = get_logger("scan_drop")
        paths = self.tk.splitlist(event.data)
        added = 0
        for path in paths:
            path = path.strip()
            if os.path.isfile(path):
                self._append_file(path)
                added += 1
            elif os.path.isdir(path):
                for name in sorted(os.listdir(path), key=str.lower):
                    full = os.path.join(path, name)
                    if os.path.isfile(full):
                        self._append_file(full)
                        added += 1

        total = len(self.tree.get_children())
        log.info(f"Dropped {added} file(s), {total} total in list")
        self.status_var.set(
            f"Added {added} file{'s' if added != 1 else ''} — {total} total."
        )
        self.footer_var.set(f"{total} file{'s' if total != 1 else ''} in list.")

    def _on_row_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return

        # Keep existing multi-selection if right-click lands on a selected row;
        # otherwise select only the clicked row.
        if item not in self.tree.selection():
            self.tree.selection_set(item)

        selected = self.tree.selection()
        paths = [self.tree.item(i, "values")[2] for i in selected]

        def extra(menu, paths, audio_paths, flac_paths):
            # ── Search Artist in Artist Info ── #
            if len(selected) == 1 and self._on_search_artist is not None:
                artist = (self.tree.item(item, "values")[4] or "").strip()
                if artist:
                    menu.add_command(
                        label=f"👤  Search Artist: {artist}",
                        command=lambda a=artist: self._on_search_artist(a),
                    )
                    menu.add_separator()

            # ── Compare track with Lib (only for single 🟡 rows) ── #
            if len(selected) == 1 and self._on_compare is not None:
                clicked_vals = self.tree.item(item, "values")
                if clicked_vals[1] == "🟡":
                    menu.add_command(
                        label="🔍  Compare track with Lib",
                        command=lambda: self._open_compare(item),
                    )
                    menu.add_separator()

            # ── Send to Lib submenu ── #
            lib_menu = tk.Menu(menu, tearoff=0)
            for partition in MUSIC_LIB_PARTITIONS:
                lib_menu.add_command(
                    label=partition,
                    command=lambda p=partition: self._open_send_to_lib(paths, p),
                )
            menu.add_cascade(label="📂  Send to Lib ▶", menu=lib_menu)
            menu.add_separator()

            # ── Delete files from disk ── #
            n = len(selected)
            menu.add_command(
                label=f"🗑  Delete {n} File{'s' if n != 1 else ''} from Disk",
                command=self._delete_selected_files,
            )
            menu.add_separator()

        menu = self._build_audio_context_menu(paths, extra_items_fn=extra)
        menu.tk_popup(event.x_root, event.y_root)

    def _open_compare(self, item):
        """Find the matching lib track and invoke the on_compare callback."""
        from panels.database import find_track_by_metadata
        vals = self.tree.item(item, "values")
        src_path = vals[2]
        artist   = (vals[4] or "").strip()
        title    = (vals[5] or "").strip()
        album    = (vals[6] or "").strip()

        matches = find_track_by_metadata(artist, title, album)
        if not matches:
            messagebox.showinfo(
                "No lib track found",
                "Could not find a matching track record in the library database.")
            return

        # Use the most-recently-updated match
        row = matches[0]
        partition = row["partition"]
        rel_path  = row["rel_path"]
        lib_root  = self._settings.get("music_lib_paths", {}).get(partition, "")
        lib_path  = os.path.join(lib_root, partition, rel_path) if lib_root else ""

        if not lib_path or not os.path.isfile(lib_path):
            messagebox.showwarning(
                "Lib file not found",
                f"DB record found but file is missing:\n{lib_path or '(path unknown)'}")
            return

        self._on_compare(src_path, lib_path, partition, rel_path)

    def _open_send_to_lib(self, paths: list[str], partition: str):
        from panels.send_to_lib_panel import SendToLibPanel
        lib_root = self._settings.get("music_lib_paths", {}).get(partition, "")
        SendToLibPanel(
            self.winfo_toplevel(),
            paths,
            partition,
            lib_root,
            on_confirm=self._send_to_lib,
        )

    def _send_to_lib(self, paths: list[str], partition: str):
        from panels.send_to_lib_panel import compute_dest_full_path, compute_dest_rel_path
        from panels.lib_ops import copy_track_to_lib

        log = get_logger("send_to_lib")
        lib_root = self._settings.get("music_lib_paths", {}).get(partition, "")
        errors: list[str] = []
        copied = 0

        for abs_path in paths:
            ext = os.path.splitext(abs_path)[1]
            # Derive destination paths before copy (tags read inside copy_track_to_lib)
            try:
                from mutagen.flac import FLAC as _FLAC
                f = _FLAC(abs_path)
                artist  = (f.get("artist")  or f.get("ARTIST")  or [""])[0]
                title   = (f.get("title")   or f.get("TITLE")   or [""])[0]
                album   = (f.get("album")   or f.get("ALBUM")   or [""])[0]
            except Exception:
                artist = title = album = ""

            dest_full = compute_dest_full_path(lib_root, partition, artist, album, title, ext)
            rel_path  = compute_dest_rel_path(artist, album, title, ext)

            try:
                copy_track_to_lib(abs_path, dest_full, partition, rel_path)
                copied += 1
            except Exception as exc:
                log.error(f"Send to lib failed: {abs_path} — {exc}")
                errors.append(f"{os.path.basename(abs_path)}: {exc}")

        if errors:
            log.warning(f"Send to lib finished with {len(errors)} error(s): partition={partition}")
            messagebox.showerror(
                "Send to Lib — errors",
                f"{copied} copied, {len(errors)} failed:\n\n" + "\n".join(errors[:8]),
            )
        else:
            log.info(f"Send to lib complete: {copied} file(s) → {partition}")
            self.status_var.set(
                f"✔  Sent {copied} file{'s' if copied != 1 else ''} → {partition}"
            )

    def _on_row_select(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        if not values:
            return
        full_path, file_type = values[2], values[3]
        if file_type == "FLAC":
            self._detail_panel.show_flac(full_path)
        else:
            self._detail_panel.clear()
