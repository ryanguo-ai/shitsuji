"""
Application logger — writes structured log lines to ~/.shitsuji/shitsuji.log

Log line format (tab-separated for easy parsing):
    2026-05-09T14:26:19.123 \t INFO \t send_to_lib \t Sent 3 files to CPOP
"""

import logging
import pathlib

LOG_PATH = pathlib.Path.home() / ".shitsuji" / "shitsuji.log"

# Column widths for alignment
_FMT = "%(asctime)s\t%(levelname)-8s\t%(operation)-20s\t%(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"

_initialised = False


def _init() -> None:
    global _initialised
    if _initialised:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("shitsuji")
    root.setLevel(logging.DEBUG)

    if not root.handlers:
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
        root.addHandler(fh)

    _initialised = True


class _OpAdapter(logging.LoggerAdapter):
    """Injects the ``operation`` field into every log record."""

    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})
        kwargs["extra"]["operation"] = self.extra.get("operation", "-")
        return msg, kwargs


def get_logger(operation: str = "-") -> _OpAdapter:
    """
    Return a logger bound to *operation*.

    Usage::

        log = get_logger("send_to_lib")
        log.info("Sent 3 files to CPOP")
        log.warning("File not found: /path/to/file.flac")
        log.error("DB write failed", exc_info=True)
    """
    _init()
    base = logging.getLogger("shitsuji")
    return _OpAdapter(base, {"operation": operation})
