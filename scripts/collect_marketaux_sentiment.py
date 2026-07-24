#!/usr/bin/env python3
"""
scripts/collect_marketaux_sentiment.py
------------------------------------------
Periodic MarketAux sentiment collector — the missing piece for H021
(research/results/registry.json — "Does MarketAux news sentiment... improve
confluence performance enough to justify re-enabling it?").

fundamentals/marketaux_client.py already exists and works, but it only ever
returns a RECENT snapshot (the free tier serves current news, not a
reconstructed historical time series — H021's own registered notes say so
explicitly). There is no way to backfill years of sentiment history the way
scripts/download_deep_history.py does for prices. The only way to get a
backtestable sample is to run this repeatedly over time and accumulate
snapshots — this script IS that accumulation step, not a one-shot backfill.

Design:
  - Appends one JSON line per symbol per run to
    data/marketaux_sentiment_log.jsonl (gitignored like every other
    data/*.csv/*.json historical dataset — see data/README.md's convention).
    Append-only, human-inspectable, trivially resumable.
  - Rate budget: MarketAux free tier is 100 req/day (verified against the
    real endpoint 2026-07-14, per marketaux_client.py's own docstring).
    Covers all 14 currently-mapped symbols (12 FX + BTCUSD + ETHUSD) in one
    run = 14 requests. Run via iatis-marketaux-collect.timer at a cadence
    that stays well under budget (default suggestion: every 4h = 6 runs/day
    x 14 = 84 req/day, leaving headroom).
  - XAUUSD is NOT in MARKETAUX_SYMBOL_MAP yet (deliberately — the client's
    own comment says metals use different entity naming on MarketAux's
    side, unconfirmed) even though it's one of H021's THREE priority
    "carrier" symbols per the registered decision rule. This script does
    NOT guess a mapping. Use --probe-xauusd to test candidate symbol
    strings against the live API and report which one(s) return real
    entity matches — a human decides whether to add the mapping from that
    evidence, this script never does it silently.

Usage (VPS — needs MARKETAUX_API_KEY in .env):
    python3 -m scripts.collect_marketaux_sentiment              # one collection run, all mapped symbols
    python3 -m scripts.collect_marketaux_sentiment --symbols BTCUSD ETHUSD
    python3 -m scripts.collect_marketaux_sentiment --probe-xauusd
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    # Explicit path — see scripts/close_orphaned_trades.py and
    # scripts/download_ctrader_fx_history.py for why bare load_dotenv()
    # is unreliable depending on how this script is invoked.
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
except PermissionError:
    _env_path = PROJECT_ROOT / ".env"
    raise SystemExit(
        f"Permission denied reading {_env_path} as the current user. "
        f".env is owned by the iatis service user (600) — run this as "
        f"that user instead:\n"
        f"  sudo -u iatis {sys.executable} -m scripts.collect_marketaux_sentiment ..."
    )

LOG_PATH = PROJECT_ROOT / "data" / "marketaux_sentiment_log.jsonl"
DAILY_REQUEST_BUDGET = 100  # MarketAux free tier — see module docstring

# Candidates for the unmapped gold entity — none confirmed. --probe-xauusd
# tests each against the live API; a human adds the confirmed one to
# fundamentals/marketaux_client.py's MARKETAUX_SYMBOL_MAP, this script
# never does it automatically.
_XAUUSD_CANDIDATES = ["XAUUSD", "XAU/USD", "GOLD", "XAU"]


def collect_once(symbols: list[str]) -> list[dict]:
    """One collection pass: get_news_sentiment() for each symbol, return
    the records that were actually appended (skips symbols where the
    client returned None — no API key, no mapping, or a failed request;
    those are logged, not silently dropped, but not written to the log
    as fabricated zero-signal rows)."""
    from fundamentals.marketaux_client import get_news_sentiment

    collected_at = datetime.now(timezone.utc).isoformat()
    records: list[dict] = []
    for symbol in symbols:
        result = get_news_sentiment(symbol)
        if result is None:
            print(f"  {symbol}: no signal (unmapped, no key, or request failed) — skipped")
            continue
        record = {"collected_at": collected_at, **result}
        records.append(record)
        print(f"  {symbol}: {result['article_count']} articles, "
              f"mean_sentiment={result['mean_sentiment']}")
    return records


def append_records(records: list[dict], path: Path = LOG_PATH) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


def probe_xauusd_mapping() -> None:
    """Test candidate gold symbol strings against the live MarketAux API.
    Prints which candidate(s) return real entity matches. Does NOT modify
    MARKETAUX_SYMBOL_MAP — that's a human decision from this evidence."""
    import os

    import requests

    from fundamentals.marketaux_client import BASE_URL

    api_key = os.environ.get("MARKETAUX_API_KEY", "")
    if not api_key:
        raise SystemExit("MARKETAUX_API_KEY not set — cannot probe.")

    print("Probing candidate gold entity symbols against the live MarketAux API...")
    for candidate in _XAUUSD_CANDIDATES:
        params = {
            "symbols": candidate, "filter_entities": "true",
            "language": "en", "limit": 5, "api_token": api_key,
        }
        try:
            resp = requests.get(BASE_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  {candidate!r}: request failed ({exc})")
            continue
        if "error" in data:
            print(f"  {candidate!r}: API error — {data['error'].get('message', data['error'])}")
            continue
        n_articles = len(data.get("data", []))
        matched_entities = sum(
            1 for a in data.get("data", []) for e in a.get("entities", [])
            if e.get("symbol") == candidate
        )
        print(f"  {candidate!r}: {n_articles} articles returned, "
              f"{matched_entities} entity matches for this exact symbol string")
        time.sleep(1)  # stay polite — this burns real rate-limit budget

    print("\nPick the candidate with real entity matches (not just articles —\n"
          "articles can return without a matching entity) and add it to\n"
          "fundamentals/marketaux_client.py's MARKETAUX_SYMBOL_MAP by hand.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="override the default symbol list (all MARKETAUX_SYMBOL_MAP keys)")
    parser.add_argument("--probe-xauusd", action="store_true",
                        help="test candidate gold symbols against the live API, then exit")
    args = parser.parse_args()

    if args.probe_xauusd:
        probe_xauusd_mapping()
        return 0

    from fundamentals.marketaux_client import MARKETAUX_SYMBOL_MAP

    symbols = args.symbols or list(MARKETAUX_SYMBOL_MAP.keys())
    print(f"Collecting MarketAux sentiment for {len(symbols)} symbol(s) "
          f"({len(symbols)} of the {DAILY_REQUEST_BUDGET} req/day budget used this run)...")
    records = collect_once(symbols)
    append_records(records)
    print(f"\nAppended {len(records)} record(s) to {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
