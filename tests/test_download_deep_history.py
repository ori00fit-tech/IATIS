"""tests/test_download_deep_history.py

Regression coverage for the ccxt/Binance crypto routing added to
scripts/download_deep_history.py — BTCUSD/ETHUSD must go through ccxt
(real, unrated exchange history) instead of Twelve Data (free-plan gated).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from download_deep_history import CCXT_DEEP, fetch_ccxt_deep


def _fake_df() -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=3, freq="4h", tz="UTC")
    return pd.DataFrame(
        {"open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1],
         "low": [0.9, 1.9, 2.9], "close": [1.0, 2.0, 3.0],
         "volume": [10, 20, 30]},
        index=idx,
    )


def test_crypto_symbols_are_ccxt_routed():
    assert CCXT_DEEP == {"BTCUSD", "ETHUSD"}


def test_fetch_ccxt_deep_maps_interval_and_requests_deep_window():
    with patch("core.ccxt_provider.fetch_ccxt", return_value=_fake_df()) as mock_fetch:
        df = fetch_ccxt_deep("BTCUSD", "1day")

    mock_fetch.assert_called_once_with("BTCUSD", timeframe="1d", days=3650)
    assert len(df) == 3


def test_fetch_ccxt_deep_raises_on_empty_result():
    with patch("core.ccxt_provider.fetch_ccxt", return_value=None):
        with pytest.raises(RuntimeError, match="ccxt returned no data"):
            fetch_ccxt_deep("ETHUSD", "4h")
