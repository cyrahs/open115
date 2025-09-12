"""
Unified logging configuration: logs to console and to log/YYMMDD.log.

Usage:
    from app.core import logger
    log = logger.get("open115")
    log.info("message")

Config:
    LOG_LEVEL environment variable controls verbosity (DEBUG, INFO, WARNING, ERROR, CRITICAL).
"""

from __future__ import annotations

import logging
import os
import sys

# Internal flag to avoid duplicate configuration
_configured = False

# Human-readable formats
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _determine_level(level: int | str | None = None) -> int:
    """Resolve a numeric log level from an int/str/None value.

    Order of precedence:
    - explicit level argument
    - LOG_LEVEL environment variable
    - INFO (default)
    """
    if isinstance(level, int):
        return level
    level_name = level.upper() if isinstance(level, str) else os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


def setup(level: int | str | None = None) -> None:
    """Configure the root logger with console + daily file handlers.

    This is idempotent and safe to call multiple times.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    numeric_level = _determine_level(level)
    root.setLevel(numeric_level)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # Add console handler if not present
    console_name = "app-console"
    if not any(getattr(h, "name", None) == console_name for h in root.handlers):
        console = logging.StreamHandler(stream=sys.stdout)
        console.set_name(console_name)
        console.setLevel(numeric_level)
        console.setFormatter(formatter)
        root.addHandler(console)

    _configured = True


def get(name: str | None = None) -> logging.Logger:
    """Get a logger that logs to console via root handlers.

    Example:
        log = get("open115")
        log.info("Hello")
    """
    setup()
    return logging.getLogger(name) if name else logging.getLogger()
