#!/usr/bin/env python3
"""
scripts/engine_subset_search.py
---------------------------------
H015 (rigorous) — find the engine subset that maximises OUT-OF-SAMPLE
profit factor, and decide whether it beats the live production-4.

The prior ablation (research/results/ablation_20260703.json) was single
leave-one-out, in-sample, on an all-10 baseline. It hinted that some live
engines dilute (removing SMC raised mean PF) and some dormant ones add
value (market_structure, quant) — but single-LOO can't see interactions
and in-sample deltas overfit. This does it honestly:

  * Greedy bidirectional search (add OR drop one engine per step) starting
    from the production-4, driven ONLY by the TRAIN slice.
  * The selected subset is then scored on a held-out TEST slice, alongside
    the production-4 and the all-9 references, per symbol and pooled.
  * Pre-registered adoption rule: adopt the pruned/expanded set only if its
    pooled TEST PF beats production-4 by ≥ +0.03 AND it wins on ≥⅔ of
    symbols. Anything less = keep production-4 (the in-sample winner is
    almost always a mirage; the OOS split is the judge).

Every candidate engine must already hold a registry entry at RESEARCH+
(edge_gate parity). No engine is enabled in config by this script — it only
measures and recommends.

    python3 scripts/engine_subset_search.py --symbols EURUSD XAUUSD BTCUSD \
        --bars 6000 --step 6
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.disable(logging.CRITICAL)  # engine/backtest logs would drown the search

import pandas as pd  # noqa: E402

PROD4 = ("smc", "price_action", "nnfx", "wyckoff")
ALL_ENGINES = ("smc", "price_action", "nnfx", "wyckoff",
               "ict", "quant", "divergence", "market_structure", "sentiment")
IMPROVE_EPS = 0.005          # min TRAIN mean-PF gain to accept a greedy move
ADOPT_MARGIN = 0.03          # min TEST pooled-PF edge over prod-4 to recommend


def _load(sym: str, data_dir: Path, bars: int | None) -> pd.DataFrame:
    df = pd.read_csv(data_dir / f"{sym}_H4_deep.csv")
    dt = next(c for c in df.columns
              if c.lower() in ("datetime", "date", "timestamp", "time"))
    df[dt] = pd.to_datetime(df[dt], utc=True, errors="coerce")
    df = df.dropna(subset=[dt]).set_index(dt).sort_index()
    df.columns = [c.lower() for c in df.columns]
    if bars and len(df) > bars:
        df = df.iloc[-bars:]
    return df


def _pf(df: pd.DataFrame, sym: str, subset: tuple[str, ...],
        base_cfg: dict, step: int) -> tuple[float, int]:
    from backtesting.backtest_engine import BacktestConfig, run_backtest
    cfg = copy.deepcopy(base_cfg)
    cfg.setdefault("engines", {})["enabled"] = {k: (k in subset) for k in ALL_ENGINES}
    r = run_backtest(df, BacktestConfig.from_profile(sym, step_bars=step),
                     engine_config=cfg)
    return round(r.profit_factor, 4), r.execute_count


def _pooled(dfs: dict, subset: tuple[str, ...], base_cfg: dict, step: int,
            which: str) -> dict:
    """Mean PF (trade-weighted-agnostic simple mean) across symbols on the
    given slice for one engine subset."""
    per = {}
    pfs = []
    for sym, (tr, te) in dfs.items():
        d = tr if which == "train" else te
        pf, n = _pf(d, sym, subset, base_cfg, step)
        per[sym] = {"pf": pf, "trades": n}
        pfs.append(pf)
    return {"mean_pf": round(sum(pfs) / len(pfs), 4), "per_symbol": per}


def greedy_search(dfs: dict, base_cfg: dict, step: int) -> tuple[tuple, list]:
    """Greedy bidirectional search on TRAIN, from PROD4. Returns the selected
    subset and the trail of accepted moves."""
    current = tuple(sorted(PROD4))
    best = _pooled(dfs, current, base_cfg, step, "train")["mean_pf"]
    trail = [{"move": "start", "subset": list(current), "train_pf": best}]
    print(f"  start PROD4 train mean-PF={best}")

    while True:
        candidates = []
        # drop moves
        for e in current:
            cand = tuple(sorted(set(current) - {e}))
            if cand:
                candidates.append(("drop " + e, cand))
        # add moves
        for e in ALL_ENGINES:
            if e not in current:
                candidates.append(("add " + e, tuple(sorted(set(current) | {e}))))

        scored = []
        for label, cand in candidates:
            pf = _pooled(dfs, cand, base_cfg, step, "train")["mean_pf"]
            scored.append((pf, label, cand))
            print(f"    try {label:24s} train mean-PF={pf}")
        scored.sort(reverse=True)
        top_pf, top_label, top_cand = scored[0]
        if top_pf > best + IMPROVE_EPS:
            best = top_pf
            current = top_cand
            trail.append({"move": top_label, "subset": list(current), "train_pf": top_pf})
            print(f"  ✓ accept {top_label} → {list(current)} (train PF={top_pf})")
        else:
            print(f"  ✗ no move beats {best}+{IMPROVE_EPS}; stop")
            break
    return current, trail


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["EURUSD", "XAUUSD", "BTCUSD"])
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--bars", type=int, default=6000, help="cap to last N H4 bars")
    ap.add_argument("--train-frac", type=float, default=0.65)
    ap.add_argument("--step", type=int, default=6, help="backtest stride")
    ap.add_argument("--output", default="research/results/engine_subset_search.json")
    args = ap.parse_args()

    from utils.helpers import load_config
    base_cfg = load_config()

    dfs = {}
    for sym in args.symbols:
        f = args.data_dir / f"{sym}_H4_deep.csv"
        if not f.exists():
            print(f"⚠️  {sym}: {f} not found — skipping")
            continue
        df = _load(sym, args.data_dir, args.bars)
        split = int(len(df) * args.train_frac)
        dfs[sym] = (df.iloc[:split], df.iloc[split:])
        print(f"{sym}: {len(df)} bars → train {split} / test {len(df)-split} "
              f"(split @ {df.index[split]})")
    if not dfs:
        sys.exit("No data.")

    t0 = time.time()
    print("\n── Greedy bidirectional search (TRAIN only) ──")
    selected, trail = greedy_search(dfs, base_cfg, args.step)

    print("\n── Out-of-sample validation (TEST slice) ──")
    refs = {
        "prod4": tuple(sorted(PROD4)),
        "all9": tuple(sorted(ALL_ENGINES)),
        "selected": tuple(sorted(selected)),
    }
    test_scores = {}
    for name, subset in refs.items():
        sc = _pooled(dfs, subset, base_cfg, args.step, "test")
        test_scores[name] = {"subset": list(subset), **sc}
        print(f"  {name:9s} {list(subset)}")
        print(f"            TEST mean-PF={sc['mean_pf']}  "
              + " ".join(f"{s}={v['pf']}" for s, v in sc['per_symbol'].items()))

    prod_pf = test_scores["prod4"]["mean_pf"]
    sel_pf = test_scores["selected"]["mean_pf"]
    sel_wins = sum(1 for s in dfs
                   if test_scores["selected"]["per_symbol"][s]["pf"]
                   > test_scores["prod4"]["per_symbol"][s]["pf"])
    adopt = (refs["selected"] != refs["prod4"]
             and sel_pf >= prod_pf + ADOPT_MARGIN
             and sel_wins >= (2 * len(dfs) + 2) // 3)
    verdict = ("ADOPT " + str(list(refs["selected"]))) if adopt else "KEEP prod4"

    print(f"\n  selected vs prod4 on TEST: {sel_pf} vs {prod_pf} "
          f"(Δ={sel_pf-prod_pf:+.3f}, wins {sel_wins}/{len(dfs)})")
    print(f"  → RECOMMENDATION: {verdict}")
    print(f"  ({time.time()-t0:.0f}s)")

    out = {
        "study": "H015_engine_subset_search",
        "method": "greedy bidirectional from prod4 on TRAIN; validate on TEST",
        "params": {"symbols": list(dfs), "bars": args.bars,
                   "train_frac": args.train_frac, "step": args.step,
                   "improve_eps": IMPROVE_EPS, "adopt_margin": ADOPT_MARGIN},
        "train_trail": trail,
        "test_scores": test_scores,
        "recommendation": verdict,
        "adopt": adopt,
    }
    outp = Path(args.output)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=2))
    print(f"  written: {outp}")

    try:
        from research.manifest import build_manifest, write_manifest
        m = build_manifest(kind="engine_subset_search", config=base_cfg,
                           params=out["params"], datasets=[],
                           results={"train_trail": trail, "test_scores": test_scores,
                                    "recommendation": verdict, "adopt": adopt})
        mp = write_manifest(m, f"engine_subset_search_{time.strftime('%Y%m%d')}")
        print(f"  manifest: {mp}")
    except Exception as exc:
        print(f"  manifest skipped: {exc}")


if __name__ == "__main__":
    main()
