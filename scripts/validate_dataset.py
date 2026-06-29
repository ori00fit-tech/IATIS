#!/usr/bin/env python3
"""
scripts/validate_dataset.py
-----------------------------
IATIS Dataset Validation Tool

Validates all CSV files before backtesting.
Checks: OHLC integrity, duplicates, gaps, NaN, timezone, staleness.

Usage:
    python3 scripts/validate_dataset.py
    python3 scripts/validate_dataset.py --sym XAUUSD BTCUSD
    python3 scripts/validate_dataset.py --tf 15m 1h
    python3 scripts/validate_dataset.py --report  # save HTML report
"""
from __future__ import annotations
import argparse, sys, json
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

DATA_DIR = Path("data")

# Expected gap between candles per timeframe (minutes)
TF_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
    "M15": 15, "H1": 60, "H4": 240, "D1": 1440,
}

# Trading sessions (UTC) — gaps expected outside these hours for forex
FOREX_SESSIONS = {
    "open":  0,   # Monday 00:00 UTC
    "close": 22,  # Friday 22:00 UTC
}

SYMBOLS = ["EURUSD","GBPUSD","AUDUSD","USDCAD","NZDUSD",
           "XAUUSD","XAGUSD","BTCUSD","ETHUSD",
           "USDJPY","EURJPY","GBPJPY","NAS100","SPX500","US30"]

CRYPTO = {"BTCUSD", "ETHUSD"}


def detect_tf(filename: str) -> str | None:
    """Extract timeframe from filename like EURUSD_15m_2y.csv"""
    parts = filename.replace(".csv","").split("_")
    for part in parts:
        if part in TF_MINUTES:
            return part
    return None


def detect_symbol(filename: str) -> str | None:
    parts = filename.replace(".csv","").split("_")
    return parts[0] if parts else None


def validate_file(path: Path) -> dict:
    """Validate one CSV file. Returns validation result dict."""
    sym = detect_symbol(path.name)
    tf  = detect_tf(path.name)
    tf_min = TF_MINUTES.get(tf, 0) if tf else 0
    is_crypto = sym in CRYPTO if sym else False

    result = {
        "file": path.name,
        "symbol": sym,
        "timeframe": tf,
        "status": "PASS",
        "errors": [],
        "warnings": [],
        "stats": {},
    }

    # ── Load ──────────────────────────────────────────────────────────────
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception as e:
        result["status"] = "FAIL"
        result["errors"].append(f"Cannot read CSV: {e}")
        return result

    if len(df) == 0:
        result["status"] = "FAIL"
        result["errors"].append("Empty file")
        return result

    # ── Timezone ──────────────────────────────────────────────────────────
    try:
        df.index = pd.to_datetime(df.index, utc=True)
        tz_ok = True
    except Exception:
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            result["warnings"].append("No timezone info — assuming UTC")
        tz_ok = False

    df = df.sort_index()

    # ── Basic stats ───────────────────────────────────────────────────────
    n_rows = len(df)
    first_date = str(df.index[0])[:19]
    last_date  = str(df.index[-1])[:19]
    date_range = (df.index[-1] - df.index[0]).days
    staleness  = (datetime.now(timezone.utc) - df.index[-1].replace(tzinfo=timezone.utc)).days if tz_ok else 0

    result["stats"] = {
        "rows": n_rows,
        "first_date": first_date,
        "last_date": last_date,
        "date_range_days": date_range,
        "staleness_days": staleness,
    }

    # ── Check 1: Staleness ────────────────────────────────────────────────
    if staleness > 7 and tf not in ("1w", "1d"):
        result["warnings"].append(f"Data is {staleness} days old")
    elif staleness > 30:
        result["errors"].append(f"Data is {staleness} days old — too stale")
        result["status"] = "WARN"

    # ── Check 2: NaN values ───────────────────────────────────────────────
    nan_count = df.isnull().sum().sum()
    result["stats"]["nan_count"] = int(nan_count)
    if nan_count > 0:
        nan_pct = nan_count / (len(df) * len(df.columns)) * 100
        if nan_pct > 1:
            result["errors"].append(f"NaN values: {nan_count} ({nan_pct:.2f}%)")
            result["status"] = "FAIL"
        else:
            result["warnings"].append(f"Minor NaN: {nan_count} values")

    # Drop NaN for further checks
    df = df.dropna(subset=["open","high","low","close"] if "open" in df.columns else df.columns[:4])

    # ── Check 3: Column names ─────────────────────────────────────────────
    df.columns = [c.lower() for c in df.columns]
    required = {"open","high","low","close"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        result["status"] = "FAIL"
        result["errors"].append(f"Missing columns: {missing_cols}")
        return result

    # ── Check 4: OHLC integrity ───────────────────────────────────────────
    ohlc_errors = (
        (df["high"] < df["open"]).sum() +
        (df["high"] < df["close"]).sum() +
        (df["low"] > df["open"]).sum() +
        (df["low"] > df["close"]).sum() +
        (df["high"] < df["low"]).sum()
    )
    result["stats"]["ohlc_errors"] = int(ohlc_errors)
    if ohlc_errors > 0:
        pct = ohlc_errors / len(df) * 100
        if pct > 0.5:
            result["errors"].append(f"OHLC violations: {ohlc_errors} ({pct:.2f}%)")
            result["status"] = "FAIL"
        else:
            result["warnings"].append(f"Minor OHLC issues: {ohlc_errors}")

    # ── Check 5: Duplicates ───────────────────────────────────────────────
    dup_count = df.index.duplicated().sum()
    result["stats"]["duplicates"] = int(dup_count)
    if dup_count > 0:
        pct = dup_count / len(df) * 100
        if pct > 0.1:
            result["errors"].append(f"Duplicate timestamps: {dup_count} ({pct:.2f}%)")
            result["status"] = "FAIL"
        else:
            result["warnings"].append(f"Minor duplicates: {dup_count}")

    df = df[~df.index.duplicated(keep="last")]

    # ── Check 6: Chronological order ─────────────────────────────────────
    if not df.index.is_monotonic_increasing:
        result["errors"].append("Index not sorted chronologically")
        result["status"] = "FAIL"
        df = df.sort_index()

    # ── Check 7: Gaps analysis ────────────────────────────────────────────
    if tf_min > 0 and len(df) > 1:
        time_diffs = df.index.to_series().diff().dt.total_seconds().dropna() / 60
        expected = tf_min

        # Count gaps (>2× expected interval, excluding weekends for forex)
        gaps = time_diffs[time_diffs > expected * 2]
        weekend_gaps = gaps[gaps >= 60 * 48]  # gaps >= 48h = likely weekends
        real_gaps = len(gaps) - len(weekend_gaps)

        result["stats"]["gaps"] = real_gaps
        result["stats"]["weekend_gaps"] = len(weekend_gaps)

        # Expected coverage
        if tf_min <= 60:  # short TF
            expected_candles = date_range * (1440 // tf_min)
            if not is_crypto:
                expected_candles = int(expected_candles * 5/7 * 0.85)  # forex ~85% of weekdays
            coverage_pct = min(100, n_rows / max(expected_candles, 1) * 100)
        else:
            coverage_pct = min(100, date_range / 730 * 100)

        result["stats"]["coverage_pct"] = round(coverage_pct, 1)

        if real_gaps > len(df) * 0.02:  # >2% gaps
            result["warnings"].append(f"Data gaps: {real_gaps} unexpected gaps")
        if coverage_pct < 50:
            result["errors"].append(f"Low coverage: {coverage_pct:.1f}% of expected bars")
            result["status"] = "FAIL"
        elif coverage_pct < 70:
            result["warnings"].append(f"Partial coverage: {coverage_pct:.1f}%")

    # ── Check 8: Price sanity ─────────────────────────────────────────────
    result["stats"]["min_price"] = round(float(df["low"].min()), 6)
    result["stats"]["max_price"] = round(float(df["high"].max()), 6)
    result["stats"]["avg_spread"] = round(float((df["high"] - df["low"]).mean()), 6)

    zero_prices = (df["close"] <= 0).sum()
    if zero_prices > 0:
        result["errors"].append(f"Zero/negative prices: {zero_prices}")
        result["status"] = "FAIL"

    # Extreme price changes (>20% in one candle)
    pct_change = df["close"].pct_change().abs()
    extreme = (pct_change > 0.20).sum()
    if extreme > 5:
        result["warnings"].append(f"Extreme candles (>20%): {extreme}")

    # ── Check 9: Volume ───────────────────────────────────────────────────
    if "volume" in df.columns:
        neg_vol = (df["volume"] < 0).sum()
        if neg_vol > 0:
            result["errors"].append(f"Negative volume: {neg_vol}")
            result["status"] = "FAIL"

    # ── Final score ───────────────────────────────────────────────────────
    if result["errors"] and result["status"] == "PASS":
        result["status"] = "FAIL"
    elif result["warnings"] and not result["errors"] and result["status"] == "PASS":
        result["status"] = "WARN"

    # Valid data %
    result["stats"]["valid_pct"] = round(
        100 - (nan_count + ohlc_errors + dup_count) / max(n_rows, 1) * 100, 2
    )

    return result


def print_result(r: dict, verbose: bool = False) -> None:
    status = r["status"]
    icon = "✅" if status == "PASS" else "⚠️ " if status == "WARN" else "❌"
    s = r["stats"]

    rows   = s.get("rows", 0)
    days   = s.get("date_range_days", 0)
    cov    = s.get("coverage_pct", "?")
    valid  = s.get("valid_pct", "?")
    gaps   = s.get("gaps", 0)
    dups   = s.get("duplicates", 0)
    ohlc   = s.get("ohlc_errors", 0)
    nan_c  = s.get("nan_count", 0)
    stale  = s.get("staleness_days", 0)

    print(f"{icon} {r['symbol']:<8} {r['timeframe']:<5} "
          f"rows={rows:>7,} days={days:>4} "
          f"cov={cov:>5}% valid={valid:>6}% "
          f"gaps={gaps:>4} dups={dups:>3} ohlc={ohlc:>3} nan={nan_c:>4} "
          f"stale={stale}d")

    if verbose or status != "PASS":
        for e in r["errors"]:
            print(f"     ❌ ERROR: {e}")
        for w in r["warnings"]:
            print(f"     ⚠️  WARN:  {w}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sym", nargs="+", default=None)
    parser.add_argument("--tf",  nargs="+", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--report", action="store_true", help="Save JSON report")
    parser.add_argument("--dir", default=str(DATA_DIR))
    args = parser.parse_args()

    data_dir = Path(args.dir)
    files = sorted(data_dir.glob("*.csv"))

    if args.sym:
        files = [f for f in files if any(s in f.name for s in args.sym)]
    if args.tf:
        files = [f for f in files if any(f"_{t}_" in f.name or f"_{t}." in f.name or f"_M15" in f.name for t in args.tf)]

    if not files:
        print(f"No CSV files found in {data_dir}")
        return

    print(f"\n{'='*90}")
    print(f"IATIS DATASET VALIDATION — {len(files)} files")
    print(f"{'='*90}")
    print(f"{'':2} {'Symbol':<8} {'TF':<5} {'Rows':>8} {'Days':>5} "
          f"{'Cov%':>6} {'Valid%':>7} {'Gaps':>5} {'Dups':>4} {'OHLC':>4} {'NaN':>5} {'Stale':>6}")
    print("-" * 90)

    results = []
    passes = warns = fails = 0

    for f in files:
        r = validate_file(f)
        results.append(r)
        print_result(r, args.verbose)

        if r["status"] == "PASS":   passes += 1
        elif r["status"] == "WARN": warns  += 1
        else:                        fails  += 1

    total = len(results)
    quality_pct = (passes + warns * 0.5) / total * 100 if total > 0 else 0

    print(f"\n{'='*90}")
    print(f"SUMMARY: ✅ {passes} PASS | ⚠️  {warns} WARN | ❌ {fails} FAIL | Total: {total}")
    print(f"OVERALL DATA QUALITY: {quality_pct:.1f}%")

    if quality_pct >= 95:
        print("STATUS: ✅ READY FOR BACKTEST")
    elif quality_pct >= 80:
        print("STATUS: ⚠️  PROCEED WITH CAUTION")
    else:
        print("STATUS: ❌ FIX DATA ISSUES BEFORE BACKTEST")

    print(f"{'='*90}\n")

    if args.report:
        out = Path("storage") / f"validation_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        out.write_text(json.dumps({
            "generated": datetime.now().isoformat(),
            "quality_pct": quality_pct,
            "summary": {"pass": passes, "warn": warns, "fail": fails},
            "results": results,
        }, indent=2, default=str), encoding="utf-8")
        print(f"Report saved: {out}")

    return quality_pct


if __name__ == "__main__":
    main()
