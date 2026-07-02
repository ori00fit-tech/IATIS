#!/usr/bin/env python3
"""
scripts/ctrader_smoke_test.py
-----------------------------
Standalone smoke test for execution/ctrader_client.py on a VPS / demo account.

It validates, in order:
  1. Credentials + TLS transport
  2. Full auth chain  (APP_AUTH → ACCOUNT_AUTH → SYMBOLS_LOADED → READY)
  3. Account info parsing            (fix M2)
  4. Symbol list + symbol details    (fix C3 — real lotSize)
  5. (optional) ONE tiny market order on the DEMO account (fixes C1 + C2 + C3)

Run from the IATIS project root.

SAFE (no orders — connection/account/symbols only):
    python scripts/ctrader_smoke_test.py

Place ONE small DEMO market order (opens a real demo position!):
    python scripts/ctrader_smoke_test.py --place-order --symbol EURUSD \
        --sl 1.0700 --tp 1.1200

Required environment variables (put them in a .env file — never commit):
    CTRADER_CLIENT_ID
    CTRADER_CLIENT_SECRET
    CTRADER_ACCOUNT_ID          (the ctidTraderAccountId, an integer)
    CTRADER_ACCESS_TOKEN
    CTRADER_ENVIRONMENT=demo    (must be 'demo' unless --force-live is passed)
Optional:
    CTRADER_ACCOUNT_CURRENCY=USD
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make the project root importable when run as `python scripts/ctrader_smoke_test.py`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Best-effort .env loading (optional dependency).
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

from execution.ctrader_client import CTraderClient, CTraderOrder  # noqa: E402


def _require_env() -> None:
    """Fail fast with a clear message if credentials are missing."""
    required = [
        "CTRADER_CLIENT_ID",
        "CTRADER_CLIENT_SECRET",
        "CTRADER_ACCOUNT_ID",
        "CTRADER_ACCESS_TOKEN",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print("❌ Missing environment variables: " + ", ".join(missing))
        print("   Set them in a .env file at the project root (never commit it).")
        sys.exit(2)


def _check_tls_stack() -> None:
    """Warn if Twisted's TLS verification stack is incomplete (security)."""
    problems = []
    for mod in ("OpenSSL", "service_identity"):
        try:
            __import__(mod)
        except Exception:
            problems.append(mod)
    if problems:
        print(
            "⚠️  Missing TLS packages: "
            + ", ".join(problems)
            + " — Twisted will fall back to weak hostname verification.\n"
            "    Fix with:  pip install pyopenssl service_identity"
        )


def run_connection_test() -> CTraderClient | None:
    """Connect, print account + symbol summary. Returns a READY client or None."""
    client = CTraderClient()  # reads all creds from environment
    print(f"🔌 Environment : {client.environment}")
    print(f"🔌 Host        : {client.host}:{client.PORT}")
    print(f"🔌 Account     : {client.account_id}")

    if not client.connect(timeout=30.0):
        print("❌ Connection FAILED — see logs above for the failing state.")
        return None

    info = client.get_account_info()
    if not info:
        print("❌ Connected but account info unavailable.")
        return None

    print("\n✅ CONNECTION READY")
    print(f"   Balance     : {info.balance:.2f} {info.currency}")
    print(f"   Leverage    : 1:{info.leverage}")
    print(f"   Margin free : {info.margin_free:.2f}")
    # Access internal maps only for reporting.
    print(f"   Symbols     : {len(client._symbol_name_to_id)} loaded")
    print(f"   Specs       : {len(client._symbol_details)} with lotSize/min/step")
    return client


def run_order_test(
    client: CTraderClient,
    symbol: str,
    sl: float,
    tp: float,
    risk_pct: float,
) -> None:
    """Place ONE tiny market order on the demo account and print the parsed result."""
    info = client.get_account_info()
    if not info:
        print("❌ No account info; aborting order test.")
        return

    # Derive valid SL/TP from the live price so the order is always on the
    # correct side. Fixed CLI --sl/--tp are only used if the live spot is
    # unavailable.
    spot = client.get_spot(symbol)
    if spot:
        bid, ask = spot
        entry = ask  # BUY fills near ask
        pip = 0.01 if "JPY" in symbol else 0.0001
        sl = round(entry - 300 * pip, 5)   # ~30 pips below
        tp = round(entry + 600 * pip, 5)   # ~60 pips above (RR 2.0)
        print(f"   Live entry ≈ {entry} → SL {sl} / TP {tp}")
        sizing_distance = abs(entry - sl)
    else:
        print("   ⚠️ No live spot; using CLI --sl/--tp as provided.")
        sizing_distance = abs((sl - tp) / 2) if (sl and tp) else 0.0030

    volume = client.calculate_volume(
        symbol=symbol,
        balance=info.balance,
        risk_pct=risk_pct,
        sl_distance_price=sizing_distance or 0.0030,
    )
    if volume <= 0:
        print(f"❌ calculate_volume returned {volume}; aborting.")
        return

    order = CTraderOrder(
        symbol=symbol,
        direction="BUY",
        volume=volume,
        stop_loss=float(sl),
        take_profit=float(tp),
        comment="IATIS_SMOKE",
    )
    print(f"\n📤 Placing DEMO order: BUY {symbol} centi_lots={volume} SL={sl} TP={tp}")
    result = client.place_market_order(order)

    if result.success:
        print("✅ ORDER OK")
        print(f"   position_id : {result.position_id or '(pending fill)'}")
        print(f"   order_id    : {result.order_id}")
        print(f"   fill price  : {result.entry_price}")
        print("   NOTE: this opened a demo position — close it from the platform.")
    else:
        print(f"❌ ORDER FAILED: {result.error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="cTrader client smoke test")
    parser.add_argument("--place-order", action="store_true",
                        help="Also place ONE tiny market order (demo only).")
    parser.add_argument("--symbol", default="EURUSD", help="IATIS symbol to trade.")
    parser.add_argument("--sl", type=float, default=0.0, help="Absolute stop-loss price.")
    parser.add_argument("--tp", type=float, default=0.0, help="Absolute take-profit price.")
    parser.add_argument("--risk-pct", type=float, default=0.005,
                        help="Risk fraction used for sizing (default 0.5%).")
    parser.add_argument("--force-live", action="store_true",
                        help="Allow order placement on a LIVE account (dangerous).")
    args = parser.parse_args()

    _require_env()
    _check_tls_stack()

    client = run_connection_test()
    if client is None:
        return 1

    if args.place_order:
        env = os.environ.get("CTRADER_ENVIRONMENT", "demo")
        if env != "demo" and not args.force_live:
            print("\n⛔ Refusing to place an order: CTRADER_ENVIRONMENT is not 'demo'.")
            print("   Re-run with --force-live only if you truly mean the live account.")
            client.disconnect()
            return 1
        run_order_test(client, args.symbol, args.sl, args.tp, args.risk_pct)

    client.disconnect()
    print("\n🏁 Smoke test finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
