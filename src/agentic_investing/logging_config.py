"""Application logging configuration.

Logging is intentionally concise and structured around operational events:
startup, external calls, tool dispatch, risk decisions, order outcomes, and
failures. Secrets and full prompt contents are never logged.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from .config import log_level


_LOGGER_NAME = "agentic_investing"


def configure_logging(*, log_dir: str | Path | None = None, force: bool = False) -> None:
    """Configure console and rotating file logging once per process.

    ``force=True`` is intended for tests and controlled application
    reconfiguration; normal callers should use the idempotent default.
    """

    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        if not force:
            return
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    level = getattr(logging, log_level(), logging.INFO)
    logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    destination = Path(log_dir or os.environ.get("AGENTIC_INVESTING_LOG_DIR", "logs"))
    destination.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        destination / "agentic_investing.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False


def get_logger(name: str | None = None) -> logging.Logger:
    """Return an application logger; configure it lazily for library callers."""

    configure_logging()
    if not name:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(name if name.startswith(f"{_LOGGER_NAME}.") else f"{_LOGGER_NAME}.{name}")


def shutdown_logging() -> None:
    """Close and remove application handlers, primarily for tests and shutdown."""

    logger = logging.getLogger(_LOGGER_NAME)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
