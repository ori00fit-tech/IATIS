"""
tests/test_logger_config.py
-----------------------------
config.yaml's logging keys used to live under `fundamentals:` and were
read by nothing — `file:`/`level:` were dead config and file logging
silently never existed (production audit, Phase 9). These tests pin the
new wiring: utils/logger._read_logging_config() resolves the `logging:`
block, honors the IATIS_LOG_LEVEL override, and never raises.
"""
from __future__ import annotations

from utils import logger as logger_module
from utils.helpers import load_config


def test_config_yaml_has_logging_block_and_no_dead_keys():
    cfg = load_config()
    assert "logging" in cfg
    assert cfg["logging"].get("level")
    # The two keys that used to sit, unread, under fundamentals:
    assert "file" not in cfg["fundamentals"]
    assert "level" not in cfg["fundamentals"]


def test_read_logging_config_uses_config_yaml():
    level, log_file = logger_module._read_logging_config()
    assert level == load_config()["logging"]["level"]
    assert log_file == (load_config()["logging"].get("file") or "")


def test_env_var_overrides_config_level(monkeypatch):
    monkeypatch.setenv("IATIS_LOG_LEVEL", "DEBUG")
    level, _ = logger_module._read_logging_config()
    assert level == "DEBUG"


def test_broken_config_falls_back_to_info(monkeypatch):
    def boom():
        raise RuntimeError("config.yaml unreadable")

    monkeypatch.setattr("utils.helpers.load_config", boom)
    monkeypatch.delenv("IATIS_LOG_LEVEL", raising=False)
    level, log_file = logger_module._read_logging_config()
    assert (level, log_file) == ("INFO", "")


def test_get_logger_still_returns_working_logger():
    log = logger_module.get_logger("iatis.test")
    log.info("smoke")  # must not raise
