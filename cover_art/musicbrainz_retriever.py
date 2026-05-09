"""MusicBrainz + Cover Art Archive retriever."""

import logging
import re
from pathlib import Path
from urllib.parse import quote, urljoin

from cover_art.abstract_retriever import AbstractCoverRetriever

LOGGER = logging.getLogger("cover_art.musicbrainz")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "DNT": "1",
}
_INVALID_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(value: str) -> str:
    cleaned = _INVALID_FILENAME_RE.sub("_", value.strip())
    return cleaned.strip("._") or "unknown"


def _normalize(value: str) -> str:
    return value.strip().casefold()


class MusicBrainzCoverRetriever(AbstractCoverRetriever):
    """Retrieve cover art from MusicBrainz and Cover Art Archive."""

    def get_archive_org_real_url(self, url: str) -> str:
        import requests

        session = requests.Session()
        session.headers.update(HEADERS)

        current_url = url
        for _ in range(10):
            response = session.get(current_url, timeout=10,
                                   allow_redirects=False, stream=True)
            response.close()   # don't download the body
            if response.status_code not in (301, 302, 307, 308):
                return current_url
            location = response.headers.get("Location")
            if not location:
                return current_url
            current_url = urljoin(current_url, location)

        return current_url

    def get_cover_arts(self, artist: str, album: str, title: str,
                       cache_dir: str) -> list[str]:
        import requests
        from urllib.parse import urlencode

        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)

        # MusicBrainz "release" = album name.  Prefer album; fall back to title.
        release_term = album.strip() or title.strip()
        lucene_q = f"artist:({quote(artist)}) AND release:({quote(release_term)})"
        search_url = (
            "https://musicbrainz.org/ws/2/release/?"
            + urlencode({"fmt": "json", "query": lucene_q})
        )
        LOGGER.info("Searching MusicBrainz: artist=%s release=%s", artist, release_term)

        session = requests.Session()
        session.headers.update(HEADERS)
        response = session.get(search_url, timeout=20)
        response.raise_for_status()
        payload = response.json()

        matching_release_ids: list[str] = []
        for release in payload.get("releases", []):
            artist_credit = release.get("artist-credit") or []
            if not artist_credit:
                continue
            credit_name = str(artist_credit[0].get("name", ""))
            if _normalize(credit_name) != _normalize(artist):
                continue

            release_id = release.get("id")
            if release_id and release_id not in matching_release_ids:
                matching_release_ids.append(release_id)
            if len(matching_release_ids) >= 5:
                break

        results: list[str] = []
        base_name = f"{_sanitize(artist)}-{_sanitize(release_term)}.musicbrainz"
        for release_id in matching_release_ids:
            if len(results) >= 5:
                break
            try:
                cover_response = session.get(
                    f"https://coverartarchive.org/release/{release_id}",
                    timeout=20,
                )
                cover_response.raise_for_status()
                cover_payload = cover_response.json()
                images = cover_payload.get("images") or []
                if not images:
                    LOGGER.debug("No cover images for release %s", release_id)
                    continue

                first_image = images[0]
                thumbnails = first_image.get("thumbnails") or {}
                image_url = thumbnails.get("500") or first_image.get("image")
                if not image_url:
                    LOGGER.debug("No usable cover URL for release %s", release_id)
                    continue

                real_url = self.get_archive_org_real_url(image_url)
                image_response = session.get(real_url, timeout=30)
                image_response.raise_for_status()

                target_path = cache_path / f"{base_name}.{len(results) + 1}.jpg"
                target_path.write_bytes(image_response.content)
                results.append(str(target_path))
                LOGGER.info("Downloaded MusicBrainz cover: %s", target_path)
            except Exception:
                LOGGER.exception("Failed to retrieve MusicBrainz cover for release %s", release_id)

        return results
