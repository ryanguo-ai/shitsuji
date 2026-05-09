# Shitsuji

A personal music library manager built with Python / Tkinter.

---

## Requirements

- Python 3.11+
- Dependencies (install with `pip install -r requirements.txt`):
  - `mutagen` — audio tag reading / writing
  - `Pillow` — cover art thumbnailing
  - `tkinterdnd2` — drag-and-drop support

---

## Runtime files

| File | Purpose |
|---|---|
| `~/.shitsuji/settings.json` | User preferences (foobar path, music lib paths, window geometry, …) |
| `~/.shitsuji/inventory.db` | SQLite music inventory database |

---

## Database schema

### `tracks`

Raw scan results — one row per physical file discovered on disk.

```sql
CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path   TEXT    NOT NULL UNIQUE,   -- absolute path to the file
    file_type   TEXT,                      -- e.g. "flac", "mp3"
    artist      TEXT,
    title       TEXT,
    album       TEXT,
    year        TEXT,
    genre       TEXT,
    bitrate     TEXT,
    file_size   INTEGER,                   -- bytes
    modified_at TEXT,                      -- file mtime (ISO-8601)
    scanned_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks (artist);
CREATE INDEX IF NOT EXISTS idx_tracks_album  ON tracks (album);
```

### `track_info`

Curated music library inventory — one row per catalogued track.
`rel_path` is relative to the partition root folder configured in Settings.

```sql
CREATE TABLE IF NOT EXISTS track_info (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    partition    TEXT    NOT NULL,         -- e.g. "CPOP", "JPOP", "OST"
    rel_path     TEXT    NOT NULL,         -- e.g. "Cyndi Lauper/She's So Unusual/01 - Girls Just Want to Have Fun.flac"
    artist       TEXT,                     -- e.g. "Cyndi Lauper"
    title        TEXT,                     -- e.g. "Girls Just Want to Have Fun"
    album        TEXT,                     -- e.g. "She's So Unusual"
    updated_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),

    UNIQUE (partition, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_track_info_partition ON track_info (partition);
CREATE INDEX IF NOT EXISTS idx_track_info_artist    ON track_info (artist);
CREATE INDEX IF NOT EXISTS idx_track_info_album     ON track_info (album);
```

**Partitions** (configured in Settings → Music library folder paths):

| Partition | Default root |
|---|---|
| POP | `C:\_MUSIC_LIB` |
| CPOP | `C:\_MUSIC_LIB` |
| JPOP | `C:\_MUSIC_LIB` |
| OST | `C:\_MUSIC_LIB` |
| Instrumental | `C:\_MUSIC_LIB` |
| OTHER | `C:\_MUSIC_LIB` |

---

## Project layout

```
shitsuji/
├── main.py                     # Application entry point
└── panels/
    ├── __init__.py
    ├── audio_details_panel.py  # FLAC tag viewer / editor with cover art
    ├── database.py             # SQLite schema + CRUD helpers
    ├── edit_tags_panel.py      # Batch FLAC tag editor
    ├── folder_scanner.py       # Scan tab — file tree, DnD, multi-select play
    ├── lyrics_panel.py         # FLAC lyrics viewer / editor
    ├── search_panel.py         # Search tab — DnD file list
    └── settings_panel.py       # Settings dialog + load/save helpers
```
