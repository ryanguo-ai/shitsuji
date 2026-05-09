"""
Folder Scanner UI — browse and list all files in a selected directory.
"""

import io
import json
import os
import pathlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

from mutagen.flac import FLAC
from PIL import Image, ImageTk

AUDIO_EXTENSIONS = {
    "FLAC", "MP3", "AAC", "OGG", "OPUS", "WAV", "AIFF", "APE",
    "WV", "M4A", "WMA", "DSF", "DFF", "MPC",
}
DEFAULTS = {
    "foobar_path": r"C:\_soft\foobar2000_2.25.8\foobar2000.exe",
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


class FolderScannerApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Folder Scanner")

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        if screen_w > 0 and screen_h > 0:
            win_w, win_h = screen_w // 2, screen_h // 2
        else:
            screen_w, screen_h = 1280, 720
            win_w, win_h = 640, 360
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self.minsize(700, 450)
        self.configure(bg="#f5f5f5")

        self._settings = self._load_settings()
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

        tk.Button(
            top, text="⚙", font=("Segoe UI", 14),
            fg="white", bg="#2c3e50",
            activeforeground="#ecf0f1", activebackground="#34495e",
            relief=tk.FLAT, bd=0, cursor="hand2",
            command=self._open_settings,
        ).pack(side=tk.RIGHT)

        # ── Path entry row ────────────────────────────────────────────── #
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

        # ── Right: detail panel ───────────────────────────────────────── #
        self._build_detail_panel()

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
    # Settings                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _load_settings() -> dict:
        try:
            return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))}
        except Exception:
            return dict(DEFAULTS)

    def _save_settings(self):
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(
                json.dumps(self._settings, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def _open_settings(self):
        dlg = tk.Toplevel(self)
        dlg.title("Settings")
        dlg.resizable(False, False)
        dlg.grab_set()

        # Center over main window
        self.update_idletasks()
        w, h = 480, 120
        mx = self.winfo_x() + (self.winfo_width() - w) // 2
        my = self.winfo_y() + (self.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{mx}+{my}")
        dlg.configure(bg="#f5f5f5")

        # ── foobar2000 path ── #
        frm = tk.Frame(dlg, bg="#f5f5f5", padx=16, pady=16)
        frm.pack(fill=tk.BOTH, expand=True)

        tk.Label(frm, text="foobar2000 path:", font=("Segoe UI", 9),
                 bg="#f5f5f5").grid(row=0, column=0, sticky=tk.W, pady=(0, 6))

        foobar_var = tk.StringVar(value=self._settings.get("foobar_path", ""))
        entry = tk.Entry(frm, textvariable=foobar_var, font=("Segoe UI", 9),
                         relief=tk.SOLID, bd=1, width=42)
        entry.grid(row=0, column=1, sticky=tk.EW, padx=(8, 4), pady=(0, 6))

        def browse_foobar():
            path = filedialog.askopenfilename(
                title="Select foobar2000.exe",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
                initialfile=foobar_var.get(),
            )
            if path:
                foobar_var.set(path)

        ttk.Button(frm, text="…", width=3, command=browse_foobar).grid(
            row=0, column=2, pady=(0, 6))

        frm.columnconfigure(1, weight=1)

        # ── Buttons ── #
        btn_frm = tk.Frame(dlg, bg="#f5f5f5", padx=16, pady=(0, 12))
        btn_frm.pack(fill=tk.X)

        def save():
            self._settings["foobar_path"] = foobar_var.get().strip()
            self._save_settings()
            dlg.destroy()

        ttk.Button(btn_frm, text="Save", command=save).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_frm, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT)

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
        file_count = [0]
        row_index = [0]

        try:
            self._populate_list(folder, show_hidden, recursive, file_count, row_index)
        except PermissionError as exc:
            messagebox.showerror("Permission denied", str(exc))

        total = file_count[0]
        self.status_var.set(f"Found {total} file{'s' if total != 1 else ''}  in  {folder}")
        self.footer_var.set(f"Scan complete — {total} file{'s' if total != 1 else ''} found.")

    # ------------------------------------------------------------------ #
    # List population                                                      #
    # ------------------------------------------------------------------ #

    def _populate_list(self, folder, show_hidden, recursive, file_count, row_index):
        """Walk the folder and insert every file as a flat row."""
        walker = os.walk(folder) if recursive else self._single_level(folder)

        for dirpath, dirnames, filenames in walker:
            if not show_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                filenames = [f for f in filenames if not f.startswith(".")]

            for name in sorted(filenames, key=str.lower):
                full_path = os.path.join(dirpath, name)
                try:
                    stat = os.stat(full_path)
                    size = format_size(stat.st_size)
                    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                except OSError:
                    size = "—"
                    modified = "—"

                ext = os.path.splitext(name)[1].lstrip(".").upper()
                file_type = ext if ext else "File"

                artist, title, bitrate = _read_flac_tags(full_path) if ext == "FLAC" else ("", "", "")

                tag = "odd" if row_index[0] % 2 == 0 else "even"
                row_index[0] += 1

                self.tree.insert(
                    "", "end",
                    values=(full_path, file_type, artist, title, bitrate, size, modified),
                    tags=(tag,),
                )
                file_count[0] += 1

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

    def _on_row_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        values = self.tree.item(item, "values")
        full_path = values[0]
        ext = os.path.splitext(full_path)[1].lstrip(".").upper()

        menu = tk.Menu(self, tearoff=0)

        if ext in AUDIO_EXTENSIONS:
            menu.add_command(
                label="▶  Play in foobar2000",
                command=lambda: self._play_file(full_path),
            )
            menu.add_separator()

        menu.add_command(
            label="Copy File Path",
            command=lambda: self._copy_path(full_path),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _play_file(self, path: str):
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
        subprocess.Popen([foobar, "/play", path])

    def _copy_path(self, path: str):
        self.clipboard_clear()
        self.clipboard_append(path)

    # ------------------------------------------------------------------ #
    # Detail panel                                                        #
    # ------------------------------------------------------------------ #

    def _build_detail_panel(self):
        detail_frame = tk.Frame(self._paned, bg="#f0f0f0", width=280)
        self._paned.add(detail_frame, stretch="never", minsize=240)

        tk.Label(
            detail_frame, text="File Details",
            font=("Segoe UI", 11, "bold"),
            bg="#f0f0f0", fg="#2c3e50", anchor="w", padx=10, pady=8,
        ).pack(fill=tk.X)

        ttk.Separator(detail_frame, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Cover art
        self._cover_label = tk.Label(
            detail_frame, bg="#f0f0f0",
            text="No cover art", font=("Segoe UI", 9, "italic"), fg="#7f8c8d",
        )
        self._cover_label.pack(pady=(12, 8))

        ttk.Separator(detail_frame, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Tags table
        tag_frame = tk.Frame(detail_frame, bg="#f0f0f0")
        tag_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._tag_tree = ttk.Treeview(
            tag_frame, columns=("tag", "value"), show="headings",
            selectmode="none",
        )
        self._tag_tree.heading("tag", text="Tag", anchor=tk.W)
        self._tag_tree.heading("value", text="Value", anchor=tk.W)
        self._tag_tree.column("tag", width=90, stretch=False)
        self._tag_tree.column("value", width=160, stretch=True)

        tag_vsb = ttk.Scrollbar(tag_frame, orient=tk.VERTICAL, command=self._tag_tree.yview)
        self._tag_tree.configure(yscrollcommand=tag_vsb.set)
        tag_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tag_tree.pack(fill=tk.BOTH, expand=True)

        self._cover_photo = None  # keep reference to avoid GC

    def _on_row_select(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        if not values:
            return
        full_path, file_type = values[0], values[1]
        if file_type == "FLAC":
            self._show_flac_details(full_path)
        else:
            self._clear_detail_panel()

    def _show_flac_details(self, path: str):
        self._clear_detail_panel()
        try:
            flac = FLAC(path)
        except Exception:
            return

        # Cover art
        pictures = flac.pictures
        cover_pic = next(
            (p for p in pictures if p.type == 3),  # type 3 = Front Cover
            pictures[0] if pictures else None,
        )
        if cover_pic:
            try:
                img = Image.open(io.BytesIO(cover_pic.data))
                img.thumbnail((240, 240), Image.LANCZOS)
                self._cover_photo = ImageTk.PhotoImage(img)
                self._cover_label.configure(image=self._cover_photo, text="")
            except Exception:
                self._cover_label.configure(image="", text="(cover unreadable)")
        else:
            self._cover_label.configure(image="", text="No cover art")

        # Tags
        tags = flac.tags or {}
        for key, values in sorted(tags.items()):
            display_val = " / ".join(values) if isinstance(values, list) else values
            self._tag_tree.insert("", "end", values=(key.upper(), display_val))

    def _clear_detail_panel(self):
        self._cover_label.configure(image="", text="No cover art")
        self._cover_photo = None
        self._tag_tree.delete(*self._tag_tree.get_children())


if __name__ == "__main__":
    app = FolderScannerApp()
    app.mainloop()
