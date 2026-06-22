"""
utils/logger.py
----------------
Centralized logger for IATIS. Every module should import get_logger(__name__)
instead of creating its own logging config, so log format/level stays
consistent across the whole system.
"""

import logging
import os

_CONFIGURED = False


def _configure_root(level: str = "INFO", log_file: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    handlers = [logging.StreamHandler()]

    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger. Configures root logging on first call."""
    _configure_root()
    return logging.getLogger(name)
