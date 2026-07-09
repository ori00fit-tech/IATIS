"""
scripts/download_cot.py
------------------------
Weekly CFTC Commitments-of-Traders downloader for the Sentiment engine.

Fetches the CFTC legacy Futures-Only current-week file
(https://www.cftc.gov/dea/newcot/deafut.txt — free, no key), extracts
Large-Speculator (non-commercial) net positioning for every contract in
engines.sentiment_engine.COT_SYMBOLS, and writes the per-symbol JSON
caches the engine already consumes (data/cot/{SYMBOL}.json — override the
directory with IATIS_COT_DIR).

Each cache keeps a small weekly history so `net_change_4w` becomes real
after ~4 weekly runs (it is 0 until then — the engine treats that as
"mixed positioning", which is the honest cold-start reading).

Run weekly (COT is published Fridays ~15:30 ET; Saturday is a safe slot):

    venv/bin/python -m scripts.download_cot            # fetch + write
    venv/bin/python -m scripts.download_cot --dry-run  # parse, don't write

This closes the Sentiment engine's placeholder gap (production audit
Phase 4; philosophy audit engine table). The engine itself stays DISABLED
(H012 RESEARCH) — real data is a prerequisite for evaluating it, not a
license to enable it.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from engines.sentiment_engine import COT_SYMBOLS
from utils.logger import get_logger

logger = get_logger(__name__)

COT_URL = "https://www.cftc.gov/dea/newcot/deafut.txt"
HISTORY_WEEKS = 12  # keep ~3 months of weekly nets per symbol

# Legacy Futures-Only short-format column layout (no header row in the file;
# layout documented by CFTC and identical to the yearly deacot archives):
#   0  Market_and_Exchange_Names
#   1  As_of_Date_In_Form_YYMMDD
#   2  As_of_Date_In_Form_YYYY-MM-DD
#   3  CFTC_Contract_Market_Code
#   4  CFTC_Market_Code
#   5  CFTC_Region_Code
#   6  CFTC_Commodity_Code
#   7  Open_Interest_All
#   8  NonComm_Positions_Long_All
#   9  NonComm_Positions_Short_All
_IDX_NAME, _IDX_DATE = 0, 2
_IDX_OI, _IDX_NC_LONG, _IDX_NC_SHORT = 7, 8, 9

# Contract-size variants that must NOT match the standard contract.
_EXCLUDED_PREFIXES = ("MICRO", "E-MINI", "MINI", "NANO", "E-MICRO")


def _to_int(s: str) -> int:
    return int(str(s).replace(",", "").strip() or 0)


def parse_cot_text(text: str) -> dict[str, dict]:
    """Parse deafut.txt into {internal_symbol: {name, date, net, oi}}.

    Matching: the CFTC market name must START WITH the mapped contract
    name (e.g. 'EURO FX - CHICAGO MERCANTILE EXCHANGE') and must not be a
    micro/mini variant ('MICRO BITCOIN - ...' is a different contract).
    """
    out: dict[str, dict] = {}
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) <= _IDX_NC_SHORT:
            continue
        market = row[_IDX_NAME].strip().upper()
        if market.startswith(_EXCLUDED_PREFIXES):
            continue
        for internal, contract in COT_SYMBOLS.items():
            if not market.startswith(contract.upper()):
                continue
            try:
                oi = _to_int(row[_IDX_OI])
                nc_long = _to_int(row[_IDX_NC_LONG])
                nc_short = _to_int(row[_IDX_NC_SHORT])
            except ValueError:
                logger.warning(f"Unparseable COT row for {market!r} — skipped")
                continue
            # Layout sanity: positions cannot exceed open interest.
            if oi > 0 and (nc_long > oi or nc_short > oi):
                logger.warning(
                    f"COT sanity check failed for {market!r} "
                    f"(long={nc_long} short={nc_short} oi={oi}) — layout drift? skipped"
                )
                continue
            out[internal] = {
                "market": market,
                "report_date": row[_IDX_DATE].strip(),
                "large_spec_long": nc_long,
                "large_spec_short": nc_short,
                "large_spec_net": nc_long - nc_short,
                "open_interest": oi,
            }
    return out


def _cot_dir() -> Path:
    return Path(os.environ.get("IATIS_COT_DIR", "data/cot"))


def update_caches(parsed: dict[str, dict], now: float | None = None) -> list[str]:
    """Merge this week's nets into the per-symbol caches; return symbols written."""
    now = now or time.time()
    out_dir = _cot_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for symbol, rec in parsed.items():
        path = out_dir / f"{symbol}.json"
        history: list[dict] = []
        if path.exists():
            try:
                history = json.loads(path.read_text()).get("history", [])
            except Exception:
                history = []
        # Idempotent per report date.
        history = [h for h in history if h.get("report_date") != rec["report_date"]]
        history.append({"report_date": rec["report_date"],
                        "net": rec["large_spec_net"], "ts": now})
        history = sorted(history, key=lambda h: h["report_date"])[-HISTORY_WEEKS:]

        # net_change_4w: vs the nearest entry >= 21 days older (falls back
        # to the oldest available; 0 when this is the first week).
        cutoff = now - 21 * 86400
        older = [h for h in history if h["ts"] <= cutoff]
        baseline = (older[-1] if older else history[0])
        net_change = rec["large_spec_net"] - baseline["net"] if len(history) > 1 else 0

        payload = {
            "symbol": symbol,
            "market": rec["market"],
            "report_date": rec["report_date"],
            "large_spec_net": rec["large_spec_net"],
            "large_spec_long": rec["large_spec_long"],
            "large_spec_short": rec["large_spec_short"],
            "open_interest": rec["open_interest"],
            "net_change_4w": net_change,
            "timestamp": now,
            "history": history,
        }
        path.write_text(json.dumps(payload, indent=1))
        written.append(symbol)
        logger.info(
            f"COT {symbol}: net={rec['large_spec_net']:+,} "
            f"Δ4w={net_change:+,} ({rec['market'][:40]})"
        )
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="parse but don't write")
    ap.add_argument("--url", default=COT_URL)
    args = ap.parse_args()

    logger.info(f"Fetching {args.url}")
    try:
        with urllib.request.urlopen(args.url, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.error(f"COT fetch failed: {exc}")
        return 2

    parsed = parse_cot_text(text)
    if not parsed:
        logger.error("COT parse produced 0 contracts — layout drift or empty file")
        return 1
    missing = sorted(set(COT_SYMBOLS) - set(parsed))
    if missing:
        logger.warning(f"No COT row matched: {missing}")

    if args.dry_run:
        for sym, rec in sorted(parsed.items()):
            print(f"{sym:8s} net={rec['large_spec_net']:+10,} "
                  f"long={rec['large_spec_long']:,} short={rec['large_spec_short']:,} "
                  f"({rec['report_date']}) {rec['market'][:45]}")
        return 0

    written = update_caches(parsed)
    print(f"COT caches written for {len(written)} symbols → {_cot_dir()}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
