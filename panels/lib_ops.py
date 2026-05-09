"""
Shared library operations — copy a source file into the music lib and update the DB.

Used by both the Scan tab (Send to Lib) and the Compare Tracks tab (Update Lib Track)
to avoid duplicating file-copy + DB-upsert logic.
"""

import os
import shutil

from panels.database import compute_file_md5, upsert_track_info
from panels.logger import get_logger


def copy_track_to_lib(
    src_path: str,
    dest_path: str,
    partition: str,
    rel_path: str,
) -> str:
    """
    Copy *src_path* to *dest_path*, compute its MD5, read FLAC tags, and
    upsert the ``track_info`` DB record for (*partition*, *rel_path*).

    Returns the MD5 hex digest of the source file.
    Raises on I/O or DB failure so the caller can handle errors uniformly.
    """
    log = get_logger("lib_ops")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy2(src_path, dest_path)
    log.info(f"Copied: {src_path!r} → {dest_path!r}")

    # Read FLAC tags from the source (before or after copy — same bytes)
    artist = title = album = bitrate = ""
    try:
        from mutagen.flac import FLAC
        f = FLAC(src_path)
        artist  = (f.get("artist")  or f.get("ARTIST")  or [""])[0]
        title   = (f.get("title")   or f.get("TITLE")   or [""])[0]
        album   = (f.get("album")   or f.get("ALBUM")   or [""])[0]
        bitrate = f"{round(f.info.bitrate / 1000)} kbps" if f.info else ""
    except Exception as exc:
        log.warning(f"Tag read failed for {src_path!r}: {exc}")

    md5 = compute_file_md5(src_path)
    upsert_track_info(partition, rel_path, artist, title, album, bitrate, md5)
    log.info(f"DB upserted: {partition}/{rel_path}  md5={md5}")
    return md5
