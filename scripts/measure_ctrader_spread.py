#!/usr/bin/env python3
"""
scripts/measure_ctrader_spread.py
------------------------------------
Measure REAL broker spread from cTrader (IC Markets demo) and check it
against the cost the backtests assume.

Why this matters: every PF number the project quotes
(h4_yearly_stability, engine_activation, d1/h4 backtests) assumes a
FIXED round-trip cost of commission_pips=0.5 + slippage_pips=0.5 = 1.0
pip. If IC Markets' real spread on a symbol is wider than that, the
backtest overstates its edge; if narrower, it understates it. This
prints the live spread per enabled symbol next to the assumed cost, so
the PF claims can be re-stated honestly (audit Phase 5 discipline).

READ-ONLY: connects, reads spot bid/ask, disconnects. Places no orders.
Runs on the VPS/demo (cTrader Open API is network-blocked from the audit
sandbox). Samples a few times to smooth momentary quotes.

    python3 scripts/measure_ctrader_spread.py
    python3 scripts/measure_ctrader_spread.py --samples 5 --interval 3
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from utils.helpers import load_config

# The backtest's assumed round-trip cost (BacktestConfig defaults).
ASSUMED_COMMISSION_PIPS = 0.5
ASSUMED_SLIPPAGE_PIPS = 0.5
ASSUMED_ROUNDTRIP_PIPS = ASSUMED_COMMISSION_PIPS + ASSUMED_SLIPPAGE_PIPS


def _pip_size(symbol: str) -> float:
    """Pip size per symbol — mirrors the backtest/ctrader convention."""
    if "JPY" in symbol:
        return 0.01
    if symbol in ("XAUUSD", "XAGUSD", "USOIL"):
        return 0.01
    if symbol in ("US30", "NAS100", "SPX500", "BTCUSD", "ETHUSD"):
        return 1.0  # index/crypto "pip" ≈ 1 point; spread reported in points
    return 0.0001


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args()

    cfg = load_config()
    enabled = [s["internal"] for s in cfg["data"]["twelve_data_symbols"] if s.get("enabled")]

    from execution.ctrader_client import CTraderClient

    client = CTraderClient()
    print("Connecting to cTrader (demo)…")
    if not client.connect(timeout=30.0):
        print("❌ Could not reach READY state. Likely an expired CTRADER_ACCESS_TOKEN —")
        print("   refresh it via CTRADER_REFRESH_TOKEN, or re-run the OAuth flow. Aborting.")
        sys.exit(1)
    print("✅ Connected.\n")

    acct = client.get_account_info()
    if acct:
        print(f"Account: balance={getattr(acct, 'balance', '?')} "
              f"{getattr(acct, 'currency', '')}  env=demo\n")

    print(f"{'symbol':8s} {'spread(pips)':>12s} {'assumed':>8s} {'verdict':>10s}   bid/ask")
    print("-" * 68)
    rows = []
    for sym in enabled:
        samples = []
        last_quote = None
        for _ in range(args.samples):
            q = client.get_spot(sym)
            if q:
                bid, ask = q
                last_quote = q
                samples.append((ask - bid) / _pip_size(sym))
            time.sleep(args.interval)
        if not samples:
            print(f"{sym:8s} {'—':>12s}  (no quote — market closed or symbol unmapped)")
            continue
        spread = round(statistics.median(samples), 2)
        verdict = "OK" if spread <= ASSUMED_ROUNDTRIP_PIPS else "UNDER-COST"
        rows.append({"symbol": sym, "spread_pips": spread,
                     "assumed_roundtrip_pips": ASSUMED_ROUNDTRIP_PIPS,
                     "realistic": verdict == "OK"})
        b, a = last_quote
        print(f"{sym:8s} {spread:>12.2f} {ASSUMED_ROUNDTRIP_PIPS:>8.1f} {verdict:>10s}   {b}/{a}")

    client.disconnect()

    under = [r for r in rows if not r["realistic"]]
    print(f"\n{len(rows)} symbols measured; {len(under)} have real spread ABOVE the "
          f"assumed {ASSUMED_ROUNDTRIP_PIPS}-pip round-trip cost.")
    if under:
        print("  → Backtest PF for these is OPTIMISTIC. Consider raising "
              "BacktestConfig commission/slippage to the measured spread and re-running.")
        print("  ", ", ".join(f"{r['symbol']}({r['spread_pips']})" for r in under))
    else:
        print("  → The 1.0-pip assumption is conservative for all measured symbols. PF stands.")

    # Evidence trail
    try:
        from research.manifest import build_manifest, write_manifest
        m = build_manifest(
            kind="ctrader_spread_measurement", config=cfg,
            params={"assumed_roundtrip_pips": ASSUMED_ROUNDTRIP_PIPS,
                    "samples": args.samples, "source": "IC Markets demo via cTrader Open API",
                    "note": "Live spread vs backtest cost assumption. Spread varies by session; "
                            "measured at run time."},
            datasets=[], results={"per_symbol": rows})
        out = write_manifest(m, f"ctrader_spread_{time.strftime('%Y%m%d')}")
        print(f"\nManifest: {out}")
    except Exception as exc:
        print(f"manifest skipped: {exc}")


if __name__ == "__main__":
    main()
