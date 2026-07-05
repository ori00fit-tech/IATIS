"""
tests/test_storage_resilience.py
-----------------------------------
Storage must never kill a decision run.

The production incident these tests guard against: main.py used to call
log_decision_db() / record_engine_votes() bare, BEFORE telegram_send().
A D1 Worker outage (Cloudflare incident, rotated token, network blip)
raised D1Error out of run_pipeline(), the scheduler caught it per-symbol
and moved on — and the EXECUTE signal was silently never delivered.

Now every persistence call in run_pipeline() goes through
main._safe_store(), which retries once and then logs-and-continues, so
the report is still returned and Telegram delivery still happens.
"""
from __future__ import annotations

import requests

import main as main_module
from utils.helpers import load_config


def _d1_down(url, json=None, headers=None, timeout=None, **kwargs):
    raise requests.ConnectionError("simulated D1 Worker outage")


def test_pipeline_survives_d1_outage(monkeypatch):
    """run_pipeline() must complete and return a report while every D1
    call fails, and the Telegram path must still be reachable."""
    monkeypatch.setattr("storage.d1_client._post", _d1_down)
    monkeypatch.setattr(main_module, "_STORE_RETRY_DELAY_S", 0.0)
    # Suppress the local JSONL write (side effect on the real log file),
    # same pattern as tests/test_decision_db.py.
    monkeypatch.setattr(main_module, "log_decision", lambda report: None)

    sent = []
    monkeypatch.setattr(main_module, "telegram_send", lambda r: sent.append(r))

    config = load_config()
    config["data"]["source"] = "synthetic"
    config["telegram"] = {"enabled": True}

    report = main_module.run_pipeline(config)  # must NOT raise

    assert isinstance(report, dict)
    assert report["final_verdict"] in ("EXECUTE", "NO_TRADE")
    # Telegram delivery must not have been blocked by the storage outage:
    # exactly one send for an EXECUTE verdict, none for NO_TRADE.
    assert len(sent) == (1 if report["final_verdict"] == "EXECUTE" else 0)


def test_safe_store_retries_once_then_succeeds(monkeypatch):
    monkeypatch.setattr(main_module, "_STORE_RETRY_DELAY_S", 0.0)
    calls = []

    def flaky(arg):
        calls.append(arg)
        if len(calls) == 1:
            raise requests.ConnectionError("transient blip")

    assert main_module._safe_store("flaky", flaky, "x") is True
    assert calls == ["x", "x"]


def test_safe_store_gives_up_after_two_attempts_without_raising(monkeypatch):
    monkeypatch.setattr(main_module, "_STORE_RETRY_DELAY_S", 0.0)
    calls = []

    def broken(arg):
        calls.append(arg)
        raise RuntimeError("permanent failure")

    assert main_module._safe_store("broken", broken, "x") is False
    assert len(calls) == 2
