"""
Shared audio context-menu actions.

AudioMenuMixin provides _play_files, _edit_tags, _find_cover_art, _copy_paths,
and _build_audio_context_menu.  Both ScanTab and SearchTab inherit from it so
the implementations are never duplicated.

Requires the host class to expose ``self._settings``.
"""

import os
import subprocess
import tkinter as tk
from tkinter import messagebox

AUDIO_EXTENSIONS = {
    "FLAC", "MP3", "AAC", "OGG", "OPUS", "WAV", "AIFF", "APE",
    "WV", "M4A", "WMA", "DSF", "DFF", "MPC",
}

# File types into which the Cover Art Finder can embed a front-cover image.
COVER_ART_EMBED_EXTENSIONS = {"FLAC", "MP3", "M4A"}


class AudioMenuMixin:
    """Mixin that adds audio file context-menu actions to a tk.Frame subclass."""

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _play_files(self, paths: list[str]):
        foobar = self._settings.get("foobar_path", "").strip()
        if not foobar:
            messagebox.showwarning(
                "foobar2000 not set",
                "Please set the foobar2000 path in Settings.",
            )
            return
        if not os.path.isfile(foobar):
            messagebox.showerror(
                "foobar2000 not found",
                f"Executable not found:\n{foobar}",
            )
            return
        subprocess.Popen([foobar, "/play", *paths])

    def _add_to_playlist(self, paths: list[str]):
        foobar = self._settings.get("foobar_path", "").strip()
        if not foobar:
            messagebox.showwarning(
                "foobar2000 not set",
                "Please set the foobar2000 path in Settings.",
            )
            return
        if not os.path.isfile(foobar):
            messagebox.showerror(
                "foobar2000 not found",
                f"Executable not found:\n{foobar}",
            )
            return
        subprocess.Popen([foobar, "/add", *paths])

    def _edit_tags(self, paths: list[str]):
        from music.edit_tags_panel import EditTagsPanel
        EditTagsPanel(self.winfo_toplevel(), paths)

    def _find_cover_art(self, paths: list[str]):
        from music.cover_art_panel import CoverArtPanel
        CoverArtPanel(self.winfo_toplevel(), paths, self._settings)

    def _analyze_track(self, path: str):
        from music.audio_analysis_panel import AudioAnalysisPanel
        AudioAnalysisPanel(self.winfo_toplevel(), path)

    def _copy_paths(self, paths: list[str]):
        self.clipboard_clear()
        self.clipboard_append("\n".join(paths))

    # ------------------------------------------------------------------ #
    # Menu builder                                                         #
    # ------------------------------------------------------------------ #

    def _build_audio_context_menu(
        self,
        paths: list[str],
        *,
        extra_items_fn=None,
    ) -> tk.Menu:
        """Return a populated context Menu for the given file paths.

        ``extra_items_fn(menu, paths, audio_paths, flac_paths)`` is called
        (when provided) just before the Copy Path entry so callers can inject
        panel-specific items (e.g. Compare / Send to Lib in ScanTab).
        """
        audio_paths = [
            p for p in paths
            if os.path.splitext(p)[1].lstrip(".").upper() in AUDIO_EXTENSIONS
        ]
        flac_paths = [
            p for p in paths
            if os.path.splitext(p)[1].lstrip(".").upper() == "FLAC"
        ]
        cover_art_paths = [
            p for p in paths
            if os.path.splitext(p)[1].lstrip(".").upper() in COVER_ART_EMBED_EXTENSIONS
        ]

        menu = tk.Menu(self, tearoff=0)

        if audio_paths:
            n = len(audio_paths)
            menu.add_command(
                label=f"▶  Play {n} file{'s' if n > 1 else ''} in foobar2000",
                command=lambda: self._play_files(audio_paths),
            )
            menu.add_command(
                label=f"➕  Add {n} file{'s' if n > 1 else ''} to foobar2000 playlist",
                command=lambda: self._add_to_playlist(audio_paths),
            )

        if flac_paths:
            menu.add_command(
                label="🏷  Edit Tags",
                accelerator="Shift+E",
                command=lambda: self._edit_tags(flac_paths),
            )

        if cover_art_paths:
            menu.add_command(
                label="🎨  Find Cover Art",
                command=lambda: self._find_cover_art(cover_art_paths),
            )

        if audio_paths:
            # "Analyze Track" only makes sense for a single file at a time —
            # the analyzer renders one spectrogram + report per window.
            if len(audio_paths) == 1:
                menu.add_command(
                    label="🔬  Analyze Track (Hi-Res check)",
                    command=lambda p=audio_paths[0]: self._analyze_track(p),
                )
            else:
                analyze_menu = tk.Menu(menu, tearoff=0)
                for p in audio_paths:
                    analyze_menu.add_command(
                        label=os.path.basename(p),
                        command=lambda pp=p: self._analyze_track(pp),
                    )
                menu.add_cascade(
                    label="🔬  Analyze Track (Hi-Res check)",
                    menu=analyze_menu,
                )

        if audio_paths or flac_paths:
            menu.add_separator()

        if extra_items_fn:
            extra_items_fn(menu, paths, audio_paths, flac_paths)

        menu.add_command(
            label=f"Copy Path{'s' if len(paths) > 1 else ''}",
            command=lambda: self._copy_paths(paths),
        )

        return menu
