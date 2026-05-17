"""
Entry point — builds the tabbed application window and starts the event loop.
"""

import tkinter as tk
from tkinter import ttk
from tkinterdnd2 import TkinterDnD

from music.folder_scanner import ScanTab
from music.scan_folders_panel import ScanFoldersTab
from music.search_panel import SearchTab
from music.artist_panel import ArtistTab
from music.compare_tracks_panel import CompareTracksTab
from music.settings_panel import SettingsDialog, load_settings, save_settings
from music.database import init_db
from common.logger import get_logger

_log = get_logger("startup")


class App(TkinterDnD.Tk):

    def __init__(self):
        super().__init__()
        init_db()
        _log.info("Application started")
        self._settings = load_settings()
        self.title("Shitsuji")
        self.minsize(700, 450)

        # Apply saved geometry, or default to centred half-screen
        saved_geom = self._settings.get("window_geometry")
        if saved_geom:
            self.geometry(saved_geom)
        else:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            if screen_w <= 0 or screen_h <= 0:
                screen_w, screen_h = 1280, 720
            win_w, win_h = screen_w // 2, screen_h // 2
            x = (screen_w - win_w) // 2
            y = (screen_h - win_h) // 2
            self.geometry(f"{win_w}x{win_h}+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_toolbar()
        self._apply_styles()

        self._notebook = ttk.Notebook(self, style="App.TNotebook")
        self._notebook.pack(fill=tk.BOTH, expand=True)

        self._compare_tab = CompareTracksTab(
            self._notebook,
            settings_getter=lambda: self._settings,
        )
        self._scan_tab = ScanTab(
            self._notebook,
            on_compare=self._open_compare,
            on_search_artist=self._open_artist_search,
        )
        self._artist_tab = ArtistTab(self._notebook)
        self._notebook.add(SearchTab(self._notebook, on_search_artist=self._open_artist_search),   text="  Search In Lib  ")
        self._notebook.add(self._scan_tab,              text="  Scan  ")
        self._notebook.add(
            ScanFoldersTab(self._notebook, on_scan_folders=self._open_scan_folders),
            text="  Scan Folders  ",
        )
        self._notebook.add(self._compare_tab,           text="  Compare Tracks  ")
        self._notebook.add(self._artist_tab,            text="  Artist Info  ")

        self._restore_active_tab()
        self._notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ------------------------------------------------------------------ #
    # Styles                                                               #
    # ------------------------------------------------------------------ #

    def _apply_styles(self) -> None:
        style = ttk.Style(self)

        # Switch to 'clam' theme — the Windows 'vista' native theme draws
        # tabs using OS visual styles and ignores style.map() colour/font
        # overrides entirely.  'clam' uses pure Tk rendering so every
        # configure/map property works as expected.
        if "clam" in style.theme_names():
            style.theme_use("clam")

        _DARK = "#2c3e50"   # same as the Shitsuji toolbar bar

        style.configure("App.TNotebook", background="#f5f5f5")
        style.configure(
            "App.TNotebook.Tab",
            background="#d0d3d4",
            foreground=_DARK,
            padding=[14, 5],
            font=("Segoe UI", 9),
        )
        style.map(
            "App.TNotebook.Tab",
            background=[("selected", _DARK),     ("active", "#95a5a6")],
            foreground=[("selected", "#ffffff"),  ("active", _DARK)],
            font=[("selected", ("Segoe UI", 9, "bold"))],
        )

    # ------------------------------------------------------------------ #
    # Toolbar                                                              #
    # ------------------------------------------------------------------ #

    def _build_toolbar(self):
        toolbar = tk.Frame(self, bg="#2c3e50", pady=6, padx=12)
        toolbar.pack(fill=tk.X)

        tk.Label(
            toolbar, text="Shitsuji",
            font=("Segoe UI", 13, "bold"),
            fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        tk.Button(
            toolbar, text="⚙", font=("Segoe UI", 13),
            fg="white", bg="#2c3e50",
            activeforeground="#ecf0f1", activebackground="#34495e",
            relief=tk.FLAT, bd=0, cursor="hand2",
            command=self._open_settings,
        ).pack(side=tk.RIGHT)

    def _open_settings(self):
        SettingsDialog(
            self,
            self._settings,
            on_save=self._on_settings_saved,
        )

    def _on_settings_saved(self, updated: dict):
        self._settings.update(updated)
        self._scan_tab._settings.update(updated)

    def _open_compare(self, src_path: str, lib_path: str, partition: str, rel_path: str):
        """Switch to the Compare Tracks tab and load the two files."""
        self._compare_tab.show_comparison(src_path, lib_path, partition, rel_path)
        # Switch to the Compare Tracks tab
        for idx in range(self._notebook.index("end")):
            if self._notebook.tab(idx, "text").strip() == "Compare Tracks":
                self._notebook.select(idx)
                break

    def _open_artist_search(self, artist_name: str):
        """Switch to the Artist Info tab and search for *artist_name* in the DB."""
        for idx in range(self._notebook.index("end")):
            if self._notebook.tab(idx, "text").strip() == "Artist Info":
                self._notebook.select(idx)
                break
        self._artist_tab.search_artist(artist_name)

    def _open_scan_folders(self, folders: list[str]):
        """Switch to the Scan tab and populate it with *folders*."""
        for idx in range(self._notebook.index("end")):
            if self._notebook.tab(idx, "text").strip() == "Scan":
                self._notebook.select(idx)
                break
        self._scan_tab.scan_folders(folders)

    def _on_close(self):
        self._settings["window_geometry"] = self.geometry()
        save_settings(self._settings)
        _log.info("Application closed")
        self.destroy()

    # ------------------------------------------------------------------ #
    # Tab persistence                                                      #
    # ------------------------------------------------------------------ #

    def _on_tab_changed(self, _event):
        tab_id = self._notebook.select()
        tab_name = self._notebook.tab(tab_id, "text").strip()
        self._settings["active_tab"] = tab_name
        save_settings(self._settings)

    def _restore_active_tab(self):
        saved = self._settings.get("active_tab", "")
        for idx in range(self._notebook.index("end")):
            if self._notebook.tab(idx, "text").strip() == saved:
                self._notebook.select(idx)
                break


if __name__ == "__main__":
    App().mainloop()
