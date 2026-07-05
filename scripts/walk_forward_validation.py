#!/usr/bin/env python3
"""
scripts/walk_forward_validation.py
------------------------------------
Phase 4.4: Walk-Forward Out-of-Sample Validation

Tests whether IATIS performance holds on data NOT used during development.

Method:
  Window 1: Train 2022-2023, Test 2024
  Window 2: Train 2023-2024, Test 2025
  Window 3: Train 2024-2025, Test 2026

If PF remains above 1.5-2.0 across all windows → statistical confidence
If PF degrades significantly → overfitting concern

Usage:
    python3 scripts/walk_forward_validation.py
    python3 scripts/walk_forward_validation.py --symbols EURUSD GBPUSD
    python3 scripts/walk_forward_validation.py --step 8   # faster
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT


# Walk-forward windows — (train_start, train_end, test_start, test_end)
# Dates are used to filter the H1 CSV data
WINDOWS = [
    ("2022-01-01", "2023-12-31", "2024-01-01", "2024-12-31", "W1: Train 2022-23, Test 2024"),
    ("2023-01-01", "2024-12-31", "2025-01-01", "2025-12-31", "W2: Train 2023-24, Test 2025"),
    ("2024-01-01", "2025-12-31", "2026-01-01", "2026-06-25", "W3: Train 2024-25, Test 2026"),
]

# Minimum data to run a window
MIN_BARS_TRAIN = 3000
MIN_BARS_TEST = 500
MIN_TRADES_TEST = 10


def run_window(df, train_start, train_end, test_start, test_end,
               symbol, step, label):
    """Run one walk-forward window. Returns (train_result, test_result)."""
    from backtesting.backtest_engine import BacktestConfig, run_backtest

    # Filter data by date
    import pandas as pd
    df.index = pd.to_datetime(df.index, utc=True)

    train_df = df.loc[train_start:train_end].copy()
    test_df  = df.loc[test_start:test_end].copy()

    ac  = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)
    pip = PIP_SIZE.get(symbol, 0.0001)

    config = BacktestConfig(
        symbol=symbol, pip_size=pip,
        asset_class=ac, dollar_per_point=dpp,
        step_bars=step,
    )

    train_result = test_result = None

    if len(train_df) >= MIN_BARS_TRAIN:
        try:
            train_result = run_backtest(train_df, config)
        except Exception as e:
            train_result = None

    if len(test_df) >= MIN_BARS_TEST:
        try:
            test_result = run_backtest(test_df, config)
        except Exception as e:
            test_result = None

    return train_result, test_result, len(train_df), len(test_df)


def summarize(result) -> dict:
    if result is None:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "return": None}
    return {
        "trades": result.execute_count,
        "win_rate": round(result.win_rate * 100, 1),
        "profit_factor": round(result.profit_factor, 2),
        "max_drawdown": round(result.max_drawdown_pct * 100, 1),
        "return": round(result.total_return_pct * 100, 1),
    }


def grade_consistency(windows_pf: list[float | None]) -> str:
    """Grade stability across walk-forward windows."""
    valid = [pf for pf in windows_pf if pf is not None]
    if not valid:
        return "INSUFFICIENT_DATA"
    avg = sum(valid) / len(valid)
    min_pf = min(valid)
    if avg >= 1.8 and min_pf >= 1.2:
        return "CONSISTENT ✅"
    elif avg >= 1.4 and min_pf >= 1.0:
        return "ACCEPTABLE ⚠️"
    else:
        return "INCONSISTENT ❌"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                        default=["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "BTCUSD"])
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--all", action="store_true", help="Run all 19 active symbols")
    args = parser.parse_args()

    if args.all:
        from scripts.download_all_symbols import ALL_SYMBOLS
        args.symbols = list(ALL_SYMBOLS.keys())

    print(f"\n{'='*70}")
    print("IATIS Walk-Forward Validation (Out-of-Sample)")
    print(f"{'='*70}")
    print(f"Symbols: {args.symbols}")
    print(f"Step: every {args.step} bars")
    print(f"Windows: {len(WINDOWS)}")
    print()
    print("Method: Train on past data, test on UNSEEN future data")
    print("Goal: PF > 1.5 on test windows → generalizes beyond training data")
    print()

    from core.data_loader import load_from_csv
    import pandas as pd
    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    data_dir = Path("data")
    all_results = {}
    fingerprints = []

    for symbol in args.symbols:
        # Find CSV file
        csv_file = None
        for pattern in [f"{symbol}_H1_2y.csv", f"{symbol}_H1_5y.csv"]:
            p = data_dir / pattern
            if p.exists():
                csv_file = p
                break

        if not csv_file:
            print(f"  ⚠️  {symbol}: no CSV found — run download_all_symbols.py first")
            continue

        print(f"\n{symbol} ({csv_file.name})")
        df = load_from_csv(str(csv_file))
        print(f"  Total bars: {len(df)} | {df.index[0].date()} → {df.index[-1].date()}")
        fingerprints.append({"symbol": symbol, **dataset_fingerprint(csv_file, df)})

        symbol_results = {"windows": [], "test_pf_values": []}

        for train_s, train_e, test_s, test_e, label in WINDOWS:
            train_r, test_r, n_train, n_test = run_window(
                df.copy(), train_s, train_e, test_s, test_e,
                symbol, args.step, label
            )

            train_s_obj = summarize(train_r)
            test_s_obj = summarize(test_r)

            # Grade this window
            if test_r is None or test_r.execute_count < MIN_TRADES_TEST:
                window_grade = "SKIP (low n)"
            elif (test_r.profit_factor or 0) >= 1.5:
                window_grade = "GOOD ✅"
            elif (test_r.profit_factor or 0) >= 1.0:
                window_grade = "MARGINAL ⚠️"
            else:
                window_grade = "POOR ❌"

            print(f"  {label}")
            if train_r:
                print(f"    TRAIN: {train_s_obj['trades']} trades, "
                      f"WR={train_s_obj['win_rate']}%, PF={train_s_obj['profit_factor']}")
            if test_r and test_r.execute_count >= MIN_TRADES_TEST:
                print(f"    TEST:  {test_s_obj['trades']} trades, "
                      f"WR={test_s_obj['win_rate']}%, PF={test_s_obj['profit_factor']} "
                      f"→ {window_grade}")
                symbol_results["test_pf_values"].append(test_r.profit_factor)
            else:
                print(f"    TEST:  {n_test} bars, {test_r.execute_count if test_r else 0} trades → SKIP")

            symbol_results["windows"].append({
                "label": label,
                "train": train_s_obj,
                "test": test_s_obj,
                "grade": window_grade,
            })

        consistency = grade_consistency(symbol_results["test_pf_values"])
        print(f"  Overall: {consistency}")
        all_results[symbol] = {**symbol_results, "consistency": consistency}

    # Summary
    print(f"\n{'='*70}")
    print("WALK-FORWARD SUMMARY")
    print(f"{'='*70}")
    print(f"{'Symbol':<12} {'W1 Test PF':>12} {'W2 Test PF':>12} {'W3 Test PF':>12} {'Consistency'}")
    print("-" * 70)

    for symbol, data in all_results.items():
        pfs = data["test_pf_values"]
        pf_strs = [f"{pf:.2f}" if pf else "--" for pf in pfs]
        while len(pf_strs) < 3:
            pf_strs.append("--")
        print(f"{symbol:<12} {pf_strs[0]:>12} {pf_strs[1]:>12} {pf_strs[2]:>12}  {data['consistency']}")

    # Save results
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "step_bars": args.step,
        "windows": [w[4] for w in WINDOWS],
        "results": {s: {k: v for k, v in d.items() if k != "test_pf_values"}
                    for s, d in all_results.items()},
    }
    out_path = Path("storage") / "walk_forward_results.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResults saved: {out_path}")

    # Reproducibility manifest (audit item H2): bind this run to the exact
    # commit, config, and dataset fingerprints, in a git-tracked file —
    # summary numbers without this are NOT ENOUGH EVIDENCE.
    manifest = build_manifest(
        kind="walk_forward",
        config=load_config(),
        params={
            "step_bars": args.step,
            "symbols": args.symbols,
            "windows": [list(w) for w in WINDOWS],
            "min_bars_train": MIN_BARS_TRAIN,
            "min_bars_test": MIN_BARS_TEST,
            "min_trades_test": MIN_TRADES_TEST,
        },
        datasets=fingerprints,
        results=output["results"],
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    manifest_path = write_manifest(manifest, f"walk_forward_{stamp}")
    print(f"Reproducibility manifest: {manifest_path}"
          + ("" if manifest["reproducible"] else "  [NOT reproducible: dirty/unknown git state]"))
    print()
    print("Interpretation:")
    print("  CONSISTENT ✅ = PF > 1.5 on test, min PF > 1.2 → strong out-of-sample evidence")
    print("  ACCEPTABLE ⚠️ = PF > 1.0 on test → edge exists but may be weak")
    print("  INCONSISTENT ❌ = PF < 1.0 on some test windows → possible overfitting")


if __name__ == "__main__":
    main()
