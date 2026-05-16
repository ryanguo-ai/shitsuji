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

    -- ------------------------------------------------------------------ --
    -- artist_info                                                          --
    -- Canonical artist records, optionally linked to a MusicBrainz ID.   --
    -- ------------------------------------------------------------------ --
    CREATE TABLE IF NOT EXISTS artist_info (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT    NOT NULL,
        sort_name       TEXT,
        country         TEXT,
        musicbrainz_id  TEXT    UNIQUE,
        created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
    );

    CREATE INDEX IF NOT EXISTS idx_artist_info_name ON artist_info (name);
    CREATE INDEX IF NOT EXISTS idx_artist_info_mb   ON artist_info (musicbrainz_id);

    -- ------------------------------------------------------------------ --
    -- artist_alias                                                         --
    -- Alternative names / romanisations for an artist.                    --
    -- ------------------------------------------------------------------ --
    CREATE TABLE IF NOT EXISTS artist_alias (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        artist_id   INTEGER NOT NULL
                        REFERENCES artist_info (id) ON DELETE CASCADE,
        alias       TEXT    NOT NULL,
        locale      TEXT,
        alias_type  TEXT,

        UNIQUE (artist_id, alias)
    );

    CREATE INDEX IF NOT EXISTS idx_artist_alias_artist_id ON artist_alias (artist_id);
    CREATE INDEX IF NOT EXISTS idx_artist_alias_alias     ON artist_alias (alias);

    -- ------------------------------------------------------------------ --
    -- track_ranking                                                        --
    -- User-assigned 0-5 star/heart rating for a catalogued track.         --
    -- ------------------------------------------------------------------ --
    CREATE TABLE IF NOT EXISTS track_ranking (
        track_id    INTEGER PRIMARY KEY
                        REFERENCES track_info (id) ON DELETE CASCADE,
        ranking     INTEGER NOT NULL DEFAULT 0
                        CHECK (ranking BETWEEN 0 AND 5),
        modified_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
    );
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
    """Return track_info rows (with ranking), optionally filtered by partition."""
    with _connect() as conn:
        base = """
            SELECT ti.*, COALESCE(tr.ranking, 0) AS ranking
              FROM track_info ti
              LEFT JOIN track_ranking tr ON ti.id = tr.track_id
        """
        if partition:
            return conn.execute(
                base + " WHERE ti.partition = ? ORDER BY ti.artist, ti.album, ti.title",
                (partition,),
            ).fetchall()
        return conn.execute(
            base + " ORDER BY ti.partition, ti.artist, ti.album, ti.title"
        ).fetchall()


def find_track_by_metadata(artist: str, title: str, album: str) -> list[sqlite3.Row]:
    """Return track_info rows whose artist/title/album match (case-insensitive)."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT * FROM track_info
             WHERE lower(trim(artist)) = lower(trim(?))
               AND lower(trim(title))  = lower(trim(?))
               AND lower(trim(album))  = lower(trim(?))
             ORDER BY updated_at DESC
            """,
            (artist, title, album),
        ).fetchall()


def find_track_by_artist_title_album(
    artist: str, title: str, album: str
) -> list[sqlite3.Row]:
    """Return track_info rows matching artist+title+album (case-insensitive)."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT * FROM track_info
             WHERE lower(trim(artist)) = lower(trim(?))
               AND lower(trim(title))  = lower(trim(?))
               AND lower(trim(album))  = lower(trim(?))
             ORDER BY updated_at DESC
            """,
            (artist, title, album),
        ).fetchall()


def find_track_by_artist_title(artist: str, title: str) -> list[sqlite3.Row]:
    """Return track_info rows matching artist+title (case-insensitive)."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT * FROM track_info
             WHERE lower(trim(artist)) = lower(trim(?))
               AND lower(trim(title))  = lower(trim(?))
             ORDER BY updated_at DESC
            """,
            (artist, title),
        ).fetchall()


def find_track_by_artist_title_bitrate(
    artist: str, title: str, bitrate: str
) -> list[sqlite3.Row]:
    """Return track_info rows whose artist/title/bitrate match (case-insensitive).

    Used by Compare / Update Track flows which now key on (artist, title, bitrate)
    rather than (artist, title, album).
    """
    with _connect() as conn:
        return conn.execute(
            """
            SELECT * FROM track_info
             WHERE lower(trim(artist))  = lower(trim(?))
               AND lower(trim(title))   = lower(trim(?))
               AND lower(trim(bitrate)) = lower(trim(?))
             ORDER BY updated_at DESC
            """,
            (artist, title, bitrate),
        ).fetchall()


# ------------------------------------------------------------------ #
# track_ranking helpers                                                #
# ------------------------------------------------------------------ #

def set_track_ranking(track_id: int, ranking: int) -> None:
    """Insert or update the 0-5 ranking for a track_info row."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO track_ranking (track_id, ranking, modified_at)
            VALUES (?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now'))
            ON CONFLICT(track_id) DO UPDATE SET
                ranking     = excluded.ranking,
                modified_at = excluded.modified_at
            """,
            (track_id, max(0, min(5, ranking))),
        )


def get_track_ranking(track_id: int) -> int:
    """Return the ranking (0-5) for a track_info row, defaulting to 0."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT ranking FROM track_ranking WHERE track_id = ?", (track_id,)
        ).fetchone()
        return int(row["ranking"]) if row else 0


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


# ------------------------------------------------------------------ #
# artist_info helpers                                                  #
# ------------------------------------------------------------------ #

def get_all_artists() -> list[sqlite3.Row]:
    """Return all artist_info rows ordered by name."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM artist_info ORDER BY sort_name, name"
        ).fetchall()


def search_artists_local(query: str) -> list[sqlite3.Row]:
    """Return artist_info rows whose name, sort_name, or any alias contains *query*.

    Matching is case-insensitive substring.  An empty *query* returns all rows.
    Results are de-duplicated and ordered by sort_name, name.
    """
    with _connect() as conn:
        if not query or not query.strip():
            return conn.execute(
                "SELECT * FROM artist_info ORDER BY sort_name, name"
            ).fetchall()
        pattern = f"%{query.strip()}%"
        return conn.execute(
            """
            SELECT DISTINCT ai.*
              FROM artist_info ai
              LEFT JOIN artist_alias aa ON aa.artist_id = ai.id
             WHERE ai.name      LIKE ? COLLATE NOCASE
                OR ai.sort_name LIKE ? COLLATE NOCASE
                OR aa.alias     LIKE ? COLLATE NOCASE
             ORDER BY ai.sort_name, ai.name
            """,
            (pattern, pattern, pattern),
        ).fetchall()


def get_artist(artist_id: int) -> sqlite3.Row | None:
    """Return a single artist_info row by primary key, or None."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM artist_info WHERE id = ?", (artist_id,)
        ).fetchone()


def upsert_artist(
    name: str,
    sort_name: str = "",
    country: str = "",
    musicbrainz_id: str = "",
) -> int:
    """Insert a new artist or update the existing row matched by musicbrainz_id.
    Returns the row id."""
    with _connect() as conn:
        if musicbrainz_id:
            conn.execute(
                """
                INSERT INTO artist_info (name, sort_name, country, musicbrainz_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(musicbrainz_id) DO UPDATE SET
                    name      = excluded.name,
                    sort_name = excluded.sort_name,
                    country   = excluded.country
                """,
                (name, sort_name, country, musicbrainz_id),
            )
            row = conn.execute(
                "SELECT id FROM artist_info WHERE musicbrainz_id = ?",
                (musicbrainz_id,),
            ).fetchone()
        else:
            conn.execute(
                """
                INSERT INTO artist_info (name, sort_name, country)
                VALUES (?, ?, ?)
                """,
                (name, sort_name, country),
            )
            row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        return row["id"]


def update_artist(
    artist_id: int,
    name: str,
    sort_name: str = "",
    country: str = "",
    musicbrainz_id: str = "",
) -> None:
    """Update an existing artist_info row."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE artist_info
               SET name = ?, sort_name = ?, country = ?, musicbrainz_id = ?
             WHERE id = ?
            """,
            (name, sort_name, country, musicbrainz_id or None, artist_id),
        )


def delete_artist(artist_id: int) -> None:
    """Delete an artist_info row (cascades to artist_alias)."""
    with _connect() as conn:
        conn.execute("DELETE FROM artist_info WHERE id = ?", (artist_id,))


# ------------------------------------------------------------------ #
# artist_alias helpers                                                 #
# ------------------------------------------------------------------ #

def get_aliases(artist_id: int) -> list[sqlite3.Row]:
    """Return all aliases for an artist, ordered by alias text."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM artist_alias WHERE artist_id = ? ORDER BY alias",
            (artist_id,),
        ).fetchall()


def add_alias(
    artist_id: int,
    alias: str,
    locale: str = "",
    alias_type: str = "",
) -> None:
    """Insert an alias; silently ignores duplicate (artist_id, alias) pairs."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO artist_alias (artist_id, alias, locale, alias_type)
            VALUES (?, ?, ?, ?)
            """,
            (artist_id, alias, locale or None, alias_type or None),
        )


def delete_alias(alias_id: int) -> None:
    """Delete an artist_alias row by its primary key."""
    with _connect() as conn:
        conn.execute("DELETE FROM artist_alias WHERE id = ?", (alias_id,))


def update_alias(
    alias_id: int,
    alias: str,
    locale: str = "",
    alias_type: str = "",
) -> None:
    """Update the text, locale and type of an existing alias row."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE artist_alias
               SET alias = ?, locale = ?, alias_type = ?
             WHERE id = ?
            """,
            (alias, locale or None, alias_type or None, alias_id),
        )


def get_artist_name_variants(query: str) -> set[str]:
    """Return every name variant (name, sort_name, alias) for artists that match *query*.

    Used by Search In Lib to expand an artist query through the alias table so
    that a track stored under any alias of an artist is included in results.
    Returns an empty set if *query* is blank.
    """
    if not query.strip():
        return set()
    q = f"%{query.strip()}%"
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ai.name, ai.sort_name, aa.alias
              FROM artist_info ai
              LEFT JOIN artist_alias aa ON aa.artist_id = ai.id
             WHERE ai.name      LIKE ? COLLATE NOCASE
                OR ai.sort_name LIKE ? COLLATE NOCASE
                OR aa.alias     LIKE ? COLLATE NOCASE
            """,
            (q, q, q),
        ).fetchall()
    variants: set[str] = set()
    for row in rows:
        if row["name"]:      variants.add(row["name"])
        if row["sort_name"]: variants.add(row["sort_name"])
        if row["alias"]:     variants.add(row["alias"])
    return variants
