"""
Compare Tracks tab — side-by-side comparison of two FLAC tracks.

Loaded when the user right-clicks a 🟡 (metadata-match / different MD5) row in
the Scan tab and chooses "Compare track with Lib".
"""

import io
import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

from mutagen.flac import FLAC


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _load_flac_info(path: str) -> dict:
    """Return a dict with tags, bitrate, duration, and first cover art for *path*."""
    result: dict = {
        "path": path,
        "bitrate": "",
        "duration": "",
        "tags": {},
        "cover": None,        # raw bytes of first picture, or None
        "cover_dims": "",
        "cover_count": 0,
        "cover_size_kb": "",
    }
    if not path or not os.path.isfile(path):
        return result
    try:
        flac = FLAC(path)
        if flac.info:
            br = flac.info.bitrate
            result["bitrate"] = f"{round(br / 1000)} kbps" if br else ""
            secs = int(flac.info.length or 0)
            result["duration"] = f"{secs // 60}:{secs % 60:02d}"
        if flac.tags:
            for k, vlist in flac.tags.as_dict().items():
                result["tags"][k.upper()] = vlist[0] if vlist else ""
        result["cover_count"] = len(flac.pictures)
        for pic in flac.pictures:
            result["cover"] = pic.data
            kb = len(pic.data) / 1024
            result["cover_size_kb"] = f"{kb:.1f} KB"
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(pic.data))
                result["cover_dims"] = f"{img.width}×{img.height}"
            except Exception:
                result["cover_dims"] = "?"
            break          # only need the first picture
    except Exception:
        pass
    return result


# ------------------------------------------------------------------ #
# Tab widget                                                          #
# ------------------------------------------------------------------ #

class CompareTracksTab(tk.Frame):
    """
    Persistent notebook tab.
    Call ``show_comparison(src_path, lib_path)`` to load a comparison.
    """

    def __init__(self, master, settings_getter=None):
        """
        Parameters
        ----------
        settings_getter : callable () → dict
            Should return the current application settings dict so we can read
            the foobar2000 executable path at click-time.
        """
        super().__init__(master, bg="#f5f5f5")
        self._settings_getter = settings_getter
        self._src_path = ""
        self._lib_path = ""
        self._partition = ""
        self._rel_path = ""
        self._src_photo = None   # keep PhotoImage refs alive
        self._lib_photo = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────── #
        top = tk.Frame(self, bg="#2c3e50", pady=12, padx=16)
        top.pack(fill=tk.X)
        tk.Label(
            top, text="🔍  Compare Tracks",
            font=("Segoe UI", 16, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # ── Cover art / header row ────────────────────────────────────── #
        art_row = tk.Frame(self, bg="#f5f5f5", padx=16, pady=8)
        art_row.pack(fill=tk.X)

        # Left column — scan track
        left = tk.Frame(art_row, bg="#f5f5f5")
        left.pack(side=tk.LEFT, fill=tk.X, expand=True, anchor="n")

        tk.Label(left, text="Scan Track", font=("Segoe UI", 10, "bold"),
                 bg="#f5f5f5", fg="#2c3e50").pack(anchor="w")
        self._src_name_var = tk.StringVar(value="—")
        tk.Label(left, textvariable=self._src_name_var,
                 font=("Segoe UI", 9), fg="#555555", bg="#f5f5f5",
                 wraplength=340, justify="left").pack(anchor="w")
        self._src_cover = tk.Label(left, bg="#d5d8dc", text="No cover art",
                                   relief=tk.GROOVE)
        self._src_cover.pack(pady=4, anchor="w")
        self._src_dims_var = tk.StringVar(value="")
        tk.Label(left, textvariable=self._src_dims_var,
                 font=("Segoe UI", 8), fg="#888888", bg="#f5f5f5").pack(anchor="w")

        # Divider
        tk.Frame(art_row, bg="#bdc3c7", width=2).pack(
            side=tk.LEFT, fill=tk.Y, padx=14)

        # Right column — lib track
        right = tk.Frame(art_row, bg="#f5f5f5")
        right.pack(side=tk.LEFT, fill=tk.X, expand=True, anchor="n")

        tk.Label(right, text="Lib Track", font=("Segoe UI", 10, "bold"),
                 bg="#f5f5f5", fg="#2c3e50").pack(anchor="w")
        self._lib_name_var = tk.StringVar(value="—")
        tk.Label(right, textvariable=self._lib_name_var,
                 font=("Segoe UI", 9), fg="#555555", bg="#f5f5f5",
                 wraplength=340, justify="left").pack(anchor="w")
        self._lib_cover = tk.Label(right, bg="#d5d8dc", text="No cover art",
                                   relief=tk.GROOVE)
        self._lib_cover.pack(pady=4, anchor="w")
        self._lib_dims_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self._lib_dims_var,
                 font=("Segoe UI", 8), fg="#888888", bg="#f5f5f5").pack(anchor="w")

        # ── Single play button + update button below both columns ────── #
        play_row = tk.Frame(self, bg="#f5f5f5", padx=16, pady=4)
        play_row.pack(fill=tk.X)
        self._play_btn = ttk.Button(
            play_row, text="▶  Play both tracks in foobar2000",
            state="disabled",
            command=self._play_both,
        )
        self._play_btn.pack(side=tk.LEFT)
        self._update_btn = ttk.Button(
            play_row, text="⬆  Update Lib Track",
            state="disabled",
            command=self._update_lib_track,
        )
        self._update_btn.pack(side=tk.LEFT, padx=(12, 0))

        # ── Comparison table ──────────────────────────────────────────── #
        table_frame = tk.Frame(self, bg="#f5f5f5", padx=16)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        cols = ("property", "scan_value", "lib_value")
        self._tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", selectmode="browse")
        self._tree.heading("property",   text="Property",   anchor=tk.W)
        self._tree.heading("scan_value", text="Scan Track", anchor=tk.W)
        self._tree.heading("lib_value",  text="Lib Track",  anchor=tk.W)
        self._tree.column("property",   width=150, stretch=False)
        self._tree.column("scan_value", width=340, stretch=True)
        self._tree.column("lib_value",  width=340, stretch=True)

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        # Row styles
        self._tree.tag_configure("header", background="#d6eaf8", foreground="#1a5276",
                                 font=("Segoe UI", 9, "bold"))
        self._tree.tag_configure("diff",   background="#fefce8", foreground="#7d6608")
        self._tree.tag_configure("same",   background="#ffffff",  foreground="#1a1a1a")
        self._tree.tag_configure("even",   background="#ecf0f1",  foreground="#1a1a1a")

        # ── Status bar ────────────────────────────────────────────────── #
        bar = tk.Frame(self, bg="#bdc3c7", height=24)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self._status_var = tk.StringVar(
            value="No tracks loaded. Right-click a 🟡 row in the Scan tab.")
        tk.Label(
            bar, textvariable=self._status_var,
            font=("Segoe UI", 9), bg="#bdc3c7",
            anchor="w", padx=8,
        ).pack(fill=tk.X)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def show_comparison(self, src_path: str, lib_path: str,
                        partition: str = "", rel_path: str = ""):
        """Populate the panel with a comparison of *src_path* vs *lib_path*."""
        self._src_path = src_path
        self._lib_path = lib_path
        self._partition = partition
        self._rel_path = rel_path
        self._status_var.set("Loading…")
        self.update_idletasks()

        src_info = _load_flac_info(src_path)
        lib_info = _load_flac_info(lib_path)

        self._src_name_var.set(src_path)
        self._lib_name_var.set(lib_path)

        self._update_cover(self._src_cover, src_info["cover"],
                           src_info["cover_dims"], self._src_dims_var, is_src=True)
        self._update_cover(self._lib_cover, lib_info["cover"],
                           lib_info["cover_dims"], self._lib_dims_var, is_src=False)

        self._play_btn.configure(
            state="normal" if (os.path.isfile(src_path) or os.path.isfile(lib_path))
            else "disabled")
        self._update_btn.configure(
            state="normal" if (os.path.isfile(src_path) and partition and rel_path)
            else "disabled")

        self._populate_table(src_info, lib_info)
        self._status_var.set(
            f"Comparing  Scan: {os.path.basename(src_path)}"
            f"  ↔  Lib: {os.path.basename(lib_path)}"
        )

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _update_cover(self, label: tk.Label, data: bytes | None,
                      dims: str, dims_var: tk.StringVar, *, is_src: bool):
        if data:
            try:
                from PIL import Image, ImageTk
                img = Image.open(io.BytesIO(data))
                img.thumbnail((240, 240))
                photo = ImageTk.PhotoImage(img)
                label.configure(image=photo, text="", bg="#ffffff",
                                width=photo.width(), height=photo.height())
                if is_src:
                    self._src_photo = photo
                else:
                    self._lib_photo = photo
                dims_var.set(dims)
                return
            except Exception:
                pass
        # Fallback: no cover or failed to decode
        if is_src:
            self._src_photo = None
        else:
            self._lib_photo = None
        label.configure(image="", text="No cover art", bg="#d5d8dc",
                        width=20, height=5)
        dims_var.set("")

    def _populate_table(self, src: dict, lib: dict):
        self._tree.delete(*self._tree.get_children())

        def _add_header(label: str):
            self._tree.insert("", "end",
                              values=(f"── {label} ──", "", ""),
                              tags=("header",))

        def _add_row(prop: str, sv: str, lv: str, idx: int):
            differs = (sv != lv)
            tag = "diff" if differs else ("same" if idx % 2 == 0 else "even")
            self._tree.insert("", "end", values=(prop, sv, lv), tags=(tag,))

        # ── Technical ── #
        _add_header("Technical")
        technical = [
            ("Bitrate",      src["bitrate"],                          lib["bitrate"]),
            ("Duration",     src["duration"],                         lib["duration"]),
            ("Image count",  str(src["cover_count"]),                 str(lib["cover_count"])),
            ("Cover art",    src["cover_dims"] or "None",             lib["cover_dims"] or "None"),
            ("Cover size",   src["cover_size_kb"] or "None",          lib["cover_size_kb"] or "None"),
        ]
        for i, (prop, sv, lv) in enumerate(technical):
            _add_row(prop, sv, lv, i)

        # ── Tags (union of both files' tags) ── #
        _add_header("Tags")
        all_keys = sorted(set(src["tags"]) | set(lib["tags"]))
        for i, key in enumerate(all_keys):
            _add_row(key,
                     src["tags"].get(key, ""),
                     lib["tags"].get(key, ""),
                     i)

        # ── File paths ── #
        _add_header("File")
        _add_row("Filename",
                 os.path.basename(src["path"]),
                 os.path.basename(lib["path"]), 0)
        _add_row("Full path", src["path"], lib["path"], 1)

        # ── File size ── #
        def _fsize(p: str) -> str:
            try:
                n = os.path.getsize(p)
                for unit in ("B", "KB", "MB", "GB"):
                    if n < 1024:
                        return f"{n:.1f} {unit}"
                    n /= 1024
                return f"{n:.1f} GB"
            except OSError:
                return "—"

        _add_row("File size", _fsize(src["path"]), _fsize(lib["path"]), 2)

    def _update_lib_track(self):
        """Copy the scan track over the lib track and update the DB record."""
        from tkinter import messagebox
        from music.lib_ops import copy_track_to_lib

        if not self._src_path or not self._partition or not self._rel_path:
            messagebox.showwarning("Nothing to update", "No lib track loaded.")
            return

        confirm = messagebox.askyesno(
            "Update Lib Track",
            f"Overwrite lib track with scan track?\n\n"
            f"  From: {self._src_path}\n"
            f"  To:   {self._lib_path}\n\n"
            f"This cannot be undone.",
        )
        if not confirm:
            return

        try:
            copy_track_to_lib(
                self._src_path, self._lib_path,
                self._partition, self._rel_path,
            )
            self._status_var.set(
                f"✔  Lib track updated: {os.path.basename(self._lib_path)}"
            )
            # Reload comparison so cover/tags reflect the new file
            self.show_comparison(
                self._src_path, self._lib_path,
                self._partition, self._rel_path,
            )
        except Exception as exc:
            messagebox.showerror("Update failed", str(exc))

    def _play_both(self):
        settings = self._settings_getter() if callable(self._settings_getter) else {}
        foobar = settings.get("foobar_path", "").strip()
        if not foobar:
            messagebox.showwarning(
                "foobar2000 not set",
                "Please set the foobar2000 path in Settings ⚙.")
            return
        if not os.path.isfile(foobar):
            messagebox.showerror(
                "foobar2000 not found",
                f"Executable not found:\n{foobar}")
            return
        paths = [p for p in (self._src_path, self._lib_path) if os.path.isfile(p)]
        if paths:
            subprocess.Popen([foobar, "/play", *paths])
