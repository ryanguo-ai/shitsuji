"""
Minimal MusicBrainz Web-Service client (read-only, JSON).

MusicBrainz rate-limit policy: max 1 authenticated request/second.
We stay well within that by inserting a short delay after every call.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from panels.logger import get_logger

_log = get_logger("musicbrainz")

_BASE_URL  = "https://musicbrainz.org/ws/2"
_USER_AGENT = "Shitsuji/1.0 (music-manager; github.com/ryanguo-ai/shitsuji)"
_RATE_DELAY = 1.1   # seconds between requests


class MusicBrainzError(Exception):
    """Raised when the MusicBrainz API returns an error or is unreachable."""


def _get(url: str) -> dict:
    """Perform an authenticated GET request and return parsed JSON."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise MusicBrainzError(f"HTTP {exc.code}: {exc.reason}") from exc
    except OSError as exc:
        raise MusicBrainzError(f"Network error: {exc}") from exc
    finally:
        time.sleep(_RATE_DELAY)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MusicBrainzError(f"Invalid JSON response: {exc}") from exc


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

def search_artists(query: str, limit: int = 25) -> list[dict]:
    """
    Search MusicBrainz for artists matching *query*.

    Returns a list of artist dicts (keys vary by result but always include
    'id', 'name', 'sort-name', 'score').  Returns an empty list on empty
    *query*.
    """
    query = query.strip()
    if not query:
        return []

    params = urllib.parse.urlencode({"query": query, "limit": limit, "fmt": "json"})
    url = f"{_BASE_URL}/artist?{params}"
    _log.info(f"MusicBrainz artist search: {query!r}")

    data = _get(url)
    artists = data.get("artists", [])
    _log.info(f"  → {len(artists)} result(s) (total count={data.get('count', '?')})")
    return artists


def parse_artist(mb_artist: dict) -> dict:
    """
    Extract the fields we care about from a raw MusicBrainz artist dict.

    Returns::

        {
            "musicbrainz_id": str,
            "name":           str,
            "sort_name":      str,
            "country":        str,
            "score":          int,
            "aliases":        [{"alias": str, "locale": str|None, "type": str|None}],
        }
    """
    aliases = []
    for raw in mb_artist.get("aliases", []):
        alias_text = (raw.get("name") or "").strip()
        if alias_text:
            aliases.append({
                "alias":      alias_text,
                "locale":     raw.get("locale") or "",
                "alias_type": raw.get("type")   or "",
            })

    return {
        "musicbrainz_id": mb_artist.get("id", ""),
        "name":           mb_artist.get("name", ""),
        "sort_name":      mb_artist.get("sort-name", ""),
        "country":        mb_artist.get("country", ""),
        "score":          int(mb_artist.get("score", 0)),
        "aliases":        aliases,
    }
