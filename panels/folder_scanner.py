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
from panels.settings_panel import load_settings, save_settings, MUSIC_LIB_PARTITIONS
from panels.logger import get_logger

AUDIO_EXTENSIONS = {
    "FLAC", "MP3", "AAC", "OGG", "OPUS", "WAV", "AIFF", "APE",
    "WV", "M4A", "WMA", "DSF", "DFF", "MPC",
}


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

class ScanTab(tk.Frame):

    def __init__(self, master, on_compare=None):
        super().__init__(master, bg="#f5f5f5")
        self._settings = load_settings()
        self._on_compare = on_compare   # callable(src_path, lib_path) or None
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

        ttk.Button(row, text="Check Tracks", command=self._check_tracks).pack(side=tk.LEFT)

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

        self.tree.heading("flibready",  text="Lib Ready",  anchor=tk.CENTER)
        self.tree.heading("finlib",     text="In Lib",     anchor=tk.CENTER)
        self.tree.heading("fpath",      text="Full Path",  anchor=tk.W)
        self.tree.heading("ftype",      text="Type",       anchor=tk.W)
        self.tree.heading("fartist",    text="Artist",     anchor=tk.W)
        self.tree.heading("ftitle",     text="Title",      anchor=tk.W)
        self.tree.heading("falbum",     text="Album",      anchor=tk.W)
        self.tree.heading("fbitrate",   text="Bitrate",    anchor=tk.E)
        self.tree.heading("fsize",      text="Size",       anchor=tk.E)
        self.tree.heading("fmodified",  text="Modified",   anchor=tk.W)

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
            artist    = (vals[4] or "").strip().lower()
            title     = (vals[5] or "").strip().lower()
            album     = (vals[6] or "").strip().lower()

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
        audio_paths = [
            p for p in paths
            if os.path.splitext(p)[1].lstrip(".").upper() in AUDIO_EXTENSIONS
        ]

        menu = tk.Menu(self, tearoff=0)

        if audio_paths:
            n = len(audio_paths)
            label = f"▶  Play {n} file{'s' if n > 1 else ''} in foobar2000"
            menu.add_command(
                label=label,
                command=lambda: self._play_files(audio_paths),
            )

        flac_paths = [
            p for p in paths
            if os.path.splitext(p)[1].lstrip(".").upper() == "FLAC"
        ]
        if flac_paths:
            menu.add_command(
                label=f"🏷  Edit Tags",
                command=lambda: self._edit_tags(flac_paths),
            )
            menu.add_command(
                label="🎨  Find Cover Art",
                command=lambda: self._find_cover_art(flac_paths),
            )

        if audio_paths or flac_paths:
            menu.add_separator()

        # ── Compare track with Lib (only for single 🟡 rows) ── #
        if (len(selected) == 1 and self._on_compare is not None):
            clicked_vals = self.tree.item(item, "values")
            if clicked_vals[1] == "🟡":          # inlib_diff — metadata match, different MD5
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

        menu.add_command(
            label=f"Copy Path{'s' if len(paths) > 1 else ''}",
            command=lambda: self._copy_paths(paths),
        )
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

    def _play_files(self, paths: list[str]):
        import subprocess
        foobar = self._settings.get("foobar_path", "").strip()
        if not foobar:
            messagebox.showwarning("foobar2000 not set",
                                   "Please set the foobar2000 path in Settings.")
            return
        if not os.path.isfile(foobar):
            messagebox.showerror("foobar2000 not found",
                                 f"Executable not found:\n{foobar}")
            return
        subprocess.Popen([foobar, "/play", *paths])

    def _edit_tags(self, paths: list[str]):
        from panels.edit_tags_panel import EditTagsPanel
        EditTagsPanel(self.winfo_toplevel(), paths)

    def _find_cover_art(self, paths: list[str]):
        from panels.cover_art_panel import CoverArtPanel
        CoverArtPanel(self.winfo_toplevel(), paths, self._settings)

    def _copy_paths(self, paths: list[str]):
        self.clipboard_clear()
        self.clipboard_append("\n".join(paths))

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
