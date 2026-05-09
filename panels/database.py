"""
Music inventory database — SQLite schema and access helpers.
"""

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
        updated_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),

        UNIQUE (partition, rel_path)
    );

    CREATE INDEX IF NOT EXISTS idx_track_info_partition ON track_info (partition);
    CREATE INDEX IF NOT EXISTS idx_track_info_artist    ON track_info (artist);
    CREATE INDEX IF NOT EXISTS idx_track_info_album     ON track_info (album);
"""


def init_db() -> None:
    """Create the database file and schema if they don't exist yet."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_DDL)


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
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

def upsert_track_info(
    partition: str,
    rel_path: str,
    artist: str = "",
    title: str = "",
    album: str = "",
) -> None:
    """Insert or update a track_info record identified by (partition, rel_path)."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO track_info (partition, rel_path, artist, title, album, updated_at)
            VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now'))
            ON CONFLICT(partition, rel_path) DO UPDATE SET
                artist     = excluded.artist,
                title      = excluded.title,
                album      = excluded.album,
                updated_at = excluded.updated_at
            """,
            (partition, rel_path, artist, title, album),
        )


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
    """Remove a single track_info record."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM track_info WHERE partition = ? AND rel_path = ?",
            (partition, rel_path),
        )
