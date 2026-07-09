"""
scripts/smc_fullspec_ab.py
---------------------------
Controlled A/B for H017: does full-spec SMC (order blocks + FVG + BOS/CHoCH
as internal score modulators) improve the production system?

Same bars, same config, ONE variable flipped (engines.smc_full_spec), the
same discipline as the crypto-volume A/B. Run on the VPS where the deep H4
CSVs live:

    venv/bin/python -m scripts.smc_fullspec_ab --data-dir data \
        --symbols EURUSD XAUUSD BTCUSD ETHUSD

Decision rule (pre-registered here, before results exist):
  - ADOPT (flip the flag) only if mean PF improves by >= 0.03 on a
    chronological TEST slice (final 35%) AND does not lose on more than
    one symbol. TRAIN-slice improvement alone is the H008b/H015 mirage.
  - Otherwise the flag stays off and H017 records the negative result.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import pandas as pd

from backtesting.backtest_engine import BacktestConfig, run_backtest
from utils.helpers import load_config

TRAIN_FRAC = 0.65


def _load(data_dir: Path, symbol: str) -> pd.DataFrame | None:
    for pattern in (f"{symbol}_H4_deep.csv", f"{symbol}_H4.csv"):
        p = data_dir / pattern
        if p.exists():
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            df.columns = [c.lower() for c in df.columns]
            return df
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                    default=["EURUSD", "XAUUSD", "BTCUSD", "ETHUSD"])
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--json", metavar="PATH")
    args = ap.parse_args()

    base_cfg = load_config()
    results: dict = {"train": {}, "test": {}}

    for symbol in args.symbols:
        df = _load(Path(args.data_dir), symbol)
        if df is None:
            print(f"!! {symbol}: no H4 CSV in {args.data_dir} — skipped")
            continue
        split = int(len(df) * TRAIN_FRAC)
        slices = {"train": df.iloc[:split], "test": df.iloc[split:]}
        for slice_name, sdf in slices.items():
            row = {}
            for variant, flag in (("baseline", False), ("full_spec", True)):
                cfg = copy.deepcopy(base_cfg)
                cfg.setdefault("engines", {})["smc_full_spec"] = flag
                bt = BacktestConfig.from_profile(symbol) if hasattr(
                    BacktestConfig, "from_profile") else BacktestConfig(symbol=symbol)
                r = run_backtest(sdf, config=bt, engine_config=cfg)
                row[variant] = {"pf": round(r.profit_factor, 3),
                                "wr": round(r.win_rate * 100, 1),
                                "trades": r.execute_count,
                                "max_dd": round(r.max_drawdown_pct, 1)}
            row["delta_pf"] = round(row["full_spec"]["pf"] - row["baseline"]["pf"], 3)
            results[slice_name][symbol] = row
            b, f = row["baseline"], row["full_spec"]
            print(f"{slice_name:5s} {symbol:8s} baseline PF={b['pf']:.3f}/{b['trades']}t "
                  f"| full_spec PF={f['pf']:.3f}/{f['trades']}t | ΔPF={row['delta_pf']:+.3f}")

    test_rows = results["test"].values()
    if test_rows:
        mean_delta = sum(r["delta_pf"] for r in test_rows) / len(test_rows)
        losers = sum(1 for r in test_rows if r["delta_pf"] < 0)
        verdict = ("ADOPT" if mean_delta >= 0.03 and losers <= 1 else "KEEP FLAG OFF")
        print(f"\nTEST mean ΔPF = {mean_delta:+.3f}, losing symbols = {losers} "
              f"→ {verdict} (rule pre-registered in this script's docstring)")
        results["verdict"] = {"test_mean_delta_pf": round(mean_delta, 3),
                              "losing_symbols": losers, "decision": verdict}

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))
        print(f"JSON written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
