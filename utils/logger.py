"""
utils/logger.py
----------------
Centralized logger for IATIS. Every module should import get_logger(__name__)
instead of creating its own logging config, so log format/level stays
consistent across the whole system.

Configuration comes from config.yaml's `logging:` block (level, optional
rotating file) with an IATIS_LOG_LEVEL environment override — reading it
must never be able to break logging itself, so any failure falls back to
INFO/stderr.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_CONFIGURED = False

# Rotation bounds for the optional file handler — an unbounded FileHandler
# on a VPS is how disks fill up silently.
_MAX_BYTES = 10_000_000
_BACKUP_COUNT = 5


def _read_logging_config() -> tuple[str, str]:
    """Resolve (level, file) from config.yaml `logging:` + env override.

    Never raises: logging must come up even if config.yaml is missing,
    unparseable, or has no `logging:` block (e.g. unit tests, fresh
    checkouts, research scripts run from odd working directories).
    """
    level, log_file = "INFO", ""
    try:
        from utils.helpers import load_config

        cfg = load_config().get("logging") or {}
        level = str(cfg.get("level") or level)
        log_file = str(cfg.get("file") or "")
    except Exception:
        pass
    return os.environ.get("IATIS_LOG_LEVEL", level), log_file


def _configure_root(level: str | None = None, log_file: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    cfg_level, cfg_file = _read_logging_config()
    level = level or cfg_level
    log_file = cfg_file if log_file is None else log_file

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(
            RotatingFileHandler(log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT)
        )

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
