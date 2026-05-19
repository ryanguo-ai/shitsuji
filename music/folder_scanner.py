"""
Folder Scanner UI — browse and list all files in a selected directory.
"""

import json
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

from mutagen.flac import FLAC
from tkinterdnd2 import DND_FILES

from music.audio_details_panel import AudioDetailsPanel
from music.audio_menu import AUDIO_EXTENSIONS, AudioMenuMixin
from common.keyboard_selection import attach_keyboard_range_selection
from music.settings_panel import load_settings, save_settings, MUSIC_LIB_PARTITIONS
from common.logger import get_logger


class _ScanFileDetailsPanel(tk.Frame):
    """File-Details panel for the Scan tab.

    Top    → two cover-art thumbnails (Local | Lib) with image-info and bitrate.
    Bottom → one combined Treeview (Property | Local | Lib) showing technical
             stats and the union of tags, with diff rows highlighted amber.
             Mirrors the visual style of the Compare Tracks panel.

    If no lib track is supplied, the right cover stays blank and the "Lib"
    column is empty.
    """

    def __init__(self, master):
        super().__init__(master, bg="#f0f0f0")
        self._local_photo = None
        self._lib_photo   = None
        self._local_cover_bytes: bytes | None = None
        self._lib_cover_bytes:   bytes | None = None
        # Current paths for the two columns (set by show()).
        self._local_path = ""
        self._lib_path   = ""
        # Map of editable tag-row iid → tag key (only populated for the
        # "── Tags ──" section; technical/file/header rows are excluded).
        self._tag_row_keys: dict[str, str] = {}
        # State for the in-place edit Entry (one at a time).
        self._edit_entry: tk.Entry | None = None
        self._build()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build(self):
        tk.Label(
            self, text="File Details",
            font=("Segoe UI", 11, "bold"),
            bg="#f0f0f0", fg="#2c3e50", anchor="w", padx=10, pady=8,
        ).pack(fill=tk.X)
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # ── Cover row: Local | Lib ─────────────────────────────────── #
        covers = tk.Frame(self, bg="#f0f0f0")
        covers.pack(fill=tk.X, pady=(8, 4))
        covers.columnconfigure(0, weight=1, uniform="side")
        covers.columnconfigure(1, weight=1, uniform="side")

        self._local_info_var = tk.StringVar()
        self._lib_info_var   = tk.StringVar()
        self._local_cover    = self._build_cover_column(
            covers, col=0, title="Local File",
            info_var=self._local_info_var, is_local=True)
        self._lib_cover      = self._build_cover_column(
            covers, col=1, title="Lib Track",
            info_var=self._lib_info_var,   is_local=False)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(4, 0))

        # ── Combined tag/properties table ──────────────────────────── #
        tbl = tk.Frame(self, bg="#f0f0f0")
        tbl.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._tree = ttk.Treeview(
            tbl, columns=("property", "local", "lib"),
            show="headings", selectmode="browse",
        )
        self._tree.heading("property", text="Property", anchor=tk.W)
        self._tree.heading("local",    text="Local",    anchor=tk.W)
        self._tree.heading("lib",      text="Lib",      anchor=tk.W)
        self._tree.column("property", width=120, stretch=False)
        self._tree.column("local",    width=200, stretch=True)
        self._tree.column("lib",      width=200, stretch=True)

        vsb = ttk.Scrollbar(tbl, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(tbl, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        # Row style tags (same palette as Compare Tracks)
        self._tree.tag_configure("header",
                                 background="#d6eaf8", foreground="#1a5276",
                                 font=("Segoe UI", 9, "bold"))
        self._tree.tag_configure("diff", background="#fefce8", foreground="#7d6608")
        self._tree.tag_configure("same", background="#ffffff", foreground="#1a1a1a")
        self._tree.tag_configure("even", background="#ecf0f1", foreground="#1a1a1a")

        # Double-click → edit a tag value (Local or Lib column only).
        self._tree.bind("<Double-1>", self._on_tag_double_click)

    def _build_cover_column(self, parent, *, col: int, title: str,
                            info_var: tk.StringVar, is_local: bool) -> tk.Label:
        wrap = tk.Frame(parent, bg="#f0f0f0")
        wrap.grid(row=0, column=col, sticky="nsew", padx=8)
        tk.Label(
            wrap, text=title,
            font=("Segoe UI", 9, "bold"),
            bg="#f0f0f0", fg="#2c3e50",
        ).pack()
        cover = tk.Label(
            wrap, bg="#f0f0f0",
            text="No cover art",
            font=("Segoe UI", 9, "italic"), fg="#7f8c8d",
        )
        cover.pack(pady=(4, 2))
        cover.bind(
            "<Button-3>",
            lambda e, src=is_local: self._on_cover_right_click(e, is_local=src),
        )
        tk.Label(
            wrap, textvariable=info_var,
            font=("Segoe UI", 8), fg="#7f8c8d", bg="#f0f0f0",
            justify=tk.CENTER,
        ).pack()
        return cover

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def show(self, local_path: str, lib_path: str) -> None:
        """Populate both columns. Pass an empty string to leave a side blank."""
        from music.compare_tracks_panel import _load_flac_info

        # Commit any in-progress inline edit before reloading.
        if self._edit_entry is not None:
            try:
                self._edit_entry.event_generate("<Return>")
            except Exception:
                self._edit_entry.destroy()
            self._edit_entry = None

        self._local_path = local_path or ""
        self._lib_path   = lib_path   or ""

        local_info = _load_flac_info(local_path) if local_path else None
        lib_info   = _load_flac_info(lib_path)   if lib_path   else None

        self._render_cover(self._local_cover, local_info, self._local_info_var,
                           is_local=True)
        self._render_cover(self._lib_cover,   lib_info,   self._lib_info_var,
                           is_local=False)

        self._populate_table(local_info, lib_info, local_path, lib_path)

    def show_flac(self, path: str) -> None:
        """Backwards-compatible single-pane API (lib column is cleared)."""
        self.show(path, "")

    def clear(self) -> None:
        if self._edit_entry is not None:
            self._edit_entry.destroy()
            self._edit_entry = None
        self._local_path = ""
        self._lib_path   = ""
        self._tag_row_keys.clear()
        self._render_cover(self._local_cover, None, self._local_info_var,
                           is_local=True)
        self._render_cover(self._lib_cover,   None, self._lib_info_var,
                           is_local=False)
        self._tree.delete(*self._tree.get_children())

    # ------------------------------------------------------------------ #
    # Cover-art rendering                                                  #
    # ------------------------------------------------------------------ #

    def _render_cover(self, label: tk.Label, info: dict | None,
                      info_var: tk.StringVar, *, is_local: bool) -> None:
        # No info → blank state
        if not info or not info.get("cover"):
            label.configure(image="", text="No cover art",
                            bg="#f0f0f0", width=20, height=5)
            if is_local:
                self._local_photo = None
                self._local_cover_bytes = None
            else:
                self._lib_photo = None
                self._lib_cover_bytes = None
            # Bitrate line still shown if info exists but no cover
            if info:
                info_var.set(self._format_info_line(info))
            else:
                info_var.set("")
            return

        # Decode + thumbnail
        try:
            import io
            from PIL import Image, ImageTk
            img = Image.open(io.BytesIO(info["cover"]))
            img.thumbnail((240, 240), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            label.configure(image=photo, text="", bg="#ffffff",
                            width=photo.width(), height=photo.height())
            if is_local:
                self._local_photo = photo
                self._local_cover_bytes = info["cover"]
            else:
                self._lib_photo = photo
                self._lib_cover_bytes = info["cover"]
        except Exception:
            label.configure(image="", text="(cover unreadable)",
                            bg="#f0f0f0", width=20, height=5)
            if is_local:
                self._local_photo = None
                self._local_cover_bytes = None
            else:
                self._lib_photo = None
                self._lib_cover_bytes = None

        info_var.set(self._format_info_line(info))

    @staticmethod
    def _format_info_line(info: dict) -> str:
        """Cover dims · cover size · bitrate (bitrate appears after image size)."""
        dims    = info.get("cover_dims") or ""
        sz      = info.get("cover_size_kb") or ""
        bitrate = info.get("bitrate") or ""
        parts: list[str] = []
        if dims and sz:
            parts.append(f"{dims} px  ·  {sz}")
        elif dims:
            parts.append(f"{dims} px")
        elif sz:
            parts.append(sz)
        if bitrate:
            parts.append(bitrate)
        return "  ·  ".join(parts)

    # ------------------------------------------------------------------ #
    # Table population                                                     #
    # ------------------------------------------------------------------ #

    def _populate_table(self, src: dict | None, lib: dict | None,
                        src_path: str, lib_path: str) -> None:
        self._tree.delete(*self._tree.get_children())
        self._tag_row_keys.clear()

        if src is None and lib is None:
            return

        src = src or {"tags": {}, "bitrate": "", "duration": "",
                      "cover_count": 0, "cover_dims": "", "cover_size_kb": ""}
        lib = lib or {"tags": {}, "bitrate": "", "duration": "",
                      "cover_count": 0, "cover_dims": "", "cover_size_kb": ""}

        idx = [0]

        def _add_header(label: str):
            self._tree.insert("", "end",
                              values=(f"── {label} ──", "", ""),
                              tags=("header",))

        def _add_row(prop: str, sv: str, lv: str, *, tag_key: str | None = None):
            # Treat both columns as blank-equivalent when one side is missing
            differs = (sv != lv) and bool(sv or lv) and src_path and lib_path
            tag = ("diff" if differs
                   else ("same" if idx[0] % 2 == 0 else "even"))
            iid = self._tree.insert("", "end", values=(prop, sv, lv), tags=(tag,))
            if tag_key is not None:
                self._tag_row_keys[iid] = tag_key
            idx[0] += 1

        # Technical
        _add_header("Technical")
        for prop, sv, lv in [
            ("Bitrate",     src["bitrate"],                 lib["bitrate"]),
            ("Duration",    src["duration"],                lib["duration"]),
            ("Image count", str(src["cover_count"]),        str(lib["cover_count"])),
            ("Cover dims",  src["cover_dims"] or "None",    lib["cover_dims"] or "None"),
            ("Cover size",  src["cover_size_kb"] or "None", lib["cover_size_kb"] or "None"),
        ]:
            _add_row(prop, sv, lv)

        # Tags (union, sorted) — these rows are editable.
        _add_header("Tags")
        all_keys = sorted(set(src["tags"]) | set(lib["tags"]))
        for key in all_keys:
            _add_row(key,
                     src["tags"].get(key, ""),
                     lib["tags"].get(key, ""),
                     tag_key=key)

        # File
        _add_header("File")
        _add_row("Filename",
                 os.path.basename(src_path) if src_path else "",
                 os.path.basename(lib_path) if lib_path else "")
        _add_row("Full path", src_path or "", lib_path or "")

        def _fsize(p: str) -> str:
            if not p:
                return ""
            try:
                n = os.path.getsize(p)
                for unit in ("B", "KB", "MB", "GB"):
                    if n < 1024:
                        return f"{n:.1f} {unit}"
                    n /= 1024
                return f"{n:.1f} GB"
            except OSError:
                return "—"

        _add_row("File size", _fsize(src_path), _fsize(lib_path))

    # ------------------------------------------------------------------ #
    # Inline tag editing                                                   #
    # ------------------------------------------------------------------ #

    # Map tree-column id to (values tuple index, side, attribute path).
    _EDIT_COLS = {"#2": ("local", 1), "#3": ("lib", 2)}

    def _on_tag_double_click(self, event) -> None:
        """Begin in-place editing of a tag value (Local or Lib column)."""
        # Commit any prior edit first.
        if self._edit_entry is not None:
            self._edit_entry.event_generate("<Return>")

        iid = self._tree.identify_row(event.y)
        col = self._tree.identify_column(event.x)
        if not iid or col not in self._EDIT_COLS:
            return
        if iid not in self._tag_row_keys:
            # Header / technical / file rows are not editable.
            return

        side, val_idx = self._EDIT_COLS[col]
        path = self._local_path if side == "local" else self._lib_path
        if not path:
            return     # No file loaded on that side → nothing to edit.

        bbox = self._tree.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox

        tag_key  = self._tag_row_keys[iid]
        cur_vals = list(self._tree.item(iid, "values"))
        current  = cur_vals[val_idx]

        var = tk.StringVar(value=current)
        entry = tk.Entry(
            self._tree, textvariable=var,
            font=("Segoe UI", 9), relief=tk.SOLID, bd=1,
        )
        entry.place(x=x, y=y, width=w, height=h)
        entry.select_range(0, tk.END)
        entry.focus_set()
        self._edit_entry = entry

        done = [False]

        def commit(_e=None):
            if done[0]:
                return "break"
            done[0] = True
            new_val = var.get().strip()
            entry.destroy()
            if self._edit_entry is entry:
                self._edit_entry = None
            if new_val == current:
                return "break"
            ok = self._write_flac_tag(path, tag_key, new_val)
            if not ok:
                return "break"
            cur_vals[val_idx] = new_val
            # Recompute diff colouring for this row.
            local_v, lib_v = cur_vals[1], cur_vals[2]
            differs = (local_v != lib_v) and bool(local_v or lib_v) \
                      and self._local_path and self._lib_path
            existing_tags = self._tree.item(iid, "tags") or ()
            keep = [t for t in existing_tags
                    if t not in ("diff", "same", "even")]
            keep.append("diff" if differs else "same")
            self._tree.item(iid, values=cur_vals, tags=tuple(keep))
            return "break"

        def cancel(_e=None):
            if done[0]:
                return "break"
            done[0] = True
            entry.destroy()
            if self._edit_entry is entry:
                self._edit_entry = None
            return "break"

        entry.bind("<Return>",   commit)
        entry.bind("<Tab>",      commit)
        entry.bind("<Escape>",   cancel)
        entry.bind("<FocusOut>", commit)

    @staticmethod
    def _write_flac_tag(path: str, tag_key: str, value: str) -> bool:
        """Write *value* under *tag_key* in the FLAC at *path*.

        An empty string deletes the tag.  Returns True on success.
        """
        try:
            flac = FLAC(path)
            key = tag_key.lower()
            if value == "":
                if key in flac:
                    del flac[key]
            else:
                flac[key] = [value]
            flac.save()
            return True
        except Exception as exc:
            messagebox.showerror(
                "Tag save failed",
                f"Could not save tag {tag_key!r} to:\n{path}\n\n{exc}",
            )
            return False

    # ------------------------------------------------------------------ #
    # Cover right-click → copy image to clipboard                          #
    # ------------------------------------------------------------------ #

    def _on_cover_right_click(self, event, *, is_local: bool) -> None:
        path = self._local_path if is_local else self._lib_path
        data = self._local_cover_bytes if is_local else self._lib_cover_bytes
        # No file loaded on that side at all → no actions are meaningful.
        if not path and not data:
            return
        menu = tk.Menu(self, tearoff=0)
        if data:
            menu.add_command(
                label="📋  Copy Image to Clipboard",
                command=lambda d=data: self._copy_cover_to_clipboard(d),
            )
        # Embed from clipboard — available whenever a FLAC file is loaded on
        # that side, even if it currently has no cover art.
        if path and path.lower().endswith(".flac"):
            menu.add_command(
                label="🖼  Embed cover art from clipboard",
                command=lambda local=is_local: self._embed_cover_from_clipboard(
                    is_local=local),
            )
        if menu.index("end") is None:
            return
        menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------ #
    # Embed cover art from clipboard                                       #
    # ------------------------------------------------------------------ #

    def _embed_cover_from_clipboard(self, *, is_local: bool) -> None:
        """Read clipboard image, prompt for confirmation, embed into the file."""
        from PIL import Image, ImageGrab

        path = self._local_path if is_local else self._lib_path
        if not path:
            return

        # ── Grab from clipboard ── #
        try:
            img = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror("Clipboard error", str(exc), parent=self)
            return
        if not isinstance(img, Image.Image):
            messagebox.showinfo(
                "No image in clipboard",
                "The clipboard does not contain an image.\n\n"
                "Copy an image first, then try again.",
                parent=self,
            )
            return

        # ── Encode → JPEG or PNG (PNG only if alpha) ── #
        import io as _io
        buf = _io.BytesIO()
        if img.mode in ("RGBA", "LA", "PA"):
            img.save(buf, "PNG")
            mime = "image/png"
        else:
            img = img.convert("RGB")
            img.save(buf, "JPEG", quality=95)
            mime = "image/jpeg"
        img_bytes = buf.getvalue()

        # ── Confirmation dialog (reuses Scan-tab's _PasteCoverArtDialog) ── #
        dlg = _PasteCoverArtDialog(self.winfo_toplevel(),
                                   img, img_bytes, mime, 1)
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        # ── Write to FLAC ── #
        from mutagen.flac import Picture
        try:
            flac = FLAC(path)
            flac.clear_pictures()
            pic         = Picture()
            pic.type    = 3                                  # Front cover
            pic.mime    = mime
            pic.width   = img.size[0]
            pic.height  = img.size[1]
            pic.depth   = 32 if img.mode in ("RGBA", "LA") else 24
            pic.data    = img_bytes
            flac.add_picture(pic)
            flac.save()
            get_logger("scan_details_embed_cover").info(
                f"Embedded cover art ({'local' if is_local else 'lib'}): {path}")
        except Exception as exc:
            messagebox.showerror(
                "Embed failed",
                f"Could not embed cover art into:\n{path}\n\n{exc}",
                parent=self,
            )
            return

        # ── Refresh the panel so the new cover shows immediately ── #
        self.show(self._local_path, self._lib_path)

    @staticmethod
    def _copy_cover_to_clipboard(data: bytes) -> None:
        """Copy *data* (encoded image bytes) to the Windows clipboard (CF_DIB)."""
        import ctypes
        import io
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "BMP")
            dib = buf.getvalue()[14:]

            GMEM_MOVEABLE = 0x0002
            CF_DIB        = 8
            k32 = ctypes.windll.kernel32
            u32 = ctypes.windll.user32
            k32.GlobalAlloc.restype   = ctypes.c_void_p
            k32.GlobalAlloc.argtypes  = [ctypes.c_uint, ctypes.c_size_t]
            k32.GlobalLock.restype    = ctypes.c_void_p
            k32.GlobalLock.argtypes   = [ctypes.c_void_p]
            k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            u32.SetClipboardData.restype  = ctypes.c_void_p
            u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            u32.OpenClipboard(0)
            try:
                u32.EmptyClipboard()
                h = k32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
                p = k32.GlobalLock(h)
                ctypes.memmove(p, dib, len(dib))
                k32.GlobalUnlock(h)
                u32.SetClipboardData(CF_DIB, h)
            finally:
                u32.CloseClipboard()
        except Exception as exc:
            messagebox.showerror("Copy failed", str(exc))


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


def _read_audio_tags(file_path: str) -> tuple[str, str, str, str]:
    """Return (artist, title, album, bitrate) for any mutagen-supported audio file."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio is None:
            return "", "", "", ""
        tags = audio.tags or {}
        artist  = tags.get("artist", [""])[0] if "artist" in tags else ""
        title   = tags.get("title",  [""])[0] if "title"  in tags else ""
        album   = tags.get("album",  [""])[0] if "album"  in tags else ""
        bitrate = ""
        info = getattr(audio, "info", None)
        if info and getattr(info, "bitrate", 0):
            bitrate = f"{round(info.bitrate / 1000)} kbps"
        return artist, title, album, bitrate
    except Exception:
        return "", "", "", ""


def _check_lib_ready(file_path: str) -> bool:
    """
    Return True if the file meets lib-ready criteria:
      1. Has non-empty ARTIST, TITLE and ALBUM tags
      2. Has at least one embedded image with both dimensions > 300 px
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
            if w >= 300 and h >= 300:
                return True
        return False
    except Exception:
        return False


# Characters illegal in Windows file names
_ILLEGAL_CHARS = r'\/:*?"<>|'


def _sanitize_filename(name: str) -> str:
    """Replace Windows-illegal characters and strip leading/trailing spaces and dots."""
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "_")
    return name.strip(" .") or "_"


class _NormalizeResultWindow(tk.Toplevel):
    """Modal-ish result window listing renamed files and errors from a normalize run."""

    def __init__(self, parent, renamed: list, errors: list):
        super().__init__(parent)
        self.title("Normalize File Name — Results")
        self.configure(bg="#f5f5f5")
        self.minsize(700, 380)
        self.resizable(True, True)
        self._build(renamed, errors)
        self._center()

    def _build(self, renamed, errors):
        # ── Header ── #
        hdr = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        hdr.pack(fill=tk.X)
        n_ok  = len(renamed)
        n_err = len(errors)
        tk.Label(
            hdr,
            text=(
                f"✅ {n_ok} renamed"
                + (f"   ❌ {n_err} error{'s' if n_err != 1 else ''}" if n_err else "")
            ),
            font=("Segoe UI", 11, "bold"), fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # ── Table ── #
        frame = tk.Frame(self, bg="#f5f5f5")
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        cols = ("status", "original", "result")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tree.heading("status",   text="Status",        anchor=tk.W)
        tree.heading("original", text="Original Path", anchor=tk.W)
        tree.heading("result",   text="New Name / Error", anchor=tk.W)
        tree.column("status",   width=90,  stretch=False)
        tree.column("original", width=310, stretch=True)
        tree.column("result",   width=280, stretch=True)

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL,   command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        tree.pack(fill=tk.BOTH, expand=True)

        tree.tag_configure("ok",  background="#eafaf1", foreground="#1e8449")
        tree.tag_configure("err", background="#fdf2f2", foreground="#922b21")

        for old, new in renamed:
            tree.insert("", "end",
                        values=("✅ Renamed", old, os.path.basename(new)),
                        tags=("ok",))
        for path, msg in errors:
            tree.insert("", "end",
                        values=("❌ Error", path, msg),
                        tags=("err",))

        # ── Close button ── #
        ttk.Button(self, text="Close", command=self.destroy).pack(
            side=tk.BOTTOM, pady=(0, 10))

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  700)
        h = max(self.winfo_reqheight(), 380)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class _RestoreTitlesFromAIDialog(tk.Toplevel):
    """Paste-an-AI-response → preview → tag-update dialog.

    Accepts a list of source scan rows (with artist/title/album/path) and lets
    the user paste the JSON array produced by the "Restore Original-Language
    Names" prompt.  Matches AI entries to source rows by (artist, title, album)
    case-insensitively (with looser fallbacks), shows a preview tree the user
    can edit / toggle row-by-row, and on confirm writes ARTIST / TITLE / ALBUM
    Vorbis tags into each selected FLAC file.
    """

    # Treeview columns
    _COLS = (
        "apply",
        "cur_artist",  "new_artist",
        "cur_title",   "new_title",
        "cur_album",   "new_album",
    )
    # Columns the user can edit inline (double-click).
    _EDITABLE_COLS = {"#3": "new_artist", "#5": "new_title", "#7": "new_album"}

    def __init__(self, parent, panel, rows: list[dict]):
        super().__init__(parent)
        self._panel = panel
        self._rows  = rows
        # tree iid → dict(scan_iid, path, cur_*, new_*, apply, matched)
        self._preview: dict[str, dict] = {}

        self.title("Restore Tags from AI Response")
        self.configure(bg="#f5f5f5")
        self.minsize(1100, 600)
        self.resizable(True, True)
        self.grab_set()
        self._build()
        self._center()

    # ------------------------------------------------------------------ #
    # UI                                                                   #
    # ------------------------------------------------------------------ #

    def _build(self):
        hdr = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr, text="🤖  Restore Tags from AI Response",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)
        tk.Label(
            hdr,
            text=f"{len(self._rows)} source track{'s' if len(self._rows) != 1 else ''}",
            font=("Segoe UI", 9), fg="#bdc3c7", bg="#2c3e50",
        ).pack(side=tk.RIGHT)

        tk.Label(
            self,
            text="Paste the JSON array returned by the AI, then click "
                 "“Parse & Preview”.  Double-click a “New …” cell to edit "
                 "before confirming, or untick the checkbox to skip a row.",
            font=("Segoe UI", 9, "italic"), fg="#555555", bg="#f5f5f5",
            anchor="w", justify="left", wraplength=1060,
        ).pack(fill=tk.X, padx=12, pady=(8, 4))

        # ── JSON input ── #
        input_frame = tk.LabelFrame(
            self, text="  AI JSON response  ",
            font=("Segoe UI", 9, "bold"), bg="#f5f5f5", fg="#2c3e50",
            padx=8, pady=6,
        )
        input_frame.pack(fill=tk.BOTH, padx=12, pady=(0, 6))

        self._json_text = tk.Text(
            input_frame, height=6, wrap="word",
            font=("Consolas", 9), bg="white",
        )
        self._json_text.pack(fill=tk.BOTH, expand=True)

        btn_row = tk.Frame(self, bg="#f5f5f5")
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 6))
        ttk.Button(
            btn_row, text="📥  Paste from Clipboard",
            command=self._paste_from_clipboard,
        ).pack(side=tk.LEFT)
        ttk.Button(
            btn_row, text="🔍  Parse & Preview",
            command=self._parse_and_preview,
        ).pack(side=tk.LEFT, padx=(8, 0))

        # ── Preview tree ── #
        prev_frame = tk.LabelFrame(
            self, text="  Tag update preview  ",
            font=("Segoe UI", 9, "bold"), bg="#f5f5f5", fg="#2c3e50",
            padx=6, pady=4,
        )
        prev_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))

        self._tree = ttk.Treeview(
            prev_frame, columns=self._COLS, show="headings", selectmode="browse",
        )
        self._tree.heading("apply",      text="✓",             anchor=tk.CENTER)
        self._tree.heading("cur_artist", text="Current Artist", anchor=tk.W)
        self._tree.heading("new_artist", text="New Artist",     anchor=tk.W)
        self._tree.heading("cur_title",  text="Current Title",  anchor=tk.W)
        self._tree.heading("new_title",  text="New Title",      anchor=tk.W)
        self._tree.heading("cur_album",  text="Current Album",  anchor=tk.W)
        self._tree.heading("new_album",  text="New Album",      anchor=tk.W)
        self._tree.column("apply",      width=40,  anchor=tk.CENTER, stretch=False)
        self._tree.column("cur_artist", width=150, stretch=True)
        self._tree.column("new_artist", width=160, stretch=True)
        self._tree.column("cur_title",  width=180, stretch=True)
        self._tree.column("new_title",  width=190, stretch=True)
        self._tree.column("cur_album",  width=170, stretch=True)
        self._tree.column("new_album",  width=180, stretch=True)

        vsb = ttk.Scrollbar(prev_frame, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(prev_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.tag_configure("matched",   background="#eafaf1")
        self._tree.tag_configure("nomatch",   background="#fdf2f2", foreground="#922b21")
        self._tree.tag_configure("skipped",   background="#ecf0f1", foreground="#7f8c8d")
        self._tree.tag_configure("unchanged", background="#fdf6e3", foreground="#7d5a00")

        self._tree.bind("<Button-1>", self._on_click)
        self._tree.bind("<Double-1>", self._on_double_click)

        # Status / counts
        self._status_var = tk.StringVar(value="Paste a response above and click Parse & Preview.")
        tk.Label(
            self, textvariable=self._status_var,
            font=("Segoe UI", 9, "italic"), fg="#7f8c8d", bg="#f5f5f5",
            anchor="w", padx=12,
        ).pack(fill=tk.X)

        # ── Footer buttons ── #
        foot = tk.Frame(self, bg="#ecf0f1", pady=8, padx=12)
        foot.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(foot, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(
            foot, text="✅  Write Tags",
            command=self._confirm,
        ).pack(side=tk.RIGHT, padx=(0, 8))

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _paste_from_clipboard(self):
        try:
            data = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning(
                "Clipboard empty", "Nothing to paste.", parent=self,
            )
            return
        self._json_text.delete("1.0", "end")
        self._json_text.insert("1.0", data)

    def _parse_and_preview(self):
        raw = self._json_text.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning(
                "Empty input", "Paste the AI response first.", parent=self,
            )
            return

        # Strip optional ```json … ``` fences just in case.
        if raw.startswith("```"):
            first_nl = raw.find("\n")
            if first_nl != -1:
                raw = raw[first_nl + 1:]
            if raw.rstrip().endswith("```"):
                raw = raw.rstrip()[:-3]
            raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            messagebox.showerror(
                "Invalid JSON",
                f"Could not parse the response as JSON:\n\n{exc}",
                parent=self,
            )
            return

        if not isinstance(data, list):
            messagebox.showerror(
                "Unexpected JSON",
                "Expected a JSON array of objects with 'source' and "
                "'normalized' keys.",
                parent=self,
            )
            return

        # Index AI entries by progressively looser keys.
        ai_by_full:   dict[tuple[str, str, str], dict] = {}
        ai_by_at:     dict[tuple[str, str],      dict] = {}
        ai_by_title:  dict[str,                  dict] = {}

        def norm(s) -> str:
            return (s or "").strip().lower()

        for entry in data:
            if not isinstance(entry, dict):
                continue
            src   = entry.get("source")     or {}
            norm_ = entry.get("normalized") or {}
            if not isinstance(src, dict) or not isinstance(norm_, dict):
                continue
            s_a, s_t, s_al = norm(src.get("artist")), norm(src.get("title")), norm(src.get("album"))
            ai_by_full.setdefault((s_a, s_t, s_al), entry)
            ai_by_at.setdefault((s_a, s_t),         entry)
            ai_by_title.setdefault(s_t,             entry)

        # Clear and rebuild preview from current sources.
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._preview.clear()

        ready = matched_same = unmatched = 0
        for row in self._rows:
            s_a, s_t, s_al = norm(row["artist"]), norm(row["title"]), norm(row["album"])
            entry = (
                ai_by_full.get((s_a, s_t, s_al))
                or ai_by_at.get((s_a, s_t))
                or ai_by_title.get(s_t)
            )

            cur_artist = row["artist"]
            cur_title  = row["title"]
            cur_album  = row["album"]

            if entry is None:
                iid = self._tree.insert(
                    "", "end",
                    values=(
                        "·",
                        cur_artist, "(no AI match)",
                        cur_title,  "(no AI match)",
                        cur_album,  "(no AI match)",
                    ),
                    tags=("nomatch",),
                )
                self._preview[iid] = {
                    "scan_iid":   row["iid"],
                    "cur_artist": cur_artist,
                    "cur_title":  cur_title,
                    "cur_album":  cur_album,
                    "new_artist": cur_artist,
                    "new_title":  cur_title,
                    "new_album":  cur_album,
                    "apply":      False,
                    "matched":    False,
                }
                unmatched += 1
                continue

            norm_     = entry.get("normalized") or {}
            n_artist  = (norm_.get("artist") or cur_artist).strip() or cur_artist
            n_title   = (norm_.get("title")  or cur_title).strip()  or cur_title
            n_album   = (norm_.get("album")  or cur_album).strip()  or cur_album

            is_same = (
                n_artist == cur_artist
                and n_title  == cur_title
                and n_album  == cur_album
            )
            tag = "unchanged" if is_same else "matched"
            iid = self._tree.insert(
                "", "end",
                values=(
                    " " if is_same else "✓",
                    cur_artist, n_artist,
                    cur_title,  n_title,
                    cur_album,  n_album,
                ),
                tags=(tag,),
            )
            self._preview[iid] = {
                "scan_iid":   row["iid"],
                "cur_artist": cur_artist,
                "cur_title":  cur_title,
                "cur_album":  cur_album,
                "new_artist": n_artist,
                "new_title":  n_title,
                "new_album":  n_album,
                "apply":      not is_same,
                "matched":    True,
            }
            if is_same:
                matched_same += 1
            else:
                ready += 1

        self._status_var.set(
            f"Parsed {len(data)} AI entr{'y' if len(data) == 1 else 'ies'} · "
            f"{ready} ready to update · "
            f"{matched_same} already match · "
            f"{unmatched} no match."
        )

    def _on_click(self, event):
        # Toggle the Apply checkbox when its column is clicked.
        region = self._tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        if self._tree.identify_column(event.x) != "#1":
            return
        iid = self._tree.identify_row(event.y)
        if not iid or iid not in self._preview:
            return
        info = self._preview[iid]
        if not info["matched"]:
            return
        info["apply"] = not info["apply"]
        self._refresh_row(iid)

    def _on_double_click(self, event):
        if self._tree.identify("region", event.x, event.y) != "cell":
            return
        col = self._tree.identify_column(event.x)
        field = self._EDITABLE_COLS.get(col)
        if not field:
            return
        iid = self._tree.identify_row(event.y)
        if not iid or iid not in self._preview:
            return
        info = self._preview[iid]
        if not info["matched"]:
            return
        self._start_inline_edit(iid, col, field)

    def _start_inline_edit(self, iid: str, col: str, field: str):
        x, y, w, h = self._tree.bbox(iid, col)
        if not w:
            return
        info     = self._preview[iid]
        edit_var = tk.StringVar(value=info[field])
        entry = tk.Entry(self._tree, textvariable=edit_var, font=("Segoe UI", 9))
        entry.place(x=x, y=y, width=w, height=h)
        entry.icursor("end")
        entry.focus_set()

        def commit(_=None):
            info[field] = edit_var.get().strip()
            # Recompute "unchanged" status after edit
            is_same = (
                info["new_artist"] == info["cur_artist"]
                and info["new_title"]  == info["cur_title"]
                and info["new_album"]  == info["cur_album"]
            )
            if is_same:
                info["apply"] = False
            entry.destroy()
            self._refresh_row(iid)

        def cancel(_=None):
            entry.destroy()

        entry.bind("<Return>",   commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>",   cancel)

    def _refresh_row(self, iid: str):
        info = self._preview[iid]
        is_same = (
            info["new_artist"] == info["cur_artist"]
            and info["new_title"]  == info["cur_title"]
            and info["new_album"]  == info["cur_album"]
        )
        if not info["matched"]:
            tag      = "nomatch"
            apply_mk = "·"
            new_a    = "(no AI match)"
            new_t    = "(no AI match)"
            new_al   = "(no AI match)"
        else:
            new_a, new_t, new_al = info["new_artist"], info["new_title"], info["new_album"]
            if is_same:
                tag      = "unchanged"
                apply_mk = " "
            else:
                tag      = "matched" if info["apply"] else "skipped"
                apply_mk = "✓" if info["apply"] else " "
        self._tree.item(
            iid,
            values=(
                apply_mk,
                info["cur_artist"], new_a,
                info["cur_title"],  new_t,
                info["cur_album"],  new_al,
            ),
            tags=(tag,),
        )

    def _confirm(self):
        targets = [
            (iid, info) for iid, info in self._preview.items()
            if info["matched"] and info["apply"]
        ]
        if not targets:
            messagebox.showinfo(
                "Nothing to update",
                "No rows are checked for updating.",
                parent=self,
            )
            return

        ok = err = 0
        errors: list[tuple[str, str]] = []
        for iid, info in targets:
            success, msg = self._panel._apply_restored_tags(
                info["scan_iid"],
                info["new_artist"],
                info["new_title"],
                info["new_album"],
            )
            if success:
                ok += 1
                # Mark row as completed: the current values now equal the new ones.
                info["cur_artist"] = info["new_artist"]
                info["cur_title"]  = info["new_title"]
                info["cur_album"]  = info["new_album"]
                info["apply"]      = False
                self._refresh_row(iid)
            else:
                err += 1
                errors.append((info["cur_title"] or info["scan_iid"], msg))

        self._status_var.set(
            f"Updated {ok} file{'s' if ok != 1 else ''}"
            + (f", {err} error{'s' if err != 1 else ''}." if err else ".")
        )

        try:
            self._panel.status_var.set(
                f"🤖  Restore Tags — {ok} updated"
                + (f", {err} error{'s' if err != 1 else ''}." if err else ".")
            )
        except Exception:
            pass

        if errors:
            preview = "\n".join(f"• {name}: {msg}" for name, msg in errors[:8])
            if len(errors) > 8:
                preview += f"\n…and {len(errors) - 8} more."
            messagebox.showwarning(
                "Some files were not updated", preview, parent=self,
            )

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  1100)
        h = max(self.winfo_reqheight(), 600)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class _DeleteConfirmDialog(tk.Toplevel):
    """Modal confirmation dialog for 'Remove Lib Duplicates'.

    Shows two detailed lists:
      • Files that will be permanently deleted (MD5 confirmed in lib) —
        displays Artist, Title, Album, Type, Bitrate, Size, Scan path, Lib path.
      • Files that will be skipped (MD5 not found / file missing / error) —
        displays Artist, Title, Album, Type, Bitrate, Size, Full Path, Reason.

    Sets ``self.confirmed = True`` when the user clicks the delete button.
    """

    # Column definitions: (id, heading, width, anchor)
    _DEL_COLS = [
        ("artist",   "Artist",         130, tk.W),
        ("title",    "Title",          160, tk.W),
        ("album",    "Album",          120, tk.W),
        ("type",     "Type",            50, tk.W),
        ("bitrate",  "Bitrate",         65, tk.E),
        ("size",     "Size",            70, tk.E),
        ("scan",     "Scan File Path", 300, tk.W),
        ("lib",      "Library Path",   260, tk.W),
    ]
    _SKIP_COLS = [
        ("artist",  "Artist",         130, tk.W),
        ("title",   "Title",          160, tk.W),
        ("album",   "Album",          120, tk.W),
        ("type",    "Type",            50, tk.W),
        ("bitrate", "Bitrate",         65, tk.E),
        ("size",    "Size",            70, tk.E),
        ("path",    "Full Path",      300, tk.W),
        ("reason",  "Reason",         200, tk.W),
    ]

    def __init__(self, parent, to_delete: list, cant_delete: list):
        super().__init__(parent)
        self.confirmed = False
        self.remove_empty_folders = False
        self._remove_empty_var = tk.BooleanVar(value=True)
        self._to_delete   = to_delete    # (item_id, full_path, lib_row dict, scan_vals tuple)
        self._cant_delete = cant_delete  # (full_path, reason, scan_vals tuple)

        self.title("Remove Lib Duplicates — Confirm")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.minsize(960, 580)
        self.resizable(True, True)
        self._build()
        self._center()

    def _build(self):
        n_del  = len(self._to_delete)
        n_skip = len(self._cant_delete)

        # Use grid on the Toplevel so row weights control vertical expansion.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=3)   # delete table  → 3/5 of extra space
        self.rowconfigure(5, weight=2)   # skipped table → 2/5 of extra space

        # ── Row 0: Header ── #
        hdr = tk.Frame(self, bg="#c0392b", pady=8, padx=12)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hdr, text="🗑  Remove Lib Duplicates",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#c0392b",
        ).pack(side=tk.LEFT)

        # ── Row 1: Summary banner ── #
        summ = tk.Frame(self, bg="#fdf2f2", padx=12, pady=6)
        summ.grid(row=1, column=0, sticky="ew")
        tk.Label(
            summ,
            text=(
                f"{n_del} file{'s' if n_del != 1 else ''} will be PERMANENTLY DELETED"
                f"   ·   {n_skip} file{'s' if n_skip != 1 else ''} skipped"
                f" (not confirmed in lib)"
            ),
            font=("Segoe UI", 9, "bold"), fg="#c0392b", bg="#fdf2f2",
        ).pack(anchor="w")

        # ── Row 2: Delete section label ── #
        tk.Label(
            self,
            text=f"  Files to DELETE ({n_del})  — MD5 confirmed in library",
            font=("Segoe UI", 9, "bold"), fg="#c0392b", bg="#f5f5f5",
            anchor="w", pady=4,
        ).grid(row=2, column=0, sticky="ew")

        # ── Row 3: Delete table (expands) ── #
        del_frame = tk.Frame(self, bg="#f5f5f5")
        del_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 4))
        del_frame.columnconfigure(0, weight=1)
        del_frame.rowconfigure(0, weight=1)

        col_ids = [c[0] for c in self._DEL_COLS]
        del_tree = ttk.Treeview(del_frame, columns=col_ids, show="headings",
                                selectmode="none")
        for cid, heading, width, anchor in self._DEL_COLS:
            del_tree.heading(cid, text=heading, anchor=anchor)
            del_tree.column(cid, width=width, anchor=anchor,
                            stretch=(cid in ("scan", "lib")))
        del_tree.tag_configure("del", background="#fdf2f2")

        vsb1 = ttk.Scrollbar(del_frame, orient=tk.VERTICAL,   command=del_tree.yview)
        hsb1 = ttk.Scrollbar(del_frame, orient=tk.HORIZONTAL, command=del_tree.xview)
        del_tree.configure(yscrollcommand=vsb1.set, xscrollcommand=hsb1.set)
        del_tree.grid(row=0, column=0, sticky="nsew")
        vsb1.grid(row=0, column=1, sticky="ns")
        hsb1.grid(row=1, column=0, sticky="ew")

        for _, full_path, lib_row, sv in self._to_delete:
            lib_path = f"{lib_row['partition']} / {lib_row['rel_path']}"
            del_tree.insert("", "end", tags=("del",), values=(
                sv[4], sv[5], sv[6], sv[3], sv[7], sv[8],
                full_path, lib_path,
            ))

        # ── Row 4: Skipped section label ── #
        tk.Label(
            self,
            text=f"  Files SKIPPED ({n_skip})  — not confirmed in library",
            font=("Segoe UI", 9, "bold"), fg="#7f8c8d", bg="#f5f5f5",
            anchor="w", pady=4,
        ).grid(row=4, column=0, sticky="ew")

        # ── Row 5: Skipped table (expands) ── #
        skip_frame = tk.Frame(self, bg="#f5f5f5")
        skip_frame.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 4))
        skip_frame.columnconfigure(0, weight=1)
        skip_frame.rowconfigure(0, weight=1)

        skip_col_ids = [c[0] for c in self._SKIP_COLS]
        skip_tree = ttk.Treeview(skip_frame, columns=skip_col_ids, show="headings",
                                 selectmode="none")
        for cid, heading, width, anchor in self._SKIP_COLS:
            skip_tree.heading(cid, text=heading, anchor=anchor)
            skip_tree.column(cid, width=width, anchor=anchor,
                             stretch=(cid in ("path", "reason")))

        vsb2 = ttk.Scrollbar(skip_frame, orient=tk.VERTICAL,   command=skip_tree.yview)
        hsb2 = ttk.Scrollbar(skip_frame, orient=tk.HORIZONTAL, command=skip_tree.xview)
        skip_tree.configure(yscrollcommand=vsb2.set, xscrollcommand=hsb2.set)
        skip_tree.grid(row=0, column=0, sticky="nsew")
        vsb2.grid(row=0, column=1, sticky="ns")
        hsb2.grid(row=1, column=0, sticky="ew")

        for full_path, reason, sv in self._cant_delete:
            skip_tree.insert("", "end", values=(
                sv[4], sv[5], sv[6], sv[3], sv[7], sv[8],
                full_path, reason,
            ))

        # ── Row 6: Buttons ── #
        btn_frame = tk.Frame(self, bg="#f5f5f5", pady=8, padx=12)
        btn_frame.grid(row=6, column=0, sticky="ew")
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(
            side=tk.RIGHT, padx=(4, 0))
        del_label = (
            f"🗑  Delete {n_del} File{'s' if n_del != 1 else ''}"
            if n_del else "Nothing to Delete"
        )
        ttk.Button(
            btn_frame, text=del_label,
            command=self._confirm,
            state="normal" if n_del else "disabled",
        ).pack(side=tk.RIGHT)
        ttk.Checkbutton(
            btn_frame, text="Remove empty folders",
            variable=self._remove_empty_var,
        ).pack(side=tk.LEFT)

    def _confirm(self):
        self.confirmed = True
        self.remove_empty_folders = self._remove_empty_var.get()
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  720)
        h = max(self.winfo_reqheight(), 520)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class _DeleteFilesDialog(tk.Toplevel):
    """Modal confirmation dialog before permanently deleting files from disk.

    Displays Artist / Title / Album / Type / Size / Full Path for every
    selected file so the user can review before committing.

    Sets ``self.confirmed = True`` when the user clicks the delete button.
    """

    _COLS = [
        ("artist",  "Artist",    130, tk.W),
        ("title",   "Title",     160, tk.W),
        ("album",   "Album",     120, tk.W),
        ("type",    "Type",       50, tk.CENTER),
        ("size",    "Size",       70, tk.E),
        ("path",    "Full Path", 340, tk.W),
    ]

    def __init__(self, parent, rows: list[tuple]):
        """
        Parameters
        ----------
        rows : list of tuples
            Each tuple is the full ``values`` tuple from the scan Treeview row:
            (lib_ready, in_lib, full_path, file_type, artist, title, album,
             bitrate, size, modified)
        """
        super().__init__(parent)
        self.confirmed = False
        self._rows = rows

        self.title("Delete Files — Confirm")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.minsize(860, 420)
        self.resizable(True, True)
        self._build()
        self._center()

    # ------------------------------------------------------------------ #

    def _build(self):
        n = len(self._rows)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)   # table gets all extra vertical space

        # ── Header ── #
        hdr = tk.Frame(self, bg="#c0392b", pady=8, padx=12)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hdr, text="🗑  Delete Files",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#c0392b",
        ).pack(side=tk.LEFT)

        # ── Warning banner ── #
        warn = tk.Frame(self, bg="#fdf2f2", padx=12, pady=8)
        warn.grid(row=1, column=0, sticky="ew")
        tk.Label(
            warn,
            text=(
                f"⚠  {n} file{'s' if n != 1 else ''} will be PERMANENTLY deleted "
                "from disk.  This cannot be undone."
            ),
            font=("Segoe UI", 9, "bold"), fg="#c0392b", bg="#fdf2f2",
        ).pack(anchor="w")

        # ── File table ── #
        tbl_frame = tk.Frame(self, bg="#f5f5f5")
        tbl_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
        tbl_frame.columnconfigure(0, weight=1)
        tbl_frame.rowconfigure(0, weight=1)

        col_ids = [c[0] for c in self._COLS]
        tree = ttk.Treeview(tbl_frame, columns=col_ids, show="headings",
                            selectmode="none")
        for cid, heading, width, anchor in self._COLS:
            tree.heading(cid, text=heading, anchor=anchor)
            tree.column(cid, width=width, anchor=anchor, stretch=(cid == "path"))
        tree.tag_configure("row", background="#fdf2f2")

        vsb = ttk.Scrollbar(tbl_frame, orient=tk.VERTICAL,   command=tree.yview)
        hsb = ttk.Scrollbar(tbl_frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        for sv in self._rows:
            # sv = (lib_ready, in_lib, full_path, file_type,
            #        artist, title, album, bitrate, size, modified)
            tree.insert("", "end", tags=("row",),
                        values=(sv[4], sv[5], sv[6], sv[3], sv[8], sv[2]))

        # ── Buttons ── #
        # Visual layout (left → right): [🗑 Delete N File(s)] [Cancel]
        # Default selection (focused): Cancel — safer default for a destructive action.
        btn_frame = tk.Frame(self, bg="#f5f5f5", pady=8, padx=12)
        btn_frame.grid(row=3, column=0, sticky="ew")

        self._btn_cancel = tk.Button(
            btn_frame, text="Cancel",
            font=("Segoe UI", 9),
            bg="#d0d3d4", fg="#2c3e50",
            activebackground="#95a5a6", activeforeground="white",
            padx=14, pady=5, cursor="hand2",
            relief=tk.FLAT, bd=2,
            command=self.destroy,
        )
        self._btn_cancel.pack(side=tk.RIGHT, padx=(4, 0))

        self._btn_delete = tk.Button(
            btn_frame,
            text=f"🗑  Delete {n} File{'s' if n != 1 else ''}",
            font=("Segoe UI", 9),
            bg="#fadbd8", fg="#c0392b",
            activebackground="#c0392b", activeforeground="white",
            padx=14, pady=5, cursor="hand2",
            relief=tk.FLAT, bd=2,
            command=self._confirm,
        )
        self._btn_delete.pack(side=tk.RIGHT)

        # Left-to-right nav order: Delete(0)  Cancel(1);  default = Cancel (idx=1)
        self._nav_buttons = [self._btn_delete, self._btn_cancel]
        self._nav_idx = 1

        for btn in self._nav_buttons:
            btn.bind("<Left>",   self._on_btn_left)
            btn.bind("<Right>",  self._on_btn_right)
            btn.bind("<Return>", lambda e: self._nav_buttons[self._nav_idx].invoke())
        # Esc closes the dialog (same as Cancel)
        self.bind("<Escape>", lambda e: self.destroy())

        self._apply_btn_focus()
        self.after_idle(lambda: self._nav_buttons[self._nav_idx].focus_set())

    # ------------------------------------------------------------------ #

    # Per-button style tables: (bg, fg, relief, bd)
    _BTN_STYLE_NORMAL = {
        "delete": ("#fadbd8", "#c0392b", tk.FLAT,  1),
        "cancel": ("#d0d3d4", "#2c3e50", tk.FLAT,  1),
    }
    _BTN_STYLE_FOCUSED = {
        "delete": ("#c0392b", "white",   tk.SOLID, 2),
        "cancel": ("#555555", "white",   tk.SOLID, 2),
    }
    _BTN_KEYS = ["delete", "cancel"]

    def _apply_btn_focus(self):
        for i, (btn, key) in enumerate(zip(self._nav_buttons, self._BTN_KEYS)):
            style = (self._BTN_STYLE_FOCUSED if i == self._nav_idx
                     else self._BTN_STYLE_NORMAL)[key]
            btn.configure(bg=style[0], fg=style[1], relief=style[2], bd=style[3])

    def _on_btn_left(self, _event):
        if self._nav_idx > 0:
            self._nav_idx -= 1
            self._apply_btn_focus()
            self._nav_buttons[self._nav_idx].focus_set()

    def _on_btn_right(self, _event):
        if self._nav_idx < len(self._nav_buttons) - 1:
            self._nav_idx += 1
            self._apply_btn_focus()
            self._nav_buttons[self._nav_idx].focus_set()

    def _confirm(self):
        self.confirmed = True
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  860)
        h = max(self.winfo_reqheight(), 420)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class _PasteCoverArtDialog(tk.Toplevel):
    """Preview clipboard image before embedding it into selected FLAC files.

    Parameters
    ----------
    parent   : tk widget
    img      : PIL.Image — the image read from the clipboard
    img_bytes: bytes     — the encoded bytes that will actually be written (JPEG/PNG)
    mime     : str       — "image/jpeg" or "image/png"
    n_files  : int       — number of selected files that will be updated
    """

    def __init__(self, parent, img, img_bytes: bytes, mime: str, n_files: int):
        super().__init__(parent)
        self.confirmed = False
        self._img       = img
        self._img_bytes = img_bytes
        self._mime      = mime
        self._n_files   = n_files

        self.title("Embed Cover Art from Clipboard — Preview")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.resizable(False, False)
        self._build()
        self._center()

    def _build(self):
        from PIL import ImageTk

        # ── Header ── #
        hdr = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr, text="🖼  Embed Cover Art from Clipboard",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        body = tk.Frame(self, bg="#f5f5f5", padx=20, pady=16)
        body.pack(fill=tk.BOTH)

        # ── Image preview ── #
        thumb = self._img.copy()
        thumb.thumbnail((300, 300))
        self._photo = ImageTk.PhotoImage(thumb)
        tk.Label(body, image=self._photo, bg="#f5f5f5",
                 relief="groove", bd=1).pack(pady=(0, 14))

        # ── Metadata grid ── #
        meta = tk.Frame(body, bg="#f5f5f5")
        meta.pack(anchor="w")

        def row(label, value, r):
            tk.Label(meta, text=label, font=("Segoe UI", 9, "bold"),
                     bg="#f5f5f5", anchor="e", width=12).grid(
                row=r, column=0, sticky="e", pady=2, padx=(0, 8))
            tk.Label(meta, text=value, font=("Segoe UI", 9),
                     bg="#f5f5f5", anchor="w").grid(
                row=r, column=1, sticky="w", pady=2)

        w, h = self._img.size
        fmt   = "JPEG" if self._mime == "image/jpeg" else "PNG"
        ksize = len(self._img_bytes) / 1024
        size_str = f"{ksize:.1f} KB" if ksize < 1024 else f"{ksize / 1024:.2f} MB"
        n     = self._n_files

        row("Dimensions:", f"{w} × {h} px", 0)
        row("Format:",     fmt, 1)
        row("Size:",       size_str, 2)
        row("Apply to:",   f"{n} file{'s' if n != 1 else ''}", 3)

        # ── Warning ── #
        tk.Label(
            body,
            text="⚠  Any existing cover art in these files will be replaced.",
            font=("Segoe UI", 8, "italic"), fg="#c0392b", bg="#f5f5f5",
        ).pack(anchor="w", pady=(12, 0))

        # ── Buttons ── #
        btn_frame = tk.Frame(self, bg="#f5f5f5", pady=10, padx=12)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(
            side=tk.RIGHT, padx=(4, 0))
        ttk.Button(
            btn_frame,
            text=f"🖼  Embed in {self._n_files} File{'s' if self._n_files != 1 else ''}",
            command=self._confirm,
        ).pack(side=tk.RIGHT)

    def _confirm(self):
        self.confirmed = True
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


# ─────────────────────────────────────────────────────────────────────────── #
# Update Track in Lib — confirmation dialog                                   #
# ─────────────────────────────────────────────────────────────────────────── #

class _DeleteScanFileConfirmDialog(tk.Toplevel):
    """Small modal asking the user to confirm deleting the scan file from disk.

    Default selection is **No** so accidental keystrokes cannot trigger a
    destructive action.  Arrow keys move between the two buttons; Enter
    activates the focused one.

    Sets ``self.confirmed = True`` only when the user explicitly chooses Yes.
    """

    _BTN_STYLE_NORMAL = {
        "yes": ("#fadbd8", "#c0392b", tk.FLAT,  1),
        "no":  ("#d0d3d4", "#2c3e50", tk.FLAT,  1),
    }
    _BTN_STYLE_FOCUSED = {
        "yes": ("#c0392b", "white",   tk.SOLID, 2),
        "no":  ("#555555", "white",   tk.SOLID, 2),
    }
    _BTN_KEYS = ["yes", "no"]

    def __init__(self, parent, file_path: str):
        super().__init__(parent)
        self.confirmed = False
        self._file_path = file_path
        self.title("Confirm Delete")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.resizable(False, False)
        self._build()
        self._center()

    def _build(self):
        # ── Header ── #
        hdr = tk.Frame(self, bg="#c0392b", pady=8, padx=16)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr, text="🗑  Delete Scan File",
            font=("Segoe UI", 11, "bold"), fg="white", bg="#c0392b",
        ).pack(side=tk.LEFT)

        # ── Body ── #
        body = tk.Frame(self, bg="#f5f5f5", padx=20, pady=16)
        body.pack(fill=tk.BOTH)
        tk.Label(
            body,
            text="Permanently delete this scan file from disk?",
            font=("Segoe UI", 10), fg="#2c3e50", bg="#f5f5f5",
        ).pack(anchor="w")
        tk.Label(
            body, text=self._file_path,
            font=("Segoe UI", 8), fg="#555555", bg="#f5f5f5",
            wraplength=400, justify="left",
        ).pack(anchor="w", pady=(4, 10))
        tk.Label(
            body,
            text="⚠  This cannot be undone.",
            font=("Segoe UI", 9, "italic"), fg="#c0392b", bg="#f5f5f5",
        ).pack(anchor="w")

        # ── Buttons ── #
        btn_row = tk.Frame(self, bg="#ecf0f1", pady=10, padx=16)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)

        # Visual layout (left → right): [🗑 Yes, Delete] [No]
        self._btn_yes = tk.Button(
            btn_row, text="🗑  Yes, Delete",
            font=("Segoe UI", 9),
            bg="#fadbd8", fg="#c0392b",
            activebackground="#c0392b", activeforeground="white",
            padx=14, pady=5, cursor="hand2",
            relief=tk.FLAT, bd=2,
            command=self._on_yes,
        )
        self._btn_yes.pack(side=tk.RIGHT)

        self._btn_no = tk.Button(
            btn_row, text="No",
            font=("Segoe UI", 9),
            bg="#d0d3d4", fg="#2c3e50",
            activebackground="#95a5a6", activeforeground="white",
            padx=14, pady=5, cursor="hand2",
            relief=tk.FLAT, bd=2,
            command=self.destroy,
        )
        self._btn_no.pack(side=tk.RIGHT, padx=(0, 8))

        # Nav order left→right: No(0)  Yes(1);  default = No (idx=0)
        self._nav_buttons = [self._btn_no, self._btn_yes]
        self._nav_idx = 0   # No is the default

        for btn in self._nav_buttons:
            btn.bind("<Left>",   self._on_left)
            btn.bind("<Right>",  self._on_right)
            btn.bind("<Return>", lambda e: self._nav_buttons[self._nav_idx].invoke())
        # Esc = No
        self.bind("<Escape>", lambda e: self.destroy())

        self._apply_focus()
        self.after_idle(lambda: self._nav_buttons[self._nav_idx].focus_set())

    def _apply_focus(self):
        for i, (btn, key) in enumerate(zip(self._nav_buttons, self._BTN_KEYS)):
            style = (self._BTN_STYLE_FOCUSED if i == self._nav_idx
                     else self._BTN_STYLE_NORMAL)[key]
            btn.configure(bg=style[0], fg=style[1], relief=style[2], bd=style[3])

    def _on_left(self, _event):
        if self._nav_idx > 0:
            self._nav_idx -= 1
            self._apply_focus()
            self._nav_buttons[self._nav_idx].focus_set()

    def _on_right(self, _event):
        if self._nav_idx < len(self._nav_buttons) - 1:
            self._nav_idx += 1
            self._apply_focus()
            self._nav_buttons[self._nav_idx].focus_set()

    def _on_yes(self):
        self.confirmed = True
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  460)
        h = max(self.winfo_reqheight(), 220)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class _UpdateTrackInLibDialog(tk.Toplevel):
    """Modal confirmation dialog for overwriting a lib track with a scan track.

    Shows a side-by-side comparison of bitrate and cover art so the user can
    make an informed decision before the copy is performed.

    Sets ``self.confirmed = True`` when the user clicks the Update button.
    """

    _THUMB = 220   # max thumbnail dimension in px

    def __init__(self, parent, src_path: str, lib_path: str,
                 partition: str, rel_path: str):
        super().__init__(parent)
        self.confirmed = False
        self._src_path   = src_path
        self._lib_path   = lib_path
        self._partition  = partition
        self._rel_path   = rel_path
        self._src_photo  = None   # keep PhotoImage refs alive
        self._lib_photo  = None

        self.title("Update Track in Lib — Confirm")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.resizable(True, True)
        self._build()
        self._center()

    # ------------------------------------------------------------------ #
    # UI                                                                   #
    # ------------------------------------------------------------------ #

    def _build(self):
        from music.compare_tracks_panel import _load_flac_info

        src_info = _load_flac_info(self._src_path)
        lib_info = _load_flac_info(self._lib_path)

        # ── Header ── #
        hdr = tk.Frame(self, bg="#2c3e50", pady=10, padx=16)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr, text="⬆  Update Track in Lib",
            font=("Segoe UI", 13, "bold"), fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # ── Metadata band ── #
        artist = src_info["tags"].get("ARTIST", "") or lib_info["tags"].get("ARTIST", "")
        title  = src_info["tags"].get("TITLE",  "") or lib_info["tags"].get("TITLE",  "")
        album  = src_info["tags"].get("ALBUM",  "") or lib_info["tags"].get("ALBUM",  "")
        meta_text = "  •  ".join(filter(None, [
            f"Artist: {artist}" if artist else "",
            f"Album: {album}"   if album  else "",
            f"Title: {title}"   if title  else "",
        ])) or "(no tags)"

        meta_bar = tk.Frame(self, bg="#eaf0fb", padx=16, pady=6)
        meta_bar.pack(fill=tk.X)
        tk.Label(
            meta_bar, text=meta_text,
            font=("Segoe UI", 9), fg="#2c3e50", bg="#eaf0fb",
            anchor="w", wraplength=560,
        ).pack(anchor="w")

        # ── Side-by-side comparison body ── #
        body = tk.Frame(self, bg="#f5f5f5", padx=16, pady=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(2, weight=1, uniform="col")

        self._build_side(body, column=0, label="📁  Scan Track",
                         info=src_info, is_src=True)

        # Vertical divider
        tk.Frame(body, bg="#bdc3c7", width=1).grid(
            row=0, column=1, sticky="ns", padx=14)

        self._build_side(body, column=2, label="📚  Lib Track",
                         info=lib_info, is_src=False)

        # ── Warning banner ── #
        warn = tk.Frame(self, bg="#fdf2f2", padx=16, pady=6)
        warn.pack(fill=tk.X)
        tk.Label(
            warn,
            text="⚠  The Lib track will be permanently overwritten.  This cannot be undone.",
            font=("Segoe UI", 9, "italic"), fg="#c0392b", bg="#fdf2f2",
        ).pack(anchor="w")

        # ── Buttons ── #
        # Visual layout (left → right): [🗑 Delete Scan File]  …  [⬆ Update Lib Track] [Cancel]
        btn_row = tk.Frame(self, bg="#ecf0f1", pady=10, padx=16)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)

        self._btn_delete = tk.Button(
            btn_row, text="🗑  Delete Scan File",
            font=("Segoe UI", 9),
            bg="#fadbd8", fg="#c0392b",
            activebackground="#c0392b", activeforeground="white",
            padx=14, pady=5, cursor="hand2",
            relief=tk.FLAT, bd=2,
            command=self._on_delete_file,
        )
        self._btn_delete.pack(side=tk.LEFT)

        self._btn_cancel = tk.Button(
            btn_row, text="Cancel",
            font=("Segoe UI", 9),
            bg="#d0d3d4", fg="#2c3e50",
            activebackground="#95a5a6", activeforeground="white",
            padx=14, pady=5, cursor="hand2",
            relief=tk.FLAT, bd=2,
            command=self.destroy,
        )
        self._btn_cancel.pack(side=tk.RIGHT)

        self._btn_update = tk.Button(
            btn_row, text="⬆  Update Lib Track",
            font=("Segoe UI", 9),
            bg="#d0d3d4", fg="#2c3e50",
            activebackground="#1f618d", activeforeground="white",
            padx=14, pady=5, cursor="hand2",
            relief=tk.FLAT, bd=2,
            command=self._confirm,
        )
        self._btn_update.pack(side=tk.RIGHT, padx=(0, 8))

        # Left-to-right nav order: Delete(0)  Update(1)  Cancel(2)
        # Default selection: Update Lib Track (idx=1)
        self._nav_buttons = [self._btn_delete, self._btn_update, self._btn_cancel]
        self._nav_idx = 1

        for btn in self._nav_buttons:
            btn.bind("<Left>",   self._on_btn_left)
            btn.bind("<Right>",  self._on_btn_right)
            btn.bind("<Return>", lambda e: self._nav_buttons[self._nav_idx].invoke())
        # Esc closes the dialog (same as Cancel)
        self.bind("<Escape>", lambda e: self.destroy())

        self._apply_btn_focus()
        self.after_idle(lambda: self._nav_buttons[self._nav_idx].focus_set())

    def _build_side(self, parent, *, column: int, label: str, info: dict, is_src: bool):
        """Render one column (scan or lib) into *parent* grid."""
        frm = tk.Frame(parent, bg="#f5f5f5")
        frm.grid(row=0, column=column, sticky="nsew")

        # Section title
        tk.Label(frm, text=label, font=("Segoe UI", 10, "bold"),
                 fg="#2c3e50", bg="#f5f5f5").pack(anchor="w")

        # File name
        fname = os.path.basename(info["path"]) or "—"
        tk.Label(frm, text=fname, font=("Segoe UI", 8),
                 fg="#555555", bg="#f5f5f5",
                 wraplength=260, justify="left").pack(anchor="w", pady=(0, 6))

        # Cover art thumbnail
        cover_label = tk.Label(frm, bg="#d5d8dc", text="No cover art",
                               relief=tk.GROOVE)
        cover_label.pack(anchor="w")

        dims_var = tk.StringVar(value="")
        tk.Label(frm, textvariable=dims_var, font=("Segoe UI", 8),
                 fg="#888888", bg="#f5f5f5").pack(anchor="w")

        self._render_cover(cover_label, info["cover"], info["cover_dims"],
                           dims_var, is_src=is_src)

        # Bitrate row
        self._info_row(frm, "Bitrate",
                       info["bitrate"] or "—",
                       highlight=(info["bitrate"] != ""))

        # Cover size row
        self._info_row(frm, "Cover size", info["cover_size_kb"] or "—")

        # Cover dimensions row
        self._info_row(frm, "Cover dims", info["cover_dims"] or "—")

        # Full path (small)
        tk.Label(frm, text=info["path"], font=("Segoe UI", 7),
                 fg="#aaaaaa", bg="#f5f5f5",
                 wraplength=260, justify="left").pack(anchor="w", pady=(8, 0))

    @staticmethod
    def _info_row(parent, label: str, value: str, *, highlight: bool = False):
        row = tk.Frame(parent, bg="#f5f5f5")
        row.pack(anchor="w", pady=2)
        tk.Label(row, text=f"{label}:", font=("Segoe UI", 9, "bold"),
                 fg="#2c3e50", bg="#f5f5f5", width=12, anchor="e").pack(side=tk.LEFT)
        tk.Label(row, text=value,
                 font=("Segoe UI", 9, "bold" if highlight else "normal"),
                 fg="#1e8449" if highlight else "#333333",
                 bg="#f5f5f5").pack(side=tk.LEFT, padx=(4, 0))

    def _render_cover(self, label: tk.Label, data: bytes | None,
                      dims: str, dims_var: tk.StringVar, *, is_src: bool):
        if data:
            try:
                import io as _io
                from PIL import Image, ImageTk
                img = Image.open(_io.BytesIO(data))
                img.thumbnail((self._THUMB, self._THUMB))
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
        label.configure(image="", text="No cover art", bg="#d5d8dc",
                        width=20, height=5)
        dims_var.set("")

    # ------------------------------------------------------------------ #

    # Per-button style tables: (bg, fg, relief, bd)
    _BTN_STYLE_NORMAL = {
        "delete": ("#fadbd8", "#c0392b", tk.FLAT,  1),
        "update": ("#d0d3d4", "#2c3e50", tk.FLAT,  1),
        "cancel": ("#d0d3d4", "#2c3e50", tk.FLAT,  1),
    }
    _BTN_STYLE_FOCUSED = {
        "delete": ("#c0392b", "white",   tk.SOLID, 2),
        "update": ("#2980b9", "white",   tk.SOLID, 2),
        "cancel": ("#555555", "white",   tk.SOLID, 2),
    }
    _BTN_KEYS = ["delete", "update", "cancel"]

    def _apply_btn_focus(self):
        """Highlight the focused button; restore normal style for the others."""
        for i, (btn, key) in enumerate(zip(self._nav_buttons, self._BTN_KEYS)):
            style = (self._BTN_STYLE_FOCUSED if i == self._nav_idx
                     else self._BTN_STYLE_NORMAL)[key]
            btn.configure(bg=style[0], fg=style[1], relief=style[2], bd=style[3])

    def _on_btn_left(self, _event):
        if self._nav_idx > 0:
            self._nav_idx -= 1
            self._apply_btn_focus()
            self._nav_buttons[self._nav_idx].focus_set()

    def _on_btn_right(self, _event):
        if self._nav_idx < len(self._nav_buttons) - 1:
            self._nav_idx += 1
            self._apply_btn_focus()
            self._nav_buttons[self._nav_idx].focus_set()

    def _on_delete_file(self):
        """Ask for confirmation then delete the scan file from disk."""
        dlg = _DeleteScanFileConfirmDialog(self, self._src_path)
        self.wait_window(dlg)
        if not dlg.confirmed:
            return
        log = get_logger("update_track_in_lib")
        try:
            os.remove(self._src_path)
            log.info(f"Deleted scan file: {self._src_path}")
            self.destroy()
        except OSError as exc:
            log.error(f"Delete scan file failed: {self._src_path} — {exc}")
            messagebox.showerror("Delete failed", str(exc), parent=self)

    def _confirm(self):
        self.confirmed = True
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  620)
        h = max(self.winfo_reqheight(), 460)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


class ScanTab(tk.Frame, AudioMenuMixin):

    def __init__(self, master, on_compare=None, on_search_artist=None):
        super().__init__(master, bg="#f5f5f5")
        self._settings = load_settings()
        self._on_compare = on_compare           # callable(src_path, lib_path) or None
        self._on_search_artist = on_search_artist  # callable(artist_name) or None
        self._sort_col: str | None = None   # currently sorted column id
        self._sort_rev: bool = False        # True → descending
        # Inline cell editing state
        self._edit_entry    = None   # active tk.Entry overlay, or None
        self._edit_iid      = None
        self._edit_col_id   = None
        self._edit_col_idx  = None
        self._edit_orig_val = None
        self._modified_iids: set = set()   # iids with unsaved tag changes
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

        ttk.Button(row, text="Check Tracks",         command=self._check_tracks).pack(side=tk.LEFT)
        ttk.Button(row, text="Refresh Tags",          command=self._refresh_tags).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row, text="Normalize File Name",   command=self._normalize_filenames).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row, text="Remove Lib Duplicates", command=self._remove_lib_duplicates).pack(side=tk.LEFT, padx=(8, 0))

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

        columns = ("flibready", "finlib", "fpath", "ftype", "fartist", "ftitle", "falbum", "fbitrate", "fquality", "fsize", "fmodified")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            selectmode="extended",
        )

        _cmd = lambda c: (lambda: self._sort_column(c))
        self.tree.heading("flibready",  text="Lib Ready",  anchor=tk.CENTER, command=_cmd("flibready"))
        self.tree.heading("finlib",     text="In Lib",     anchor=tk.CENTER, command=_cmd("finlib"))
        self.tree.heading("fpath",      text="Full Path",  anchor=tk.W,      command=_cmd("fpath"))
        self.tree.heading("ftype",      text="Type",       anchor=tk.W,      command=_cmd("ftype"))
        self.tree.heading("fartist",    text="Artist",     anchor=tk.W,      command=_cmd("fartist"))
        self.tree.heading("ftitle",     text="Title",      anchor=tk.W,      command=_cmd("ftitle"))
        self.tree.heading("falbum",     text="Album",      anchor=tk.W,      command=_cmd("falbum"))
        self.tree.heading("fbitrate",   text="Bitrate",    anchor=tk.E,      command=_cmd("fbitrate"))
        self.tree.heading("fquality",   text="Quality",    anchor=tk.W,      command=_cmd("fquality"))
        self.tree.heading("fsize",      text="Size",       anchor=tk.E,      command=_cmd("fsize"))
        self.tree.heading("fmodified",  text="Modified",   anchor=tk.W,      command=_cmd("fmodified"))

        self.tree.column("flibready",  width=70,  anchor=tk.CENTER, stretch=False)
        self.tree.column("finlib",     width=55,  anchor=tk.CENTER, stretch=False)
        self.tree.column("fpath",      width=270, stretch=True)
        self.tree.column("ftype",      width=55,  stretch=False)
        self.tree.column("fartist",    width=140, stretch=False)
        self.tree.column("ftitle",     width=170, stretch=False)
        self.tree.column("falbum",     width=155, stretch=False)
        self.tree.column("fbitrate",   width=75,  anchor=tk.E, stretch=False)
        self.tree.column("fquality",   width=120, anchor=tk.W, stretch=False)
        self.tree.column("fsize",      width=65,  anchor=tk.E, stretch=False)
        self.tree.column("fmodified",  width=125, stretch=False)

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)

        # Bottom action row inside the left pane — matches the Search In
        # Lib panel's pagination row layout so the Analyze button sits in
        # the same visual position across tabs.
        action_row = tk.Frame(tree_frame, bg="#f5f5f5", pady=4)
        action_row.pack(side=tk.BOTTOM, fill=tk.X)

        ttk.Button(
            action_row, text="🔬 Analyze spec",
            command=self._analyze_selected_quality,
        ).pack(side=tk.LEFT)

        self._analyze_progress_var = tk.StringVar(value="")
        tk.Label(
            action_row, textvariable=self._analyze_progress_var,
            font=("Segoe UI", 9, "italic"),
            fg="#7f8c8d", bg="#f5f5f5",
        ).pack(side=tk.LEFT, padx=(6, 0))

        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.tag_configure("odd",         background="#ffffff")
        self.tree.tag_configure("even",        background="#ecf0f1")
        self.tree.tag_configure("inlib_exact", background="#eafaf1", foreground="#1e8449")  # green  — MD5 match
        self.tree.tag_configure("inlib_diff",  background="#fefce8", foreground="#92400e")  # amber  — metadata match, different file
        self.tree.tag_configure("notlib",      background="#fdf2f8", foreground="#922b21")
        self.tree.tag_configure("modified",    background="#fff3cd", foreground="#7d5a00")  # warm yellow — unsaved edits
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Button-3>", self._on_row_right_click)
        self.tree.bind("<Double-1>", self._on_cell_double_click)
        self.tree.bind("<Delete>", self._on_delete_key)
        self.tree.bind("<Shift-Delete>", lambda _: self._delete_selected_files())
        self.tree.bind("<Shift-u>", lambda _: self._hotkey_compare_track_in_lib())
        self.tree.bind("<Shift-U>", lambda _: self._hotkey_compare_track_in_lib())
        self.tree.bind("<Shift-e>", lambda _: self._hotkey_edit_tags())
        self.tree.bind("<Shift-E>", lambda _: self._hotkey_edit_tags())
        self.tree.bind("<F5>", lambda _: self._refresh_tags())
        self.tree.bind("<Control-a>", lambda _: self.tree.selection_set(self.tree.get_children()))
        self._kb_sel = attach_keyboard_range_selection(self.tree)

        self.tree.drop_target_register(DND_FILES)
        self.tree.dnd_bind("<<Drop>>", self._on_drop)

        # ── Right: detail panel (Local | Lib side-by-side) ────────────── #
        self._detail_panel = _ScanFileDetailsPanel(self._paned)
        self._paned.add(self._detail_panel, stretch="never", minsize=480)

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
    # Column sorting                                                       #
    # ------------------------------------------------------------------ #

    # Map column id → index in the values tuple
    _COL_IDX = {
        "flibready": 0, "finlib": 1, "fpath": 2, "ftype": 3,
        "fartist": 4, "ftitle": 5, "falbum": 6,
        "fbitrate": 7, "fsize": 8, "fmodified": 9,
    }

    _COL_LABELS = {
        "flibready": "Lib Ready", "finlib": "In Lib", "fpath": "Full Path",
        "ftype": "Type", "fartist": "Artist", "ftitle": "Title",
        "falbum": "Album", "fbitrate": "Bitrate", "fquality": "Quality",
        "fsize": "Size", "fmodified": "Modified",
    }

    # ------------------------------------------------------------------ #
    # Quality analysis (🔬 Analyze spec)                                   #
    # ------------------------------------------------------------------ #

    def _analyze_selected_quality(self):
        """Run spectral Hi-Res / lossy analysis on the selected tracks.

        Runs sequentially on a background thread so the Tk loop stays
        responsive.  Results land in the new ``Quality`` column.
        """
        if getattr(self, "_analyze_thread", None) and self._analyze_thread.is_alive():
            return

        targets: list[tuple[str, str]] = []   # (iid, full_path)
        for iid in self.tree.selection():
            full_path = self.tree.set(iid, "fpath")
            if not full_path or not os.path.isfile(full_path):
                continue
            ext = os.path.splitext(full_path)[1].lstrip(".").upper()
            if ext in AUDIO_EXTENSIONS:
                targets.append((iid, full_path))

        if not targets:
            self.status_var.set("Select one or more audio tracks to analyze.")
            return

        self._analyze_progress_var.set(f"Analyzing 0 / {len(targets)}…")
        self._analyze_thread = threading.Thread(
            target=self._analyze_worker, args=(targets,), daemon=True,
        )
        self._analyze_thread.start()

    def _analyze_worker(self, targets: list[tuple[str, str]]):
        from music.audio_analysis_panel import analyze_audio, quality_label

        log = get_logger("scan")
        total = len(targets)
        for idx, (iid, path) in enumerate(targets, start=1):
            try:
                result = analyze_audio(path)
                label = quality_label(result)
                log.info(
                    "Quality: %s → %s (cutoff=%.0f Hz, sr=%d)",
                    path, label, result["cutoff_hz"], result["sr"],
                )
            except Exception:  # noqa: BLE001
                log.exception("Quality analysis failed for %s", path)
                label = "error"
            self.after(0, self._on_quality_ready, iid, label, idx, total)

        self.after(0, lambda: self._analyze_progress_var.set(""))

    def _on_quality_ready(self, iid: str, label: str, idx: int, total: int):
        if self.tree.exists(iid):
            try:
                self.tree.set(iid, "fquality", label)
            except tk.TclError:
                pass
            if label and label != "error":
                try:
                    full_path = self.tree.set(iid, "fpath")
                    from music.database import update_track_quality
                    if full_path:
                        update_track_quality(full_path, label)
                except Exception:
                    get_logger("scan").exception(
                        "Failed to persist quality for iid=%s", iid)
        self._analyze_progress_var.set(f"Analyzing {idx} / {total}…")

    @staticmethod
    def _parse_size(val: str) -> float:
        """Convert a human-readable size string (e.g. '1.5 MB') to bytes."""
        units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}
        parts = val.strip().split()
        if len(parts) == 2:
            try:
                return float(parts[0]) * units.get(parts[1], 1)
            except ValueError:
                pass
        return 0.0

    @staticmethod
    def _parse_bitrate(val: str) -> float:
        """Convert a bitrate string (e.g. '1000 kbps') to a float."""
        parts = val.strip().split()
        if parts:
            try:
                return float(parts[0])
            except ValueError:
                pass
        return 0.0

    def _sort_column(self, col: str):
        """Sort treeview rows by *col*, toggling direction on repeated clicks."""
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False

        # Build sort key depending on column type
        if col == "fsize":
            key_fn = lambda iid: self._parse_size(self.tree.set(iid, col))
        elif col == "fbitrate":
            key_fn = lambda iid: self._parse_bitrate(self.tree.set(iid, col))
        else:
            key_fn = lambda iid: self.tree.set(iid, col).lower()

        items = sorted(self.tree.get_children(), key=key_fn, reverse=self._sort_rev)

        for i, iid in enumerate(items):
            self.tree.move(iid, "", i)

        # Re-stripe rows while preserving special in-lib highlight tags
        _special = {"inlib_exact", "inlib_diff", "notlib"}
        for i, iid in enumerate(self.tree.get_children()):
            current_tags = self.tree.item(iid, "tags")
            if current_tags and current_tags[0] in _special:
                continue  # leave colour-coded rows as-is
            self.tree.item(iid, tags=("odd" if i % 2 == 0 else "even",))

        # Update heading labels to show sort indicator; clear all others
        arrow = " ▲" if not self._sort_rev else " ▼"
        for c, label in self._COL_LABELS.items():
            self.tree.heading(c, text=label + (arrow if c == col else ""))

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

    def scan_folders(self, folders: list[str]):
        """Clear the file list and populate it from every path in *folders*.

        Called externally (e.g. from the Scan Folders panel) to load a batch
        of folders without going through the file-chooser dialog.
        """
        self.tree.delete(*self.tree.get_children())
        self.status_var.set("Scanning…")
        self.update_idletasks()

        recursive   = self.recursive_var.get()
        show_hidden = self.show_hidden_var.get()

        for folder in folders:
            if not os.path.isdir(folder):
                continue
            try:
                self._populate_list(folder, show_hidden, recursive)
            except PermissionError as exc:
                messagebox.showerror("Permission denied", str(exc))

        total = len(self.tree.get_children())
        folder_list = ", ".join(os.path.basename(f) for f in folders[:3])
        if len(folders) > 3:
            folder_list += f" …+{len(folders) - 3} more"
        self.status_var.set(
            f"Scanned {len(folders)} folder{'s' if len(folders) != 1 else ''}"
            f" ({folder_list}) — {total} file{'s' if total != 1 else ''} found."
        )
        self.footer_var.set(f"Scan complete — {total} file{'s' if total != 1 else ''} found.")

    def _refresh_tags(self):
        """Re-read tag fields (artist/title/album/bitrate) from disk for every row."""
        items = self.tree.get_children()
        if not items:
            self.status_var.set("No tracks to refresh.")
            return

        total = len(items)
        self.status_var.set(f"Refreshing tags… 0/{total}")
        self.update_idletasks()

        refreshed = 0
        for i, item in enumerate(items):
            vals      = list(self.tree.item(item, "values"))
            full_path = vals[2]
            ext = os.path.splitext(full_path)[1].lstrip(".").upper()
            if ext not in AUDIO_EXTENSIONS:
                continue
            a, t, al, br = _read_audio_tags(full_path)
            vals[4], vals[5], vals[6], vals[7] = a, t, al, br
            self.tree.item(item, values=vals)
            refreshed += 1

            if (i + 1) % 25 == 0:
                self.status_var.set(f"Refreshing tags… {i + 1}/{total}")
                self.update_idletasks()

        self.status_var.set(
            f"Tag refresh complete — {refreshed} file{'s' if refreshed != 1 else ''} updated."
        )

    def _check_tracks(self):
        """Refresh Lib Ready and In Lib columns for every row in the table.

        Status logic (artist + title are always required):
          🟢  exact match — same (artist, title, album, bitrate) exists in lib.
          🟡  partial match — same (artist, title) exists in lib but the album
              and/or bitrate differs.  User can compare and decide which to keep.
          ⬛  not in lib — no row with same (artist, title).

        MD5 is not consulted here (too expensive); for users who care about
        byte-level differences the right-click "Compare track with Lib" / "Update
        Track in Lib" actions surface the underlying file diffs.
        """
        from music.database import get_track_info

        items = self.tree.get_children()
        if not items:
            self.status_var.set("No tracks to check.")
            return

        self.status_var.set("Loading library index…")
        self.update_idletasks()

        # Two lookups against the DB:
        #   ataB_set: (artist, title, album, bitrate)  → exact match → 🟢
        #   at_set:   (artist, title)                  → at least one lib row → 🟡
        ataB_set: set[tuple[str, str, str, str]] = set()
        at_set:   set[tuple[str, str]]           = set()
        for row in get_track_info():
            a  = (row["artist"]  or "").strip().lower()
            t  = (row["title"]   or "").strip().lower()
            al = (row["album"]   or "").strip().lower()
            br = (row["bitrate"] or "").strip().lower()
            if a and t:
                ataB_set.add((a, t, al, br))
                at_set.add((a, t))

        found = partial = ready = 0
        total = len(items)
        for i, item in enumerate(items):
            vals      = list(self.tree.item(item, "values"))
            full_path = vals[2]

            # ── Refresh tags from file ── #
            ext = os.path.splitext(full_path)[1].lstrip(".").upper()
            if ext in AUDIO_EXTENSIONS:
                a, t, al, br = _read_audio_tags(full_path)
                vals[4], vals[5], vals[6], vals[7] = a, t, al, br

            artist  = (vals[4] or "").strip().lower()
            title   = (vals[5] or "").strip().lower()
            album   = (vals[6] or "").strip().lower()
            bitrate = (vals[7] or "").strip().lower()

            # ── Lib Ready ── #
            is_ready = _check_lib_ready(full_path)
            vals[0]  = "✅" if is_ready else "❌"
            if is_ready:
                ready += 1

            # ── In Lib ── #
            if artist and title and (artist, title, album, bitrate) in ataB_set:
                vals[1]  = "🟢"
                row_tag  = "inlib_exact"
                found   += 1
            elif artist and title and (artist, title) in at_set:
                vals[1]   = "🟡"
                row_tag   = "inlib_diff"
                partial  += 1
            else:
                vals[1] = "⬛"
                row_tag = "odd" if i % 2 == 0 else "even"

            self.tree.item(item, values=vals, tags=(row_tag,))

            if (i + 1) % 10 == 0:
                self.status_var.set(f"Checking… {i + 1}/{total}")
                self.update_idletasks()

        self.status_var.set(
            f"Check complete — {ready}/{total} lib-ready · "
            f"{found} in lib · {partial} partial match · "
            f"{total - found - partial} not in lib."
        )

    # ------------------------------------------------------------------ #
    # Remove Lib Duplicates                                                #
    # ------------------------------------------------------------------ #

    def _remove_lib_duplicates(self):
        """Verify scan files against library MD5s and delete confirmed duplicates.

        Processes selected rows if any are selected, otherwise all rows.
        Re-computes MD5 for each file and compares against the DB; only files
        with an exact MD5 match qualify for deletion.  Shows a confirmation
        dialog before touching anything on disk.
        """
        from music.database import get_track_info, compute_file_md5

        selected = self.tree.selection()
        items    = selected if selected else self.tree.get_children()
        if not items:
            self.status_var.set("No tracks in list.")
            return

        self.status_var.set("Loading library MD5 index…")
        self.update_idletasks()

        # Build md5 → lib row lookup (single DB query)
        md5_to_lib: dict[str, object] = {}
        for row in get_track_info():
            if row["file_md5"]:
                md5_to_lib[row["file_md5"].strip().lower()] = row

        # Classify each scan row
        # to_delete  : (item_id, full_path, lib_row dict, scan_vals tuple)
        # cant_delete: (full_path, reason, scan_vals tuple)
        to_delete:   list[tuple] = []
        cant_delete: list[tuple] = []

        total = len(items)
        for i, item in enumerate(items):
            vals      = self.tree.item(item, "values")
            full_path = vals[2]

            if not os.path.isfile(full_path):
                cant_delete.append((full_path, "File not found on disk", vals))
                continue

            try:
                md5 = compute_file_md5(full_path).lower()
            except Exception as exc:
                cant_delete.append((full_path, f"MD5 error: {exc}", vals))
                continue

            if md5 in md5_to_lib:
                lib_row = md5_to_lib[md5]
                to_delete.append((item, full_path, lib_row, vals))
            else:
                cant_delete.append((full_path, "MD5 not found in library", vals))

            if (i + 1) % 5 == 0:
                self.status_var.set(f"Verifying MD5… {i + 1}/{total}")
                self.update_idletasks()

        self.status_var.set("Verification complete — review and confirm below.")

        if not to_delete and not cant_delete:
            messagebox.showinfo("Remove Lib Duplicates", "No items to process.")
            return

        # ── Show confirmation dialog (modal) ── #
        dlg = _DeleteConfirmDialog(self.winfo_toplevel(), to_delete, cant_delete)
        self.wait_window(dlg)

        if not dlg.confirmed:
            self.status_var.set("Deletion cancelled.")
            return

        # ── Execute deletions ── #
        deleted_items: list = []
        delete_errors: list[tuple[str, str]] = []
        deleted_dirs: set[str] = set()

        for item, full_path, _lib_row, _sv in to_delete:
            try:
                os.remove(full_path)
                deleted_items.append(item)
                deleted_dirs.add(os.path.dirname(full_path))
            except OSError as exc:
                delete_errors.append((full_path, str(exc)))

        # Remove deleted rows from tree and re-stripe
        for item in deleted_items:
            self.tree.delete(item)

        _special = {"inlib_exact", "inlib_diff", "notlib"}
        for i, iid in enumerate(self.tree.get_children()):
            current_tags = self.tree.item(iid, "tags")
            if not (current_tags and current_tags[0] in _special):
                self.tree.item(iid, tags=("odd" if i % 2 == 0 else "even",))

        # ── Remove empty parent folders if requested ── #
        n_folders_removed = 0
        if dlg.remove_empty_folders:
            for folder in sorted(deleted_dirs, key=len, reverse=True):
                try:
                    if os.path.isdir(folder) and not os.listdir(folder):
                        os.rmdir(folder)
                        n_folders_removed += 1
                except OSError:
                    pass

        n_del = len(deleted_items)
        n_err = len(delete_errors)

        if delete_errors:
            messagebox.showerror(
                "Deletion Errors",
                f"{n_err} file(s) could not be deleted:\n\n"
                + "\n".join(f"• {p}\n  {e}" for p, e in delete_errors[:8]),
            )

        remaining = len(self.tree.get_children())
        status = (
            f"Deleted {n_del} file{'s' if n_del != 1 else ''}"
            + (f", {n_err} deletion error{'s' if n_err != 1 else ''}" if n_err else "")
            + (f", removed {n_folders_removed} empty folder{'s' if n_folders_removed != 1 else ''}" if n_folders_removed else "")
            + f" — {remaining} remaining."
        )
        self.status_var.set(status)
        self.footer_var.set(f"{remaining} file{'s' if remaining != 1 else ''} in list.")

    # ------------------------------------------------------------------ #
    # Normalize file names                                                 #
    # ------------------------------------------------------------------ #

    def _normalize_filenames(self):
        """Rename each listed file to '{Artist} - {Title}{ext}'.

        Processes selected rows when a selection exists, otherwise all rows.
        Opens a result window summarising successes and failures.
        """
        selected = self.tree.selection()
        items    = selected if selected else self.tree.get_children()
        if not items:
            self.status_var.set("No tracks to rename.")
            return

        renamed: list[tuple[str, str]] = []   # (old_path, new_path)
        errors:  list[tuple[str, str]] = []   # (path, reason)

        for item in items:
            vals      = list(self.tree.item(item, "values"))
            full_path = vals[2]
            artist    = (vals[4] or "").strip()
            title     = (vals[5] or "").strip()
            ext       = os.path.splitext(full_path)[1]   # includes "."

            if not artist or not title:
                errors.append((full_path,
                               "Missing artist or title — run Check Tracks first."))
                continue

            new_stem = _sanitize_filename(f"{artist} - {title}")
            new_name = new_stem + ext
            new_path = os.path.join(os.path.dirname(full_path), new_name)

            if os.path.normcase(new_path) == os.path.normcase(full_path):
                continue   # already correctly named — silently skip

            if os.path.exists(new_path):
                errors.append((full_path,
                               f"Target already exists: {new_name}"))
                continue

            try:
                os.rename(full_path, new_path)
                vals[2] = new_path
                self.tree.item(item, values=vals)
                renamed.append((full_path, new_path))
            except OSError as exc:
                errors.append((full_path, str(exc)))

        n_ok  = len(renamed)
        n_err = len(errors)

        if renamed or errors:
            _NormalizeResultWindow(self.winfo_toplevel(), renamed, errors)

        self.status_var.set(
            f"Rename complete — {n_ok} renamed"
            + (f", {n_err} error{'s' if n_err != 1 else ''}" if n_err else "")
            + ("." if n_ok or n_err else " (nothing to do).")
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

        # Look up cached quality (from a prior Analyze spec run) so users
        # don't have to re-analyze the same files every scan.
        try:
            from music.database import get_track_quality
            quality = get_track_quality(full_path)
        except Exception:
            quality = ""

        tag = "odd" if len(self.tree.get_children()) % 2 == 0 else "even"
        self.tree.insert(
            "", "end",
            values=("", "", full_path, file_type, artist, title, album, bitrate, quality, size, modified),
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

    def _delete_selected_files(self):
        """Confirm then permanently delete the selected files from disk."""
        selected = self.tree.selection()
        if not selected:
            return

        rows = [self.tree.item(iid, "values") for iid in selected]
        dlg = _DeleteFilesDialog(self, rows)
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        log = get_logger("scan_delete")
        deleted_iids, errors = [], []
        for iid, sv in zip(selected, rows):
            path = sv[2]
            try:
                os.remove(path)
                deleted_iids.append(iid)
                log.info(f"Deleted: {path}")
            except OSError as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
                log.error(f"Delete failed: {path} — {exc}")

        # Remove successfully deleted rows from the tree
        for iid in deleted_iids:
            self.tree.delete(iid)

        # Re-stripe
        for i, iid in enumerate(self.tree.get_children()):
            self.tree.item(iid, tags=("odd" if i % 2 else "even",))

        total = len(self.tree.get_children())
        self.footer_var.set(f"{total} file{'s' if total != 1 else ''} in list.")
        n_ok = len(deleted_iids)
        self.status_var.set(
            f"🗑  Deleted {n_ok} file{'s' if n_ok != 1 else ''} from disk."
            + (f"  {len(errors)} error(s)." if errors else "")
        )

        if errors:
            messagebox.showerror(
                "Delete — errors",
                f"{n_ok} deleted, {len(errors)} failed:\n\n" + "\n".join(errors[:10]),
            )

    def _ask_ai_restore_original_names(self, iids):
        """Build an AI prompt that asks an LLM to restore artist/title/album to
        their original-language script (e.g. Japanese / Chinese) and copy the
        prompt to the clipboard.

        Output the user receives in the clipboard is a single prompt string;
        the LLM is instructed to reply with a JSON array only.
        """
        tracks = []
        for iid in iids:
            if not self.tree.exists(iid):
                continue
            vals   = self.tree.item(iid, "values")
            artist = (vals[4] or "").strip()
            title  = (vals[5] or "").strip()
            album  = (vals[6] or "").strip()
            if not (artist or title or album):
                continue
            tracks.append({"artist": artist, "title": title, "album": album})

        if not tracks:
            messagebox.showinfo(
                "Ask AI",
                "No artist/title/album metadata found in the selected tracks.",
                parent=self,
            )
            return

        source_json = json.dumps(tracks, ensure_ascii=False, indent=2)

        prompt = (
            "You are a music-metadata expert. The following tracks have "
            "artist/title/album fields that may have been romanized, "
            "transliterated, or translated away from the track's original "
            "language (e.g. Japanese, Chinese, Korean, Cyrillic, etc.).\n\n"
            "For each track, restore each field to the track's ORIGINAL "
            "language and script when you are confident of the original. "
            "If a field is already in its original script, or you are not "
            "confident about the original, leave it unchanged.\n\n"
            "Source tracks (JSON):\n"
            f"{source_json}\n\n"
            "Return ONLY a JSON array — no prose, no markdown fences. Each "
            "element must have exactly this shape:\n"
            "{\n"
            '  "source":     {"artist": "...", "title": "...", "album": "..."},\n'
            '  "normalized": {"artist": "...", "title": "...", "album": "..."}\n'
            "}\n"
            "Preserve the input order. Use empty strings for fields you "
            "cannot confidently restore."
        )

        self.clipboard_clear()
        self.clipboard_append(prompt)
        # Force clipboard to persist after the app loses focus on Windows.
        self.update()

        n = len(tracks)
        self.status_var.set(
            f"🤖  AI prompt for {n} track{'s' if n != 1 else ''} copied to clipboard."
        )

    def _ask_ai_restore_titles_from_response(self, iids):
        """Open a dialog that lets the user paste the AI's JSON response,
        preview / edit the proposed new filenames, then rename matched files
        on disk."""
        rows = []
        for iid in iids:
            if not self.tree.exists(iid):
                continue
            vals = self.tree.item(iid, "values")
            rows.append({
                "iid":    iid,
                "path":   vals[2],
                "artist": (vals[4] or "").strip(),
                "title":  (vals[5] or "").strip(),
                "album":  (vals[6] or "").strip(),
            })
        if not rows:
            messagebox.showinfo(
                "Restore Titles",
                "No scan rows are selected.",
                parent=self,
            )
            return

        _RestoreTitlesFromAIDialog(self.winfo_toplevel(), self, rows)

    def _apply_restored_tags(
        self, item_iid: str, artist: str, title: str, album: str,
    ) -> tuple[bool, str]:
        """Write artist/title/album tags to the FLAC file for ``item_iid``.

        Returns (ok, message).  Updates the tree row's artist/title/album cells
        so subsequent actions / displays reflect the new values.  Empty incoming
        values are skipped (the existing tag is preserved).
        """
        if not self.tree.exists(item_iid):
            return False, "Row no longer in the list."
        vals      = list(self.tree.item(item_iid, "values"))
        full_path = vals[2]
        if not full_path.lower().endswith(".flac"):
            return False, "Only FLAC files are supported."
        if not os.path.isfile(full_path):
            return False, "File not found on disk."

        try:
            audio = FLAC(full_path)
            if artist:
                audio["ARTIST"] = artist
            if title:
                audio["TITLE"]  = title
            if album:
                audio["ALBUM"]  = album
            audio.save()
        except Exception as exc:
            return False, str(exc)

        if artist:
            vals[4] = artist
        if title:
            vals[5] = title
        if album:
            vals[6] = album
        self.tree.item(item_iid, values=vals)
        return True, "ok"

    def _paste_cover_art_to_selected(self):
        """Read an image from the clipboard and embed it into all selected FLAC files."""
        from PIL import ImageGrab, Image
        from mutagen.flac import FLAC, Picture

        selected = self.tree.selection()
        if not selected:
            return

        # ── Read clipboard image ── #
        try:
            img = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror("Clipboard error", str(exc), parent=self)
            return

        if not isinstance(img, Image.Image):
            messagebox.showinfo(
                "No image in clipboard",
                "The clipboard does not contain an image.\n\n"
                "Copy an image first, then try again.",
                parent=self,
            )
            return

        # ── Encode image ── #
        import io as _io
        buf = _io.BytesIO()
        if img.mode in ("RGBA", "LA", "PA"):
            img.save(buf, "PNG")
            mime = "image/png"
        else:
            img_rgb = img.convert("RGB")
            img_rgb.save(buf, "JPEG", quality=95)
            mime = "image/jpeg"
        img_bytes = buf.getvalue()

        # Only offer FLAC files — warn about others
        flac_paths, skipped = [], []
        for iid in selected:
            vals = self.tree.item(iid, "values")
            path = vals[2]
            if vals[3].upper() == "FLAC":
                flac_paths.append(path)
            else:
                skipped.append(os.path.basename(path))

        if not flac_paths:
            messagebox.showinfo(
                "No FLAC files selected",
                "Cover art embedding is supported for FLAC files only.\n"
                + (f"{len(skipped)} non-FLAC file(s) were skipped." if skipped else ""),
                parent=self,
            )
            return

        # ── Preview dialog ── #
        dlg = _PasteCoverArtDialog(self, img, img_bytes, mime, len(flac_paths))
        self.wait_window(dlg)
        if not dlg.confirmed:
            return

        # ── Write to files ── #
        log    = get_logger("scan_paste_cover")
        errors = []
        ok     = 0
        w, h   = img.size
        depth  = 32 if img.mode in ("RGBA", "LA") else 24

        for path in flac_paths:
            try:
                flac = FLAC(path)
                flac.clear_pictures()
                pic          = Picture()
                pic.type     = 3          # Front cover
                pic.mime     = mime
                pic.width    = w
                pic.height   = h
                pic.depth    = depth
                pic.data     = img_bytes
                flac.add_picture(pic)
                flac.save()
                ok += 1
                log.info(f"Embedded cover art: {path}")
            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
                log.error(f"Cover art embed failed: {path} — {exc}")

        # ── Status ── #
        parts = [f"🖼  Cover art embedded in {ok} file{'s' if ok != 1 else ''}."]
        if skipped:
            parts.append(f"{len(skipped)} non-FLAC file(s) skipped.")
        self.status_var.set("  ".join(parts))

        if errors:
            messagebox.showerror(
                "Embed errors",
                f"{ok} succeeded, {len(errors)} failed:\n\n" + "\n".join(errors[:10]),
                parent=self,
            )

    # ------------------------------------------------------------------ #
    # Inline cell editing (Artist / Title / Album)                        #
    # ------------------------------------------------------------------ #

    _EDITABLE_COLS = {"fartist", "ftitle", "falbum"}

    def _on_cell_double_click(self, event):
        # Commit any in-progress edit first
        if self._edit_entry:
            self._commit_cell_edit()

        if self.tree.identify_region(event.x, event.y) != "cell":
            return

        col = self.tree.identify_column(event.x)   # "#N" (1-based)
        iid = self.tree.identify_row(event.y)
        if not iid or not col:
            return

        cols    = self.tree["columns"]
        col_idx = int(col[1:]) - 1
        col_id  = cols[col_idx]

        if col_id not in self._EDITABLE_COLS:
            return

        self._start_cell_edit(iid, col, col_id, col_idx)
        return "break"   # prevent default expand/collapse

    def _start_cell_edit(self, iid, col, col_id, col_idx):
        bbox = self.tree.bbox(iid, col)
        if not bbox:
            return   # row scrolled out of view

        bx, by, bw, bh = bbox
        val_idx = self._COL_IDX[col_id]
        current = self.tree.item(iid, "values")[val_idx]

        self._edit_iid      = iid
        self._edit_col_id   = col_id
        self._edit_col_idx  = val_idx
        self._edit_orig_val = current

        entry = tk.Entry(self.tree, font=("Segoe UI", 9), relief="flat",
                         highlightthickness=1, highlightbackground="#2980b9",
                         highlightcolor="#2980b9")
        entry.insert(0, current)
        entry.select_range(0, tk.END)
        entry.place(x=bx, y=by, width=bw, height=bh)
        entry.focus_set()

        entry.bind("<Return>",   lambda _: self._commit_cell_edit())
        entry.bind("<Tab>",      lambda _: self._commit_cell_edit())
        entry.bind("<Escape>",   lambda _: self._cancel_cell_edit())
        entry.bind("<FocusOut>", lambda _: self._commit_cell_edit())

        self._edit_entry = entry

    def _commit_cell_edit(self):
        entry = self._edit_entry
        if not entry:
            return
        # Nullify first to guard against re-entrance from FocusOut
        self._edit_entry = None

        new_val  = entry.get()
        iid      = self._edit_iid
        val_idx  = self._edit_col_idx
        orig_val = self._edit_orig_val

        entry.destroy()
        self._edit_iid = self._edit_col_id = self._edit_col_idx = self._edit_orig_val = None

        if new_val == orig_val:
            return   # nothing changed

        vals        = list(self.tree.item(iid, "values"))
        vals[val_idx] = new_val
        self.tree.item(iid, values=vals)

        self._modified_iids.add(iid)
        tags = [t for t in self.tree.item(iid, "tags") if t != "modified"]
        tags.append("modified")
        self.tree.item(iid, tags=tags)

    def _cancel_cell_edit(self):
        entry = self._edit_entry
        if not entry:
            return
        self._edit_entry = None
        entry.destroy()
        self._edit_iid = self._edit_col_id = self._edit_col_idx = self._edit_orig_val = None

    # ------------------------------------------------------------------ #
    # Save pending tag edits to disk                                       #
    # ------------------------------------------------------------------ #

    def _save_pending_tag_edits(self):
        """Write every pending in-place tag edit back to the audio file on disk."""
        if not self._modified_iids:
            return

        from mutagen.flac import FLAC

        log    = get_logger("scan_tag_edit")
        errors = []
        ok     = 0

        for iid in list(self._modified_iids):
            vals      = self.tree.item(iid, "values")
            path      = vals[2]
            file_type = vals[3].upper()
            artist    = vals[4]
            title     = vals[5]
            album     = vals[6]
            try:
                if file_type == "FLAC":
                    audio = FLAC(path)
                else:
                    from mutagen import File as MutagenFile
                    audio = MutagenFile(path, easy=True)
                    if audio is None:
                        raise ValueError("Unsupported file format")

                audio["artist"] = [artist]
                audio["title"]  = [title]
                audio["album"]  = [album]
                audio.save()

                ok += 1
                self._modified_iids.discard(iid)
                log.info(f"Saved tags: {path}")

                # Remove "modified" tag; restore stripe colour
                tags = [t for t in self.tree.item(iid, "tags") if t != "modified"]
                self.tree.item(iid, tags=tags)

            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
                log.error(f"Tag save failed: {path} — {exc}")

        self.status_var.set(
            f"💾  Saved tags for {ok} file{'s' if ok != 1 else ''}."
            + (f"  {len(errors)} error(s)." if errors else "")
        )
        if errors:
            messagebox.showerror(
                "Tag save errors",
                f"{ok} saved, {len(errors)} failed:\n\n" + "\n".join(errors[:10]),
                parent=self,
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

        def extra(menu, paths, audio_paths, flac_paths):
            # ── Save pending tag edits ── #
            if self._modified_iids:
                n_mod = len(self._modified_iids)
                menu.add_command(
                    label=f"💾  Save Tag Changes ({n_mod} file{'s' if n_mod != 1 else ''})",
                    command=self._save_pending_tag_edits,
                )
                menu.add_separator()

            # ── Search Artist in Artist Info ── #
            if len(selected) == 1 and self._on_search_artist is not None:
                artist = (self.tree.item(item, "values")[4] or "").strip()
                if artist:
                    menu.add_command(
                        label=f"👤  Search Artist: {artist}",
                        command=lambda a=artist: self._on_search_artist(a),
                    )
                    menu.add_separator()

            # ── Compare track with Lib (single row, regardless of Check Tracks status) ── #
            if len(selected) == 1 and self._on_compare is not None:
                menu.add_command(
                    label="🔍  Compare track with Lib",
                    accelerator="Shift+U",
                    command=lambda: self._open_compare(item),
                )
                menu.add_separator()

            # ── Update Track(s) in Lib (🟡 or 🟢 rows) ── #
            # 🟡 = metadata match, different file bytes
            # 🟢 = matched in lib (still useful — e.g. cover art may differ)
            updatable = [
                i for i in selected
                if self.tree.item(i, "values")[1] in ("🟡", "🟢")
            ]
            if updatable:
                n_up = len(updatable)
                label = (
                    "⬆  Update Track in Lib"
                    if n_up == 1
                    else f"⬆  Update {n_up} Tracks in Lib"
                )
                menu.add_command(
                    label=label,
                    command=lambda items=updatable: self._update_tracks_in_lib(items),
                )
                menu.add_separator()

            # ── Ask AI submenu ── #
            ai_menu = tk.Menu(menu, tearoff=0)
            ai_menu.add_command(
                label="Restore Original-Language Names…",
                command=lambda iids=tuple(selected):
                    self._ask_ai_restore_original_names(iids),
            )
            ai_menu.add_command(
                label="Restore Tags from AI Response…",
                command=lambda iids=tuple(selected):
                    self._ask_ai_restore_titles_from_response(iids),
            )
            menu.add_cascade(label="🤖  Ask AI ▶", menu=ai_menu)
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

            # ── Delete files from disk ── #
            n = len(selected)
            menu.add_command(
                label=f"🗑  Delete {n} File{'s' if n != 1 else ''} from Disk",
                accelerator="Shift+Delete",
                command=self._delete_selected_files,
            )
            menu.add_separator()

            # ── Paste cover art from clipboard ── #
            menu.add_command(
                label="🖼  Embed Cover Art from Clipboard",
                command=self._paste_cover_art_to_selected,
            )
            menu.add_separator()

        menu = self._build_audio_context_menu(paths, extra_items_fn=extra)
        menu.tk_popup(event.x_root, event.y_root)

    def _analyze_track(self, path: str):
        """Override AudioMenuMixin._analyze_track to also refresh the visible
        Quality cell once the AudioAnalysisPanel finishes (the panel itself
        already persists to ``tracks.quality`` by file path)."""
        from music.audio_analysis_panel import AudioAnalysisPanel

        # Find the row whose fpath column matches *path*.
        target_iid = next(
            (iid for iid in self.tree.get_children()
             if self.tree.set(iid, "fpath") == path),
            None,
        )

        def _on_complete(_result, label: str):
            if not label or label == "error":
                return
            if target_iid and self.tree.exists(target_iid):
                try:
                    self.tree.set(target_iid, "fquality", label)
                except tk.TclError:
                    pass

        AudioAnalysisPanel(self.winfo_toplevel(), path, on_complete=_on_complete)

    def _open_compare(self, item):
        """Find the matching lib track and invoke the on_compare callback.

        Lookup strategy:
          1. Exact match on (artist, title, album).
          2. Else first track with same (artist, title) and the highest bitrate.
          3. Else show "No lib track found" listing the queries that were tried.
        """
        from music.database import (
            find_track_by_artist_title_album,
            find_track_by_artist_title,
        )
        vals = self.tree.item(item, "values")
        src_path = vals[2]
        artist   = (vals[4] or "").strip()
        title    = (vals[5] or "").strip()
        album    = (vals[6] or "").strip()

        def _bitrate_num(s) -> int:
            import re
            m = re.search(r"\d+", str(s or ""))
            return int(m.group(0)) if m else -1

        row = None
        tried: list[str] = []

        tried.append(f"artist+title+album: artist={artist!r} title={title!r} album={album!r}")
        exact = find_track_by_artist_title_album(artist, title, album) if (artist and title and album) else []
        if exact:
            row = exact[0]
        else:
            tried.append(f"artist+title (highest bitrate): artist={artist!r} title={title!r}")
            at_matches = find_track_by_artist_title(artist, title) if (artist and title) else []
            if at_matches:
                row = max(at_matches, key=lambda r: _bitrate_num(r["bitrate"]))

        if row is None:
            messagebox.showinfo(
                "No lib track found",
                "Could not find a matching track record in the library database.\n\n"
                "Queries tried:\n  • " + "\n  • ".join(tried))
            return

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

    def _open_update_track_in_lib(self, item) -> bool:
        """Show the Update Track in Lib confirmation dialog for a single scan row.

        Lookup strategy (also used by the Update Track in Lib menu action):
          1. Try (artist, title, album) — most specific.
          2. Fall back to (artist, title) if step 1 finds nothing.
        Within either set, the most-recently-updated row wins.

        Returns True if the lib track was successfully updated, False otherwise
        (no match, user cancelled, or copy failed).
        """
        from music.database import (
            find_track_by_artist_title_album,
            find_track_by_artist_title,
        )
        from music.lib_ops import copy_track_to_lib

        vals     = self.tree.item(item, "values")
        src_path = vals[2]
        artist   = (vals[4] or "").strip()
        title    = (vals[5] or "").strip()
        album    = (vals[6] or "").strip()

        matches = find_track_by_artist_title_album(artist, title, album)
        if not matches:
            matches = find_track_by_artist_title(artist, title)
        if not matches:
            messagebox.showinfo(
                "No lib track found",
                "Could not find a matching track record in the library database.\n\n"
                f"artist={artist!r}  title={title!r}  album={album!r}")
            return False

        row       = matches[0]
        partition = row["partition"]
        rel_path  = row["rel_path"]
        lib_root  = self._settings.get("music_lib_paths", {}).get(partition, "")
        lib_path  = os.path.join(lib_root, partition, rel_path) if lib_root else ""

        if not lib_path or not os.path.isfile(lib_path):
            messagebox.showwarning(
                "Lib file not found",
                f"DB record found but file is missing:\n{lib_path or '(path unknown)'}")
            return False

        dlg = _UpdateTrackInLibDialog(
            self.winfo_toplevel(), src_path, lib_path, partition, rel_path)
        self.wait_window(dlg)

        if not dlg.confirmed:
            return False

        log = get_logger("update_track_in_lib")
        try:
            copy_track_to_lib(src_path, lib_path, partition, rel_path)
            log.info(f"Updated lib track: {lib_path}")
            self.status_var.set(
                f"✔  Lib track updated: {os.path.basename(lib_path)}"
            )
            # Refresh In Lib indicator for the updated row
            vals_list    = list(vals)
            vals_list[1] = "🟢"
            self.tree.item(item, values=vals_list)
            return True
        except Exception as exc:
            log.error(f"Update lib track failed: {lib_path} — {exc}")
            messagebox.showerror("Update failed", str(exc))
            return False

    def _hotkey_edit_tags(self):
        """Shift+E handler — opens Edit Tags for any FLAC files in the current selection."""
        selected = self.tree.selection()
        if not selected:
            return
        paths = [self.tree.item(i, "values")[2] for i in selected]
        flac_paths = [p for p in paths if p.lower().endswith(".flac")]
        if flac_paths:
            self._edit_tags(flac_paths)

    def _update_tracks_in_lib(self, items):
        """Run the Update Track in Lib flow for each given row sequentially.

        Each item gets the existing per-track confirmation dialog (side-by-side
        diff of bitrate + cover art).  If the user cancels a track, it is
        skipped and the batch continues with the next track.  Rows whose status
        isn't 🟡/🟢 are silently ignored.
        """
        items = [
            i for i in items
            if self.tree.exists(i)
            and self.tree.item(i, "values")[1] in ("🟡", "🟢")
        ]
        if not items:
            return

        total = len(items)
        updated = 0
        for idx, item in enumerate(items, start=1):
            if total > 1:
                self.status_var.set(f"Update Track in Lib — {idx}/{total}…")
                self.update_idletasks()
            if self._open_update_track_in_lib(item):
                updated += 1

        if total > 1:
            self.status_var.set(
                f"✔  Update Track in Lib — {updated}/{total} track(s) updated."
            )

    def _hotkey_compare_track_in_lib(self):
        """Shift+U handler — runs Compare track with Lib for the current selection."""
        selected = self.tree.selection()
        if len(selected) != 1 or self._on_compare is None:
            return
        self._open_compare(selected[0])

    def _hotkey_update_track_in_lib(self):
        """Shift+U handler — runs Update Track in Lib for the current 🟡/🟢 selection."""
        selected = self.tree.selection()
        if not selected:
            return
        self._update_tracks_in_lib(list(selected))

    def _open_send_to_lib(self, paths: list[str], partition: str):
        from music.send_to_lib_panel import SendToLibPanel
        lib_root = self._settings.get("music_lib_paths", {}).get(partition, "")
        SendToLibPanel(
            self.winfo_toplevel(),
            paths,
            partition,
            lib_root,
            on_confirm=self._send_to_lib,
        )

    def _send_to_lib(self, paths: list[str], partition: str):
        from music.send_to_lib_panel import compute_dest_full_path, compute_dest_rel_path
        from music.lib_ops import copy_track_to_lib

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
        artist = (values[4] or "").strip()
        title  = (values[5] or "").strip()
        album  = (values[6] or "").strip()

        # ── Resolve matching lib track (same lookup strategy as Update Track) ── #
        lib_path = ""
        if artist and title:
            try:
                from music.database import (
                    find_track_by_artist_title_album,
                    find_track_by_artist_title,
                )
                matches = find_track_by_artist_title_album(artist, title, album)
                if not matches:
                    matches = find_track_by_artist_title(artist, title)
                if matches:
                    row = matches[0]
                    lib_root = self._settings.get("music_lib_paths", {}).get(
                        row["partition"], "")
                    candidate = (
                        os.path.join(lib_root, row["partition"], row["rel_path"])
                        if lib_root else ""
                    )
                    if candidate and os.path.isfile(candidate):
                        lib_path = candidate
            except Exception:
                lib_path = ""

        if file_type == "FLAC":
            self._detail_panel.show(full_path, lib_path)
        else:
            self._detail_panel.show("", lib_path)
