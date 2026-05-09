"""Cover art finder and FLAC embedder panel."""

import io
import os
import pathlib
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from mutagen.flac import FLAC, Picture
from PIL import Image, ImageTk

from cover_art.musicbrainz_retriever import MusicBrainzCoverRetriever
from panels.logger import get_logger


class CoverArtPanel(tk.Toplevel):
    """Search cover art from remote sources and embed it into FLAC files."""

    def __init__(self, parent: tk.Widget, flac_paths: list[str], settings: dict):
        super().__init__(parent)
        self._flac_paths = list(flac_paths)
        self._settings = settings
        self._log = get_logger("cover_art")
        self._cache_dir = pathlib.Path.home() / ".shitsuji" / "cover_art_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._results: list[dict] = []
        self._selected_index: int | None = None
        self._search_thread: threading.Thread | None = None

        first_name = os.path.basename(self._flac_paths[0]) if self._flac_paths else "Cover Art"
        self.title(f"Cover Art Finder — {first_name}")
        self.configure(bg="#f5f5f5")
        self.geometry("780x600")
        self.minsize(720, 520)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.artist_var = tk.StringVar()
        self.album_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Idle")

        self._build_ui()
        self._prefill_tags()
        self._center_on_parent(parent)

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg="#2c3e50", pady=10, padx=14)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="🎨  Cover Art Finder",
            font=("Segoe UI", 14, "bold"),
            fg="white",
            bg="#2c3e50",
        ).pack(side=tk.LEFT)

        controls = tk.Frame(self, bg="#f5f5f5", padx=14, pady=10)
        controls.pack(fill=tk.X)

        tk.Label(controls, text="Artist:", bg="#f5f5f5").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.artist_var, width=28).grid(row=0, column=1, sticky="ew", padx=(6, 16))
        tk.Label(controls, text="Album:", bg="#f5f5f5").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.album_var, width=28).grid(row=0, column=3, sticky="ew", padx=(6, 16))
        tk.Label(controls, text="Title:", bg="#f5f5f5").grid(row=0, column=4, sticky="w")
        ttk.Entry(controls, textvariable=self.title_var, width=28).grid(row=0, column=5, sticky="ew", padx=(6, 0))
        controls.grid_columnconfigure(1, weight=1)
        controls.grid_columnconfigure(3, weight=1)
        controls.grid_columnconfigure(5, weight=1)

        action_row = tk.Frame(self, bg="#f5f5f5", padx=14, pady=10)
        action_row.pack(fill=tk.X)
        self.search_button = ttk.Button(action_row, text="🔍 Search", command=self._start_search)
        self.search_button.pack(side=tk.LEFT)
        ttk.Button(
            action_row, text="📋 Paste Image from Clipboard",
            command=self._paste_from_clipboard,
        ).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            action_row,
            textvariable=self.status_var,
            bg="#f5f5f5",
            fg="#566573",
            font=("Segoe UI", 9, "italic"),
        ).pack(side=tk.LEFT, padx=(14, 0))

        tk.Label(
            self,
            text="Results (click to select):",
            bg="#f5f5f5",
            anchor="w",
            padx=14,
        ).pack(fill=tk.X)

        results_outer = tk.Frame(self, bg="#f5f5f5", padx=14, pady=8)
        results_outer.pack(fill=tk.BOTH, expand=True)

        self._results_canvas = tk.Canvas(results_outer, bg="#ffffff", highlightthickness=1, highlightbackground="#d5dbdb")
        self._results_scrollbar = ttk.Scrollbar(results_outer, orient=tk.VERTICAL, command=self._results_canvas.yview)
        self._results_canvas.configure(yscrollcommand=self._results_scrollbar.set)
        self._results_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._results_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._results_frame = tk.Frame(self._results_canvas, bg="#ffffff")
        self._results_window = self._results_canvas.create_window((0, 0), window=self._results_frame, anchor="nw")
        self._results_frame.bind("<Configure>", self._on_results_configure)
        self._results_canvas.bind("<Configure>", self._on_canvas_configure)

        footer = tk.Frame(self, bg="#ecf0f1", pady=8, padx=12)
        footer.pack(fill=tk.X)
        self.embed_button = ttk.Button(
            footer,
            text=f"Embed into {len(self._flac_paths)} file(s)",
            command=self._embed_selected,
            state=tk.DISABLED,
        )
        self.embed_button.pack(side=tk.LEFT)
        ttk.Button(footer, text="Clear Results", command=self._clear_results).pack(side=tk.RIGHT)

    def _prefill_tags(self) -> None:
        if not self._flac_paths:
            return
        try:
            flac = FLAC(self._flac_paths[0])
            tags = flac.tags or {}
            self.artist_var.set(tags.get("artist", [""])[0])
            self.album_var.set(tags.get("album", [""])[0])
            self.title_var.set(tags.get("title", [""])[0])
        except Exception:
            self._log.exception("Failed to prefill tags from %s", self._flac_paths[0])

    def _center_on_parent(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        parent.update_idletasks()

        width = 780
        height = 600
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width() or width
        parent_height = parent.winfo_height() or height
        x = parent_x + max((parent_width - width) // 2, 0)
        y = parent_y + max((parent_height - height) // 2, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _on_results_configure(self, _event=None) -> None:
        self._results_canvas.configure(scrollregion=self._results_canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._results_canvas.itemconfigure(self._results_window, width=event.width)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_searching(self, searching: bool) -> None:
        self.search_button.configure(state=tk.DISABLED if searching else tk.NORMAL)

    def _start_search(self) -> None:
        artist = self.artist_var.get().strip()
        album  = self.album_var.get().strip()
        title  = self.title_var.get().strip()
        if not artist or not (album or title):
            messagebox.showwarning("Missing metadata",
                                   "Artist and at least one of Album or Title are required.")
            return
        if self._search_thread and self._search_thread.is_alive():
            return

        self._clear_results()
        self._set_searching(True)
        self._set_status("Searching MusicBrainz…")
        self._search_thread = threading.Thread(
            target=self._search_worker,
            args=(artist, album, title),
            daemon=True,
        )
        self._search_thread.start()

    def _search_worker(self, artist: str, album: str, title: str) -> None:
        cache_dir = str(self._cache_dir)

        def post_result(path: str, source: str):
            self.after(0, lambda p=path, s=source: self._add_single_result(p, s))

        try:
            for path in MusicBrainzCoverRetriever().get_cover_arts(
                    artist, album, title, cache_dir):
                post_result(path, "musicbrainz")
        except Exception as exc:
            self._log.error("Cover art search failed: %s", exc, exc_info=True)
            self.after(0, lambda e=str(exc): self._on_search_failed(e))
            return

        self.after(0, self._on_search_done)

    def _add_single_result(self, path: str, source: str) -> None:
        """Add one thumbnail to the results grid immediately when found."""
        try:
            image_bytes = pathlib.Path(path).read_bytes()
            with Image.open(io.BytesIO(image_bytes)) as img:
                width, height = img.size
                thumb = img.copy()
            thumb.thumbnail((130, 130))
            photo = ImageTk.PhotoImage(thumb)
        except Exception:
            self._log.exception("Failed to load result image: %s", path)
            pathlib.Path(path).unlink(missing_ok=True)
            return

        index = len(self._results)
        cell = tk.Frame(self._results_frame, bg="#f8f9f9", padx=6, pady=6, bd=1, relief=tk.FLAT)
        cell.grid(row=index // 4, column=index % 4, padx=10, pady=10, sticky="n")

        img_lbl = tk.Label(cell, image=photo, bg="#f8f9f9")
        img_lbl.image = photo
        img_lbl.pack()
        src_lbl = tk.Label(cell, text=source, bg="#f8f9f9", font=("Segoe UI", 9, "bold"))
        src_lbl.pack(pady=(6, 0))
        dim_lbl = tk.Label(cell, text=f"{width}×{height}", bg="#f8f9f9", fg="#5d6d7e", font=("Segoe UI", 8))
        dim_lbl.pack()

        for widget in (cell, img_lbl, src_lbl, dim_lbl):
            widget.bind("<Button-1>", lambda _e, idx=index: self._toggle_selection(idx))

        self._results.append({
            "path": path, "source": source, "bytes": image_bytes,
            "size": (width, height), "frame": cell,
            "labels": [img_lbl, src_lbl, dim_lbl],
        })
        self._set_status(f"Found {len(self._results)} result(s)…")

    def _on_search_done(self) -> None:
        self._set_searching(False)
        count = len(self._results)
        self._set_status(f"Done — {count} result{'s' if count != 1 else ''} found" if count else "No results found")

    def _paste_from_clipboard(self) -> None:
        """Grab an image from the clipboard and add it to the results grid."""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror("Clipboard error", str(exc))
            return

        if img is None:
            messagebox.showinfo("No image", "No image found in the clipboard.")
            return

        # Convert to RGB so we can save as PNG regardless of original mode
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        import time
        filename = f"clipboard.{int(time.time())}.png"
        dest = self._cache_dir / filename
        try:
            img.save(dest, format="PNG")
        except Exception as exc:
            messagebox.showerror("Save failed", f"Could not save clipboard image:\n{exc}")
            return

        self._add_single_result(str(dest), "clipboard")
        self._set_status(f"Pasted from clipboard — {img.width}×{img.height}")

    def _on_search_failed(self, error: str) -> None:
        self._set_searching(False)
        self._set_status("Search failed")
        messagebox.showerror("Cover Art Search Failed", error)

    def _toggle_selection(self, index: int) -> None:
        if self._selected_index == index:
            self._apply_selection(None)
            return
        self._apply_selection(index)

    def _apply_selection(self, index: int | None) -> None:
        self._selected_index = index
        for idx, result in enumerate(self._results):
            selected = idx == index
            bg = "#dbeafe" if selected else "#f8f9f9"
            relief = tk.SOLID if selected else tk.FLAT
            bd = 2 if selected else 1
            result["frame"].configure(bg=bg, relief=relief, bd=bd)
            for label in result["labels"]:
                label.configure(bg=bg)
        self.embed_button.configure(state=tk.NORMAL if index is not None else tk.DISABLED)

    def _embed_selected(self) -> None:
        if self._selected_index is None:
            messagebox.showwarning("No selection", "Select an image to embed.")
            return

        result = self._results[self._selected_index]
        image_bytes = result["bytes"]
        mime, width, height, depth = self._get_picture_info(image_bytes)

        errors: list[str] = []
        embedded = 0
        for path in self._flac_paths:
            try:
                flac = FLAC(path)
                other_pictures = [existing for existing in flac.pictures if existing.type != 3]
                flac.clear_pictures()
                for existing in other_pictures:
                    flac.add_picture(existing)

                picture = Picture()
                picture.type = 3
                picture.mime = mime
                picture.desc = "Front Cover"
                picture.data = image_bytes
                picture.width = width
                picture.height = height
                picture.depth = depth
                flac.add_picture(picture)
                flac.save()
                embedded += 1
            except Exception as exc:
                self._log.error(f"Failed to embed cover art into {path}: {exc}", exc_info=True)
                errors.append(f"{os.path.basename(path)}: {exc}")

        if errors:
            messagebox.showerror(
                "Embed Cover Art",
                f"Embedded into {embedded} file(s), {len(errors)} failed.\n\n" + "\n".join(errors[:8]),
            )
            self._set_status(f"Embedded into {embedded} file(s), {len(errors)} failed")
            return

        messagebox.showinfo("Embed Cover Art", f"Embedded cover art into {embedded} file(s).")
        self._set_status(f"Embedded into {embedded} file(s)")

    def _get_picture_info(self, image_bytes: bytes) -> tuple[str, int, int, int]:
        with Image.open(io.BytesIO(image_bytes)) as image:
            mime = Image.MIME.get(image.format or "", "image/jpeg")
            width, height = image.size
            depth = max(len(image.getbands()), 1) * 8
        return mime, width, height, depth

    def _clear_results(self) -> None:
        for result in self._results:
            try:
                pathlib.Path(result["path"]).unlink(missing_ok=True)
            except Exception:
                self._log.exception("Failed to delete cached image: %s", result["path"])
            frame = result.get("frame")
            if frame is not None and frame.winfo_exists():
                frame.destroy()

        self._results.clear()
        self._apply_selection(None)
        self._set_status("Idle")
        self._results_canvas.yview_moveto(0)

    def _on_close(self) -> None:
        self._clear_results()
        self.destroy()
