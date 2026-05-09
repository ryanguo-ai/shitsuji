"""
Music inventory database — SQLite schema and access helpers.
"""

import hashlib
import pathlib
import sqlite3
from contextlib import contextmanager

DB_PATH = pathlib.Path.home() / ".shitsuji" / "inventory.db"

_DDL = """
    -- ------------------------------------------------------------------ --
    -- tracks                                                               --
    -- Raw scan results: one row per physical file discovered on disk.      --
    -- ------------------------------------------------------------------ --
    CREATE TABLE IF NOT EXISTS tracks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path   TEXT    NOT NULL UNIQUE,
        file_type   TEXT,
        artist      TEXT,
        title       TEXT,
        album       TEXT,
        year        TEXT,
        genre       TEXT,
        bitrate     TEXT,
        file_size   INTEGER,
        modified_at TEXT,
        scanned_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
    );

    CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks (artist);
    CREATE INDEX IF NOT EXISTS idx_tracks_album  ON tracks (album);

    -- ------------------------------------------------------------------ --
    -- track_info                                                           --
    -- Curated music library inventory: one row per catalogued track.       --
    -- rel_path is relative to the partition root folder.                   --
    -- ------------------------------------------------------------------ --
    CREATE TABLE IF NOT EXISTS track_info (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        partition    TEXT    NOT NULL,
        rel_path     TEXT    NOT NULL,
        artist       TEXT,
        title        TEXT,
        album        TEXT,
        bitrate      TEXT,
        file_md5     TEXT,                -- MD5 hex digest of the source file
        updated_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),

        UNIQUE (partition, rel_path)
    );

    -- ------------------------------------------------------------------ --
    -- track_tags                                                           --
    -- Arbitrary key-value tags for a track_info row.                      --
    -- One row per tag; (track_id, tag_name) is unique so upserts are      --
    -- safe and there are no duplicate tag names per track.                 --
    -- ------------------------------------------------------------------ --
    CREATE TABLE IF NOT EXISTS track_tags (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id   INTEGER NOT NULL
                       REFERENCES track_info (id) ON DELETE CASCADE,
        tag_name   TEXT    NOT NULL,
        tag_value  TEXT,

        UNIQUE (track_id, tag_name)
    );

    CREATE INDEX IF NOT EXISTS idx_track_tags_track_id  ON track_tags (track_id);
    CREATE INDEX IF NOT EXISTS idx_track_tags_tag_name  ON track_tags (tag_name);
"""


def init_db() -> None:
    """Create the database file and schema if they don't exist yet."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_DDL)
        # Migrations for track_info schema evolution
        cols = {r[1] for r in conn.execute("PRAGMA table_info(track_info)")}
        if "bitrate" not in cols:
            conn.execute("ALTER TABLE track_info ADD COLUMN bitrate TEXT")
        if "file_md5" not in cols:
            conn.execute("ALTER TABLE track_info ADD COLUMN file_md5 TEXT")

@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_track(
    file_path: str,
    file_type: str = "",
    artist: str = "",
    title: str = "",
    album: str = "",
    year: str = "",
    genre: str = "",
    bitrate: str = "",
    file_size: int = 0,
    modified_at: str = "",
) -> None:
    """Insert or replace a track record."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tracks
                (file_path, file_type, artist, title, album, year, genre,
                 bitrate, file_size, modified_at, scanned_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now'))
            ON CONFLICT(file_path) DO UPDATE SET
                file_type   = excluded.file_type,
                artist      = excluded.artist,
                title       = excluded.title,
                album       = excluded.album,
                year        = excluded.year,
                genre       = excluded.genre,
                bitrate     = excluded.bitrate,
                file_size   = excluded.file_size,
                modified_at = excluded.modified_at,
                scanned_at  = excluded.scanned_at
            """,
            (file_path, file_type, artist, title, album, year, genre,
             bitrate, file_size, modified_at),
        )


def get_all_tracks() -> list[sqlite3.Row]:
    """Return all tracks ordered by artist, album, title."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM tracks ORDER BY artist, album, title"
        ).fetchall()


def delete_track(file_path: str) -> None:
    """Remove a track by its file path."""
    with _connect() as conn:
        conn.execute("DELETE FROM tracks WHERE file_path = ?", (file_path,))


def clear_all_tracks() -> None:
    """Wipe the entire inventory."""
    with _connect() as conn:
        conn.execute("DELETE FROM tracks")


# ------------------------------------------------------------------ #
# track_info helpers                                                   #
# ------------------------------------------------------------------ #

def compute_file_md5(file_path: str, chunk_size: int = 1 << 20) -> str:
    """Return the MD5 hex digest of a file, reading in chunks to handle large files."""
    h = hashlib.md5()
    with open(file_path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def upsert_track_info(
    partition: str,
    rel_path: str,
    artist: str = "",
    title: str = "",
    album: str = "",
    bitrate: str = "",
    file_md5: str = "",
) -> None:
    """Insert or update a track_info record identified by (partition, rel_path)."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO track_info
                (partition, rel_path, artist, title, album, bitrate, file_md5, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now'))
            ON CONFLICT(partition, rel_path) DO UPDATE SET
                artist     = excluded.artist,
                title      = excluded.title,
                album      = excluded.album,
                bitrate    = excluded.bitrate,
                file_md5   = excluded.file_md5,
                updated_at = excluded.updated_at
            """,
            (partition, rel_path, artist, title, album, bitrate, file_md5),
        )


def find_by_md5(file_md5: str) -> list[sqlite3.Row]:
    """Return all track_info rows that share the given MD5 digest."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM track_info WHERE file_md5 = ? ORDER BY partition, rel_path",
            (file_md5,),
        ).fetchall()


def get_track_info(partition: str | None = None) -> list[sqlite3.Row]:
    """Return track_info rows, optionally filtered by partition."""
    with _connect() as conn:
        if partition:
            return conn.execute(
                "SELECT * FROM track_info WHERE partition = ? ORDER BY artist, album, title",
                (partition,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM track_info ORDER BY partition, artist, album, title"
        ).fetchall()


def delete_track_info(partition: str, rel_path: str) -> None:
    """Remove a single track_info record (cascades to track_tags)."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM track_info WHERE partition = ? AND rel_path = ?",
            (partition, rel_path),
        )


# ------------------------------------------------------------------ #
# track_tags helpers                                                   #
# ------------------------------------------------------------------ #

def set_tag(track_id: int, tag_name: str, tag_value: str) -> None:
    """Insert or replace a single tag for a track."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO track_tags (track_id, tag_name, tag_value)
            VALUES (?, ?, ?)
            ON CONFLICT(track_id, tag_name) DO UPDATE SET
                tag_value = excluded.tag_value
            """,
            (track_id, tag_name, tag_value),
        )


def set_tags(track_id: int, tags: dict[str, str]) -> None:
    """Bulk-replace all supplied tags for a track (other existing tags are untouched)."""
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO track_tags (track_id, tag_name, tag_value)
            VALUES (?, ?, ?)
            ON CONFLICT(track_id, tag_name) DO UPDATE SET
                tag_value = excluded.tag_value
            """,
            [(track_id, k, v) for k, v in tags.items()],
        )


def get_tags(track_id: int) -> dict[str, str]:
    """Return all tags for a track as {tag_name: tag_value}."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT tag_name, tag_value FROM track_tags WHERE track_id = ? ORDER BY tag_name",
            (track_id,),
        ).fetchall()
    return {r["tag_name"]: r["tag_value"] for r in rows}


def delete_tag(track_id: int, tag_name: str) -> None:
    """Remove a single tag from a track."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM track_tags WHERE track_id = ? AND tag_name = ?",
            (track_id, tag_name),
        )


def delete_all_tags(track_id: int) -> None:
    """Remove all tags for a track."""
    with _connect() as conn:
        conn.execute("DELETE FROM track_tags WHERE track_id = ?", (track_id,))
