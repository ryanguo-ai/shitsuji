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


def _read_flac_tags(file_path: str) -> tuple[str, str, str]:
    """Return (artist, title, bitrate) from a FLAC file's tags/info, or ('', '', '') on failure."""
    try:
        flac = FLAC(file_path)
        tags = flac.tags or {}
        artist = tags.get("artist", [""])[0]
        title = tags.get("title", [""])[0]
        bitrate = f"{round(flac.info.bitrate / 1000)} kbps" if flac.info.bitrate else ""
        return artist, title, bitrate
    except Exception:
        return "", "", ""


class ScanTab(tk.Frame):

    def __init__(self, master):
        super().__init__(master, bg="#f5f5f5")
        self._settings = load_settings()
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

        columns = ("finlib", "fpath", "ftype", "fartist", "ftitle", "fbitrate", "fsize", "fmodified")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            selectmode="extended",
        )

        self.tree.heading("finlib",    text="In Lib",    anchor=tk.CENTER)
        self.tree.heading("fpath",     text="Full Path", anchor=tk.W)
        self.tree.heading("ftype",     text="Type",      anchor=tk.W)
        self.tree.heading("fartist",   text="Artist",    anchor=tk.W)
        self.tree.heading("ftitle",    text="Title",     anchor=tk.W)
        self.tree.heading("fbitrate",  text="Bitrate",   anchor=tk.E)
        self.tree.heading("fsize",     text="Size",      anchor=tk.E)
        self.tree.heading("fmodified", text="Modified",  anchor=tk.W)

        self.tree.column("finlib",    width=55,  anchor=tk.CENTER, stretch=False)
        self.tree.column("fpath",     width=300, stretch=True)
        self.tree.column("ftype",     width=60,  stretch=False)
        self.tree.column("fartist",   width=150, stretch=False)
        self.tree.column("ftitle",    width=190, stretch=False)
        self.tree.column("fbitrate",  width=80,  anchor=tk.E, stretch=False)
        self.tree.column("fsize",     width=70,  anchor=tk.E, stretch=False)
        self.tree.column("fmodified", width=130, stretch=False)

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.tag_configure("odd",    background="#ffffff")
        self.tree.tag_configure("even",   background="#ecf0f1")
        self.tree.tag_configure("inlib",  background="#eafaf1", foreground="#1e8449")
        self.tree.tag_configure("notlib", background="#fdf2f8", foreground="#922b21")

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
        """Refresh the In Lib column for every row in the table."""
        from panels.database import get_track_info

        items = self.tree.get_children()
        if not items:
            self.status_var.set("No tracks to check.")
            return

        self.status_var.set("Checking library…")
        self.update_idletasks()

        # Build a lookup set: (artist_lower, title_lower, album_lower)
        lib_set: set[tuple[str, str, str]] = set()
        for row in get_track_info():
            lib_set.add((
                (row["artist"] or "").strip().lower(),
                (row["title"]  or "").strip().lower(),
                (row["album"]  or "").strip().lower(),
            ))

        found = 0
        for i, item in enumerate(items):
            vals   = list(self.tree.item(item, "values"))
            artist = (vals[3] or "").strip().lower()
            title  = (vals[4] or "").strip().lower()
            matched = bool(artist and title) and any(
                a == artist and t == title
                for a, t, _ in lib_set
            )
            if matched:
                vals[0] = "🟢"
                self.tree.item(item, values=vals, tags=("inlib",))
                found += 1
            else:
                vals[0] = "🔴"
                plain = "odd" if i % 2 == 0 else "even"
                self.tree.item(item, values=vals, tags=(plain,))

        total = len(items)
        self.status_var.set(
            f"Check complete — {found} of {total} track{'s' if total != 1 else ''} found in lib."
        )


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
        artist, title, bitrate = _read_flac_tags(full_path) if ext == "FLAC" else ("", "", "")

        tag = "odd" if len(self.tree.get_children()) % 2 == 0 else "even"
        self.tree.insert(
            "", "end",
            values=("", full_path, file_type, artist, title, bitrate, size, modified),
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
        paths = [self.tree.item(i, "values")[1] for i in selected]
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

        if audio_paths or flac_paths:
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
        import shutil
        from panels.database import upsert_track_info
        from panels.send_to_lib_panel import compute_dest_full_path, compute_dest_rel_path
        from mutagen.flac import FLAC as _FLAC

        log = get_logger("send_to_lib")
        lib_root = self._settings.get("music_lib_paths", {}).get(partition, "")
        errors: list[str] = []
        copied = 0

        for abs_path in paths:
            ext = os.path.splitext(abs_path)[1]
            artist = title = album = bitrate = ""
            try:
                f = _FLAC(abs_path)
                artist  = (f.get("artist")  or f.get("ARTIST")  or [""])[0]
                title   = (f.get("title")   or f.get("TITLE")   or [""])[0]
                album   = (f.get("album")   or f.get("ALBUM")   or [""])[0]
                bitrate = f"{round(f.info.bitrate / 1000)} kbps" if f.info else ""
            except Exception as exc:
                log.warning(f"Tag read failed: {abs_path} — {exc}")

            dest_full = compute_dest_full_path(lib_root, partition, artist, album, title, ext)
            rel_path  = compute_dest_rel_path(artist, album, title, ext)

            try:
                os.makedirs(os.path.dirname(dest_full), exist_ok=True)
                shutil.copy2(abs_path, dest_full)
                log.info(f"Copied: {abs_path!r} → {dest_full!r}")
            except Exception as exc:
                log.error(f"File copy failed: {abs_path} — {exc}")
                errors.append(f"{os.path.basename(abs_path)}: {exc}")
                continue

            try:
                upsert_track_info(partition, rel_path, artist, title, album, bitrate)
                log.info(f"DB saved: {partition}/{rel_path}")
                copied += 1
            except Exception as exc:
                log.error(f"DB write failed: {abs_path} — {exc}")
                errors.append(f"{os.path.basename(abs_path)} (DB): {exc}")

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
        full_path, file_type = values[1], values[2]
        if file_type == "FLAC":
            self._detail_panel.show_flac(full_path)
        else:
            self._detail_panel.clear()
