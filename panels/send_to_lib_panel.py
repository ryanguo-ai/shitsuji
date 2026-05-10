"""
Send to Lib — confirmation panel.

Shows a preview of every file's computed destination path before any changes
are made.  Destination rel_path structure:

    {artist_first_word}/{artist}/{artist} - {album}/{artist} - {title}.{ext}

Example:
    Cyndi/Cyndi Lauper/Cyndi Lauper - She's So Unusual/
        Cyndi Lauper - Girls Just Want to Have Fun.flac
"""

import io
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


def _is_cjk(ch: str) -> bool:
    """Return True if *ch* is a CJK / East-Asian ideograph or syllable."""
    cp = ord(ch)
    return (
        0x2E80  <= cp <= 0x2EFF  or   # CJK Radicals Supplement
        0x3000  <= cp <= 0x9FFF  or   # CJK Unified Ideographs (+ kana, hangul intro)
        0xA000  <= cp <= 0xA4CF  or   # Yi Syllables / Radicals
        0xAC00  <= cp <= 0xD7AF  or   # Hangul Syllables
        0xF900  <= cp <= 0xFAFF  or   # CJK Compatibility Ideographs
        0x20000 <= cp <= 0x2A6DF       # CJK Extension B–F
    )


def _first_word(artist: str) -> str:
    """
    Return the index token used as the top-level folder for an artist.

    - ASCII / Western names  → first whitespace-separated word  (e.g. "Cyndi")
    - CJK / East-Asian names → first character only             (e.g. "张")
    """
    name = artist.strip()
    if not name:
        return "Unknown"
    if _is_cjk(name[0]):
        return name[0]
    parts = name.split()
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


def compute_dest_full_path(lib_root: str, partition: str,
                           artist: str, album: str, title: str, ext: str) -> str:
    """
    Build the full absolute destination path for a track.

    Structure:
        {lib_root}/{partition}/{first_word}/{artist}/{artist} - {album}/{artist} - {title}.{ext}

    Example:
        C:/_MUSIC_LIB/POP/Cyndi/Cyndi Lauper/Cyndi Lauper - She's So Unusual/
            Cyndi Lauper - Girls Just Want to Have Fun.flac
    """
    rel = compute_dest_rel_path(artist, album, title, ext)
    return os.path.join(lib_root, partition, rel)


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


def _check_lib_ready(path: str) -> bool:
    """Return True if ARTIST/TITLE/ALBUM tags are present AND cover > 300×300."""
    try:
        from PIL import Image
        flac = FLAC(path)
        tags = flac.tags or {}
        if not (tags.get("artist", [""])[0] and
                tags.get("title",  [""])[0] and
                tags.get("album",  [""])[0]):
            return False
        for pic in flac.pictures:
            img = Image.open(io.BytesIO(pic.data))
            if img.width >= 300 and img.height >= 300:
                return True
        return False
    except Exception:
        return False


def _build_lib_index() -> tuple[set[str], set[tuple[str, str, str]]]:
    """Return (md5_set, aat_set) from track_info for In Lib matching."""
    from panels.database import get_track_info
    md5_set: set[str] = set()
    aat_set: set[tuple[str, str, str]] = set()
    for row in get_track_info():
        if row["file_md5"]:
            md5_set.add(row["file_md5"].strip().lower())
        aat_set.add((
            (row["artist"] or "").strip().lower(),
            (row["title"]  or "").strip().lower(),
            (row["album"]  or "").strip().lower(),
        ))
    return md5_set, aat_set


def _inlib_status(path: str, artist: str, title: str, album: str,
                  md5_set: set[str],
                  aat_set: set[tuple[str, str, str]]) -> str:
    """Return 🟢 / 🟡 / ⬛ In Lib indicator for one file."""
    from panels.database import compute_file_md5
    try:
        md5 = compute_file_md5(path).lower()
    except Exception:
        md5 = ""
    if md5 and md5 in md5_set:
        return "🟢"
    key = (artist.strip().lower(), title.strip().lower(), album.strip().lower())
    if key[0] and key[1] and key in aat_set:
        return "🟡"
    return "⬛"


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

        cols = ("libready", "inlib", "src", "artist", "album", "title", "dest")
        self._tree = ttk.Treeview(
            tree_frm, columns=cols, show="headings", selectmode="extended",
        )
        headings = {
            "libready": ("Lib Ready", 70),
            "inlib":    ("In Lib",    55),
            "src":      ("Source file",        200),
            "artist":   ("Artist",             130),
            "album":    ("Album",              140),
            "title":    ("Title",              140),
            "dest":     ("→ Destination path", 300),
        }
        for col, (label, width) in headings.items():
            self._tree.heading(col, text=label, anchor=tk.CENTER if col in ("libready", "inlib") else tk.W)
            stretch = col in ("src", "dest")
            anchor  = tk.CENTER if col in ("libready", "inlib") else tk.W
            self._tree.column(col, width=width, stretch=stretch, anchor=anchor)

        vsb = ttk.Scrollbar(tree_frm, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frm, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.tag_configure("odd",      background="#ffffff")
        self._tree.tag_configure("even",     background="#f8f9fa")
        self._tree.tag_configure("warn",     background="#fff8e1", foreground="#856404")
        self._tree.tag_configure("notready", background="#fde8e8", foreground="#922b21")
        self._tree.tag_configure("inlib",    background="#fef3e2", foreground="#7d4e00")

        self._tree.bind("<Delete>", lambda _: self._remove_selected())

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
        ttk.Button(
            bar, text="🗑  Remove Selected",
            command=self._remove_selected,
        ).pack(side=tk.LEFT)
        ttk.Button(
            bar, text="⚠  Remove Blocking Rows",
            command=self._remove_blocking,
        ).pack(side=tk.LEFT, padx=(6, 0))

    # ------------------------------------------------------------------ #
    # Data                                                                 #
    # ------------------------------------------------------------------ #

    def _populate(self):
        self._tree.delete(*self._tree.get_children())
        self._status.set("Checking tracks…")
        self.update_idletasks()

        md5_set, aat_set = _build_lib_index()

        not_ready   = 0
        already_lib = 0
        for i, src in enumerate(self._paths):
            ext    = os.path.splitext(src)[1]
            fname  = os.path.basename(src)
            artist, album, title = _read_tags(src)

            ready  = _check_lib_ready(src)
            inlib  = _inlib_status(src, artist, title, album, md5_set, aat_set)

            ready_icon = "✅" if ready else "❌"
            if not ready:
                not_ready += 1
            if inlib in ("🟢", "🟡"):
                already_lib += 1

            dest_full = compute_dest_full_path(
                self._lib_root, self._partition, artist, album, title, ext
            )

            if not ready:
                row_tag = "notready"
            elif inlib in ("🟢", "🟡"):
                row_tag = "inlib"
            elif not artist or not title:
                row_tag = "warn"
            else:
                row_tag = "odd" if i % 2 == 0 else "even"

            self._tree.insert(
                "", "end",
                iid=src,
                values=(ready_icon, inlib, fname, artist, album, title, dest_full),
                tags=(row_tag,),
            )
            self._log.info(f"Preview: {fname!r} → {dest_full}")

        self._update_status(not_ready, already_lib)

    def _update_status(self, not_ready: int | None = None, already_lib: int | None = None):
        """Refresh the status label and Confirm button state."""
        if not_ready is None:
            not_ready = sum(
                1 for iid in self._tree.get_children()
                if "notready" in self._tree.item(iid, "tags")
            )
        if already_lib is None:
            already_lib = sum(
                1 for iid in self._tree.get_children()
                if "inlib" in self._tree.item(iid, "tags")
            )
        n = len(self._paths)
        self.title(f"Send to Lib — {self._partition} ({n} file{'s' if n != 1 else ''})")

        if n == 0:
            self._status.set("No files remaining.")
            self._confirm_btn.configure(state="disabled")
            return

        problems = []
        if not_ready:
            problems.append(
                f"{not_ready} file{'s' if not_ready != 1 else ''} not Lib Ready (❌) — "
                "fix tags/cover art first"
            )
        if already_lib:
            problems.append(
                f"{already_lib} file{'s' if already_lib != 1 else ''} already in Lib (🟢/🟡) — "
                "remove or use Compare Tracks to update"
            )

        if problems:
            self._status.set("⛔  " + "   |   ".join(problems))
            self._confirm_btn.configure(state="disabled")
        else:
            self._status.set(
                f"{n} file{'s' if n != 1 else ''} ready to send to {self._partition}."
            )
            self._confirm_btn.configure(state="normal")

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _remove_selected(self):
        """Remove the selected rows from the preview list (does not touch disk)."""
        selected = self._tree.selection()
        if not selected:
            return
        for iid in selected:
            self._paths.remove(iid)   # iid == full source path
            self._tree.delete(iid)

        # Re-stripe remaining rows (preserve notready/warn tags)
        for i, iid in enumerate(self._tree.get_children()):
            tags = self._tree.item(iid, "tags")
            if not any(t in tags for t in ("notready", "warn")):
                self._tree.item(iid, tags=("odd" if i % 2 == 0 else "even",))

        self._update_status()

    def _remove_blocking(self):
        """Remove all rows that block confirmation (not ready or already in lib)."""
        blocking = [
            iid for iid in self._tree.get_children()
            if any(t in self._tree.item(iid, "tags") for t in ("notready", "inlib"))
        ]
        if not blocking:
            return
        for iid in blocking:
            self._paths.remove(iid)
            self._tree.delete(iid)

        # Re-stripe remaining rows
        for i, iid in enumerate(self._tree.get_children()):
            tags = self._tree.item(iid, "tags")
            if not any(t in tags for t in ("notready", "inlib", "warn")):
                self._tree.item(iid, tags=("odd" if i % 2 == 0 else "even",))

        self._update_status()

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
