#!/usr/bin/env python3
"""
run_h008c.py
-------------
Run the honest, look-ahead-free, out-of-sample BOS+FVG re-test (H008c) on
REAL deep M15 data fetched by scripts/fetch_m15_twelvedata.py.

    python3 scripts/fetch_m15_twelvedata.py --symbols EUR/USD,GBP/USD,XAU/USD --pages 16
    python3 run_h008c.py

Reports, per symbol and POOLED (train/test chronological split):
  * causal unfiltered WR       (H008 concept, look-ahead removed)
  * causal + London/ATR filter (H008b concept, look-ahead removed)
  * how much the old look-ahead detector inflated WR
The verdict is decided on the held-out TEST slice.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from research.experiments.H008c_oos import (  # noqa: E402
    TRAIN_FRAC, MIN_SAMPLE,
    causal_bos_fvg_setups, _wr_block, _verdict, run_experiment,
)

SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD"]
DATA = Path("data")


def _load(sym: str) -> pd.DataFrame | None:
    f = DATA / f"{sym}_M15_real.csv"
    if not f.exists():
        print(f"  ⚠️  {sym}: {f} not found — run fetch_m15_twelvedata.py first")
        return None
    df = pd.read_csv(f)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def _fmt(b: dict) -> str:
    if b["n"] == 0 or b["wr"] is None:
        return f"n={b['n']:<4} WR=—"
    return (f"n={b['n']:<4} WR={b['wr']:.3f} "
            f"Δ={b['improvement']:+.3f} p={b['p_value']:.3f}")


def main() -> None:
    print("=" * 66)
    print("H008c — BOS+FVG, look-ahead-free, out-of-sample (real M15)")
    print("=" * 66)

    pooled = {"cu_train": [], "cu_test": [], "cf_train": [], "cf_test": []}
    loaded = 0
    for sym in SYMBOLS:
        df = _load(sym)
        if df is None or len(df) < 500:
            continue
        loaded += 1
        res = run_experiment(df, source=f"real:TwelveData_{sym}_M15", symbol=sym)
        print(f"\n{sym}  ({res.n_bars} bars, split @ {res.train_test_boundary[:16]})")
        print(f"  causal unfiltered  test: {_fmt(res.causal_unfiltered['test'])}")
        print(f"  causal +London+ATR test: {_fmt(res.causal_filtered['test'])}")
        print(f"  look-ahead inflation: {res.lookahead_inflation_pp}pp "
              f"(old {res.lookahead_full['wr']} → causal {res.causal_full['wr']})")
        print(f"  → {res.status}")

        # accumulate pooled setups (recompute on the same split)
        split = int(len(df) * TRAIN_FRAC)
        tr, te = df.iloc[:split], df.iloc[split:]
        pooled["cu_train"] += causal_bos_fvg_setups(tr)
        pooled["cu_test"] += causal_bos_fvg_setups(te)
        pooled["cf_train"] += causal_bos_fvg_setups(tr, london=True, atr_mult=1.5)
        pooled["cf_test"] += causal_bos_fvg_setups(te, london=True, atr_mult=1.5)

    if loaded == 0:
        sys.exit("\nNo M15 data. Run scripts/fetch_m15_twelvedata.py first.")

    cu_test = _wr_block(pooled["cu_test"])
    cf_test = _wr_block(pooled["cf_test"])
    cu_train = _wr_block(pooled["cu_train"])
    cf_train = _wr_block(pooled["cf_train"])
    primary = cf_test if (cf_test["n"] >= MIN_SAMPLE and cf_test["wr"] is not None
                          and (cu_test["wr"] is None or cf_test["wr"] > cu_test["wr"])) else cu_test
    pooled_status = _verdict(primary)

    print("\n" + "=" * 66)
    print(f"POOLED across {loaded} symbols")
    print(f"  unfiltered  train: {_fmt(cu_train)}")
    print(f"  unfiltered  test : {_fmt(cu_test)}")
    print(f"  +London+ATR train: {_fmt(cf_train)}")
    print(f"  +London+ATR test : {_fmt(cf_test)}")
    print(f"  → POOLED VERDICT (test slice): {pooled_status}")
    print("=" * 66)

    # git-tracked manifest
    try:
        from research.manifest import build_manifest, write_manifest
        from utils.helpers import load_config
        m = build_manifest(
            kind="h008c_oos_bosfvg", config=load_config(),
            params={"symbols": SYMBOLS, "train_frac": TRAIN_FRAC,
                    "forward_bars": 20, "swing_window": 3, "bos_max_bars": 10,
                    "filters": "London 02-10 UTC + BOS candle >= 1.5*ATR14",
                    "baseline": "H001 directional WR 0.4978 (n=225)",
                    "method": "causal swing confirmation (no look-ahead); "
                              "chronological train/test; verdict on TEST"},
            datasets=[], results={
                "pooled": {"unfiltered_test": cu_test, "filtered_test": cf_test,
                           "unfiltered_train": cu_train, "filtered_train": cf_train,
                           "status": pooled_status}})
        import time
        out = write_manifest(m, f"h008c_oos_{time.strftime('%Y%m%d')}")
        print(f"Manifest: {out}")
    except Exception as exc:
        print(f"manifest skipped: {exc}")


if __name__ == "__main__":
    main()
