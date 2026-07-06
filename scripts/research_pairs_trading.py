#!/usr/bin/env python3
"""
scripts/research_pairs_trading.py
------------------------------------
Statistical-arbitrage / pairs-trading research (evidence, not opinion).

Tests the "high-accuracy market-neutral" hypothesis raised for IATIS:
cointegrated pairs mean-revert, so trade the spread on a z-score. This is
the ONE proposed idea orthogonal to the directional confluence engines,
so it's the only one that could genuinely diversify — IF it holds up out
of sample.

Method (honest, no curve-fitting):
  1. Daily close per symbol from data/<SYM>_H4_deep.csv (resampled).
  2. Split 60/40. SELECT cointegrated pairs on the in-sample half only
     (Engle-Granger p<0.05), fit the hedge ratio there.
  3. TEST on the untouched out-of-sample half: z-score entry ±2, exit
     ±0.5, with a per-leg cost. Fixed params — tuning them to pass would
     be exactly the overfitting this guards against.

2026-07-06 finding on the 15-symbol FX/metal/crypto universe: 3/105
pairs cointegrated in-sample, 0 profitable out-of-sample. No edge here.
The classic gold/silver pair (XAUUSD/XAGUSD) needs XAG data (Yahoo-only,
present on the VPS) — run there to include it.

    python3 scripts/research_pairs_trading.py
    python3 scripts/research_pairs_trading.py --symbols XAUUSD XAGUSD
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_SYMS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "EURJPY", "GBPJPY",
                "AUDJPY", "XAUUSD", "XAGUSD", "USOIL", "US30", "NAS100",
                "SPX500", "BTCUSD", "ETHUSD"]


def _zscore_backtest(a, b, beta, entry=2.0, exit_=0.5, cost_bps=2.0):
    spread = a - beta * b
    z = (spread - spread.mean()) / spread.std()
    sp = spread.values
    pos, e, pnl = 0, 0.0, []
    for i in range(1, len(z)):
        zi = z.iloc[i]
        if pos == 0:
            if zi > entry: pos, e = -1, sp[i]
            elif zi < -entry: pos, e = 1, sp[i]
        elif pos == 1 and zi >= -exit_:
            pnl.append((sp[i] - e) - abs(sp[i] + e) * cost_bps / 1e4); pos = 0
        elif pos == -1 and zi <= exit_:
            pnl.append((e - sp[i]) - abs(sp[i] + e) * cost_bps / 1e4); pos = 0
    if not pnl:
        return 0, None, 0.0
    p = np.array(pnl); gp = p[p > 0].sum(); gl = -p[p < 0].sum()
    return len(p), (round(gp / gl, 3) if gl > 0 else None), round(p.sum(), 4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMS)
    ap.add_argument("--pmax", type=float, default=0.05)
    args = ap.parse_args()

    from statsmodels.tsa.stattools import coint
    import statsmodels.api as sm

    close = {}
    for s in args.symbols:
        f = DATA / f"{s}_H4_deep.csv"
        if not f.exists():
            print(f"  skip {s}: no {f.name}")
            continue
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        close[s] = df["close"].resample("1D").last()

    px = pd.DataFrame(close).dropna()
    print(f"aligned daily bars: {len(px)}  {px.index[0].date()} -> {px.index[-1].date()}")
    split = int(len(px) * 0.6)
    ins, oos = px.iloc[:split], px.iloc[split:]

    res = []
    pairs = list(itertools.combinations(px.columns, 2))
    for a, b in pairs:
        try:
            _, pv, _ = coint(ins[a], ins[b])
        except Exception:
            continue
        if pv < args.pmax:
            beta = sm.OLS(ins[a], sm.add_constant(ins[b])).fit().params.iloc[1]
            n, pf, ret = _zscore_backtest(oos[a], oos[b], beta)
            res.append({"pair": f"{a}/{b}", "coint_p": round(pv, 4),
                        "beta": round(beta, 4), "oos_trades": n,
                        "oos_pf": pf, "oos_pnl": ret})

    res.sort(key=lambda r: (r["oos_pf"] or 0), reverse=True)
    print(f"\ncointegrated in-sample (p<{args.pmax}): {len(res)} of {len(pairs)} tested\n")
    print(f"{'pair':18s}{'coint_p':>9s}{'OOSn':>6s}{'OOS_PF':>8s}{'OOS_pnl':>10s}")
    for r in res[:20]:
        print(f"{r['pair']:18s}{r['coint_p']:>9}{r['oos_trades']:>6}{str(r['oos_pf']):>8}{r['oos_pnl']:>10}")
    prof = [r for r in res if r["oos_pf"] and r["oos_pf"] > 1.1 and r["oos_trades"] >= 10]
    print(f"\nOUT-OF-SAMPLE profitable (PF>1.1, n>=10): {len(prof)}")
    for r in prof:
        print(f"  {r['pair']}  PF={r['oos_pf']}  n={r['oos_trades']}")
    if not prof:
        print("  none — no exploitable pairs edge on this universe. Do NOT build a "
              "stat-arb module on this evidence.")

    try:
        from research.manifest import build_manifest, write_manifest
        from utils.helpers import load_config
        import time
        m = build_manifest(kind="pairs_trading_research", config=load_config(),
            params={"method": "Engle-Granger coint on 60% in-sample, z-score(±2/±0.5) "
                              "backtest on 40% out-of-sample, 2bps/leg cost, fixed params",
                    "universe": list(px.columns),
                    "verdict": f"{len(prof)} OOS-profitable of {len(res)} cointegrated "
                               f"({len(pairs)} tested)"},
            datasets=[], results={"pairs": res, "oos_profitable": prof})
        out = write_manifest(m, f"pairs_trading_{time.strftime('%Y%m%d')}")
        print(f"\nManifest: {out}")
    except Exception as exc:
        print(f"manifest skipped: {exc}")


if __name__ == "__main__":
    main()
