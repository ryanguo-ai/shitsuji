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
from panels.settings_panel import load_settings, save_settings

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

        tk.Label(row, text="Folder:", font=("Segoe UI", 10),
                 bg="#f5f5f5").pack(side=tk.LEFT, padx=(0, 6))

        self.path_var = tk.StringVar(value=r"D:/_sample/")
        self.path_entry = tk.Entry(
            row, textvariable=self.path_var,
            font=("Segoe UI", 10), relief=tk.SOLID, bd=1,
        )
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(row, text="Browse…", command=self._browse).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row, text="Scan", command=self._scan).pack(side=tk.LEFT)

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

        columns = ("fpath", "ftype", "fartist", "ftitle", "fbitrate", "fsize", "fmodified")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            selectmode="extended",
        )

        self.tree.heading("fpath", text="Full Path", anchor=tk.W)
        self.tree.heading("ftype", text="Type", anchor=tk.W)
        self.tree.heading("fartist", text="Artist", anchor=tk.W)
        self.tree.heading("ftitle", text="Title", anchor=tk.W)
        self.tree.heading("fbitrate", text="Bitrate", anchor=tk.E)
        self.tree.heading("fsize", text="Size", anchor=tk.E)
        self.tree.heading("fmodified", text="Modified", anchor=tk.W)

        self.tree.column("fpath", width=340, stretch=True)
        self.tree.column("ftype", width=80, stretch=False)
        self.tree.column("fartist", width=160, stretch=False)
        self.tree.column("ftitle", width=200, stretch=False)
        self.tree.column("fbitrate", width=90, anchor=tk.E, stretch=False)
        self.tree.column("fsize", width=80, anchor=tk.E, stretch=False)
        self.tree.column("fmodified", width=140, stretch=False)

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.tag_configure("odd", background="#ffffff")
        self.tree.tag_configure("even", background="#ecf0f1")

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Button-3>", self._on_row_right_click)

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

    def _browse(self):
        folder = filedialog.askdirectory(title="Select a folder to scan")
        if folder:
            self.path_var.set(folder)

    def _scan(self):
        folder = self.path_var.get().strip()
        if not folder:
            messagebox.showwarning("No folder", "Please select a folder first.")
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
        artist, title, bitrate = _read_flac_tags(full_path) if ext == "FLAC" else ("", "", "")

        tag = "odd" if len(self.tree.get_children()) % 2 == 0 else "even"
        self.tree.insert(
            "", "end",
            values=(full_path, file_type, artist, title, bitrate, size, modified),
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

    def _on_drop(self, event):
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
        paths = [self.tree.item(i, "values")[0] for i in selected]
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

    def _on_row_select(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        if not values:
            return
        full_path, file_type = values[0], values[1]
        if file_type == "FLAC":
            self._detail_panel.show_flac(full_path)
        else:
            self._detail_panel.clear()
