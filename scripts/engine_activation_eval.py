#!/usr/bin/env python3
"""
scripts/engine_activation_eval.py
------------------------------------
Marginal-value evaluation for enabling dormant engines (2026-07-05).

Answers "should we turn on more engines for trade quality / diversification?"
with evidence instead of intuition: for a FX/metal/crypto basket on deep
H4 data (frozen production config, all gates, 0.5+0.5 pip costs), compares
the 4-engine live baseline against baseline+one-dormant-engine and against
all-9-together, aggregated at portfolio level plus per-symbol deltas.

Verdict on the 2026-07-05 run: every addition LOWERED portfolio PF
(baseline 1.27 → ALL9 1.108). Engines were NOT enabled. See
research/results/engine_activation_20260705_manifest.json.

Run on a host that has data/<SYM>_H4_deep.csv (VPS or after
scripts/download_deep_history.py).
"""
import sys, json, time, copy
sys.path.insert(0, "/home/user/IATIS")
import pandas as pd
from collections import defaultdict
from utils.helpers import load_config
from backtesting.backtest_engine import BacktestConfig, run_backtest

DATA = "/home/user/IATIS/data"
OUT = "/tmp/claude-0/-home-user-IATIS/3f90ca86-9e95-5d90-a529-2d1e3d631146/scratchpad"
# Basket spanning FX / metal / crypto — the classes that behaved differently.
SYMS = ["EURUSD","GBPUSD","USDJPY","XAUUSD","BTCUSD","ETHUSD"]
DORMANT = ["ict","quant","divergence","market_structure","sentiment","macro"]
ALL9 = ["smc","price_action","nnfx","wyckoff","ict","quant",
        "divergence","market_structure","sentiment"]  # macro needs alt-data, excl.

base_cfg = load_config()
assert base_cfg["data"]["timeframes"][0] == "H4"

def cfg_with(enabled_names):
    c = copy.deepcopy(base_cfg)
    en = c["engines"]["enabled"]
    for k in en: en[k] = k in enabled_names
    return c

def run_variant(name, enabled_names):
    agg = {"gp": 0.0, "gl": 0.0, "n": 0, "w": 0}
    per_sym = {}
    for sym in SYMS:
        try:
            df = pd.read_csv(f"{DATA}/{sym}_H4_deep.csv", index_col=0, parse_dates=True)
        except FileNotFoundError:
            continue
        df.index = pd.to_datetime(df.index, utc=True)
        r = run_backtest(df, BacktestConfig.from_profile(sym, step_bars=1),
                         engine_config=cfg_with(enabled_names))
        closed = [t for t in r.trades if t.exit_bar >= 0]
        gp = sum(t.pnl_usd for t in closed if t.pnl_usd > 0)
        gl = abs(sum(t.pnl_usd for t in closed if t.pnl_usd <= 0))
        w = sum(1 for t in closed if t.pnl_usd > 0)
        agg["gp"]+=gp; agg["gl"]+=gl; agg["n"]+=len(closed); agg["w"]+=w
        per_sym[sym] = {"trades": len(closed),
                        "pf": round(gp/gl,3) if gl>0 else None,
                        "wr": round(100*w/len(closed),1) if closed else None}
    portfolio = {"trades": agg["n"],
                 "wr": round(100*agg["w"]/agg["n"],1) if agg["n"] else None,
                 "pf": round(agg["gp"]/agg["gl"],3) if agg["gl"]>0 else None}
    print(f"{name:28s} n={portfolio['trades']:5d} WR={portfolio['wr']}% PF={portfolio['pf']}", flush=True)
    return {"enabled": enabled_names, "portfolio": portfolio, "per_symbol": per_sym}

results = {}
BASE = ["smc","price_action","nnfx","wyckoff"]
t0=time.time()
results["BASELINE"] = run_variant("BASELINE (4 live)", BASE)
for e in DORMANT:
    if e == "macro": continue
    results[f"+{e}"] = run_variant(f"+{e}", BASE+[e])
results["ALL9"] = run_variant("ALL9", ALL9)
print(f"\ntotal runtime {round(time.time()-t0,1)}s")
json.dump(results, open(f"{OUT}/engine_eval_results.json","w"), indent=1)
print("DONE")
