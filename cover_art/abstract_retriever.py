from abc import ABC, abstractmethod


class AbstractCoverRetriever(ABC):
    @abstractmethod
    def get_cover_arts(self, artist: str, album: str, title: str, cache_dir: str) -> list[str]:
        """Search and download cover images. Returns list of local file paths."""
        pass
