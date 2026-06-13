"""
Shared library operations — copy a source file into the music lib and update the DB.

Used by both the Scan tab (Send to Lib) and the Compare Tracks tab (Update Lib Track)
to avoid duplicating file-copy + DB-upsert logic.
"""

import os
import shutil

from music.database import (
    compute_file_md5, upsert_track_info, update_track_info_quality,
    get_track_quality,
)
from common.logger import get_logger


def copy_track_to_lib(
    src_path: str,
    dest_path: str,
    partition: str,
    rel_path: str,
    quality: str | None = None,
) -> str:
    """
    Copy *src_path* to *dest_path*, compute its MD5, read FLAC tags, and
    upsert the ``track_info`` DB record for (*partition*, *rel_path*).

    If *quality* is not provided, any previously-cached quality label for
    *src_path* (saved by the Scan tab's Analyze spec) is carried over to
    the new ``track_info`` row so the Search In Lib panel can show it
    without re-running analysis.

    Returns the MD5 hex digest of the source file.
    Raises on I/O or DB failure so the caller can handle errors uniformly.
    """
    log = get_logger("lib_ops")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy2(src_path, dest_path)
    log.info(f"Copied: {src_path!r} → {dest_path!r}")

    # Read tags from the source via mutagen's easy interface so FLAC, MP3,
    # and M4A all work uniformly. (Reading from src or dest is equivalent —
    # bytes are identical after shutil.copy2.)
    artist = title = album = bitrate = ""
    try:
        import mutagen
        audio = mutagen.File(src_path, easy=True)
        if audio is not None:
            tags = audio.tags or {}

            def _first(key: str) -> str:
                v = tags.get(key) or tags.get(key.upper())
                if not v:
                    return ""
                if isinstance(v, (list, tuple)):
                    return str(v[0]) if v else ""
                return str(v)

            artist = _first("artist")
            title  = _first("title")
            album  = _first("album")
            info = getattr(audio, "info", None)
            if info is not None and getattr(info, "bitrate", 0):
                bitrate = f"{round(info.bitrate / 1000)} kbps"
    except Exception as exc:
        log.warning(f"Tag read failed for {src_path!r}: {exc}")

    md5 = compute_file_md5(src_path)
    upsert_track_info(partition, rel_path, artist, title, album, bitrate, md5)

    # Carry over any cached spectral quality the user has already
    # produced for the source file (Scan tab → 🔬 Analyze spec).
    if not quality:
        try:
            quality = get_track_quality(src_path) or ""
        except Exception:
            quality = ""
    if quality:
        try:
            update_track_info_quality(partition, rel_path, quality)
            log.info(f"Quality carried over to lib: {partition}/{rel_path} → {quality}")
        except Exception as exc:
            log.warning(f"Quality persistence failed for {partition}/{rel_path}: {exc}")

    log.info(f"DB upserted: {partition}/{rel_path}  md5={md5}")
    return md5
