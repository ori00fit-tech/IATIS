"""
scripts/min_info_share_fx_ab.py
---------------------------------
Controlled A/B for H020: is confluence.min_informative_weight_share (the
Axis-8 "SPEAKING panel" gate, currently 0.6) net-negative for the FX book
specifically, while neutral-to-positive for the carriers (XAUUSD, BTCUSD,
ETHUSD)?

Same bars, same config, ONE variable flipped (confluence.
min_informative_weight_share: 0.0 vs 0.6) — the same discipline as H017's
scripts/smc_fullspec_ab.py. Run on the VPS where the deep H4 CSVs live:

    venv/bin/python -m scripts.min_info_share_fx_ab --data-dir data

Decision rule (pre-registered in research/results/registry.json's H020
entry, before results exist):
  - dPF = PF(gate=0.0) minus PF(gate=0.6), TEST slice (final 35%) only.
  - VERDICT "FX-NEGATIVE — WORTH ASSET-CLASS SCOPING" only if ALL of:
    (1) mean dPF across the 7 FX pairs >= +0.03
    (2) at most 1 of 7 FX pairs shows dPF < 0
    (3) mean |dPF| across the 3 carriers <= 0.02
  - Otherwise VERDICT "NO ACTION".
  - This script only produces a verdict. It does not change config.yaml —
    no config change may be made off this result alone without its own
    pre-registration (CLAUDE.md: never change entries/exits/thresholds
    mid-sample).
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

FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "EURJPY", "GBPJPY", "AUDJPY"]
CARRIER_SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]


def _load(data_dir: Path, symbol: str) -> pd.DataFrame | None:
    for pattern in (f"{symbol}_H4_deep.csv", f"{symbol}_H4.csv"):
        p = data_dir / pattern
        if p.exists():
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            df.columns = [c.lower() for c in df.columns]
            return df
    return None


def compute_verdict(fx_rows: list[dict], carrier_rows: list[dict]) -> dict:
    """Pure decision-rule function — the pre-registered H020 rule,
    factored out so it's unit-testable without running a real backtest."""
    fx_mean_dpf = sum(r["delta_pf"] for r in fx_rows) / len(fx_rows)
    fx_losers = sum(1 for r in fx_rows if r["delta_pf"] < 0)
    carrier_mean_abs_dpf = sum(abs(r["delta_pf"]) for r in carrier_rows) / len(carrier_rows)

    verdict = (
        "FX-NEGATIVE - WORTH ASSET-CLASS SCOPING"
        if fx_mean_dpf >= 0.03 and fx_losers <= 1 and carrier_mean_abs_dpf <= 0.02
        else "NO ACTION"
    )
    return {
        "fx_test_mean_delta_pf": round(fx_mean_dpf, 3),
        "fx_losing_pairs": fx_losers,
        "fx_pairs_tested": len(fx_rows),
        "carrier_test_mean_abs_delta_pf": round(carrier_mean_abs_dpf, 3),
        "decision": verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=FX_SYMBOLS + CARRIER_SYMBOLS)
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
            for variant, value in (("gate_off", 0.0), ("gate_on", 0.6)):
                cfg = copy.deepcopy(base_cfg)
                cfg.setdefault("confluence", {})["min_informative_weight_share"] = value
                bt = BacktestConfig.from_profile(symbol)
                r = run_backtest(sdf, config=bt, engine_config=cfg)
                row[variant] = {"pf": round(r.profit_factor, 3),
                                "wr": round(r.win_rate * 100, 1),
                                "trades": r.execute_count,
                                "max_dd": round(r.max_drawdown_pct, 1)}
            row["delta_pf"] = round(row["gate_off"]["pf"] - row["gate_on"]["pf"], 3)
            results[slice_name][symbol] = row
            off, on = row["gate_off"], row["gate_on"]
            print(f"{slice_name:5s} {symbol:8s} gate_off PF={off['pf']:.3f}/{off['trades']}t "
                  f"| gate_on PF={on['pf']:.3f}/{on['trades']}t | dPF={row['delta_pf']:+.3f}")

    test = results["test"]
    fx_rows = [test[s] for s in FX_SYMBOLS if s in test]
    carrier_rows = [test[s] for s in CARRIER_SYMBOLS if s in test]

    if fx_rows and carrier_rows:
        results["verdict"] = compute_verdict(fx_rows, carrier_rows)
        v = results["verdict"]
        print(f"\nFX TEST mean dPF = {v['fx_test_mean_delta_pf']:+.3f}, "
              f"FX losing pairs = {v['fx_losing_pairs']}/{v['fx_pairs_tested']}")
        print(f"Carrier TEST mean |dPF| = {v['carrier_test_mean_abs_delta_pf']:.3f}")
        print(f"-> VERDICT: {v['decision']} (rule pre-registered in registry.json's H020 entry)")
    else:
        print("\nIncomplete data (need both FX and carrier symbols) — no verdict computed.")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))
        print(f"JSON written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
