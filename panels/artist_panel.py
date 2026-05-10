"""
Artist Info tab — manage a local artist directory linked to MusicBrainz.

Layout
------
Top bar     : title
Search row  : query entry + [Search MusicBrainz] + [Add Manually]
PanedWindow :
  Left pane  : artist list Treeview (Name / Sort Name / Country / MB-ID)
  Right pane : alias list Treeview + [Add Alias] / [Remove Alias]
               + artist detail edit form + [Save Changes]
Bottom row  : [Delete Artist]  status label
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from panels.database import (
    add_alias,
    delete_alias,
    delete_artist,
    get_aliases,
    get_all_artists,
    update_artist,
    upsert_artist,
)
from panels.keyboard_selection import attach_keyboard_range_selection
from panels.logger import get_logger
from panels.musicbrainz_client import MusicBrainzError, parse_artist, search_artists

_log = get_logger("artist_panel")


# ===================================================================== #
# MusicBrainz search dialog                                             #
# ===================================================================== #

class _MBSearchDialog(tk.Toplevel):
    """
    Modal dialog: type a query, browse MusicBrainz results, pick one.

    Accessible via ``.result`` after the dialog is destroyed:
    ``None`` if cancelled, otherwise a parsed-artist dict.
    """

    def __init__(self, parent: tk.Widget, initial_query: str = "") -> None:
        super().__init__(parent)
        self.title("Search MusicBrainz")
        self.resizable(True, True)
        self.minsize(640, 380)
        self.grab_set()
        self.configure(bg="#f5f5f5")

        self.result: dict | None = None
        self._raw_results: list[dict] = []

        self._build(initial_query)
        self._center(parent)

        if initial_query.strip():
            self.after(100, self._do_search)

    # ------------------------------------------------------------------ #

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        pw, ph = self.winfo_width(), self.winfo_height()
        try:
            rx = parent.winfo_rootx() + parent.winfo_width()  // 2
            ry = parent.winfo_rooty() + parent.winfo_height() // 2
        except Exception:
            rx, ry = 400, 300
        self.geometry(f"{pw}x{ph}+{max(0, rx - pw // 2)}+{max(0, ry - ph // 2)}")

    def _build(self, initial_query: str) -> None:
        # ── Query row ──────────────────────────────────────────────────── #
        qrow = tk.Frame(self, bg="#f5f5f5", padx=12, pady=10)
        qrow.pack(fill=tk.X)

        tk.Label(qrow, text="Artist:", font=("Segoe UI", 9), bg="#f5f5f5").pack(side=tk.LEFT)
        self._query_var = tk.StringVar(value=initial_query)
        entry = ttk.Entry(qrow, textvariable=self._query_var, width=36)
        entry.pack(side=tk.LEFT, padx=(6, 10))
        entry.bind("<Return>", lambda _: self._do_search())
        entry.focus_set()

        ttk.Button(qrow, text="Search", command=self._do_search).pack(side=tk.LEFT)

        self._status_var = tk.StringVar(value="")
        tk.Label(
            qrow, textvariable=self._status_var,
            font=("Segoe UI", 9, "italic"), fg="#7f8c8d", bg="#f5f5f5",
        ).pack(side=tk.LEFT, padx=(12, 0))

        # ── Results tree ───────────────────────────────────────────────── #
        tree_frame = tk.Frame(self, bg="#f5f5f5", padx=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("score", "name", "sort_name", "country", "mb_id")
        self._tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings",
            selectmode="browse", height=12,
        )
        self._tree.heading("score",     text="Score",     anchor=tk.E)
        self._tree.heading("name",      text="Name",      anchor=tk.W)
        self._tree.heading("sort_name", text="Sort Name", anchor=tk.W)
        self._tree.heading("country",   text="Country",   anchor=tk.CENTER)
        self._tree.heading("mb_id",     text="MB ID",     anchor=tk.W)

        self._tree.column("score",     width=50,  anchor=tk.E,      stretch=False)
        self._tree.column("name",      width=180, anchor=tk.W,      stretch=True)
        self._tree.column("sort_name", width=160, anchor=tk.W,      stretch=False)
        self._tree.column("country",   width=60,  anchor=tk.CENTER, stretch=False)
        self._tree.column("mb_id",     width=120, anchor=tk.W,      stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.bind("<Double-1>", lambda _: self._on_ok())
        attach_keyboard_range_selection(self._tree)

        # ── Buttons ────────────────────────────────────────────────────── #
        brow = tk.Frame(self, bg="#f5f5f5", padx=12, pady=10)
        brow.pack(fill=tk.X)

        ttk.Button(brow, text="Import Selected", command=self._on_ok).pack(side=tk.LEFT)
        ttk.Button(brow, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=(8, 0))

    # ------------------------------------------------------------------ #

    def _do_search(self) -> None:
        query = self._query_var.get().strip()
        if not query:
            return
        self._status_var.set("Searching…")
        self._tree.delete(*self._tree.get_children())
        self.update_idletasks()

        def _worker():
            try:
                raw = search_artists(query)
                parsed = [parse_artist(a) for a in raw]
            except MusicBrainzError as exc:
                self.after(0, lambda: self._status_var.set(f"Error: {exc}"))
                return
            self.after(0, lambda: self._populate(parsed))

        threading.Thread(target=_worker, daemon=True).start()

    def _populate(self, artists: list[dict]) -> None:
        self._raw_results = artists
        self._tree.delete(*self._tree.get_children())
        for a in artists:
            self._tree.insert(
                "", "end",
                values=(
                    a["score"],
                    a["name"],
                    a["sort_name"],
                    a["country"],
                    a["musicbrainz_id"],
                ),
            )
        count = len(artists)
        self._status_var.set(f"{count} result{'s' if count != 1 else ''} found.")

    def _on_ok(self) -> None:
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning("No selection", "Please select an artist.", parent=self)
            return
        idx = self._tree.index(sel[0])
        self.result = self._raw_results[idx]
        self.destroy()


# ===================================================================== #
# Add-alias dialog                                                       #
# ===================================================================== #

class _AddAliasDialog(tk.Toplevel):
    """Small modal for entering a new alias."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self.title("Add Alias")
        self.resizable(False, False)
        self.grab_set()
        self.configure(bg="#f5f5f5")
        self.result: tuple[str, str, str] | None = None   # (alias, locale, type)
        self._build()
        self._center(parent)

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        pw, ph = self.winfo_width(), self.winfo_height()
        try:
            rx = parent.winfo_rootx() + parent.winfo_width()  // 2
            ry = parent.winfo_rooty() + parent.winfo_height() // 2
        except Exception:
            rx, ry = 400, 300
        self.geometry(f"{pw}x{ph}+{max(0, rx - pw // 2)}+{max(0, ry - ph // 2)}")

    def _build(self) -> None:
        frm = tk.Frame(self, bg="#f5f5f5", padx=16, pady=14)
        frm.pack(fill=tk.BOTH, expand=True)

        def row(label: str, row_idx: int) -> ttk.Entry:
            tk.Label(frm, text=label, font=("Segoe UI", 9),
                     bg="#f5f5f5", anchor="w").grid(
                row=row_idx, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            entry = ttk.Entry(frm, textvariable=var, width=30)
            entry.grid(row=row_idx, column=1, padx=(8, 0), pady=4)
            return entry

        self._alias_entry  = row("Alias:",  0)
        self._locale_entry = row("Locale:", 1)
        self._type_entry   = row("Type:",   2)

        self._alias_entry.focus_set()
        self._alias_entry.bind("<Return>", lambda _: self._ok())

        brow = tk.Frame(frm, bg="#f5f5f5")
        brow.grid(row=3, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(brow, text="Add",    command=self._ok).pack(side=tk.LEFT)
        ttk.Button(brow, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=6)

    def _ok(self) -> None:
        alias = self._alias_entry.get().strip()
        if not alias:
            messagebox.showwarning("Empty alias", "Alias text cannot be empty.", parent=self)
            return
        self.result = (alias, self._locale_entry.get().strip(), self._type_entry.get().strip())
        self.destroy()


# ===================================================================== #
# Main ArtistTab                                                         #
# ===================================================================== #

class ArtistTab(tk.Frame):
    """Tab for browsing, creating, and updating artist records."""

    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master, bg="#f5f5f5")
        self._selected_artist_id: int | None = None
        self._build_ui()
        self._refresh_artist_list()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        # ── Top bar ──────────────────────────────────────────────────── #
        top = tk.Frame(self, bg="#2c3e50", pady=12, padx=16)
        top.pack(fill=tk.X)
        tk.Label(
            top, text="👤  Artist Info",
            font=("Segoe UI", 16, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # ── Action row ───────────────────────────────────────────────── #
        act = tk.Frame(self, bg="#f5f5f5", pady=8, padx=16)
        act.pack(fill=tk.X)

        tk.Label(act, text="Artist:", font=("Segoe UI", 9), bg="#f5f5f5").pack(side=tk.LEFT)
        self._search_var = tk.StringVar()
        entry = ttk.Entry(act, textvariable=self._search_var, width=28)
        entry.pack(side=tk.LEFT, padx=(4, 10))
        entry.bind("<Return>", lambda _: self._on_mb_search())

        ttk.Button(
            act, text="🔍 Search MusicBrainz", command=self._on_mb_search,
        ).pack(side=tk.LEFT)
        ttk.Button(
            act, text="+ Add Manually", command=self._on_add_manual,
        ).pack(side=tk.LEFT, padx=(8, 0))

        self._status_var = tk.StringVar(value="")
        tk.Label(
            act, textvariable=self._status_var,
            font=("Segoe UI", 9, "italic"), fg="#7f8c8d", bg="#f5f5f5",
        ).pack(side=tk.LEFT, padx=(14, 0))

        # ── PanedWindow ──────────────────────────────────────────────── #
        paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, bg="#d0d3d4",
            sashrelief=tk.FLAT, sashwidth=5,
        )
        paned.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))

        # Left pane — artist list
        left = tk.Frame(paned, bg="#f5f5f5")
        paned.add(left, stretch="always", minsize=340)
        self._build_artist_list(left)

        # Right pane — detail / aliases
        right = tk.Frame(paned, bg="#f5f5f5")
        paned.add(right, stretch="always", minsize=300)
        self._build_detail_pane(right)

        # ── Bottom status bar ─────────────────────────────────────────── #
        bar = tk.Frame(self, bg="#bdc3c7", height=24)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self._footer_var = tk.StringVar(value="Ready.")
        tk.Label(
            bar, textvariable=self._footer_var,
            font=("Segoe UI", 9), bg="#bdc3c7",
            anchor="w", padx=8,
        ).pack(fill=tk.X)

    def _build_artist_list(self, parent: tk.Frame) -> None:
        tk.Label(
            parent, text="Artists", font=("Segoe UI", 10, "bold"),
            bg="#f5f5f5", anchor="w",
        ).pack(fill=tk.X, padx=4, pady=(6, 2))

        tree_frame = tk.Frame(parent, bg="#f5f5f5")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("name", "sort_name", "country", "mb_id")
        self._artist_tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings",
            selectmode="browse",
        )
        self._artist_tree.heading("name",      text="Name",      anchor=tk.W)
        self._artist_tree.heading("sort_name", text="Sort Name", anchor=tk.W)
        self._artist_tree.heading("country",   text="Country",   anchor=tk.CENTER)
        self._artist_tree.heading("mb_id",     text="MB ID",     anchor=tk.W)

        self._artist_tree.column("name",      width=160, stretch=True)
        self._artist_tree.column("sort_name", width=140, stretch=False)
        self._artist_tree.column("country",   width=60,  anchor=tk.CENTER, stretch=False)
        self._artist_tree.column("mb_id",     width=110, stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._artist_tree.yview)
        self._artist_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._artist_tree.pack(fill=tk.BOTH, expand=True)

        self._artist_tree.tag_configure("odd",  background="#ffffff")
        self._artist_tree.tag_configure("even", background="#ecf0f1")

        self._artist_tree.bind("<<TreeviewSelect>>", self._on_artist_select)
        attach_keyboard_range_selection(self._artist_tree)

        # Delete button below list
        btn_row = tk.Frame(parent, bg="#f5f5f5", pady=4)
        btn_row.pack(fill=tk.X)
        ttk.Button(
            btn_row, text="🗑 Delete Artist", command=self._on_delete_artist,
        ).pack(side=tk.LEFT, padx=4)

    def _build_detail_pane(self, parent: tk.Frame) -> None:
        # ── Edit form ────────────────────────────────────────────────── #
        form = tk.LabelFrame(
            parent, text="Artist Details",
            font=("Segoe UI", 9, "bold"),
            bg="#f5f5f5", padx=10, pady=8,
        )
        form.pack(fill=tk.X, padx=6, pady=(6, 4))

        def _field(label: str, row: int, width: int = 30) -> ttk.Entry:
            tk.Label(form, text=label, font=("Segoe UI", 9),
                     bg="#f5f5f5", anchor="w").grid(
                row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar()
            e = ttk.Entry(form, textvariable=var, width=width)
            e.grid(row=row, column=1, padx=(8, 0), pady=3, sticky="ew")
            form.columnconfigure(1, weight=1)
            return e

        self._name_var      = tk.StringVar()
        self._sort_name_var = tk.StringVar()
        self._country_var   = tk.StringVar()
        self._mb_id_var     = tk.StringVar()

        def _fld(label: str, var: tk.StringVar, row: int) -> None:
            tk.Label(form, text=label, font=("Segoe UI", 9),
                     bg="#f5f5f5", anchor="w").grid(
                row=row, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=var, width=30).grid(
                row=row, column=1, padx=(8, 0), pady=3, sticky="ew")

        _fld("Name:",         self._name_var,      0)
        _fld("Sort Name:",    self._sort_name_var,  1)
        _fld("Country:",      self._country_var,    2)
        _fld("MusicBrainz ID:", self._mb_id_var,   3)

        ttk.Button(
            form, text="💾 Save Changes", command=self._on_save_artist,
        ).grid(row=4, column=1, sticky="e", pady=(6, 0))

        # ── Aliases ──────────────────────────────────────────────────── #
        alias_frame = tk.LabelFrame(
            parent, text="Aliases",
            font=("Segoe UI", 9, "bold"),
            bg="#f5f5f5", padx=6, pady=6,
        )
        alias_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        alias_tree_frame = tk.Frame(alias_frame, bg="#f5f5f5")
        alias_tree_frame.pack(fill=tk.BOTH, expand=True)

        a_cols = ("alias", "locale", "type")
        self._alias_tree = ttk.Treeview(
            alias_tree_frame, columns=a_cols, show="headings",
            selectmode="browse", height=8,
        )
        self._alias_tree.heading("alias",  text="Alias",  anchor=tk.W)
        self._alias_tree.heading("locale", text="Locale", anchor=tk.W)
        self._alias_tree.heading("type",   text="Type",   anchor=tk.W)

        self._alias_tree.column("alias",  width=180, stretch=True)
        self._alias_tree.column("locale", width=60,  stretch=False)
        self._alias_tree.column("type",   width=100, stretch=False)

        a_vsb = ttk.Scrollbar(alias_tree_frame, orient=tk.VERTICAL, command=self._alias_tree.yview)
        self._alias_tree.configure(yscrollcommand=a_vsb.set)
        a_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._alias_tree.pack(fill=tk.BOTH, expand=True)

        self._alias_tree.tag_configure("odd",  background="#ffffff")
        self._alias_tree.tag_configure("even", background="#ecf0f1")

        attach_keyboard_range_selection(self._alias_tree)

        a_btn = tk.Frame(alias_frame, bg="#f5f5f5", pady=4)
        a_btn.pack(fill=tk.X)
        ttk.Button(a_btn, text="+ Add Alias",    command=self._on_add_alias).pack(side=tk.LEFT)
        ttk.Button(a_btn, text="✕ Remove Alias", command=self._on_remove_alias).pack(side=tk.LEFT, padx=(8, 0))

    # ------------------------------------------------------------------ #
    # Data loading                                                         #
    # ------------------------------------------------------------------ #

    def _refresh_artist_list(self) -> None:
        self._artist_tree.delete(*self._artist_tree.get_children())
        artists = get_all_artists()
        for i, a in enumerate(artists):
            self._artist_tree.insert(
                "", "end",
                iid=str(a["id"]),
                values=(
                    a["name"]           or "",
                    a["sort_name"]      or "",
                    a["country"]        or "",
                    a["musicbrainz_id"] or "",
                ),
                tags=("odd" if i % 2 == 0 else "even",),
            )
        count = len(artists)
        self._footer_var.set(f"{count} artist{'s' if count != 1 else ''} in database.")

    def _refresh_alias_list(self, artist_id: int) -> None:
        self._alias_tree.delete(*self._alias_tree.get_children())
        for i, row in enumerate(get_aliases(artist_id)):
            self._alias_tree.insert(
                "", "end",
                iid=str(row["id"]),
                values=(
                    row["alias"]      or "",
                    row["locale"]     or "",
                    row["alias_type"] or "",
                ),
                tags=("odd" if i % 2 == 0 else "even",),
            )

    # ------------------------------------------------------------------ #
    # Event handlers                                                       #
    # ------------------------------------------------------------------ #

    def _on_artist_select(self, _event=None) -> None:
        sel = self._artist_tree.selection()
        if not sel:
            return
        artist_id = int(sel[0])
        self._selected_artist_id = artist_id

        vals = self._artist_tree.item(sel[0], "values")
        self._name_var.set(vals[0])
        self._sort_name_var.set(vals[1])
        self._country_var.set(vals[2])
        self._mb_id_var.set(vals[3])

        self._refresh_alias_list(artist_id)

    def _on_mb_search(self) -> None:
        query = self._search_var.get().strip()
        dlg = _MBSearchDialog(self, initial_query=query)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._import_mb_artist(dlg.result)

    def _import_mb_artist(self, parsed: dict) -> None:
        """Save an artist parsed from MusicBrainz into the local DB."""
        artist_id = upsert_artist(
            name=parsed["name"],
            sort_name=parsed["sort_name"],
            country=parsed["country"],
            musicbrainz_id=parsed["musicbrainz_id"],
        )
        for a in parsed["aliases"]:
            add_alias(artist_id, a["alias"], a.get("locale", ""), a.get("alias_type", ""))
        _log.info(
            f"Imported artist '{parsed['name']}' (mbid={parsed['musicbrainz_id']}) "
            f"with {len(parsed['aliases'])} alias(es)."
        )
        self._refresh_artist_list()
        self._status_var.set(f"Imported: {parsed['name']}")
        # Select the newly imported row
        iid = str(artist_id)
        if iid in self._artist_tree.get_children():
            self._artist_tree.selection_set(iid)
            self._artist_tree.see(iid)
            self._on_artist_select()

    def _on_add_manual(self) -> None:
        name = simpledialog.askstring("Add Artist", "Artist name:", parent=self)
        if not name or not name.strip():
            return
        artist_id = upsert_artist(name=name.strip())
        self._refresh_artist_list()
        iid = str(artist_id)
        if iid in self._artist_tree.get_children():
            self._artist_tree.selection_set(iid)
            self._artist_tree.see(iid)
            self._on_artist_select()
        self._status_var.set(f"Added: {name.strip()}")

    def _on_save_artist(self) -> None:
        if self._selected_artist_id is None:
            messagebox.showwarning("No selection", "Select an artist first.", parent=self)
            return
        update_artist(
            self._selected_artist_id,
            name=self._name_var.get().strip(),
            sort_name=self._sort_name_var.get().strip(),
            country=self._country_var.get().strip(),
            musicbrainz_id=self._mb_id_var.get().strip(),
        )
        self._refresh_artist_list()
        # Re-select
        iid = str(self._selected_artist_id)
        if iid in self._artist_tree.get_children():
            self._artist_tree.selection_set(iid)
            self._artist_tree.see(iid)
        self._status_var.set("Changes saved.")

    def _on_delete_artist(self) -> None:
        if self._selected_artist_id is None:
            messagebox.showwarning("No selection", "Select an artist to delete.", parent=self)
            return
        name = self._name_var.get() or f"ID {self._selected_artist_id}"
        if not messagebox.askyesno(
            "Confirm delete",
            f"Delete artist '{name}' and all their aliases?",
            parent=self,
        ):
            return
        delete_artist(self._selected_artist_id)
        self._selected_artist_id = None
        self._clear_form()
        self._alias_tree.delete(*self._alias_tree.get_children())
        self._refresh_artist_list()
        self._status_var.set(f"Deleted: {name}")

    def _on_add_alias(self) -> None:
        if self._selected_artist_id is None:
            messagebox.showwarning("No selection", "Select an artist first.", parent=self)
            return
        dlg = _AddAliasDialog(self)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        alias, locale, alias_type = dlg.result
        add_alias(self._selected_artist_id, alias, locale, alias_type)
        self._refresh_alias_list(self._selected_artist_id)

    def _on_remove_alias(self) -> None:
        sel = self._alias_tree.selection()
        if not sel:
            messagebox.showwarning("No selection", "Select an alias to remove.", parent=self)
            return
        alias_id = int(sel[0])
        alias_text = self._alias_tree.item(sel[0], "values")[0]
        if not messagebox.askyesno(
            "Confirm remove",
            f"Remove alias '{alias_text}'?",
            parent=self,
        ):
            return
        delete_alias(alias_id)
        if self._selected_artist_id is not None:
            self._refresh_alias_list(self._selected_artist_id)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _clear_form(self) -> None:
        for var in (self._name_var, self._sort_name_var,
                    self._country_var, self._mb_id_var):
            var.set("")
