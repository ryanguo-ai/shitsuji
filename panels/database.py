"""
Music inventory database — SQLite schema and access helpers.
"""

import pathlib
import sqlite3
from contextlib import contextmanager

DB_PATH = pathlib.Path.home() / ".shitsuji" / "inventory.db"


def init_db() -> None:
    """Create the database file and schema if they don't exist yet."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
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
        """)


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
