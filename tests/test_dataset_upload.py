"""
tests/test_dataset_upload.py — POST /research/datasets/upload (Phase 4a,
2026-07-25 institutional redesign): CSV/Parquet dataset import.

Mirrors tests/test_api_contract.py's client/HDR fixture conventions and its
existing monkeypatch.setattr(m, "_DATA_DIR", tmp_path) seam (research.py's
own module-level comment says _DATA_DIR is module-level exactly so tests can
monkeypatch it instead of chdir()ing the whole process).
"""
from __future__ import annotations

import io
import os

import pandas as pd
import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"
    from execution.api_server import app
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="fastapi not installed")

HDR = {"X-API-Key": "test-key-123"}
URL = "/research/datasets/upload"


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _valid_ohlcv_df(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    open_ = [1.1000 + 0.0001 * i for i in range(n)]
    close = [o + 0.0004 for o in open_]
    high = [max(o, c) + 0.0002 for o, c in zip(open_, close)]
    low = [min(o, c) - 0.0002 for o, c in zip(open_, close)]
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1000}, index=idx)


def _valid_ohlcv_csv_bytes(n: int = 60) -> bytes:
    return _valid_ohlcv_df(n).to_csv(index_label="timestamp").encode()


def _valid_ohlcv_parquet_bytes(n: int = 60) -> bytes:
    buf = io.BytesIO()
    _valid_ohlcv_df(n).to_parquet(buf)
    return buf.getvalue()


def test_upload_requires_auth(client):
    r = client.post(URL)
    assert r.status_code == 401


def test_upload_oversized_body_413(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(m, "_MAX_UPLOAD_BYTES", 200)

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EURUSD", "timeframe": "H1"},
        files={"file": ("data.csv", _valid_ohlcv_csv_bytes(200), "text/csv")},
    )
    assert r.status_code == 413, r.text


def test_upload_invalid_symbol_400(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EUR/USD!", "timeframe": "H1"},
        files={"file": ("data.csv", _valid_ohlcv_csv_bytes(), "text/csv")},
    )
    assert r.status_code == 400, r.text
    assert "symbol" in r.json()["detail"].lower()


def test_upload_invalid_timeframe_400(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EURUSD", "timeframe": "H2"},
        files={"file": ("data.csv", _valid_ohlcv_csv_bytes(), "text/csv")},
    )
    assert r.status_code == 400, r.text
    assert "timeframe" in r.json()["detail"].lower()


def test_upload_malformed_csv_400(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EURUSD", "timeframe": "H1"},
        files={"file": ("data.csv", b"\x00\x01\x02NOTCSV\xff\xfe\x00garbage", "text/csv")},
    )
    assert r.status_code == 400, r.text


def test_upload_fails_ohlcv_validation_400(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    df = _valid_ohlcv_df(60)
    df.loc[df.index[5], "high"] = df.loc[df.index[5], "low"] - 0.01  # high < low
    bad_csv = df.to_csv(index_label="timestamp").encode()

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EURUSD", "timeframe": "H1"},
        files={"file": ("data.csv", bad_csv, "text/csv")},
    )
    assert r.status_code == 400, r.text


def test_upload_below_min_rows_400(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EURUSD", "timeframe": "H1"},
        files={"file": ("data.csv", _valid_ohlcv_csv_bytes(10), "text/csv")},
    )
    assert r.status_code == 400, r.text
    assert "row" in r.json()["detail"].lower()


def test_upload_valid_csv_success_and_appears_in_listing(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EURUSD", "timeframe": "H1"},
        files={"file": ("data.csv", _valid_ohlcv_csv_bytes(60), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "EURUSD"
    assert body["timeframe"] == "H1"
    assert body["file"] == "EURUSD_H1_uploaded.csv"
    assert body["rows"] == 60

    listing = client.get("/research/datasets", headers=HDR).json()
    assert any(d["file"] == "EURUSD_H1_uploaded.csv" and d["readable"] for d in listing["datasets"])


def test_upload_valid_parquet_success_and_appears_in_listing(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "BTCUSD", "timeframe": "H4"},
        files={"file": ("data.parquet", _valid_ohlcv_parquet_bytes(60), "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file"] == "BTCUSD_H4_uploaded.parquet"

    listing = client.get("/research/datasets", headers=HDR).json()
    assert any(d["file"] == "BTCUSD_H4_uploaded.parquet" and d["readable"] for d in listing["datasets"])


def test_upload_overwrite_without_flag_409(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    (tmp_path / "EURUSD_H1_uploaded.csv").write_bytes(_valid_ohlcv_csv_bytes(60))

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EURUSD", "timeframe": "H1"},
        files={"file": ("data.csv", _valid_ohlcv_csv_bytes(60), "text/csv")},
    )
    assert r.status_code == 409, r.text


def test_upload_overwrite_with_flag_success(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    (tmp_path / "EURUSD_H1_uploaded.csv").write_bytes(_valid_ohlcv_csv_bytes(60))

    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "EURUSD", "timeframe": "H1", "overwrite": "true"},
        files={"file": ("data.csv", _valid_ohlcv_csv_bytes(80), "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 80


def test_upload_content_sniff_beats_declared_extension(client, tmp_path, monkeypatch):
    import execution.routes.research as m
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path)

    # Real Parquet bytes, but declared as a .csv file with a text/csv
    # Content-Type — both are attacker/client-controlled and must be
    # ignored in favor of sniffing the actual PAR1 magic bytes.
    r = client.post(
        URL,
        headers=HDR,
        data={"symbol": "ETHUSD", "timeframe": "D1"},
        files={"file": ("misleading.csv", _valid_ohlcv_parquet_bytes(60), "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["file"] == "ETHUSD_D1_uploaded.parquet"
