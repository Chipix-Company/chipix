"""
Logging configuration for ChipVerify AI.

Provides a consistent, coloured logger for all modules.
"""

from __future__ import annotations

import io
import logging
import sys

try:
    import config
except ModuleNotFoundError:
    from original_core import config


class _SafeTextStream(io.TextIOBase):
    """Text stream wrapper that replaces unencodable characters instead of failing."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._encoding = getattr(stream, "encoding", None) or "utf-8"

    @property
    def encoding(self):
        return self._encoding

    def write(self, text):
        if not isinstance(text, str):
            text = str(text)
        safe_text = text.encode(self._encoding, errors="replace").decode(self._encoding)
        return self._stream.write(safe_text)

    def flush(self):
        return self._stream.flush()

    def isatty(self):
        return bool(getattr(self._stream, "isatty", lambda: False)())

    def fileno(self):
        fileno = getattr(self._stream, "fileno", None)
        if fileno is None:
            raise OSError("Underlying stream does not expose fileno()")
        return fileno()


def get_logger(name: str) -> logging.Logger:
    """Create a named logger with consistent formatting."""
    logger = logging.getLogger(f"ChipVerify.{name}")

    if not logger.handlers:
        handler = logging.StreamHandler(_SafeTextStream(sys.stdout))
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(name)-28s | %(levelname)-7s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    return logger
