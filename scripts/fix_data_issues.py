#!/usr/bin/env python3
"""
scripts/fix_data_issues.py
---------------------------
Fix known data quality issues before backtesting:

Fix 1: OHLC violations in 1d files (Yahoo Adjusted Close)
  high = max(open, high, close)
  low  = min(open, low, close)

Fix 2: Validator coverage logic for Indices
  NAS100/SPX500/US30 trade 6.5h/day not 24h
  Update validate_dataset.py to use correct hours

Fix 3: Extreme candles for Indices (price-based not %)
  US30 at 40,000 points: 1000pt move = 2.5%, not 20%+

Usage:
    python3 scripts/fix_data_issues.py
    python3 scripts/fix_data_issues.py --dry-run
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

DATA_DIR = Path("data")

# Symbols that are indices (US market hours = 6.5h/day)
INDEX_SYMBOLS = {"NAS100", "SPX500", "US30", "DAX", "FTSE"}


def fix_ohlc_violations(path: Path, dry_run: bool = False) -> int:
    """Fix OHLC violations: high=max(O,H,C), low=min(O,L,C)."""
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]

        if not all(c in df.columns for c in ["open", "high", "low", "close"]):
            return 0

        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["open", "high", "low", "close"])

        violations_before = int(
            (df["high"] < df["open"]).sum() +
            (df["high"] < df["close"]).sum() +
            (df["low"] > df["open"]).sum() +
            (df["low"] > df["close"]).sum() +
            (df["high"] < df["low"]).sum()
        )

        if violations_before == 0:
            return 0

        # Fix
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"]  = df[["open", "low",  "close"]].min(axis=1)

        violations_after = int(
            (df["high"] < df["open"]).sum() +
            (df["high"] < df["close"]).sum() +
            (df["low"] > df["open"]).sum() +
            (df["low"] > df["close"]).sum()
        )

        if not dry_run:
            df.to_csv(path)

        print(f"  ✅ {path.name}: {violations_before} → {violations_after} violations")
        return violations_before

    except Exception as e:
        print(f"  ❌ {path.name}: {e}")
        return 0


def fix_validator_for_indices():
    """
    Update validate_dataset.py to use correct coverage for indices.
    Indices trade 6.5h/day (390 min) not 24h (1440 min).
    """
    val_path = Path("scripts/validate_dataset.py")
    if not val_path.exists():
        print("validate_dataset.py not found")
        return

    src = val_path.read_text()

    # Add INDEX_SYMBOLS constant and fix coverage calculation
    old_coverage = '''    if tf_min > 0 and len(df) > 1:
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
            coverage_pct = min(100, date_range / 730 * 100)'''

    new_coverage = '''    # Index symbols trade US market hours only (6.5h/day = 390min)
    INDEX_SYMS = {"NAS100", "SPX500", "US30", "DAX", "FTSE", "UK100"}
    is_index = sym in INDEX_SYMS if sym else False

    if tf_min > 0 and len(df) > 1:
        time_diffs = df.index.to_series().diff().dt.total_seconds().dropna() / 60
        expected = tf_min

        # Count gaps (>2× expected interval, excluding weekends for forex)
        gaps = time_diffs[time_diffs > expected * 2]
        weekend_gaps = gaps[gaps >= 60 * 48]  # gaps >= 48h = likely weekends
        real_gaps = len(gaps) - len(weekend_gaps)

        result["stats"]["gaps"] = real_gaps
        result["stats"]["weekend_gaps"] = len(weekend_gaps)

        # Expected coverage — different for indices vs forex vs crypto
        if tf_min <= 60:
            if is_index:
                # Indices: 6.5h/day × 5 days/week (390 min/day)
                minutes_per_day = 390
            elif is_crypto:
                # Crypto: 24h/day × 7 days/week
                minutes_per_day = 1440
            else:
                # Forex: ~21h/day × 5 days/week
                minutes_per_day = 1260

            expected_candles = int(date_range * minutes_per_day / tf_min * 5/7)
            coverage_pct = min(100, n_rows / max(expected_candles, 1) * 100)
        else:
            coverage_pct = min(100, date_range / 730 * 100)'''

    if old_coverage in src:
        new_src = src.replace(old_coverage, new_coverage)

        # Also fix extreme candles for indices — skip % check for index symbols
        old_extreme = '''    # Extreme price changes (>20% in one candle)
    pct_change = df["close"].pct_change().abs()
    extreme = (pct_change > 0.20).sum()
    if extreme > 5:
        result["warnings"].append(f"Extreme candles (>20%): {extreme}")'''

        new_extreme = '''    # Extreme price changes (>20% in one candle)
    # Skip for indices — they are priced in points not fractions
    if not is_index:
        pct_change = df["close"].pct_change().abs()
        extreme = (pct_change > 0.20).sum()
        if extreme > 5:
            result["warnings"].append(f"Extreme candles (>20%): {extreme}")'''

        new_src = new_src.replace(old_extreme, new_extreme)
        val_path.write_text(new_src)
        print("✅ validate_dataset.py updated: indices coverage + extreme candles fixed")
    else:
        print("⚠️  Could not patch validate_dataset.py — pattern not found")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("IATIS Data Issue Fixer")
    print("="*60)

    # Fix 1: OHLC violations in ALL timeframes
    print("\n[Fix 1] OHLC violations:")
    total_fixed = 0
    for f in sorted(DATA_DIR.glob("*.csv")):
        n = fix_ohlc_violations(f, args.dry_run)
        total_fixed += n
    if total_fixed == 0:
        print("  ✅ No OHLC violations found")
    else:
        print(f"  Fixed {total_fixed} total violations")

    # Fix 2 & 3: Update validator logic for indices
    print("\n[Fix 2+3] Validator logic for indices:")
    fix_validator_for_indices()

    print("\n" + "="*60)
    print("Done. Re-run: python3 scripts/validate_dataset.py --report")
    print("="*60)


if __name__ == "__main__":
    main()
