"""
Search In Lib tab — fuzzy search across the music library inventory.
"""

import difflib
import os
import tkinter as tk
from tkinter import ttk

from mutagen.flac import FLAC

from music.audio_details_panel import AudioDetailsPanel
from music.audio_menu import AudioMenuMixin
from music.database import (
    compute_file_md5, delete_track, delete_track_info,
    get_artist_name_variants, get_track_info, upsert_track_info, set_track_ranking,
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

        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(
            side=tk.RIGHT, padx=(4, 0))
        del_label = f"🗑  Delete {n} Track{'s' if n != 1 else ''}"
        ttk.Button(
            btn_frame, text=del_label,
            command=self._confirm,
        ).pack(side=tk.RIGHT)

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

        ttk.Button(inp, text="Search", command=self._search).pack(side=tk.LEFT)
        ttk.Button(inp, text="Clear",  command=self._clear).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(
            inp, text="🎲 Random song list", command=self._random_song_list,
        ).pack(side=tk.LEFT, padx=(6, 0))

        # Rating filter
        tk.Label(inp, text="Rating:", font=("Segoe UI", 9), bg="#f5f5f5").pack(
            side=tk.LEFT, padx=(20, 4))
        self._rank_filter_var = tk.StringVar(value=_RANK_FILTER_LABELS[0])
        rank_cb = ttk.Combobox(
            inp, textvariable=self._rank_filter_var,
            values=_RANK_FILTER_LABELS, state="readonly", width=11,
        )
        rank_cb.pack(side=tk.LEFT)
        rank_cb.bind("<<ComboboxSelected>>", lambda _: self._search())

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
        for row in get_track_info():
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
            if flac_paths:
                menu.add_command(
                    label="🖼  Embed Cover Art from Clipboard",
                    command=lambda fp=flac_paths: self._paste_cover_art_to_selected(fp),
                )
                menu.add_separator()

            # ── Delete tracks ── #
            menu.add_separator()
            n_sel = len(selected)
            menu.add_command(
                label=f"🗑  Delete {'Track' if n_sel == 1 else f'{n_sel} Tracks'} from Library…",
                command=lambda iids=selected: self._delete_selected_tracks(iids),
            )

        menu = self._build_audio_context_menu(paths, extra_items_fn=extra)
        menu.tk_popup(event.x_root, event.y_root)

    def _paste_cover_art_to_selected(self, flac_paths: list[str]) -> None:
        """Read an image from the clipboard, show a preview dialog, then embed on confirm."""
        from PIL import ImageGrab, Image
        from mutagen.flac import FLAC, Picture
        import io as _io
        from music.folder_scanner import _PasteCoverArtDialog

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

        dlg = _PasteCoverArtDialog(self, img, img_bytes, mime, len(flac_paths))
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        log = get_logger("search_paste_cover")
        errors: list[str] = []
        for path in flac_paths:
            try:
                flac = FLAC(path)
                other_pictures = [p for p in flac.pictures if p.type != 3]
                flac.clear_pictures()
                for p in other_pictures:
                    flac.add_picture(p)
                pic = Picture()
                pic.type = 3
                pic.mime = mime
                pic.desc = "Front Cover"
                pic.data = img_bytes
                pic.width = img.width
                pic.height = img.height
                pic.depth = max(len(img.getbands()), 1) * 8
                flac.add_picture(pic)
                flac.save()
                log.info("Embedded cover art: %s", path)
            except Exception as exc:
                log.error("Failed to embed cover art into %s: %s", path, exc, exc_info=True)
                errors.append(f"{os.path.basename(path)}: {exc}")

        from tkinter import messagebox
        n = len(flac_paths)
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
    # Pagination                                                           #
    # ------------------------------------------------------------------ #

    def _total_pages(self) -> int:
        return max(1, -(-len(self._results) // PAGE_SIZE))   # ceiling division

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
