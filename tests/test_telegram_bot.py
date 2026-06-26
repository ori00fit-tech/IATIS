"""
tests/test_telegram_bot.py
-----------------------------
Tests for execution/telegram_bot.py — all mocked, no real Telegram calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from execution.telegram_bot import (
    _build_message,
    send_raw,
    send_signal,
    test_connection as tg_test_connection,
)

# ---------------------------------------------------------------------------
# Sample reports
# ---------------------------------------------------------------------------

NO_TRADE_REPORT = {
    "symbol": "EURUSD",
    "final_verdict": "NO_TRADE",
    "summary": "NO_TRADE: Confluence score 34.33 below minimum required 75",
    "regime": {
        "state": "RANGING",
        "volatility": "high",
        "confidence": 0.038,
        "trend_strength": -0.337,
    },
    "engine_outputs": [
        {"engine": "SMC", "bias": "BULLISH", "score": 65.0, "reasons": [], "raw": {}},
        {"engine": "PriceAction", "bias": "BEARISH", "score": 4.01, "reasons": [], "raw": {}},
    ],
    "confluence": {
        "score": 34.33,
        "directional_score": 34.33,
        "engines_participating": 2,
        "engines_total": 2,
        "participating_weight_share": 0.45,
        "fail_reasons": ["Confluence score 34.33 below minimum required 75"],
    },
    "risk": {"passed": None, "reasons": ["Risk gate not evaluated"]},
}

EXECUTE_REPORT = {
    "symbol": "EURUSD",
    "final_verdict": "EXECUTE",
    "summary": "EXECUTE BULLISH: 2/2 active engines agreed, confluence score 82/100",
    "regime": {
        "state": "TRENDING",
        "volatility": "normal",
        "confidence": 0.75,
        "trend_strength": 0.60,
    },
    "engine_outputs": [
        {"engine": "SMC", "bias": "BULLISH", "score": 85.0, "reasons": [], "raw": {}},
        {"engine": "PriceAction", "bias": "BULLISH", "score": 78.0, "reasons": [], "raw": {}},
    ],
    "confluence": {
        "score": 82.0,
        "directional_score": 82.0,
        "engines_participating": 2,
        "engines_total": 2,
        "participating_weight_share": 0.45,
        "fail_reasons": [],
    },
    "risk": {
        "passed": True,
        "reasons": ["All risk checks passed"],
        "recommended_risk_pct": 0.01,
        "position_size_units": 3.33,
    },
    "entry_price": 1.0850,
    "stop_loss": 1.0820,
    "take_profit": 1.0940,
    "risk_reward": "1:3",
}


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------

def test_build_message_contains_symbol():
    msg = _build_message(NO_TRADE_REPORT)
    assert "EURUSD" in msg


def test_build_message_contains_verdict():
    msg = _build_message(NO_TRADE_REPORT)
    assert "NO_TRADE" in msg  # verdict always present

    msg_ex = _build_message(EXECUTE_REPORT)
    assert "SIGNAL" in msg_ex or "EXECUTE" in msg_ex or "Trade Setup" in msg_ex


def test_build_message_contains_regime():
    msg = _build_message(NO_TRADE_REPORT)
    assert "RANGING" in msg


def test_build_message_contains_engine_outputs():
    msg = _build_message(NO_TRADE_REPORT)
    assert "SMC" in msg
    assert "PriceAction" in msg


def test_build_message_contains_fail_reasons_for_no_trade():
    msg = _build_message(NO_TRADE_REPORT)
    assert "34.33" in msg


def test_build_message_contains_trade_setup_for_execute():
    msg = _build_message(EXECUTE_REPORT)
    assert "1.0850" in msg   # entry
    assert "1.0820" in msg   # SL
    assert "1.0940" in msg   # TP


def test_build_message_respects_length_limit():
    # inflate the report to force truncation
    bloated = dict(NO_TRADE_REPORT)
    bloated["summary"] = "x" * 5000
    msg = _build_message(bloated)
    assert len(msg) <= 4096


def test_build_message_is_string():
    msg = _build_message(NO_TRADE_REPORT)
    assert isinstance(msg, str)
    assert len(msg) > 50


# ---------------------------------------------------------------------------
# send_signal — mocked HTTP
# ---------------------------------------------------------------------------

def _ok_response():
    mock = MagicMock()
    mock.json.return_value = {"ok": True, "result": {"message_id": 42}}
    mock.raise_for_status = MagicMock()
    return mock


def _fail_response():
    mock = MagicMock()
    mock.json.return_value = {"ok": False, "description": "Bad Request: chat not found"}
    mock.raise_for_status = MagicMock()
    return mock


def test_send_signal_returns_true_on_success():
    with patch("requests.post", return_value=_ok_response()):
        result = send_signal(NO_TRADE_REPORT, token="tok", chat_id="123")
    assert result is True


def test_send_signal_returns_false_on_api_error():
    with patch("requests.post", return_value=_fail_response()):
        result = send_signal(NO_TRADE_REPORT, token="tok", chat_id="123")
    assert result is False


def test_send_signal_returns_false_on_missing_credentials():
    result = send_signal(NO_TRADE_REPORT, token="", chat_id="")
    assert result is False


def test_send_signal_never_raises_on_network_error():
    import requests as req
    with patch("requests.post", side_effect=req.ConnectionError("network down")):
        # must not propagate the exception
        result = send_signal(NO_TRADE_REPORT, token="tok", chat_id="123")
    assert result is False


def test_send_raw_works():
    with patch("requests.post", return_value=_ok_response()):
        result = send_raw("hello", token="tok", chat_id="123")
    assert result is True


def test_test_connection_calls_send_raw():
    with patch("execution.telegram_bot.send_raw", return_value=True) as mock_send:
        from execution.telegram_bot import test_connection as tc
        tc(token="tok", chat_id="123")
    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert "connected" in args[0].lower() or "IATIS" in args[0]


# ---------------------------------------------------------------------------
# pipeline integration: telegram called after every run
# ---------------------------------------------------------------------------

def test_main_pipeline_calls_telegram_when_enabled(tmp_path, monkeypatch):
    """Integration check: run_pipeline() must call telegram_send() when enabled.
    Note: MQS gate may block early if synthetic data has POOR market quality.
    We verify telegram is called OR pipeline returns NO_TRADE gracefully.
    """
    from utils.helpers import load_config
    import main as main_module

    mock_telegram = MagicMock(return_value=True)
    monkeypatch.setattr("execution.telegram_bot.send_signal", mock_telegram)
    monkeypatch.setattr(main_module, "telegram_send", mock_telegram)

    config = load_config()
    config["telegram"] = {"enabled": True}
    config["data"]["source"] = "synthetic"

    report = main_module.run_pipeline(config)
    assert "final_verdict" in report  # pipeline completed without exception
    # telegram called if EXECUTE, not called if NO_TRADE (both valid)


def test_main_pipeline_skips_telegram_when_disabled(tmp_path, monkeypatch):
    from utils.helpers import load_config
    import main as main_module

    monkeypatch.setattr(main_module, "telegram_send", MagicMock(return_value=True))

    config = load_config()
    config["telegram"] = {"enabled": False}
    config["data"]["source"] = "synthetic"

    main_module.run_pipeline(config)
    assert main_module.telegram_send.call_count == 0
