"""
Compare & Pick dialog — modal side-by-side comparison of two library tracks.

Used from the Search-In-Lib panel right-click menu when exactly two rows are
selected. Shows cover art, song info, and an editable tag comparison table,
then lets the user keep one side and delete the other (file + DB).
"""

import io
import os
import tkinter as tk
from tkinter import ttk, messagebox

from mutagen.flac import FLAC, Picture
from PIL import Image, ImageTk, ImageGrab

from common.logger import get_logger
from music.database import (
    compute_file_md5,
    delete_track,
    delete_track_info,
    upsert_track_info,
)

_log = get_logger("compare_pick")


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _load_flac_snapshot(path: str) -> dict:
    """Return a dict of {tags, bitrate, duration, cover bytes, …} for *path*."""
    out: dict = {
        "path": path, "tags": {}, "bitrate": "", "duration": "",
        "cover": None, "cover_dims": "", "cover_size": "",
        "cover_count": 0, "file_size": "",
    }
    if not path or not os.path.isfile(path):
        return out
    try:
        flac = FLAC(path)
        if flac.info:
            br = flac.info.bitrate
            out["bitrate"] = f"{round(br / 1000)} kbps" if br else ""
            secs = int(flac.info.length or 0)
            out["duration"] = f"{secs // 60}:{secs % 60:02d}"
        if flac.tags:
            for k, vlist in flac.tags.as_dict().items():
                out["tags"][k.upper()] = vlist[0] if vlist else ""
        out["cover_count"] = len(flac.pictures)
        front = next((p for p in flac.pictures if p.type == 3),
                     flac.pictures[0] if flac.pictures else None)
        if front:
            out["cover"] = front.data
            try:
                img = Image.open(io.BytesIO(front.data))
                out["cover_dims"] = f"{img.width}×{img.height}"
            except Exception:
                out["cover_dims"] = "?"
            out["cover_size"] = _fmt_size(len(front.data))
        try:
            out["file_size"] = _fmt_size(os.path.getsize(path))
        except OSError:
            pass
    except Exception:
        _log.exception("Failed to read FLAC: %s", path)
    return out


class ComparePickDialog(tk.Toplevel):
    """Side-by-side compare-and-pick dialog.

    Parameters
    ----------
    parent : tk widget
    left, right : dict
        Track dicts with at least ``full_path``, ``partition``, ``rel_path``,
        ``id`` (track_info row id), plus standard metadata fields.

    After the user confirms, ``self.confirmed`` is True and:
      * ``self.kept_track``    – the dict kept
      * ``self.deleted_track`` – the dict deleted
      * ``self.edited_tags``   – dict of tag→value to write to the kept file
        before deletion (may be empty if the user didn't change anything for
        the kept side).
    """

    def __init__(self, parent, left: dict, right: dict):
        super().__init__(parent)
        self.title("Compare & Pick — Keep One Track")
        self.configure(bg="#f5f5f5")
        self.grab_set()
        self.minsize(960, 600)
        self.resizable(True, True)

        self.confirmed = False
        self.kept_track:    dict | None = None
        self.deleted_track: dict | None = None
        self.edited_tags:   dict[str, str] = {}
        self.kept_cover_override: tuple[bytes, str] | None = None

        self._left  = left
        self._right = right
        self._left_info  = _load_flac_snapshot(left.get("full_path", ""))
        self._right_info = _load_flac_snapshot(right.get("full_path", ""))

        # PhotoImage refs (must outlive the labels)
        self._left_photo  = None
        self._right_photo = None

        # Pending cover overrides — when the user pastes a new cover into one
        # of the sides, the bytes are kept here and embedded into the kept
        # track on confirm. Format: (image_bytes, mime).
        self._cover_overrides: dict[str, tuple[bytes, str]] = {}

        # Cover labels (kept on self so paste-handler can refresh them)
        self._cover_labels: dict[str, tk.Label] = {}

        # Editable tag StringVars, keyed by (side, TAG) → StringVar
        self._tag_vars: dict[tuple[str, str], tk.StringVar] = {}

        self._build()
        self._center()

    # ------------------------------------------------------------------ #
    # UI                                                                   #
    # ------------------------------------------------------------------ #

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Header
        hdr = tk.Frame(self, bg="#2c3e50", pady=8, padx=12)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hdr, text="⚖️  Compare & Pick — Keep One Track",
            font=("Segoe UI", 12, "bold"), fg="white", bg="#2c3e50",
        ).pack(side=tk.LEFT)

        info = tk.Frame(self, bg="#fef9e7", padx=12, pady=6)
        info.grid(row=1, column=0, sticky="ew")
        tk.Label(
            info,
            text=("Edit tags inline below. When you click Keep, the chosen "
                  "track is saved with your edits and the other is permanently "
                  "deleted from disk and from the library database."),
            font=("Segoe UI", 9), fg="#7d6608", bg="#fef9e7",
            wraplength=900, justify="left",
        ).pack(anchor="w")

        # Main two-column body
        body = tk.Frame(self, bg="#f5f5f5")
        body.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 4))
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        body.rowconfigure(0, weight=0)
        body.rowconfigure(1, weight=1)

        self._build_side(body, 0, "Left",  self._left,  self._left_info)
        self._build_side(body, 1, "Right", self._right, self._right_info)

        # Footer buttons
        btn = tk.Frame(self, bg="#f5f5f5", pady=8, padx=12)
        btn.grid(row=3, column=0, sticky="ew")
        ttk.Button(btn, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)

    def _build_side(self, body: tk.Frame, col: int, label: str,
                    track: dict, info: dict):
        """Top: cover + song info. Bottom: editable tag table for this side."""
        side_bg = "#ffffff"

        # ── Header / cover / song info ── #
        top = tk.Frame(body, bg=side_bg, bd=1, relief=tk.SOLID)
        top.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        top.columnconfigure(1, weight=1)

        # Header row: side label + Keep button.
        # For the Left side: [Keep Left] [Left label].
        # For the Right side: [Right label] [Keep Right].
        header = tk.Frame(top, bg=side_bg)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=3)
        header.columnconfigure(1, weight=1)

        if label == "Left":
            ttk.Button(
                header, text="◀  Keep Left",
                command=lambda: self._on_keep("left"),
            ).grid(row=0, column=0, sticky="w")
            ttk.Button(
                header, text="💾  Save",
                command=lambda: self._save_side_to_file("left"),
            ).grid(row=0, column=1, sticky="w", padx=(6, 0))
            tk.Label(
                header, text=label, font=("Segoe UI", 11, "bold"),
                fg="#2c3e50", bg=side_bg, anchor="w", padx=8,
            ).grid(row=0, column=2, sticky="w")
            header.columnconfigure(2, weight=1)
        else:
            tk.Label(
                header, text=label, font=("Segoe UI", 11, "bold"),
                fg="#2c3e50", bg=side_bg, anchor="e", padx=8,
            ).grid(row=0, column=0, sticky="e")
            header.columnconfigure(0, weight=1)
            header.columnconfigure(1, weight=0)
            header.columnconfigure(2, weight=0)
            ttk.Button(
                header, text="💾  Save",
                command=lambda: self._save_side_to_file("right"),
            ).grid(row=0, column=1, sticky="e", padx=(0, 6))
            ttk.Button(
                header, text="Keep Right  ▶",
                command=lambda: self._on_keep("right"),
            ).grid(row=0, column=2, sticky="e")

        ttk.Separator(top, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=2, sticky="ew")

        # Cover art
        cover_lbl = tk.Label(top, bg="#d5d8dc", text="No cover art",
                             font=("Segoe UI", 9, "italic"), fg="#7f8c8d",
                             width=22, height=10, relief=tk.GROOVE,
                             cursor="hand2")
        cover_lbl.grid(row=2, column=0, rowspan=10, padx=6, pady=4, sticky="n")
        self._cover_labels[label.lower()] = cover_lbl
        side_key = label.lower()
        cover_lbl.bind(
            "<Button-3>",
            lambda e, s=side_key: self._on_cover_right_click(e, s),
        )

        if info.get("cover"):
            try:
                img = Image.open(io.BytesIO(info["cover"]))
                img.thumbnail((180, 180), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                cover_lbl.configure(image=photo, text="",
                                    width=photo.width(), height=photo.height(),
                                    bg=side_bg)
                if label == "Left":
                    self._left_photo = photo
                else:
                    self._right_photo = photo
            except Exception:
                pass

        # Song info rows — compact spacing
        def _row(r: int, k: str, v: str):
            tk.Label(top, text=k, font=("Segoe UI", 9, "bold"),
                     bg=side_bg, fg="#2c3e50", anchor="w", padx=4,
                     ).grid(row=r, column=1, sticky="ew", pady=(1, 0))
            tk.Label(top, text=v or "—", font=("Segoe UI", 9),
                     bg=side_bg, fg="#1a1a1a", anchor="w", padx=4,
                     wraplength=380, justify="left",
                     ).grid(row=r + 1, column=1, sticky="ew", pady=(0, 2))

        _row(2, "Artist", track.get("artist", "") or info["tags"].get("ARTIST", ""))
        _row(4, "Title",  track.get("title",  "") or info["tags"].get("TITLE",  ""))
        _row(6, "Album",  track.get("album",  "") or info["tags"].get("ALBUM",  ""))
        _row(8, "Path",   track.get("full_path", ""))

        # Quality row — highlight when the two sides disagree.
        left_q  = (self._left.get("quality")  or "").strip()
        right_q = (self._right.get("quality") or "").strip()
        my_q    = (track.get("quality") or "").strip() or "—"
        q_mismatch = bool(left_q) and bool(right_q) and left_q != right_q
        # Also treat "one side has a value, the other doesn't" as a mismatch.
        if (bool(left_q) ^ bool(right_q)):
            q_mismatch = True

        q_bg = "#fef3c7" if q_mismatch else side_bg
        q_fg = "#7d6608" if q_mismatch else "#1a1a1a"
        tk.Label(top, text="Quality" + ("  ⚠" if q_mismatch else ""),
                 font=("Segoe UI", 9, "bold"),
                 bg=q_bg, fg=("#7d6608" if q_mismatch else "#2c3e50"),
                 anchor="w", padx=4,
                 ).grid(row=10, column=1, sticky="ew", pady=(1, 0))
        tk.Label(top, text=my_q,
                 font=("Segoe UI", 9, "bold" if q_mismatch else "normal"),
                 bg=q_bg, fg=q_fg, anchor="w", padx=4,
                 wraplength=380, justify="left",
                 ).grid(row=11, column=1, sticky="ew", pady=(0, 2))

        tech = (
            f"{info.get('bitrate') or '—'}  ·  "
            f"{info.get('duration') or '—'}  ·  "
            f"{info.get('file_size') or '—'}\n"
            f"Cover: {info.get('cover_dims') or 'none'}"
            f"{('  ·  ' + info['cover_size']) if info.get('cover_size') else ''}"
            f"  ·  {info.get('cover_count', 0)} image(s)"
        )
        tk.Label(top, text=tech, font=("Segoe UI", 8), bg=side_bg,
                 fg="#7f8c8d", anchor="w", justify="left", padx=4,
                 ).grid(row=12, column=0, columnspan=2, sticky="ew", pady=(2, 4))

    # ------------------------------------------------------------------ #
    # Tag comparison table (spans the bottom row, both columns)            #
    # ------------------------------------------------------------------ #

    def _build_tag_table(self):
        # Called after _build to draw the comparison table beneath both panels
        pass   # implemented inline in _build via grid below

    # ------------------------------------------------------------------ #
    # Cover art copy / paste                                               #
    # ------------------------------------------------------------------ #

    def _on_cover_right_click(self, event, side: str):
        """Show a small context menu on the cover image of *side*."""
        info = self._left_info if side == "left" else self._right_info
        has_cover = bool(info.get("cover")) or side in self._cover_overrides
        menu = tk.Menu(self, tearoff=0)
        if has_cover:
            menu.add_command(
                label="📋  Copy Image to Clipboard",
                command=lambda s=side: self._copy_cover_to_clipboard(s),
            )
        menu.add_command(
            label="📥  Paste Image from Clipboard",
            command=lambda s=side: self._paste_cover_from_clipboard(s),
        )
        if side in self._cover_overrides:
            menu.add_separator()
            menu.add_command(
                label="↩  Revert pasted cover",
                command=lambda s=side: self._revert_cover_override(s),
            )
        menu.tk_popup(event.x_root, event.y_root)

    def _current_cover_bytes(self, side: str) -> tuple[bytes, str] | None:
        """Return (bytes, mime) for the currently-visible cover on *side*."""
        if side in self._cover_overrides:
            return self._cover_overrides[side]
        info = self._left_info if side == "left" else self._right_info
        data = info.get("cover")
        if not data:
            return None
        # Guess mime from the first few bytes
        mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
        return (data, mime)

    def _copy_cover_to_clipboard(self, side: str):
        """Copy the cover art on *side* to the Windows clipboard as CF_DIB."""
        import ctypes
        pair = self._current_cover_bytes(side)
        if not pair:
            return
        data, _mime = pair
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "BMP")
            dib = buf.getvalue()[14:]   # strip BITMAPFILEHEADER

            GMEM_MOVEABLE = 0x0002
            CF_DIB        = 8
            k32 = ctypes.windll.kernel32
            u32 = ctypes.windll.user32

            k32.GlobalAlloc.restype  = ctypes.c_void_p
            k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            k32.GlobalLock.restype   = ctypes.c_void_p
            k32.GlobalLock.argtypes  = [ctypes.c_void_p]
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
            messagebox.showerror("Copy failed", str(exc), parent=self)

    def _paste_cover_from_clipboard(self, side: str):
        """Pull an image from the clipboard, preview it, store as override."""
        try:
            img = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror("Clipboard error", str(exc), parent=self)
            return
        if not isinstance(img, Image.Image):
            messagebox.showinfo(
                "No image in clipboard",
                "The clipboard does not contain an image.\nCopy an image first.",
                parent=self,
            )
            return

        buf = io.BytesIO()
        if img.mode in ("RGBA", "LA", "PA"):
            img.save(buf, "PNG")
            mime = "image/png"
        else:
            img.convert("RGB").save(buf, "JPEG", quality=95)
            mime = "image/jpeg"
        self._cover_overrides[side] = (buf.getvalue(), mime)
        self._refresh_cover_label(side)

    def _revert_cover_override(self, side: str):
        self._cover_overrides.pop(side, None)
        self._refresh_cover_label(side)

    def _refresh_cover_label(self, side: str):
        lbl = self._cover_labels.get(side)
        if not lbl:
            return
        pair = self._current_cover_bytes(side)
        if not pair:
            lbl.configure(image="", text="No cover art",
                          width=22, height=10, bg="#d5d8dc")
            if side == "left":
                self._left_photo = None
            else:
                self._right_photo = None
            return
        data, _mime = pair
        try:
            img = Image.open(io.BytesIO(data))
            img.thumbnail((180, 180), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            lbl.configure(image=photo, text="",
                          width=photo.width(), height=photo.height(),
                          bg="#ffffff")
            if side == "left":
                self._left_photo = photo
            else:
                self._right_photo = photo
        except Exception as exc:
            messagebox.showerror("Cover load failed", str(exc), parent=self)

    # ------------------------------------------------------------------ #
    # Tag bulk copy left ↔ right                                           #
    # ------------------------------------------------------------------ #

    def _copy_all_tags(self, src: str, dst: str):
        """Overwrite *dst* side's tag values with *src* side's values for
        every tag present in the comparison table."""
        if not messagebox.askyesno(
            "Copy all tags",
            f"Overwrite every {dst.upper()} tag with the matching {src.upper()} "
            f"value? Empty source values will clear the destination.",
            parent=self,
        ):
            return
        for (s, tag), var in list(self._tag_vars.items()):
            if s != src:
                continue
            dst_var = self._tag_vars.get((dst, tag))
            if dst_var is not None:
                dst_var.set(var.get())

    # ------------------------------------------------------------------ #
    # Save a single side's tags (and any pasted cover) to its file        #
    # ------------------------------------------------------------------ #

    def _save_side_to_file(self, side: str):
        """Write the user's current edits on *side* to that side's FLAC file
        and refresh the DB row. Also embeds a pasted cover override if any.
        Updates the in-memory baseline so further diffs are relative to the
        saved state.
        """
        track = self._left if side == "left" else self._right
        info  = self._left_info if side == "left" else self._right_info

        path = track.get("full_path", "")
        if not path or not os.path.isfile(path):
            messagebox.showerror(
                "Save failed",
                f"File not found:\n{path}",
                parent=self,
            )
            return

        # Diff current StringVar values against the loaded snapshot.
        original = info.get("tags") or {}
        edits: dict[str, str] = {}
        for (s, tag), var in self._tag_vars.items():
            if s != side:
                continue
            new_val = var.get()
            if new_val != original.get(tag, ""):
                edits[tag] = new_val

        cover_override = self._cover_overrides.get(side)

        if not edits and not cover_override:
            messagebox.showinfo(
                "Nothing to save",
                f"No changes on the {side.upper()} side.",
                parent=self,
            )
            return

        try:
            flac = FLAC(path)
            for tag, val in edits.items():
                if val == "":
                    flac.pop(tag.lower(), None)
                else:
                    flac[tag.lower()] = [val]
            if cover_override is not None:
                img_bytes, mime = cover_override
                other_pictures = [p for p in flac.pictures if p.type != 3]
                flac.clear_pictures()
                for p in other_pictures:
                    flac.add_picture(p)
                pic = Picture()
                pic.type = 3
                pic.mime = mime
                pic.desc = "Front Cover"
                pic.data = img_bytes
                try:
                    im = Image.open(io.BytesIO(img_bytes))
                    pic.width  = im.width
                    pic.height = im.height
                    pic.depth  = max(len(im.getbands()), 1) * 8
                except Exception:
                    pass
                flac.add_picture(pic)
            flac.save()

            # Refresh DB row so artist/title/album/md5/bitrate stay in sync.
            try:
                tags = flac.tags or {}
                upsert_track_info(
                    track.get("partition", ""),
                    track.get("rel_path",  ""),
                    artist=(tags.get("artist", [""])[0]),
                    title=(tags.get("title",  [""])[0]),
                    album=(tags.get("album",  [""])[0]),
                    bitrate=(f"{round(flac.info.bitrate / 1000)} kbps"
                             if flac.info.bitrate else ""),
                    file_md5=compute_file_md5(path),
                )
            except Exception:
                _log.exception("DB refresh after save failed: %s", path)
        except Exception as exc:
            messagebox.showerror(
                "Save failed",
                f"Could not save tags to:\n{path}\n\n{exc}",
                parent=self,
            )
            return

        # Update in-memory baseline so the saved values become the new
        # "original" — further edits diff against them.
        for tag, val in edits.items():
            if val == "":
                original.pop(tag, None)
            else:
                original[tag] = val
        info["tags"] = original

        # If a pasted cover was saved, fold it into the loaded snapshot so
        # subsequent Keep won't re-embed it twice.
        if cover_override is not None:
            info["cover"] = cover_override[0]
            self._cover_overrides.pop(side, None)
            try:
                im = Image.open(io.BytesIO(cover_override[0]))
                info["cover_dims"] = f"{im.width}×{im.height}"
            except Exception:
                pass
            info["cover_size"] = _fmt_size(len(cover_override[0]))

        # Also refresh the track dict's artist/title/album so the Keep
        # confirmation reflects the saved values.
        tags = FLAC(path).tags or {}
        track["artist"] = tags.get("artist", [track.get("artist", "")])[0]
        track["title"]  = tags.get("title",  [track.get("title",  "")])[0]
        track["album"]  = tags.get("album",  [track.get("album",  "")])[0]

        messagebox.showinfo(
            "Saved",
            f"Saved {len(edits)} tag change{'s' if len(edits) != 1 else ''}"
            + (" + cover art" if cover_override is not None else "")
            + f" to:\n{os.path.basename(path)}",
            parent=self,
        )

    # ------------------------------------------------------------------ #
    # Action handlers                                                      #
    # ------------------------------------------------------------------ #

    def _on_keep(self, side: str):
        if side == "left":
            self.kept_track    = self._left
            self.deleted_track = self._right
            self.edited_tags   = self._collect_edits("left")
            self.kept_cover_override = self._cover_overrides.get("left")
        else:
            self.kept_track    = self._right
            self.deleted_track = self._left
            self.edited_tags   = self._collect_edits("right")
            self.kept_cover_override = self._cover_overrides.get("right")

        # Confirm destructive action
        deleted_path = self.deleted_track.get("full_path", "")
        kept_path    = self.kept_track.get("full_path", "")
        if not messagebox.askyesno(
            "Confirm deletion",
            f"This will keep:\n  {kept_path}\n\n"
            f"and PERMANENTLY DELETE:\n  {deleted_path}\n\n"
            f"This cannot be undone. Proceed?",
            parent=self,
        ):
            return

        self.confirmed = True
        self.destroy()

    def _collect_edits(self, side: str) -> dict[str, str]:
        """Return the {TAG: value} edits the user typed on *side*'s entries."""
        original = self._left_info["tags"] if side == "left" else self._right_info["tags"]
        edits: dict[str, str] = {}
        for (s, tag), var in self._tag_vars.items():
            if s != side:
                continue
            new_val = var.get()
            if new_val != original.get(tag, ""):
                edits[tag] = new_val
        return edits

    def _center(self):
        self.update_idletasks()
        w  = max(self.winfo_reqwidth(),  1000)
        h  = max(self.winfo_reqheight(), 640)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


# ---------------------------------------------------------------------- #
# Patch _build to also render the editable tag comparison table.          #
# Keeping it as a method below for clarity.                               #
# ---------------------------------------------------------------------- #

def _build_tag_section(dlg: ComparePickDialog, parent: tk.Frame, row: int):
    """Render the (Tag | Left value | Right value) editable comparison table."""
    frame = tk.Frame(parent, bg="#f5f5f5", bd=1, relief=tk.SOLID)
    frame.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)

    tk.Label(
        frame, text="Editable Tag Comparison (edit either side; only the kept side is saved)",
        font=("Segoe UI", 9, "bold"), bg="#d6eaf8", fg="#1a5276",
        anchor="w", padx=8, pady=4,
    ).grid(row=0, column=0, columnspan=2, sticky="ew")

    # Bulk copy toolbar
    tools = tk.Frame(frame, bg="#f5f5f5", padx=6, pady=4)
    tools.grid(row=1, column=0, columnspan=2, sticky="ew")
    ttk.Button(
        tools, text="⮕  Copy ALL Left → Right",
        command=lambda: dlg._copy_all_tags("left", "right"),
    ).pack(side=tk.LEFT)
    ttk.Button(
        tools, text="⬅  Copy ALL Right → Left",
        command=lambda: dlg._copy_all_tags("right", "left"),
    ).pack(side=tk.RIGHT)

    # Scrollable area
    canvas = tk.Canvas(frame, bg="#ffffff", highlightthickness=0)
    vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    canvas.grid(row=2, column=0, sticky="nsew")
    vsb.grid(row=2, column=1, sticky="ns")

    inner = tk.Frame(canvas, bg="#ffffff")
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner(_e=None):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner.bind("<Configure>", _on_inner)

    def _on_canvas(e):
        canvas.itemconfigure(inner_id, width=e.width)
    canvas.bind("<Configure>", _on_canvas)

    # Column header
    for c, w in enumerate((160, 1, 1)):
        inner.columnconfigure(c, weight=(0 if c == 0 else 1),
                              minsize=(160 if c == 0 else 200))
    head_bg = "#eaf2fb"
    for c, text in enumerate(("Tag", "Left", "Right")):
        tk.Label(inner, text=text, font=("Segoe UI", 9, "bold"),
                 bg=head_bg, fg="#1a5276", anchor="w", padx=6, pady=4,
                 ).grid(row=0, column=c, sticky="ew")

    left_tags  = dlg._left_info["tags"]
    right_tags = dlg._right_info["tags"]
    all_tags = sorted(set(left_tags) | set(right_tags))

    for i, tag in enumerate(all_tags, start=1):
        lv = left_tags.get(tag, "")
        rv = right_tags.get(tag, "")
        differs = lv != rv
        bg = "#fefce8" if differs else ("#ffffff" if i % 2 else "#f7f9fa")
        tk.Label(inner, text=tag, font=("Segoe UI", 9, "bold"),
                 bg=bg, fg=("#7d6608" if differs else "#2c3e50"),
                 anchor="w", padx=6, pady=2,
                 ).grid(row=i, column=0, sticky="ew")

        lvar = tk.StringVar(value=lv)
        rvar = tk.StringVar(value=rv)
        dlg._tag_vars[("left",  tag)] = lvar
        dlg._tag_vars[("right", tag)] = rvar

        tk.Entry(inner, textvariable=lvar, font=("Segoe UI", 9),
                 bd=1, relief=tk.SOLID, bg=bg,
                 ).grid(row=i, column=1, sticky="ew", padx=4, pady=1)
        tk.Entry(inner, textvariable=rvar, font=("Segoe UI", 9),
                 bd=1, relief=tk.SOLID, bg=bg,
                 ).grid(row=i, column=2, sticky="ew", padx=4, pady=1)


# Monkey-patch the dialog's _build to call _build_tag_section at the end.
_orig_build = ComparePickDialog._build


def _build_with_tags(self):
    _orig_build(self)
    # Find the body frame (row=2 child of self)
    body = self.grid_slaves(row=2, column=0)[0]
    body.rowconfigure(1, weight=1)
    _build_tag_section(self, body, row=1)


ComparePickDialog._build = _build_with_tags


# ---------------------------------------------------------------------- #
# High-level helper                                                       #
# ---------------------------------------------------------------------- #

def run_compare_and_pick(parent, left: dict, right: dict) -> dict | None:
    """Open the dialog. If confirmed, apply tag edits to the kept file and
    delete the other (file + DB). Returns a dict describing the outcome or
    None if cancelled.

    Returned dict keys: kept, deleted, edited_tags, file_deleted, db_deleted.
    """
    dlg = ComparePickDialog(parent, left, right)
    parent.wait_window(dlg)
    if not dlg.confirmed:
        return None

    kept    = dlg.kept_track
    deleted = dlg.deleted_track
    edits   = dlg.edited_tags
    cover_override = dlg.kept_cover_override   # (bytes, mime) or None

    # 1) Apply edits to the kept track (tags + optional pasted cover)
    kept_path = kept.get("full_path", "")
    if (edits or cover_override) and kept_path and os.path.isfile(kept_path):
        try:
            flac = FLAC(kept_path)
            for tag, val in edits.items():
                if val == "":
                    flac.pop(tag.lower(), None)
                else:
                    flac[tag.lower()] = [val]
            if cover_override is not None:
                img_bytes, mime = cover_override
                # Preserve non-front pictures, replace the front cover.
                other_pictures = [p for p in flac.pictures if p.type != 3]
                flac.clear_pictures()
                for p in other_pictures:
                    flac.add_picture(p)
                pic = Picture()
                pic.type = 3
                pic.mime = mime
                pic.desc = "Front Cover"
                pic.data = img_bytes
                try:
                    im = Image.open(io.BytesIO(img_bytes))
                    pic.width  = im.width
                    pic.height = im.height
                    pic.depth  = max(len(im.getbands()), 1) * 8
                except Exception:
                    pass
                flac.add_picture(pic)
            flac.save()
            # Refresh DB row so artist/title/album/md5 stay in sync
            try:
                tags = flac.tags or {}
                upsert_track_info(
                    kept.get("partition", ""),
                    kept.get("rel_path",  ""),
                    artist=(tags.get("artist", [""])[0]),
                    title=(tags.get("title",  [""])[0]),
                    album=(tags.get("album",  [""])[0]),
                    bitrate=(f"{round(flac.info.bitrate / 1000)} kbps"
                             if flac.info.bitrate else ""),
                    file_md5=compute_file_md5(kept_path),
                )
            except Exception:
                _log.exception("DB refresh after edit failed: %s", kept_path)
        except Exception as exc:
            messagebox.showerror(
                "Tag write failed",
                f"Could not save edited tags to:\n{kept_path}\n\n{exc}",
                parent=parent,
            )
            return None

    # 2) Delete the other file + DB record
    deleted_path = deleted.get("full_path", "")
    file_deleted = False
    db_deleted   = False

    if deleted_path and os.path.isfile(deleted_path):
        try:
            os.remove(deleted_path)
            file_deleted = True
        except OSError as exc:
            messagebox.showerror(
                "Delete failed",
                f"Could not delete file:\n{deleted_path}\n\n{exc}",
                parent=parent,
            )

    try:
        delete_track_info(
            deleted.get("partition", ""),
            deleted.get("rel_path",  ""),
        )
        delete_track(deleted_path)
        db_deleted = True
    except Exception:
        _log.exception("DB delete failed for %s", deleted_path)

    return {
        "kept":         kept,
        "deleted":      deleted,
        "edited_tags":  edits,
        "file_deleted": file_deleted,
        "db_deleted":   db_deleted,
    }
