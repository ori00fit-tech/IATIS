"""
tests/conftest.py
-----------------
Hermetic test isolation for the whole suite.

Root cause addressed (VPS incident, 2026-07-02):
    Running ``pytest`` on the production VPS failed 3 credential-guard
    tests and took 22 minutes (vs ~5s clean). The suite was reading REAL
    credentials from the process environment (.env exported on the VPS):

    - ``send_signal(report, token="", chat_id="")`` fell back to the real
      TELEGRAM_BOT_TOKEN via ``token or env_token`` and actually delivered
      a Telegram message during the test run (returned True).
    - cTrader "raises without credentials" tests found real credentials.
    - Data-provider tests found real API keys and made live HTTP calls,
      burning Twelve Data / Alpha Vantage daily credits on every test run.

Two guarantees are enforced here, for every test, on every machine:

1. **No production credentials are visible to tests.** All known secret
   env vars are removed before each test. Tests that need credentials
   must set them explicitly (monkeypatch.setenv), which documents intent.

2. **No real network I/O.** Outbound socket connections are refused with
   a loud, actionable error. Correctly mocked tests never open sockets,
   so this only trips when a test accidentally reaches a live service —
   turning a silent 22-minute, credit-burning, message-sending run into
   an immediate, explainable failure.
"""

from __future__ import annotations

import socket

import pytest

# Every secret/credential the codebase reads from the environment.
# Extend this list whenever a new integration is added.
_CREDENTIAL_ENV_VARS = [
    "TWELVE_DATA_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "FINNHUB_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "CTRADER_CLIENT_ID",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_ACCOUNT_ID",
    "CTRADER_ACCESS_TOKEN",
    "OANDA_API_TOKEN",
    "OANDA_ACCOUNT_ID",
    "IATIS_API_KEY",
]


@pytest.fixture(autouse=True)
def _isolate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all production credentials from the environment per test.

    Guarantees credential-guard tests (e.g. "raises without credentials")
    behave identically on developer laptops, CI, and the production VPS.
    """
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class NetworkAccessBlockedError(RuntimeError):
    """A test attempted a real outbound network connection."""


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse real outbound socket connections during tests.

    In-process clients (FastAPI TestClient), SQLite, and properly mocked
    HTTP never open outbound sockets, so legitimate tests are unaffected.
    """
    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        # Allow loopback for any test that spins up a genuinely local server.
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address, *args, **kwargs)
        raise NetworkAccessBlockedError(
            f"Test attempted a real network connection to {address!r}. "
            f"Mock the HTTP layer (e.g. monkeypatch requests.post) instead. "
            f"Real calls from tests send live Telegram messages and burn "
            f"API credits — see tests/conftest.py."
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
