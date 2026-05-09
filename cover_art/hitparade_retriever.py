"""Hitparade cover art retriever."""

import logging
import random
import re
import time
from pathlib import Path
from urllib.parse import quote

from cover_art.abstract_retriever import AbstractCoverRetriever

LOGGER = logging.getLogger("cover_art.hitparade")
HEADERS = {
    "Host": "hitparade.ch",
    "pragma": "no-cache",
    "cache-control": "no-cache",
    "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36 Edg/134.0.0.0",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
}
_IMAGE_URL_RE = re.compile(r".*'(https.*jpg)'.*", re.DOTALL)
_INVALID_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(value: str) -> str:
    cleaned = _INVALID_FILENAME_RE.sub("_", value.strip())
    return cleaned.strip("._") or "unknown"


class HitparadeRetriever(AbstractCoverRetriever):
    """Retrieve cover art from hitparade.ch."""

    def get_cover_arts(self, artist: str, title: str, cache_dir: str) -> list[str]:
        import requests
        from bs4 import BeautifulSoup

        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)

        search_url = (
            "https://hitparade.ch/search.asp?cat=s&from=&to=&artist="
            f"{quote(artist)}&artist_search=starts&title={quote(title)}&title_search=starts"
        )
        LOGGER.info("Searching Hitparade for %s - %s", artist, title)

        session = requests.Session()
        session.headers.update(HEADERS)
        response = session.get(search_url, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        page_urls: list[str] = []
        for link in soup.select('table a[href^="/song"]'):
            href = link.get("href", "")
            if not href:
                continue
            url = f"https://hitparade.ch{href}"
            if url not in page_urls:
                page_urls.append(url)

        results: list[str] = []
        base_name = f"{_sanitize(artist)}-{_sanitize(title)}.hitparade"
        for page_url in page_urls[:5]:   # cap at 5 subpages
            try:
                subpage_response = session.get(page_url, timeout=15)
                subpage_response.raise_for_status()
                subpage_soup = BeautifulSoup(subpage_response.text, "html.parser")
                cover_node = subpage_soup.find(class_="coversquare")
                if cover_node is None:
                    LOGGER.debug("No coversquare found for %s", page_url)
                    continue

                match = _IMAGE_URL_RE.search(str(cover_node))
                if not match:
                    LOGGER.debug("No image URL found in coversquare for %s", page_url)
                    continue

                image_url = match.group(1)
                image_response = session.get(image_url, timeout=20)
                image_response.raise_for_status()

                target_path = cache_path / f"{base_name}.{len(results) + 1}.jpg"
                target_path.write_bytes(image_response.content)
                results.append(str(target_path))
                LOGGER.info("Downloaded Hitparade cover: %s", target_path)
                time.sleep(random.uniform(0.5, 1.5))   # polite delay only on success
            except Exception:
                LOGGER.exception("Failed to retrieve Hitparade cover from %s", page_url)

        return results
