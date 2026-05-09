"""
Send to Lib — confirmation panel.

Shows a preview of every file's computed destination path before any changes
are made.  Destination rel_path structure:

    {artist_first_word}/{artist}/{artist} - {album}/{artist} - {title}.{ext}

Example:
    Cyndi/Cyndi Lauper/Cyndi Lauper - She's So Unusual/
        Cyndi Lauper - Girls Just Want to Have Fun.flac
"""

import os
import re
import tkinter as tk
from tkinter import ttk

from mutagen.flac import FLAC

from panels.logger import get_logger

# Characters illegal in Windows file/folder names
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')


def _sanitize(name: str) -> str:
    """Strip characters that are illegal in Windows path components."""
    return _ILLEGAL.sub("_", name).strip()


def _first_word(artist: str) -> str:
    """Return the first whitespace-separated token of the artist name."""
    parts = artist.strip().split()
    return parts[0] if parts else "Unknown"


def compute_dest_rel_path(artist: str, album: str, title: str, ext: str) -> str:
    """
    Build the destination relative path for a track.

    Structure:
        {first_word}/{artist}/{artist} - {album}/{artist} - {title}.{ext}
    """
    a  = _sanitize(artist) or "Unknown Artist"
    al = _sanitize(album)  or "Unknown Album"
    t  = _sanitize(title)  or "Unknown Title"
    fw = _sanitize(_first_word(artist)) or "Unknown"
    e  = ext.lower().lstrip(".")

    folder   = os.path.join(fw, a, f"{a} - {al}")
    filename = f"{a} - {t}.{e}"
    return os.path.join(folder, filename)


def _read_tags(path: str) -> tuple[str, str, str]:
    """Return (artist, album, title) from a FLAC file; empty strings on failure."""
    try:
        f = FLAC(path)
        artist = (f.get("artist") or f.get("ARTIST") or [""])[0]
        album  = (f.get("album")  or f.get("ALBUM")  or [""])[0]
        title  = (f.get("title")  or f.get("TITLE")  or [""])[0]
        return artist, album, title
    except Exception:
        return "", "", ""


class SendToLibPanel(tk.Toplevel):
    """
    Modeless confirmation window showing the computed destinations for a set of
    files before they are sent to the music library.

    Parameters
    ----------
    parent    : tk.Widget
    paths     : list of absolute source paths
    partition : target partition label (e.g. "CPOP")
    lib_root  : absolute root folder for this partition (may be empty)
    on_confirm: callable(paths, partition) invoked when the user confirms
    """

    def __init__(
        self,
        parent: tk.Widget,
        paths: list[str],
        partition: str,
        lib_root: str,
        on_confirm,
    ):
        super().__init__(parent)
        self._paths      = list(paths)
        self._partition  = partition
        self._lib_root   = lib_root.rstrip(os.sep)
        self._on_confirm = on_confirm
        self._log        = get_logger("send_to_lib_preview")

        n = len(paths)
        self.title(f"Send to Lib — {partition} ({n} file{'s' if n != 1 else ''})")
        self.configure(bg="#f5f5f5")
        self.minsize(860, 420)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build()
        self._populate()
        self._center()

    # ------------------------------------------------------------------ #
    # UI                                                                   #
    # ------------------------------------------------------------------ #

    def _build(self):
        # ── Header ── #
        hdr = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr, text=f"📂  Send to Lib — {self._partition}",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)
        if self._lib_root:
            tk.Label(
                hdr, text=self._lib_root,
                font=("Segoe UI", 8), fg="#bdc3c7", bg="#2c3e50",
            ).pack(side=tk.RIGHT)

        # ── Info bar ── #
        info = tk.Frame(self, bg="#eaf0fb", pady=4, padx=12)
        info.pack(fill=tk.X)
        tk.Label(
            info,
            text="Review the destination paths below.  "
                 "Click Confirm to proceed or Cancel to abort.",
            font=("Segoe UI", 9), fg="#2c3e50", bg="#eaf0fb",
        ).pack(side=tk.LEFT)

        # ── Treeview ── #
        tree_frm = tk.Frame(self, bg="#f5f5f5")
        tree_frm.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 0))

        cols = ("src", "artist", "album", "title", "dest")
        self._tree = ttk.Treeview(
            tree_frm, columns=cols, show="headings", selectmode="browse",
        )
        headings = {
            "src":    ("Source file",        200),
            "artist": ("Artist",             130),
            "album":  ("Album",              140),
            "title":  ("Title",              140),
            "dest":   ("→ Destination path", 300),
        }
        for col, (label, width) in headings.items():
            self._tree.heading(col, text=label, anchor=tk.W)
            stretch = col in ("src", "dest")
            self._tree.column(col, width=width, stretch=stretch, anchor=tk.W)

        vsb = ttk.Scrollbar(tree_frm, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frm, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.tag_configure("odd",  background="#ffffff")
        self._tree.tag_configure("even", background="#f8f9fa")
        self._tree.tag_configure("warn", background="#fff8e1", foreground="#856404")

        # ── Status bar ── #
        self._status = tk.StringVar()
        tk.Label(
            self, textvariable=self._status,
            font=("Segoe UI", 8), fg="#7f8c8d", bg="#f5f5f5", anchor="w", padx=12,
        ).pack(fill=tk.X, pady=(4, 0))

        # ── Bottom toolbar ── #
        bar = tk.Frame(self, bg="#ecf0f1", pady=8, padx=12)
        bar.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Button(bar, text="Cancel",  command=self.destroy).pack(side=tk.RIGHT)
        self._confirm_btn = ttk.Button(
            bar, text="✔  Confirm",
            command=self._confirm,
        )
        self._confirm_btn.pack(side=tk.RIGHT, padx=(0, 6))

    # ------------------------------------------------------------------ #
    # Data                                                                 #
    # ------------------------------------------------------------------ #

    def _populate(self):
        self._tree.delete(*self._tree.get_children())
        missing_tags = 0

        for i, src in enumerate(self._paths):
            ext    = os.path.splitext(src)[1]
            fname  = os.path.basename(src)
            artist, album, title = _read_tags(src)

            if not (artist and title):
                missing_tags += 1

            dest_rel = compute_dest_rel_path(artist, album, title, ext)

            row_tag = "warn" if not (artist and title) else ("odd" if i % 2 == 0 else "even")
            self._tree.insert(
                "", "end",
                values=(fname, artist, album, title, dest_rel),
                tags=(row_tag,),
            )
            self._log.info(
                f"Preview: {fname!r} → {self._partition}/{dest_rel}"
            )

        n = len(self._paths)
        status = f"{n} file{'s' if n != 1 else ''} ready to send to {self._partition}."
        if missing_tags:
            status += f"  ⚠ {missing_tags} file(s) have missing artist/title tags (shown in amber)."
        self._status.set(status)

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _confirm(self):
        self._log.info(
            f"User confirmed send of {len(self._paths)} file(s) to {self._partition}"
        )
        self.destroy()
        self._on_confirm(self._paths, self._partition)

    # ------------------------------------------------------------------ #
    # Geometry                                                             #
    # ------------------------------------------------------------------ #

    def _center(self):
        self.update_idletasks()
        w  = max(self.winfo_reqwidth(),  900)
        h  = max(self.winfo_reqheight(), 480)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
