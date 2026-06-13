"""
Search In Lib tab — fuzzy search across the music library inventory.
"""

import difflib
import os
import threading
import tkinter as tk
from tkinter import ttk

from mutagen.flac import FLAC

from music.audio_details_panel import AudioDetailsPanel
from music.audio_menu import AudioMenuMixin
from music.database import (
    compute_file_md5, delete_track, delete_track_info,
    find_artist_by_name_or_alias,
    get_artist_name_variants, get_track_info, upsert_track_info, set_track_ranking,
    update_track_info_quality,
)
from common.keyboard_selection import attach_keyboard_range_selection
from common.logger import get_logger
from music.settings_panel import load_settings, save_settings

PAGE_SIZE = 100

_log = get_logger("search")

# (display label, minimum ranking value)
_RANK_FILTER_OPTS = [
    ("Any Rating", 0),
    ("★  1+",      1),
    ("★★  2+",     2),
    ("★★★  3+",    3),
    ("★★★★  4+",   4),
    ("❤️  5",      5),
]
_RANK_FILTER_LABELS = [o[0] for o in _RANK_FILTER_OPTS]
_RANK_FILTER_MAP    = {o[0]: o[1] for o in _RANK_FILTER_OPTS}

# Sentinel label for the partition filter meaning "do not filter by partition".
_ALL_PARTITIONS_LABEL = "All Partitions"


def _fuzzy_match(query: str, target: str, threshold: float = 0.5) -> bool:
    """Return True if *query* fuzzy-matches *target* (empty query matches everything)."""
    if not query:
        return True
    q = query.strip().lower()
    t = target.strip().lower()
    if q in t:          # substring always counts as a match
        return True
    return difflib.SequenceMatcher(None, q, t).ratio() >= threshold


def _rank_emoji(r: int) -> str:
    """Return a display string for a 0-5 ranking value."""
    if r == 5:
        return "❤️"
    return "★" * r if r > 0 else ""


class _DeleteLibTracksDialog(tk.Toplevel):
    """Confirmation dialog before permanently deleting tracks from the library.

    Shows a table of tracks that will be deleted (file + DB record).
    Sets ``self.confirmed = True`` and ``self.remove_empty_folders`` when the
    user clicks the Delete button.
    """

    _COLS = [
        ("artist",  "Artist",   150, tk.W),
        ("title",   "Title",    180, tk.W),
        ("album",   "Album",    130, tk.W),
        ("bitrate", "Bitrate",   70, tk.E),
        ("path",    "Full Path", 320, tk.W),
    ]

    def __init__(self, parent, tracks: list[dict]):
        """
        Parameters
        ----------
        tracks : list of dicts with keys: artist, title, album, bitrate,
                 full_path, partition, rel_path
        """
        super().__init__(parent)
        self.confirmed            = False
        self.remove_empty_folders = False
        self._tracks              = tracks
        self._remove_empty_var    = tk.BooleanVar(value=True)

        n = len(tracks)
        self.title(f"Delete {'Track' if n == 1 else f'{n} Tracks'} — Confirm")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.minsize(800, 360)
        self.resizable(True, True)
        self._build()
        self._center()

    def _build(self):
        n = len(self._tracks)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Header
        hdr = tk.Frame(self, bg="#c0392b", pady=8, padx=12)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hdr, text="🗑  Delete Library Tracks",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#c0392b",
        ).pack(side=tk.LEFT)

        # Warning banner
        summ = tk.Frame(self, bg="#fdf2f2", padx=12, pady=6)
        summ.grid(row=1, column=0, sticky="ew")
        tk.Label(
            summ,
            text=(
                f"{n} track{'s' if n != 1 else ''} will be PERMANENTLY DELETED "
                f"from disk and removed from the database."
            ),
            font=("Segoe UI", 9, "bold"), fg="#c0392b", bg="#fdf2f2",
        ).pack(anchor="w")

        # Track table
        tbl_frame = tk.Frame(self, bg="#f5f5f5")
        tbl_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 4))
        tbl_frame.columnconfigure(0, weight=1)
        tbl_frame.rowconfigure(0, weight=1)

        col_ids = [c[0] for c in self._COLS]
        tree = ttk.Treeview(tbl_frame, columns=col_ids, show="headings",
                            selectmode="none")
        for cid, heading, width, anchor in self._COLS:
            tree.heading(cid, text=heading, anchor=anchor)
            tree.column(cid, width=width, anchor=anchor,
                        stretch=(cid == "path"))
        tree.tag_configure("row", background="#fdf2f2")

        vsb = ttk.Scrollbar(tbl_frame, orient=tk.VERTICAL,   command=tree.yview)
        hsb = ttk.Scrollbar(tbl_frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        for t in self._tracks:
            tree.insert("", "end", tags=("row",), values=(
                t.get("artist", ""), t.get("title", ""),
                t.get("album", ""),  t.get("bitrate", ""),
                t.get("full_path", ""),
            ))

        # Buttons row
        btn_frame = tk.Frame(self, bg="#f5f5f5", pady=8, padx=12)
        btn_frame.grid(row=3, column=0, sticky="ew")

        ttk.Checkbutton(
            btn_frame, text="Remove empty folders",
            variable=self._remove_empty_var,
        ).pack(side=tk.LEFT)

        cancel_btn = ttk.Button(btn_frame, text="Cancel", command=self.destroy)
        cancel_btn.pack(side=tk.RIGHT, padx=(4, 0))
        del_label = f"🗑  Delete {n} Track{'s' if n != 1 else ''}"
        del_btn = ttk.Button(
            btn_frame, text=del_label,
            command=self._confirm,
        )
        del_btn.pack(side=tk.RIGHT)

        # Visual order in the toolbar: [Delete] [Cancel]
        # Arrow keys cycle focus between them; Delete is the default.
        def _focus_cancel(_e=None):
            cancel_btn.focus_set()
            return "break"

        def _focus_delete(_e=None):
            del_btn.focus_set()
            return "break"

        for btn in (del_btn, cancel_btn):
            btn.bind("<Right>", _focus_cancel)
            btn.bind("<Left>",  _focus_delete)
            btn.bind("<Return>", lambda _e, b=btn: b.invoke())

        self.bind("<Escape>", lambda _e: self.destroy())

        # Default focus → the Delete button
        self.after(0, del_btn.focus_set)

    def _confirm(self):
        self.confirmed            = True
        self.remove_empty_folders = self._remove_empty_var.get()
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w  = max(self.winfo_reqwidth(),  820)
        h  = max(self.winfo_reqheight(), 380)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class _UseMainArtistDialog(tk.Toplevel):
    """Preview/confirm dialog for the *Use Main Artist Name* action.

    ``items`` is a list of dicts, one per selected track, with keys:
        track   : dict  – the result row (title/album/artist/full_path/…)
        current : str   – the track's current artist tag
        main    : str | None – the canonical name from artist_info, or None
        status  : str   – one of "rename", "unchanged", "missing"
    """

    def __init__(self, parent, items: list[dict],
                 on_search_artist=None):
        super().__init__(parent)
        self._items            = items
        self._on_search_artist = on_search_artist
        self.confirmed         = False
        self.title("Use Main Artist Name — Confirm")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.minsize(880, 380)
        self.resizable(True, True)
        self._build()
        self._center()
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Header
        hdr = tk.Frame(self, bg="#1f618d", pady=8, padx=12)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hdr, text="🎤  Use Main Artist Name",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#1f618d",
        ).pack(side=tk.LEFT)

        n_rename    = sum(1 for it in self._items if it["status"] == "rename")
        n_unchanged = sum(1 for it in self._items if it["status"] == "unchanged")
        n_missing   = sum(1 for it in self._items if it["status"] == "missing")

        summ = tk.Frame(self, bg="#eaf2fb", padx=12, pady=6)
        summ.grid(row=1, column=0, sticky="ew")
        tk.Label(
            summ,
            text=(
                f"{n_rename} track{'s' if n_rename != 1 else ''} will have the "
                f"artist tag rewritten. {n_unchanged} already use the main name. "
                f"{n_missing} have no matching Artist Info entry "
                f"(highlighted below)."
            ),
            font=("Segoe UI", 9), fg="#1a5276", bg="#eaf2fb",
            wraplength=820, justify="left",
        ).pack(anchor="w")

        # Per-track table
        tbl_frame = tk.Frame(self, bg="#f5f5f5")
        tbl_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 4))
        tbl_frame.columnconfigure(0, weight=1)
        tbl_frame.rowconfigure(0, weight=1)

        cols = ("title", "album", "current", "main", "status")
        tree = ttk.Treeview(tbl_frame, columns=cols, show="headings",
                            selectmode="extended")
        for cid, lbl, w, anc, stretch in (
            ("title",   "Title",          200, tk.W, True),
            ("album",   "Album",          160, tk.W, True),
            ("current", "Current Artist", 170, tk.W, True),
            ("main",    "New Artist",     170, tk.W, True),
            ("status",  "Status",         130, tk.W, False),
        ):
            tree.heading(cid, text=lbl, anchor=anc)
            tree.column(cid, width=w, anchor=anc, stretch=stretch)

        tree.tag_configure("rename",    background="#eafaf1")
        tree.tag_configure("unchanged", background="#f4f6f7", foreground="#7f8c8d")
        tree.tag_configure("missing",   background="#fdecea", foreground="#922b21")

        for idx, it in enumerate(self._items):
            status_lbl = {
                "rename":    "Will rename",
                "unchanged": "Already main",
                "missing":   "Artist Info not found",
            }[it["status"]]
            track = it["track"]
            tree.insert(
                "", "end", iid=str(idx), tags=(it["status"],),
                values=(
                    track.get("title", "") or "",
                    track.get("album", "") or "",
                    it["current"],
                    it["main"] or "—",
                    status_lbl,
                ),
            )

        vsb = ttk.Scrollbar(tbl_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self._tree = tree
        tree.bind("<Double-1>", lambda _e: self._search_selected_missing())

        # Buttons
        btn_frame = tk.Frame(self, bg="#f5f5f5", pady=8, padx=12)
        btn_frame.grid(row=3, column=0, sticky="ew")

        self._search_btn = ttk.Button(
            btn_frame, text="🎶 Search in MusicBrainz",
            command=self._search_selected_missing,
        )
        self._search_btn.pack(side=tk.LEFT)
        if n_missing == 0:
            self._search_btn.state(["disabled"])

        cancel_btn = ttk.Button(btn_frame, text="Cancel", command=self.destroy)
        cancel_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self._apply_btn = ttk.Button(
            btn_frame, text=f"✓ Apply {n_rename} Rename{'s' if n_rename != 1 else ''}",
            command=self._confirm,
        )
        self._apply_btn.pack(side=tk.RIGHT)
        if n_rename == 0:
            self._apply_btn.state(["disabled"])

        def _focus_cancel(_e=None):
            cancel_btn.focus_set(); return "break"

        def _focus_apply(_e=None):
            self._apply_btn.focus_set(); return "break"

        for btn in (self._apply_btn, cancel_btn):
            btn.bind("<Right>", _focus_cancel)
            btn.bind("<Left>",  _focus_apply)
            btn.bind("<Return>", lambda _e, b=btn: b.invoke())

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(0, (self._apply_btn if n_rename else cancel_btn).focus_set)

    def _refresh_row(self, idx: int) -> None:
        """Re-render row ``idx`` after its status changed (e.g. an artist was
        imported from MusicBrainz)."""
        it = self._items[idx]
        status_lbl = {
            "rename":    "Will rename",
            "unchanged": "Already main",
            "missing":   "Artist Info not found",
        }[it["status"]]
        track = it["track"]
        self._tree.item(
            str(idx),
            tags=(it["status"],),
            values=(
                track.get("title", "") or "",
                track.get("album", "") or "",
                it["current"],
                it["main"] or "—",
                status_lbl,
            ),
        )

    def _refresh_counts(self) -> None:
        n_rename  = sum(1 for it in self._items if it["status"] == "rename")
        n_missing = sum(1 for it in self._items if it["status"] == "missing")
        self._apply_btn.configure(
            text=f"✓ Apply {n_rename} Rename{'s' if n_rename != 1 else ''}")
        self._apply_btn.state(["!disabled"] if n_rename else ["disabled"])
        self._search_btn.state(["!disabled"] if n_missing else ["disabled"])

    def _search_selected_missing(self):
        # Import lazily to avoid a circular import with artist_panel.
        from music.artist_panel import prompt_mb_search_and_import

        # Pick the first missing row in the user's selection (or any missing
        # row if nothing is selected) and open the MusicBrainz prompt.
        target_idx = None
        target_name = ""
        for iid in self._tree.selection():
            it = self._items[int(iid)]
            if it["status"] == "missing":
                target_idx, target_name = int(iid), it["current"]
                break
        if target_idx is None:
            for i, it in enumerate(self._items):
                if it["status"] == "missing":
                    target_idx, target_name = i, it["current"]
                    break
        if target_idx is None:
            return

        imported = prompt_mb_search_and_import(self, query=target_name)
        if imported is None:
            return

        # Re-resolve every missing row against the freshly imported artist
        # info so all matching rows flip to "rename" in one go.
        for i, it in enumerate(self._items):
            if it["status"] != "missing" or not it["current"]:
                continue
            info = find_artist_by_name_or_alias(it["current"])
            if info is None:
                continue
            main = info["name"]
            it["main"] = main
            it["status"] = "unchanged" if main == it["current"] else "rename"
            self._refresh_row(i)
        self._refresh_counts()

    def _confirm(self):
        self.confirmed = True
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w  = max(self.winfo_reqwidth(),  800)
        h  = max(self.winfo_reqheight(), 380)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class SearchTab(tk.Frame, AudioMenuMixin):

    # (col_id, heading_label, width, anchor, stretch)
    _COL_DEFS = [
        ("rank",      "♥",          36,  tk.CENTER, False),
        ("partition", "Partition",   90,  tk.W,      False),
        ("rel_path",  "Full Path",  290,  tk.W,      True),
        ("artist",    "Artist",     150,  tk.W,      False),
        ("title",     "Title",      180,  tk.W,      False),
        ("album",     "Album",      155,  tk.W,      False),
        ("bitrate",   "Bitrate",     70,  tk.E,      False),
        ("quality",   "Quality",    120,  tk.W,      False),
        ("updated",   "Updated",    130,  tk.W,      False),
    ]

    def __init__(self, master, on_search_artist=None):
        super().__init__(master, bg="#f5f5f5")
        self._settings = load_settings()
        self._on_search_artist = on_search_artist  # callable(artist_name) or None
        self._sort_col: str | None = None
        self._sort_rev: bool = False
        self._results: list = []    # full filtered+sorted result set
        self._page: int = 0         # current page index (0-based)
        self._selected_partition: str | None = None
        self._selected_rel_path:  str | None = None
        # Cached quality labels keyed by absolute file path so they persist
        # across pagination / re-sort / refresh of the result table.
        self._quality_cache: dict[str, str] = {}
        self._analyze_thread: threading.Thread | None = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────── #
        top = tk.Frame(self, bg="#2c3e50", pady=12, padx=16)
        top.pack(fill=tk.X)

        tk.Label(
            top, text="🔍  Search In Lib",
            font=("Segoe UI", 16, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # ── Search inputs row ─────────────────────────────────────────── #
        inp = tk.Frame(self, bg="#f5f5f5", pady=10, padx=16)
        inp.pack(fill=tk.X)

        tk.Label(inp, text="Artist:", font=("Segoe UI", 9), bg="#f5f5f5").pack(side=tk.LEFT)
        self._artist_var = tk.StringVar()
        artist_entry = ttk.Entry(inp, textvariable=self._artist_var, width=24)
        artist_entry.pack(side=tk.LEFT, padx=(4, 16))
        artist_entry.bind("<Return>", lambda _: self._search())

        tk.Label(inp, text="Title:", font=("Segoe UI", 9), bg="#f5f5f5").pack(side=tk.LEFT)
        self._title_var = tk.StringVar()
        title_entry = ttk.Entry(inp, textvariable=self._title_var, width=24)
        title_entry.pack(side=tk.LEFT, padx=(4, 16))
        title_entry.bind("<Return>", lambda _: self._search())

        tk.Label(inp, text="Album:", font=("Segoe UI", 9), bg="#f5f5f5").pack(side=tk.LEFT)
        self._album_var = tk.StringVar()
        album_entry = ttk.Entry(inp, textvariable=self._album_var, width=24)
        album_entry.pack(side=tk.LEFT, padx=(4, 16))
        album_entry.bind("<Return>", lambda _: self._search())

        # Partition filter
        tk.Label(inp, text="Partition:", font=("Segoe UI", 9), bg="#f5f5f5").pack(side=tk.LEFT)
        self._partition_var = tk.StringVar(value=_ALL_PARTITIONS_LABEL)
        partitions = [_ALL_PARTITIONS_LABEL] + sorted(
            self._settings.get("music_lib_paths", {}).keys()
        )
        self._partition_cb = ttk.Combobox(
            inp, textvariable=self._partition_var,
            values=partitions, state="readonly", width=14,
        )
        self._partition_cb.pack(side=tk.LEFT, padx=(4, 16))
        self._partition_cb.bind("<<ComboboxSelected>>", lambda _: self._search())

        # Rating filter
        tk.Label(inp, text="Rating:", font=("Segoe UI", 9), bg="#f5f5f5").pack(
            side=tk.LEFT, padx=(4, 4))
        self._rank_filter_var = tk.StringVar(value=_RANK_FILTER_LABELS[0])
        rank_cb = ttk.Combobox(
            inp, textvariable=self._rank_filter_var,
            values=_RANK_FILTER_LABELS, state="readonly", width=11,
        )
        rank_cb.pack(side=tk.LEFT, padx=(0, 16))
        rank_cb.bind("<<ComboboxSelected>>", lambda _: self._search())

        ttk.Button(inp, text="Search", command=self._search).pack(side=tk.LEFT)
        ttk.Button(inp, text="Clear",  command=self._clear).pack(side=tk.LEFT, padx=(6, 0))

        # ── Utilities row ─────────────────────────────────────────────── #
        util = tk.Frame(self, bg="#f5f5f5", padx=16)
        util.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(
            util, text="🎲 Random song list", command=self._random_song_list,
        ).pack(side=tk.LEFT)
        ttk.Button(
            util, text="🆕 Latest added songs", command=self._latest_added_songs,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(
            util, text="🧬 Find Duplicates", command=self._find_duplicates,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(
            util, text="✂ Trim Spaces", command=self._trim_spaces,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(
            util, text="🎤 Use Main Artist Name", command=self._use_main_artist_name,
        ).pack(side=tk.LEFT, padx=(6, 0))

        # ── Status label ─────────────────────────────────────────────── #
        self._status_var = tk.StringVar(value="Press Search or Enter to load library.")
        tk.Label(
            self, textvariable=self._status_var,
            font=("Segoe UI", 9, "italic"),
            fg="#7f8c8d", bg="#f5f5f5", anchor="w", padx=16,
        ).pack(fill=tk.X)

        # ── Bottom status bar (packed before PanedWindow to anchor it) ── #
        bar = tk.Frame(self, bg="#bdc3c7", height=24)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self._footer_var = tk.StringVar(value="Ready.")
        tk.Label(
            bar, textvariable=self._footer_var,
            font=("Segoe UI", 9), bg="#bdc3c7",
            anchor="w", padx=8,
        ).pack(fill=tk.X)

        # ── PanedWindow: left = table+pagination, right = details ────── #
        self._paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, bg="#d0d3d4",
            sashrelief=tk.FLAT, sashwidth=5,
        )
        self._paned.pack(fill=tk.BOTH, expand=True, padx=16, pady=6)

        # Left pane
        left = tk.Frame(self._paned, bg="#f5f5f5")
        self._paned.add(left, stretch="always", minsize=400)

        # Tree
        tree_frame = tk.Frame(left, bg="#f5f5f5")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = [c[0] for c in self._COL_DEFS]
        self.tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings", selectmode="extended",
        )

        _cmd = lambda c: (lambda: self._sort_column(c))
        for col_id, label, width, anchor, stretch in self._COL_DEFS:
            self.tree.heading(col_id, text=label, anchor=anchor, command=_cmd(col_id))
            self.tree.column(col_id, width=width, anchor=anchor, stretch=stretch)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,   command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.tag_configure("odd",  background="#ffffff")
        self.tree.tag_configure("even", background="#ecf0f1")

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Button-3>",         self._on_row_right_click)
        self.tree.bind("<Control-a>", lambda _: self.tree.selection_set(self.tree.get_children()))
        self.tree.bind("<Shift-e>", lambda _: self._hotkey_edit_tags())
        self.tree.bind("<Shift-E>", lambda _: self._hotkey_edit_tags())
        self.tree.bind("<Shift-Delete>", lambda _: self._hotkey_delete_tracks())
        self.tree.bind("<Shift-KP_Delete>", lambda _: self._hotkey_delete_tracks())
        self._kb_sel = attach_keyboard_range_selection(self.tree)

        # Pagination (inside left pane, below tree)
        pag = tk.Frame(left, bg="#f5f5f5", pady=4)
        pag.pack(fill=tk.X)

        self._btn_prev = ttk.Button(pag, text="◀  Prev", command=self._prev_page, width=9)
        self._btn_prev.pack(side=tk.LEFT)

        self._page_var = tk.StringVar(value="")
        tk.Label(
            pag, textvariable=self._page_var,
            font=("Segoe UI", 9), bg="#f5f5f5", width=28,
        ).pack(side=tk.LEFT, padx=8)

        self._btn_next = ttk.Button(pag, text="Next  ▶", command=self._next_page, width=9)
        self._btn_next.pack(side=tk.LEFT)

        # Quality analyzer: runs librosa-based spectral analysis on the
        # currently-selected tracks and fills the Quality column.
        self._btn_analyze = ttk.Button(
            pag, text="🔬 Analyze spec",
            command=self._analyze_selected_quality,
        )
        self._btn_analyze.pack(side=tk.LEFT, padx=(12, 0))

        self._analyze_progress_var = tk.StringVar(value="")
        tk.Label(
            pag, textvariable=self._analyze_progress_var,
            font=("Segoe UI", 9, "italic"),
            fg="#7f8c8d", bg="#f5f5f5",
        ).pack(side=tk.LEFT, padx=(6, 0))

        self._update_pagination_controls()

        # Right pane — detail panel
        self._detail_panel = AudioDetailsPanel(
            self._paned,
            on_after_save=self._on_tags_saved,
        )
        self._paned.add(self._detail_panel, stretch="never", minsize=240)

        self._paned.bind("<ButtonRelease-1>", self._on_sash_release)
        self.after(150, self._restore_sash)

    # ------------------------------------------------------------------ #
    # Layout persistence                                                   #
    # ------------------------------------------------------------------ #

    def _on_sash_release(self, _event):
        try:
            x, _ = self._paned.sash_coord(0)
            self._settings["search_sash"] = x
            save_settings(self._settings)
        except Exception:
            pass

    def _restore_sash(self):
        x = self._settings.get("search_sash")
        if x is not None:
            try:
                self._paned.sash_place(0, int(x), 0)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Search logic                                                         #
    # ------------------------------------------------------------------ #

    def _search(self):
        artist_q = self._artist_var.get().strip()
        title_q  = self._title_var.get().strip()
        album_q  = self._album_var.get().strip()
        min_rank = _RANK_FILTER_MAP.get(self._rank_filter_var.get(), 0)
        partition_q = self._partition_var.get()
        partition_filter = (
            None if partition_q == _ALL_PARTITIONS_LABEL else partition_q
        )

        self._status_var.set("Searching…")
        self.update_idletasks()

        # Expand artist query through the alias table so tracks stored under
        # any name variant of a matched artist are included in results.
        artist_variants = get_artist_name_variants(artist_q)

        artist_q_lower    = artist_q.lower()
        variants_lower    = {v.strip().lower() for v in artist_variants}

        def _artist_priority(track_artist: str) -> int:
            """Return ordering priority for the artist match.

            1 – exact match against the query string itself
            2 – exact match against a resolved alias / name variant
            3 – fuzzy / substring match (everything else)
            """
            t = track_artist.strip().lower()
            if artist_q and t == artist_q_lower:
                return 1
            if t in variants_lower:
                return 2
            return 3

        def _artist_matches(track_artist: str) -> bool:
            if _fuzzy_match(artist_q, track_artist):
                return True
            return any(_fuzzy_match(v, track_artist) for v in artist_variants)

        self._results = []
        lib_paths = self._settings.get("music_lib_paths", {})
        for row in get_track_info(partition_filter):
            ranking = int(row["ranking"] or 0)
            if ranking < min_rank:
                continue
            if _artist_matches(row["artist"] or "") and _fuzzy_match(title_q, row["title"] or "") and _fuzzy_match(album_q, row["album"] or ""):
                d = dict(row)
                lib_root = lib_paths.get(d["partition"], "")
                d["full_path"] = (
                    os.path.join(lib_root, d["partition"], d["rel_path"])
                    if lib_root else d["rel_path"]
                )
                d["ranking"] = ranking
                d["_priority"] = _artist_priority(d["artist"] or "")
                self._results.append(d)

        # Sort by match quality: exact artist → exact alias → fuzzy
        if artist_q:
            self._results.sort(key=lambda r: r["_priority"])

        self._sort_col = None
        self._sort_rev = False
        self._reset_headings()
        self._page = 0
        self._detail_panel.clear()
        self._show_page()

        n = len(self._results)
        self._status_var.set(f"{n} result{'s' if n != 1 else ''} found.")
        self._footer_var.set(f"{n} track{'s' if n != 1 else ''} matched.")

    def _clear(self):
        self._artist_var.set("")
        self._title_var.set("")
        self._album_var.set("")
        self._partition_var.set(_ALL_PARTITIONS_LABEL)
        self._rank_filter_var.set(_RANK_FILTER_LABELS[0])
        self._results = []
        self._page = 0
        self.tree.delete(*self.tree.get_children())
        self._sort_col = None
        self._sort_rev = False
        self._reset_headings()
        self._update_pagination_controls()
        self._detail_panel.clear()
        self._selected_partition = None
        self._selected_rel_path  = None
        self._status_var.set("Press Search or Enter to load library.")
        self._footer_var.set("Ready.")

    def _search_by(self, *, artist: str = "", title: str = "", album: str = "") -> None:
        """Clear all filters, set the given field, then run a search."""
        self._artist_var.set(artist)
        self._title_var.set(title)
        self._album_var.set(album)
        self._partition_var.set(_ALL_PARTITIONS_LABEL)
        self._rank_filter_var.set(_RANK_FILTER_LABELS[0])
        self._search()

    def _random_song_list(self, n: int = 30) -> None:
        """Load *n* random tracks from the library, refreshed on every click."""
        import random

        self._status_var.set("Picking random songs…")
        self.update_idletasks()

        lib_paths = self._settings.get("music_lib_paths", {})
        all_rows = list(get_track_info())
        sample = random.sample(all_rows, min(n, len(all_rows)))

        self._results = []
        for row in sample:
            d = dict(row)
            lib_root = lib_paths.get(d["partition"], "")
            d["full_path"] = (
                os.path.join(lib_root, d["partition"], d["rel_path"])
                if lib_root else d["rel_path"]
            )
            d["ranking"] = int(row["ranking"] or 0)
            self._results.append(d)

        self._sort_col = None
        self._sort_rev = False
        self._reset_headings()
        self._page = 0
        self._detail_panel.clear()
        self._show_page()

        count = len(self._results)
        self._status_var.set(
            f"🎲 {count} random track{'s' if count != 1 else ''} "
            f"(of {len(all_rows)} in lib)."
        )
        self._footer_var.set(
            f"{count} random track{'s' if count != 1 else ''} loaded."
        )

    def _latest_added_songs(self, n: int = 100) -> None:
        """Load the *n* most recently added tracks, ordered newest first.

        Recency is based on each track's ``updated_at`` timestamp (set when a
        song is first catalogued / last refreshed in the library).
        """
        self._status_var.set("Loading latest added songs…")
        self.update_idletasks()

        lib_paths = self._settings.get("music_lib_paths", {})
        all_rows = list(get_track_info())

        # Newest first. ``updated_at`` is an ISO-ish "YYYY-MM-DD HH:MM:SS"
        # string, so lexical sorting matches chronological ordering.
        all_rows.sort(key=lambda r: (r["updated_at"] or ""), reverse=True)
        latest = all_rows[:n]

        self._results = []
        for row in latest:
            d = dict(row)
            lib_root = lib_paths.get(d["partition"], "")
            d["full_path"] = (
                os.path.join(lib_root, d["partition"], d["rel_path"])
                if lib_root else d["rel_path"]
            )
            d["ranking"] = int(row["ranking"] or 0)
            self._results.append(d)

        self._sort_col = None
        self._sort_rev = False
        self._reset_headings()
        self._page = 0
        self._detail_panel.clear()
        self._show_page()

        count = len(self._results)
        self._status_var.set(
            f"🆕 {count} latest added track{'s' if count != 1 else ''} "
            f"(of {len(all_rows)} in lib)."
        )
        self._footer_var.set(
            f"{count} latest added track{'s' if count != 1 else ''} loaded."
        )

    def _find_duplicates(self) -> None:
        """Find tracks sharing the same (artist, title) and group them together.

        Each duplicate group is shown contiguously in the result list; groups
        are ordered by artist/title so related rows stay adjacent. Uses the
        same pagination as a normal search.
        """
        self._status_var.set("Scanning library for duplicates…")
        self.update_idletasks()

        lib_paths = self._settings.get("music_lib_paths", {})

        groups: dict[tuple[str, str], list[dict]] = {}
        for row in get_track_info():
            artist = (row["artist"] or "").strip()
            title  = (row["title"]  or "").strip()
            if not artist or not title:
                continue
            key = (artist.lower(), title.lower())
            d = dict(row)
            lib_root = lib_paths.get(d["partition"], "")
            d["full_path"] = (
                os.path.join(lib_root, d["partition"], d["rel_path"])
                if lib_root else d["rel_path"]
            )
            d["ranking"] = int(row["ranking"] or 0)
            groups.setdefault(key, []).append(d)

        dup_groups = [g for g in groups.values() if len(g) >= 2]
        dup_groups.sort(key=lambda g: (
            (g[0].get("artist") or "").lower(),
            (g[0].get("title")  or "").lower(),
        ))

        self._results = []
        for g in dup_groups:
            g.sort(key=lambda r: (
                (r.get("album")     or "").lower(),
                (r.get("partition") or "").lower(),
                (r.get("rel_path")  or "").lower(),
            ))
            self._results.extend(g)

        self._sort_col = None
        self._sort_rev = False
        self._reset_headings()
        self._page = 0
        self._detail_panel.clear()
        self._show_page()

        n_groups = len(dup_groups)
        n_tracks = len(self._results)
        self._status_var.set(
            f"🧬 {n_groups} duplicate group{'s' if n_groups != 1 else ''} "
            f"({n_tracks} track{'s' if n_tracks != 1 else ''}) — "
            f"grouped by Artist + Title."
        )
        self._footer_var.set(
            f"{n_tracks} duplicate track{'s' if n_tracks != 1 else ''} "
            f"in {n_groups} group{'s' if n_groups != 1 else ''}."
        )

    # ------------------------------------------------------------------ #
    # Row selection → detail panel                                         #
    # ------------------------------------------------------------------ #

    def _on_row_select(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        values    = self.tree.item(selected[0], "values")
        partition = values[1]   # rank is [0], partition is [1]
        full_path = values[2]
        ext       = os.path.splitext(full_path)[1].lstrip(".").upper()

        if ext == "FLAC" and os.path.isfile(full_path):
            lib_root = self._settings.get("music_lib_paths", {}).get(partition, "")
            self._selected_partition = partition
            self._selected_rel_path  = (
                os.path.relpath(full_path, os.path.join(lib_root, partition))
                if lib_root else full_path
            )
            self._detail_panel.show_flac(full_path)
        else:
            self._selected_partition = None
            self._selected_rel_path  = None
            self._detail_panel.clear()

    # ------------------------------------------------------------------ #
    # Right-click context menu                                             #
    # ------------------------------------------------------------------ #

    def _hotkey_edit_tags(self):
        """Shift+E handler — opens Edit Tags for any FLAC files in the current selection."""
        selected = self.tree.selection()
        if not selected:
            return
        paths = [self.tree.item(i, "values")[2] for i in selected]
        flac_paths = [p for p in paths if p.lower().endswith(".flac")]
        if flac_paths:
            self._edit_tags(flac_paths)

    def _hotkey_delete_tracks(self):
        """Shift+Del handler — deletes the currently selected library tracks."""
        selected = self.tree.selection()
        if not selected:
            return
        self._delete_selected_tracks(list(selected))
        return "break"

    def _on_row_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return

        if item not in self.tree.selection():
            self.tree.selection_set(item)

        selected = self.tree.selection()
        # values[2] is the full path (rank=0, partition=1, full_path=2)
        paths = [self.tree.item(i, "values")[2] for i in selected]

        def extra(menu, paths, audio_paths, flac_paths):
            # ── Search Artist in Artist Info ── #
            if len(selected) == 1 and self._on_search_artist is not None:
                artist = (self.tree.item(item, "values")[3] or "").strip()
                if artist:
                    menu.add_command(
                        label=f"👤  Search Artist: {artist}",
                        command=lambda a=artist: self._on_search_artist(a),
                    )
                    menu.add_separator()

            # ── Search By submenu ── #
            if len(selected) == 1:
                vals = self.tree.item(item, "values")
                row_artist = (vals[3] or "").strip()
                row_title  = (vals[4] or "").strip()
                row_album  = (vals[5] or "").strip()

                by_menu = tk.Menu(menu, tearoff=0)
                if row_artist:
                    by_menu.add_command(
                        label=f"Artist: {row_artist}",
                        command=lambda a=row_artist: self._search_by(artist=a),
                    )
                if row_title:
                    by_menu.add_command(
                        label=f"Title: {row_title}",
                        command=lambda t=row_title: self._search_by(title=t),
                    )
                if row_album:
                    by_menu.add_command(
                        label=f"Album: {row_album}",
                        command=lambda al=row_album: self._search_by(album=al),
                    )
                if row_artist or row_title or row_album:
                    menu.add_cascade(label="🔎  Search By ▶", menu=by_menu)
                    menu.add_separator()

            # ── Rate Track submenu ── #
            rate_menu = tk.Menu(menu, tearoff=0)
            _rank_labels = [
                ("❤️  5  —  Loved",  5),
                ("★★★★  4",          4),
                ("★★★  3",           3),
                ("★★  2",            2),
                ("★  1",             1),
                ("  0  —  Unranked", 0),
            ]
            for label, val in _rank_labels:
                rate_menu.add_command(
                    label=label,
                    command=lambda v=val, iids=selected: self._apply_ranking(iids, v),
                )
            menu.add_cascade(label="⭐  Rate Track ▶", menu=rate_menu)
            menu.add_separator()

            # ── Paste cover art from clipboard ── #
            from music.cover_art_panel import SUPPORTED_EMBED_EXTS
            cover_paths = [
                p for p in paths
                if os.path.splitext(p)[1].lower() in SUPPORTED_EMBED_EXTS
            ]
            if cover_paths:
                menu.add_command(
                    label="🖼  Embed Cover Art from Clipboard",
                    command=lambda cp=cover_paths: self._paste_cover_art_to_selected(cp),
                )
                menu.add_separator()

            # ── Compare & Pick (exactly 2 selected) ── #
            if len(selected) == 2:
                menu.add_command(
                    label="⚖️  Compare & Pick (keep one)…",
                    command=lambda iids=tuple(selected): self._compare_and_pick(iids),
                )
                menu.add_separator()

            # ── Delete tracks ── #
            menu.add_separator()
            n_sel = len(selected)
            menu.add_command(
                label=f"🗑  Delete {'Track' if n_sel == 1 else f'{n_sel} Tracks'} from Library…",
                accelerator="Shift+Del",
                command=lambda iids=selected: self._delete_selected_tracks(iids),
            )

        menu = self._build_audio_context_menu(paths, extra_items_fn=extra)
        menu.tk_popup(event.x_root, event.y_root)

    def _analyze_track(self, path: str):
        """Override AudioMenuMixin._analyze_track so right-click → Analyze
        also persists the result to the curated ``track_info`` row and
        updates the visible Quality column."""
        from music.audio_analysis_panel import AudioAnalysisPanel

        row = next(
            (r for r in self._results if r.get("full_path") == path),
            None,
        )
        partition = (row or {}).get("partition", "")
        rel_path = (row or {}).get("rel_path", "")
        iid = f"ti_{row['id']}" if row and row.get("id") is not None else None

        def _on_complete(_result, label: str):
            if not label or label == "error":
                return
            self._quality_cache[path] = label
            if row is not None:
                row["quality"] = label
            if partition and rel_path:
                try:
                    update_track_info_quality(partition, rel_path, label)
                except Exception:
                    _log.exception("Failed to persist quality for %s / %s",
                                   partition, rel_path)
            if iid and self.tree.exists(iid):
                try:
                    self.tree.set(iid, "quality", label)
                except tk.TclError:
                    pass

        AudioAnalysisPanel(self.winfo_toplevel(), path, on_complete=_on_complete)

    def _paste_cover_art_to_selected(self, audio_paths: list[str]) -> None:
        """Read an image from the clipboard, show a preview dialog, then embed into FLAC/MP3/M4A files."""
        from PIL import ImageGrab, Image
        import io as _io
        from music.folder_scanner import _PasteCoverArtDialog
        from music.cover_art_panel import embed_front_cover

        try:
            img = ImageGrab.grabclipboard()
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror("Clipboard error", str(exc), parent=self)
            return

        if not isinstance(img, Image.Image):
            from tkinter import messagebox
            messagebox.showinfo(
                "No image in clipboard",
                "The clipboard does not contain an image.\n\nCopy an image first, then try again.",
                parent=self,
            )
            return

        buf = _io.BytesIO()
        if img.mode in ("RGBA", "LA", "PA"):
            img.save(buf, "PNG")
            mime = "image/png"
        else:
            img.convert("RGB").save(buf, "JPEG", quality=95)
            mime = "image/jpeg"
        img_bytes = buf.getvalue()

        dlg = _PasteCoverArtDialog(self, img, img_bytes, mime, len(audio_paths))
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        log = get_logger("search_paste_cover")
        errors: list[str] = []
        depth = max(len(img.getbands()), 1) * 8
        for path in audio_paths:
            try:
                embed_front_cover(path, img_bytes, mime, img.width, img.height, depth)
                log.info("Embedded cover art: %s", path)
            except Exception as exc:
                log.error("Failed to embed cover art into %s: %s", path, exc, exc_info=True)
                errors.append(f"{os.path.basename(path)}: {exc}")

        from tkinter import messagebox
        n = len(audio_paths)
        if errors:
            messagebox.showerror(
                "Embed Cover Art",
                f"Embedded into {n - len(errors)} file(s), {len(errors)} failed.\n\n" + "\n".join(errors[:8]),
                parent=self,
            )
        else:
            messagebox.showinfo(
                "Embed Cover Art",
                f"Embedded cover art into {n} file{'s' if n != 1 else ''}.",
                parent=self,
            )

    def _apply_ranking(self, iids, ranking: int):
        """Persist a 0-5 ranking for each selected row and refresh the emoji."""
        emoji = _rank_emoji(ranking)
        for iid in iids:
            # iid format is "ti_{track_info.id}"
            try:
                track_id = int(iid.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            set_track_ranking(track_id, ranking)
            # Update result dict in memory
            for r in self._results:
                if r.get("id") == track_id:
                    r["ranking"] = ranking
                    break
            # Refresh the visible tree cell (keep all other values)
            vals = list(self.tree.item(iid, "values"))
            vals[0] = emoji
            self.tree.item(iid, values=vals)

    def _compare_and_pick(self, iids):
        """Open the Compare & Pick dialog for exactly two selected rows."""
        from music.compare_pick_dialog import run_compare_and_pick

        if len(iids) != 2:
            return
        left  = self._row_for_iid(iids[0])
        right = self._row_for_iid(iids[1])
        if not left or not right:
            return

        outcome = run_compare_and_pick(self.winfo_toplevel(), dict(left), dict(right))
        if not outcome:
            return

        deleted = outcome["deleted"]
        kept    = outcome["kept"]
        deleted_id = deleted.get("id")
        deleted_iid = f"ti_{deleted_id}" if deleted_id is not None else None

        # Remove deleted row from results + tree
        if deleted_id is not None:
            self._results = [r for r in self._results if r.get("id") != deleted_id]
        if deleted_iid and self.tree.exists(deleted_iid):
            self.tree.delete(deleted_iid)

        # Clear detail panel if it was showing the deleted track
        if (self._selected_partition, self._selected_rel_path) == (
            deleted.get("partition"), deleted.get("rel_path"),
        ):
            self._detail_panel.clear()
            self._selected_partition = None
            self._selected_rel_path  = None

        # If tag edits were applied to the kept track, refresh that row's
        # visible cells from the new on-disk metadata.
        if outcome.get("edited_tags") and kept:
            try:
                from mutagen.flac import FLAC as _FLAC
                from datetime import datetime
                flac = _FLAC(kept.get("full_path", ""))
                tags = flac.tags or {}
                self._refresh_result_row(
                    kept.get("partition", ""),
                    kept.get("rel_path",  ""),
                    artist=(tags.get("artist", [""])[0]),
                    title=(tags.get("title",  [""])[0]),
                    album=(tags.get("album",  [""])[0]),
                    bitrate=(f"{round(flac.info.bitrate / 1000)} kbps"
                             if flac.info.bitrate else ""),
                )
                _ = datetime  # noqa  (timestamp updated inside _refresh_result_row)
            except Exception:
                _log.exception("Could not refresh kept-row metadata after compare-pick")

        # Re-stripe remaining visible rows
        for i, iid in enumerate(self.tree.get_children()):
            self.tree.item(iid, tags=("odd" if i % 2 == 0 else "even",))

        remaining = len(self._results)
        kept_label = (kept.get("title") or os.path.basename(kept.get("full_path", "")) or "?")
        self._status_var.set(
            f"⚖️  Kept “{kept_label}”, deleted the other — {remaining} remaining."
        )
        self._footer_var.set(f"{remaining} track{'s' if remaining != 1 else ''} matched.")
        self._update_pagination_controls()

    # ------------------------------------------------------------------ #
    # Delete tracks from library                                           #
    # ------------------------------------------------------------------ #

    def _delete_selected_tracks(self, iids):
        """Delete selected tracks from disk, the DB, and the results list."""
        from tkinter import messagebox

        # Build track info for each iid
        tracks = []
        for iid in iids:
            try:
                track_id = int(iid.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            for r in self._results:
                if r.get("id") == track_id:
                    tracks.append(dict(r))
                    break

        if not tracks:
            return

        dlg = _DeleteLibTracksDialog(self.winfo_toplevel(), tracks)
        self.wait_window(dlg)

        if not dlg.confirmed:
            return

        deleted_iids:  list[str] = []
        delete_errors: list[tuple[str, str]] = []
        deleted_dirs:  set[str] = set()

        for t in tracks:
            full_path = t.get("full_path", "")
            partition = t.get("partition", "")
            rel_path  = t.get("rel_path",  "")

            # Delete file from disk
            if full_path and os.path.isfile(full_path):
                try:
                    os.remove(full_path)
                    deleted_dirs.add(os.path.dirname(full_path))
                except OSError as exc:
                    delete_errors.append((full_path, str(exc)))
                    continue
            elif full_path and not os.path.isfile(full_path):
                # File already gone — still clean up DB
                pass

            # Remove from database (cascades to track_tags / track_ranking)
            try:
                delete_track_info(partition, rel_path)
                delete_track(full_path)
            except Exception as exc:
                _log.error(f"DB delete failed for {full_path}: {exc}")

            # Mark iid for tree removal
            iid = f"ti_{t['id']}"
            deleted_iids.append(iid)

        # Remove empty parent folders if requested
        n_folders = 0
        if dlg.remove_empty_folders:
            for folder in sorted(deleted_dirs, key=len, reverse=True):
                try:
                    if os.path.isdir(folder) and not os.listdir(folder):
                        os.rmdir(folder)
                        n_folders += 1
                except OSError:
                    pass

        # Remove from in-memory results
        deleted_ids = {t["id"] for t in tracks if f"ti_{t['id']}" in deleted_iids}
        self._results = [r for r in self._results if r.get("id") not in deleted_ids]

        # Remove rows from tree
        for iid in deleted_iids:
            if self.tree.exists(iid):
                self.tree.delete(iid)

        # Clear detail panel if the selected track was deleted
        if (self._selected_partition, self._selected_rel_path) in {
            (t["partition"], t["rel_path"]) for t in tracks
        }:
            self._detail_panel.clear()
            self._selected_partition = None
            self._selected_rel_path  = None

        # Re-stripe remaining visible rows
        for i, iid in enumerate(self.tree.get_children()):
            self.tree.item(iid, tags=("odd" if i % 2 == 0 else "even",))

        n_del = len(deleted_iids)
        n_err = len(delete_errors)

        if delete_errors:
            messagebox.showerror(
                "Deletion Errors",
                f"{n_err} file(s) could not be deleted:\n\n"
                + "\n".join(f"• {p}\n  {e}" for p, e in delete_errors[:8]),
                parent=self,
            )

        remaining = len(self._results)
        status = (
            f"Deleted {n_del} track{'s' if n_del != 1 else ''}"
            + (f", {n_err} error{'s' if n_err != 1 else ''}" if n_err else "")
            + (f", removed {n_folders} empty folder{'s' if n_folders != 1 else ''}" if n_folders else "")
            + f" — {remaining} remaining."
        )
        self._status_var.set(status)
        self._footer_var.set(f"{remaining} track{'s' if remaining != 1 else ''} matched.")
        self._update_pagination_controls()

    # ------------------------------------------------------------------ #
    # After-save: recompute MD5 and refresh DB                            #
    # ------------------------------------------------------------------ #

    def _on_tags_saved(self, path: str):
        if not self._selected_partition or not self._selected_rel_path:
            return
        try:
            new_md5 = compute_file_md5(path)
            flac    = FLAC(path)
            tags    = flac.tags or {}
            artist  = (tags.get("artist",  [""])[0])
            title   = (tags.get("title",   [""])[0])
            album   = (tags.get("album",   [""])[0])
            bitrate = (f"{round(flac.info.bitrate / 1000)} kbps"
                       if flac.info.bitrate else "")
            upsert_track_info(
                self._selected_partition,
                self._selected_rel_path,
                artist=artist, title=title, album=album,
                bitrate=bitrate, file_md5=new_md5,
            )
            _log.info(
                f"DB updated after tag save: {self._selected_partition}/"
                f"{self._selected_rel_path}  md5={new_md5[:8]}…"
            )
            # Refresh the matching row in _results and the visible tree
            self._refresh_result_row(
                self._selected_partition, self._selected_rel_path,
                artist, title, album, bitrate,
            )
        except Exception as exc:
            _log.error(f"DB MD5 update failed for {path}: {exc}")

    def _refresh_result_row(
        self, partition: str, rel_path: str,
        artist: str, title: str, album: str, bitrate: str,
    ):
        """Update the in-memory result list and the visible treeview row."""
        from datetime import datetime
        updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        full_path = ""
        ranking   = 0
        for row in self._results:
            if row.get("partition") == partition and row.get("rel_path") == rel_path:
                row.update(artist=artist, title=title, album=album,
                           bitrate=bitrate, updated_at=updated)
                full_path = row.get("full_path", "")
                ranking   = int(row.get("ranking") or 0)
                break

        for iid in self.tree.get_children():
            vals = self.tree.item(iid, "values")
            # rank=vals[0], partition=vals[1], full_path=vals[2]
            if vals[1] == partition and vals[2] == full_path:
                self.tree.item(iid, values=(
                    _rank_emoji(ranking),
                    partition, full_path, artist, title, album, bitrate, updated,
                ))
                break

    # ------------------------------------------------------------------ #
    # Trim whitespace in artist/title/album                                #
    # ------------------------------------------------------------------ #

    def _trim_spaces(self):
        """Strip leading/trailing whitespace from artist/title/album for the
        selected rows (or all current results if nothing is selected).

        Updates both the audio file tags (FLAC) and the ``track_info`` DB
        record so the two stay in sync.
        """
        from tkinter import messagebox

        selected = self.tree.selection()
        if selected:
            target_paths = set()
            for iid in selected:
                vals = self.tree.item(iid, "values")
                if vals and len(vals) >= 3:
                    target_paths.add(vals[2])
            rows = [r for r in self._results
                    if r.get("full_path") in target_paths]
            scope_label = f"{len(rows)} selected track{'s' if len(rows) != 1 else ''}"
        else:
            rows = list(self._results)
            scope_label = f"all {len(rows)} result{'s' if len(rows) != 1 else ''}"

        if not rows:
            messagebox.showinfo(
                "Trim Spaces", "No tracks to trim.", parent=self.winfo_toplevel())
            return

        if not messagebox.askyesno(
            "Trim Spaces",
            f"Trim leading/trailing whitespace in Artist/Title/Album for "
            f"{scope_label}?\n\nFLAC tags on disk and the library DB will be "
            f"updated.",
            parent=self.winfo_toplevel(),
        ):
            return

        changed = 0
        errors  = 0
        for row in rows:
            path      = row.get("full_path") or ""
            partition = row.get("partition")
            rel_path  = row.get("rel_path")
            if not path or not partition or not rel_path:
                continue

            old_artist = row.get("artist", "") or ""
            old_title  = row.get("title",  "") or ""
            old_album  = row.get("album",  "") or ""

            new_artist = old_artist.strip()
            new_title  = old_title.strip()
            new_album  = old_album.strip()

            if (new_artist == old_artist
                    and new_title == old_title
                    and new_album == old_album):
                continue

            try:
                file_md5 = row.get("file_md5") or ""
                bitrate  = row.get("bitrate", "") or ""
                if path.lower().endswith(".flac") and os.path.exists(path):
                    flac = FLAC(path)
                    if flac.tags is None:
                        flac.add_tags()
                    flac["artist"] = new_artist
                    flac["title"]  = new_title
                    flac["album"]  = new_album
                    flac.save()
                    file_md5 = compute_file_md5(path)
                    if flac.info.bitrate:
                        bitrate = f"{round(flac.info.bitrate / 1000)} kbps"

                upsert_track_info(
                    partition, rel_path,
                    artist=new_artist, title=new_title, album=new_album,
                    bitrate=bitrate, file_md5=file_md5,
                )
                self._refresh_result_row(
                    partition, rel_path,
                    new_artist, new_title, new_album, bitrate,
                )
                changed += 1
            except Exception as exc:
                errors += 1
                _log.error(f"Trim spaces failed for {path}: {exc}")

        msg = f"Trimmed whitespace in {changed} track{'s' if changed != 1 else ''}."
        if errors:
            msg += f" ({errors} error{'s' if errors != 1 else ''} — see log.)"
        self._footer_var.set(msg)
        self._status_var.set(msg)

    # ------------------------------------------------------------------ #
    # Use main artist name (resolve aliases via artist_info)              #
    # ------------------------------------------------------------------ #

    def _use_main_artist_name(self):
        """For each selected track, resolve the current artist name against
        ``artist_info`` and replace it with the canonical main name when the
        current value is an alias.

        Opens a confirmation dialog listing every selected track with its
        current artist tag and the proposed new artist tag. Tracks whose
        artist is missing from Artist Info are highlighted; the user can
        click *Search in MusicBrainz* to import the artist directly, which
        immediately re-classifies any matching rows as renames.
        """
        from tkinter import messagebox

        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(
                "Use Main Artist Name",
                "Select one or more tracks in the result table first.",
                parent=self.winfo_toplevel(),
            )
            return

        target_paths: list[str] = []
        seen_paths: set[str] = set()
        for iid in selected:
            vals = self.tree.item(iid, "values")
            if not vals or len(vals) < 3:
                continue
            p = vals[2]
            if p and p not in seen_paths:
                seen_paths.add(p)
                target_paths.append(p)

        path_to_row = {r.get("full_path"): r for r in self._results}
        tracks = [path_to_row[p] for p in target_paths if p in path_to_row]

        if not tracks:
            messagebox.showinfo(
                "Use Main Artist Name", "No tracks to process.",
                parent=self.winfo_toplevel())
            return

        # Cache artist_info lookups by case-insensitive current artist name.
        lookup_cache: dict[str, str | None] = {}

        def resolve_main(name: str) -> str | None:
            key = name.casefold()
            if key in lookup_cache:
                return lookup_cache[key]
            info = find_artist_by_name_or_alias(name)
            main = info["name"] if info is not None else None
            lookup_cache[key] = main
            return main

        items: list[dict] = []
        for r in tracks:
            current = (r.get("artist") or "").strip()
            if not current:
                items.append({
                    "track":   r,
                    "current": "",
                    "main":    None,
                    "status":  "missing",
                })
                continue
            main = resolve_main(current)
            if main is None:
                status = "missing"
            elif main == current:
                status = "unchanged"
            else:
                status = "rename"
            items.append({
                "track":   r,
                "current": current,
                "main":    main,
                "status":  status,
            })

        # Keep the visual order: renames first, then missing, then unchanged,
        # but preserve the selection order within each bucket.
        order = {"rename": 0, "missing": 1, "unchanged": 2}
        items.sort(key=lambda it: order[it["status"]])

        dlg = _UseMainArtistDialog(
            self.winfo_toplevel(), items,
            on_search_artist=self._on_search_artist,
        )
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        changed = 0
        errors  = 0
        for it in items:
            if it["status"] != "rename":
                continue
            row        = it["track"]
            new_artist = it["main"]
            path       = row.get("full_path") or ""
            partition  = row.get("partition")
            rel_path   = row.get("rel_path")
            if not path or not partition or not rel_path:
                continue
            title    = row.get("title", "") or ""
            album    = row.get("album", "") or ""
            bitrate  = row.get("bitrate", "") or ""
            file_md5 = row.get("file_md5") or ""
            try:
                if path.lower().endswith(".flac") and os.path.exists(path):
                    flac = FLAC(path)
                    if flac.tags is None:
                        flac.add_tags()
                    flac["artist"] = new_artist
                    flac.save()
                    file_md5 = compute_file_md5(path)
                    if flac.info.bitrate:
                        bitrate = f"{round(flac.info.bitrate / 1000)} kbps"

                upsert_track_info(
                    partition, rel_path,
                    artist=new_artist, title=title, album=album,
                    bitrate=bitrate, file_md5=file_md5,
                )
                self._refresh_result_row(
                    partition, rel_path,
                    new_artist, title, album, bitrate,
                )
                changed += 1
            except Exception as exc:
                errors += 1
                _log.error(f"Use Main Artist failed for {path}: {exc}")

        msg = f"Updated artist tag on {changed} track{'s' if changed != 1 else ''}."
        if errors:
            msg += f" ({errors} error{'s' if errors != 1 else ''} — see log.)"
        self._footer_var.set(msg)
        self._status_var.set(msg)

    # ------------------------------------------------------------------ #
    # Pagination                                                           #
    # ------------------------------------------------------------------ #

    def _total_pages(self) -> int:
        return max(1, -(-len(self._results) // PAGE_SIZE))   # ceiling division

    # ------------------------------------------------------------------ #
    # Quality analysis (Analyze spec)                                      #
    # ------------------------------------------------------------------ #

    def _analyze_selected_quality(self):
        """Run spectral analysis on the currently-selected rows.

        Selected files are processed sequentially on a background thread —
        librosa loads are CPU- and disk-heavy, so we never hold the Tk loop.
        Each completed file updates the Quality column for its row.
        """
        if self._analyze_thread and self._analyze_thread.is_alive():
            return

        sel_iids = list(self.tree.selection())
        if not sel_iids:
            self._footer_var.set("Select one or more tracks first.")
            return

        targets: list[tuple[str, str]] = []   # (iid, full_path)
        for iid in sel_iids:
            row = self._row_for_iid(iid)
            full_path = (row or {}).get("full_path") or ""
            if full_path and os.path.isfile(full_path):
                targets.append((iid, full_path))

        if not targets:
            self._footer_var.set("No analysable files in selection.")
            return

        self._btn_analyze.state(["disabled"])
        self._analyze_progress_var.set(f"0 / {len(targets)}")
        self._footer_var.set(f"Analyzing {len(targets)} track(s)…")

        self._analyze_thread = threading.Thread(
            target=self._analyze_worker, args=(targets,), daemon=True,
        )
        self._analyze_thread.start()

    def _analyze_worker(self, targets: list[tuple[str, str]]):
        from music.audio_analysis_panel import analyze_audio, quality_label

        total = len(targets)
        for idx, (iid, path) in enumerate(targets, start=1):
            try:
                result = analyze_audio(path)
                label = quality_label(result)
                _log.info(
                    "Quality: %s → %s (cutoff=%.0f Hz, sr=%d)",
                    path, label, result["cutoff_hz"], result["sr"],
                )
            except Exception as exc:  # noqa: BLE001
                _log.exception("Quality analysis failed for %s", path)
                label = "error"
            self.after(0, self._on_quality_ready, iid, path, label, idx, total)

        self.after(0, self._on_analyze_done)

    def _on_quality_ready(self, iid: str, full_path: str, label: str,
                          idx: int, total: int):
        self._quality_cache[full_path] = label

        # Mirror onto the result dict so column sorting + re-pagination
        # both see the new value without re-running analysis.
        row = self._row_for_iid(iid)
        if row is not None:
            row["quality"] = label
            partition = row.get("partition") or ""
            rel_path = row.get("rel_path") or ""
            if partition and rel_path and label and label != "error":
                try:
                    update_track_info_quality(partition, rel_path, label)
                except Exception:
                    _log.exception("Failed to persist quality for %s / %s",
                                   partition, rel_path)

        # If the iid is currently shown, patch the visible cell in place.
        if self.tree.exists(iid):
            try:
                self.tree.set(iid, "quality", label)
            except tk.TclError:
                pass

        self._analyze_progress_var.set(f"{idx} / {total}")

    def _on_analyze_done(self):
        self._btn_analyze.state(["!disabled"])
        self._footer_var.set("Analysis complete.")
        # Briefly leave the progress counter visible, then clear it.
        self.after(2500, lambda: self._analyze_progress_var.set(""))

    def _row_for_iid(self, iid: str) -> dict | None:
        """Look up the result-set dict backing a Treeview row."""
        if not iid.startswith("ti_"):
            return None
        try:
            ti_id = int(iid.split("_", 1)[1])
        except ValueError:
            return None
        for row in self._results:
            if row.get("id") == ti_id:
                return row
        return None

    # ------------------------------------------------------------------ #
    # Pagination                                                           #
    # ------------------------------------------------------------------ #

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._show_page()

    def _next_page(self):
        if self._page < self._total_pages() - 1:
            self._page += 1
            self._show_page()

    def _show_page(self):
        start     = self._page * PAGE_SIZE
        page_rows = self._results[start : start + PAGE_SIZE]

        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(page_rows):
            self.tree.insert(
                "", "end",
                iid=f"ti_{row['id']}",   # embed track_info id for fast lookup
                values=(
                    _rank_emoji(row.get("ranking", 0)),
                    row.get("partition",  "") or "",
                    row.get("full_path",  "") or "",
                    row.get("artist",     "") or "",
                    row.get("title",      "") or "",
                    row.get("album",      "") or "",
                    row.get("bitrate",    "") or "",
                    self._quality_cache.get(row.get("full_path", ""), "")
                        or row.get("quality", "") or "",
                    row.get("updated_at", "") or "",
                ),
                tags=("odd" if i % 2 == 0 else "even",),
            )

        self._update_pagination_controls()

    def _update_pagination_controls(self):
        total      = self._total_pages()
        has_results = bool(self._results)

        self._btn_prev.state(["!disabled"] if self._page > 0 else ["disabled"])
        self._btn_next.state(["!disabled"] if self._page < total - 1 else ["disabled"])

        if has_results:
            start = self._page * PAGE_SIZE + 1
            end   = min(start + PAGE_SIZE - 1, len(self._results))
            self._page_var.set(
                f"Page {self._page + 1} of {total}  ({start}–{end} of {len(self._results)})"
            )
        else:
            self._page_var.set("")

    # ------------------------------------------------------------------ #
    # Column sorting                                                       #
    # ------------------------------------------------------------------ #

    _KEY_MAP = {
        "rank":      "ranking",
        "partition": "partition", "rel_path": "full_path",
        "artist": "artist",       "title":    "title",
        "album":  "album",        "bitrate":  "bitrate",
        "quality": "quality",
        "updated": "updated_at",
    }

    def _sort_column(self, col: str):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False

        dict_key = self._KEY_MAP.get(col, col)
        if dict_key == "ranking":
            self._results.sort(
                key=lambda r: int(r.get("ranking") or 0),
                reverse=self._sort_rev,
            )
        else:
            self._results.sort(
                key=lambda r: (r.get(dict_key) or "").lower(),
                reverse=self._sort_rev,
            )

        self._page = 0
        self._show_page()

        arrow = " ▲" if not self._sort_rev else " ▼"
        for col_id, label, *_ in self._COL_DEFS:
            self.tree.heading(col_id, text=label + (arrow if col_id == col else ""))

    def _reset_headings(self):
        for col_id, label, *_ in self._COL_DEFS:
            self.tree.heading(col_id, text=label)
