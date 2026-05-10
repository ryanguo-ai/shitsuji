"""
Search In Lib tab — fuzzy search across the music library inventory.
"""

import difflib
import os
import tkinter as tk
from tkinter import ttk

from mutagen.flac import FLAC

from panels.audio_details_panel import AudioDetailsPanel
from panels.audio_menu import AudioMenuMixin
from panels.database import compute_file_md5, get_track_info, upsert_track_info
from panels.keyboard_selection import attach_keyboard_range_selection
from panels.logger import get_logger
from panels.settings_panel import load_settings, save_settings

PAGE_SIZE = 100

_log = get_logger("search")


def _fuzzy_match(query: str, target: str, threshold: float = 0.5) -> bool:
    """Return True if *query* fuzzy-matches *target* (empty query matches everything)."""
    if not query:
        return True
    q = query.strip().lower()
    t = target.strip().lower()
    if q in t:          # substring always counts as a match
        return True
    return difflib.SequenceMatcher(None, q, t).ratio() >= threshold


class SearchTab(tk.Frame, AudioMenuMixin):

    # (col_id, heading_label, width, anchor, stretch)
    _COL_DEFS = [
        ("partition", "Partition", 90,  tk.W, False),
        ("rel_path",  "Full Path", 290, tk.W, True),
        ("artist",    "Artist",    150, tk.W, False),
        ("title",     "Title",     180, tk.W, False),
        ("album",     "Album",     155, tk.W, False),
        ("bitrate",   "Bitrate",   70,  tk.E, False),
        ("updated",   "Updated",   130, tk.W, False),
    ]

    def __init__(self, master):
        super().__init__(master, bg="#f5f5f5")
        self._settings = load_settings()
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

        ttk.Button(inp, text="Search", command=self._search).pack(side=tk.LEFT)
        ttk.Button(inp, text="Clear",  command=self._clear).pack(side=tk.LEFT, padx=(6, 0))

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

        self._status_var.set("Searching…")
        self.update_idletasks()

        self._results = []
        lib_paths = self._settings.get("music_lib_paths", {})
        for row in get_track_info():
            if (    _fuzzy_match(artist_q, row["artist"] or "")
                and _fuzzy_match(title_q,  row["title"]  or "")):
                d = dict(row)
                lib_root = lib_paths.get(d["partition"], "")
                d["full_path"] = (
                    os.path.join(lib_root, d["partition"], d["rel_path"])
                    if lib_root else d["rel_path"]
                )
                self._results.append(d)

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

    # ------------------------------------------------------------------ #
    # Row selection → detail panel                                         #
    # ------------------------------------------------------------------ #

    def _on_row_select(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        values    = self.tree.item(selected[0], "values")
        partition = values[0]
        full_path = values[1]
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

    def _on_row_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return

        if item not in self.tree.selection():
            self.tree.selection_set(item)

        selected = self.tree.selection()
        # values[1] is the full path in the Search result table
        paths = [self.tree.item(i, "values")[1] for i in selected]

        menu = self._build_audio_context_menu(paths)
        menu.tk_popup(event.x_root, event.y_root)

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
        for row in self._results:
            if row.get("partition") == partition and row.get("rel_path") == rel_path:
                row.update(artist=artist, title=title, album=album,
                           bitrate=bitrate, updated_at=updated)
                full_path = row.get("full_path", "")
                break

        for iid in self.tree.get_children():
            vals = self.tree.item(iid, "values")
            if vals[0] == partition and vals[1] == full_path:
                self.tree.item(iid, values=(
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
                values=(
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
