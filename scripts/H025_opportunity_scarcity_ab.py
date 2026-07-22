#!/usr/bin/env python3
"""
scripts/H025_opportunity_scarcity_ab.py
----------------------------------------
H025 — Opportunity Scarcity Engine (statistical rarity as edge). Pre-registered.

Tests whether filtering the frozen system's EXECUTE signals to their RAREST
state-contexts improves out-of-sample PF, with a MONOTONIC dose-response
(rarer ⇒ better) — the honest version of "the rarer the opportunity, the higher
its quality."

Design (one pass, overlay derived post-hoc):
  * At each bar the ACTUAL run_pipeline() runs (frozen prod4, backtest_mode).
  * Every decision (EXECUTE and NO_TRADE) contributes its pre-declared STATE
    TUPLE to a causal frequency base.
  * For each taken EXECUTE trade, rarity = 1 − freq(its tuple among all decisions
    in the trailing 180 calendar days ending strictly before it). No look-ahead.
  * Arm A = all trades. Arm B(p) = keep only trades whose rarity is in the rarest
    p% — the p-cutoff is fit on the TRAIN slice's rarity distribution and applied
    to TEST (no TEST peeking). p ∈ {20, 10, 5, 2}.

The overlay only SUPPRESSES trades (no new entries/exits), so ranking arm-A's
real opportunities by rarity is the faithful, cheap test.

Verdict is the pre-registered rule in research/hypotheses/H025_opportunity_scarcity.md
— dose-response + a hard OOS sample guard (n≥200) that disqualifies the small-n
mirage. Do NOT tune these constants to force a pass (CLAUDE.md rule 1).

Usage:
    python3 scripts/H025_opportunity_scarcity_ab.py --all --step 8

Measurement only; ships behind features.opportunity_scarcity (default false),
frozen until the forward-demo milestone (rule 6).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.full_pipeline_backtest import (
    ACTIVE_SYMBOLS,
    build_config,
    simulate_trade,
    calc_pnl,
)
from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT

# Pre-registered constants (H025_opportunity_scarcity.md). Do not tune.
CARRIERS = {"XAUUSD", "BTCUSD", "ETHUSD"}
RARITY_WINDOW_DAYS = 180
RARITY_GRID = [20, 10, 5, 2]          # keep the rarest p% (fit on TRAIN)
TRAIN_FRAC = 0.65
MIN_BUCKET_TEST_N = 200               # anti-mirage sample guard
DOSE_TOLERANCE = 0.02
MIN_ADOPT_MARGIN = 0.15
MIN_SYMBOL_WIN_FRAC = 0.60
MAX_CARRIER_DEGRADATION = 0.05
SCORE_BIN = 5


def _state_tuple(report: dict) -> tuple:
    """Pre-declared discrete state tuple, built from fields the pipeline emits."""
    regime = report.get("regime", {})
    conf = report.get("confluence", {})
    score = conf.get("score", 0) or 0
    score_bucket = int(score // SCORE_BIN) * SCORE_BIN
    mtf = conf.get("mtf", {})
    session = report.get("market_quality", {}).get("session") \
        or report.get("session") or "?"
    return (
        regime.get("state", "?"),
        regime.get("volatility", "?"),
        score_bucket,
        bool(mtf.get("confirming", False)),
        session,
        conf.get("vote", {}).get("winning_bias", "?"),
    )


def _pf(trades: list[dict]) -> float:
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.0
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.0
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def _expectancy(trades: list[dict]) -> float:
    return (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0


def _percentile_cutoff(values: list[float], p: int) -> float:
    """Rarity value at the (100−p)th percentile — the threshold for 'rarest p%'."""
    if not values:
        return float("inf")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((100 - p) / 100.0 * (len(s) - 1)))))
    return s[k]


def scan_symbol(symbol: str, df, step: int = 8, warmup: int = 220) -> dict:
    """Single frozen-pipeline pass: record decisions (for the frequency base) and
    taken trades tagged with entry bar, timestamp, state tuple, pnl, outcome."""
    from main import run_pipeline

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)

    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True

    idx = df.index
    use_time = isinstance(idx, pd.DatetimeIndex)

    decisions: list[dict] = []   # {ts, tuple} for EVERY decision
    trades: list[dict] = []
    balance = 10_000.0
    open_until = -1
    n = len(df)

    for i in range(warmup, n - 2, step):
        if i <= open_until:
            continue
        cfg["data"]["_injected_df"] = df.iloc[: i + 1]
        try:
            report = run_pipeline(cfg)
        except Exception:
            continue
        finally:
            cfg["data"]["_injected_df"] = None

        ts = idx[i] if use_time else i
        tup = _state_tuple(report)
        decisions.append({"ts": ts, "tuple": tup})

        if report.get("final_verdict") != "EXECUTE":
            continue
        entry, sl, tp = report.get("entry_price"), report.get("stop_loss"), report.get("take_profit")
        if not all([entry, sl, tp]):
            continue
        entry, sl, tp = float(entry), float(sl), float(tp)
        direction = 1 if sl < entry else -1
        sl_dist = abs(entry - sl)
        if sl_dist <= 0 or sl_dist > abs(tp - entry):
            continue
        sim = simulate_trade(entry, sl, tp, direction, df, i + 1)
        pnl = calc_pnl(entry, sim["exit"], direction, sl_dist, balance, symbol, ac, pip, dpp)
        balance += pnl
        open_until = i + sim["bars"]
        trades.append({"i": i, "ts": ts, "tuple": tup, "pnl": pnl, "outcome": sim["outcome"]})

    # Causal rarity per trade: freq of its tuple among decisions in the trailing
    # window ending strictly before the trade.
    window = timedelta(days=RARITY_WINDOW_DAYS) if use_time else int(RARITY_WINDOW_DAYS * 6)
    for t in trades:
        ts0 = t["ts"]
        if use_time:
            lo = ts0 - window
            win = [d for d in decisions if lo <= d["ts"] < ts0]
        else:
            lo = ts0 - window
            win = [d for d in decisions if lo <= d["ts"] < ts0]
        if not win:
            t["rarity"] = None
            continue
        same = sum(1 for d in win if d["tuple"] == t["tuple"])
        t["rarity"] = 1.0 - same / len(win)

    split_idx = warmup + int((n - warmup) * TRAIN_FRAC)
    return {"symbol": symbol, "ac": ac, "split_idx": split_idx, "trades": trades}


def build_arms(scans: list[dict]) -> dict:
    """From per-symbol scans, build arm A and arm B(p) TEST sets + per-symbol PF."""
    per_symbol: dict = {}
    pooled = {"A": []}
    pooled.update({f"B{p}": [] for p in RARITY_GRID})
    carriers = {"A": []}
    carriers.update({f"B{p}": [] for p in RARITY_GRID})

    for s in scans:
        sym, split_idx = s["symbol"], s["split_idx"]
        is_carrier = sym in CARRIERS
        rated = [t for t in s["trades"] if t.get("rarity") is not None]
        train = [t for t in rated if t["i"] < split_idx]
        test = [t for t in rated if t["i"] >= split_idx]
        cutoffs = {p: _percentile_cutoff([t["rarity"] for t in train], p) for p in RARITY_GRID}

        row = {"symbol": sym, "test_n_A": len(test), "test_PF_A": round(_pf(test), 3)}
        pooled["A"].extend(test)
        if is_carrier:
            carriers["A"].extend(test)
        for p in RARITY_GRID:
            bset = [t for t in test if t["rarity"] >= cutoffs[p]]
            row[f"test_n_B{p}"] = len(bset)
            row[f"test_PF_B{p}"] = round(_pf(bset), 3)
            pooled[f"B{p}"].extend(bset)
            if is_carrier:
                carriers[f"B{p}"].extend(bset)
        per_symbol[sym] = row

    return {"per_symbol": per_symbol, "pooled": pooled, "carriers": carriers}


def evaluate_decision(arms: dict) -> dict:
    pooled = arms["pooled"]
    per_symbol = arms["per_symbol"]
    pf_a = _pf(pooled["A"])
    exp_a = _expectancy(pooled["A"])

    buckets = {}
    for p in RARITY_GRID:
        b = pooled[f"B{p}"]
        buckets[p] = {"n": len(b), "PF": round(_pf(b), 3), "expectancy": round(_expectancy(b), 4)}

    # dose-response: PF non-decreasing as p tightens (20->10->5->2)
    pf_seq = [buckets[p]["PF"] for p in RARITY_GRID]
    monotone = all(pf_seq[k + 1] >= pf_seq[k] - DOSE_TOLERANCE for k in range(len(pf_seq) - 1))

    # tightest sample-valid bucket carries the adopt decision
    valid_buckets = [p for p in RARITY_GRID if buckets[p]["n"] >= MIN_BUCKET_TEST_N]
    adopt_p = min(valid_buckets) if valid_buckets else None   # min p == rarest that still has n

    checks = {}
    if adopt_p is None:
        verdict = "INCONCLUSIVE (no rarity bucket reaches n>=200 on TEST — untestable at these thresholds)"
        adopt = False
    else:
        b = buckets[adopt_p]
        improved = [r for r in per_symbol.values()
                    if r.get(f"test_n_B{adopt_p}", 0) >= 15 and r.get(f"test_PF_B{adopt_p}", 0) > r["test_PF_A"]]
        eligible = [r for r in per_symbol.values() if r.get(f"test_n_B{adopt_p}", 0) >= 15]
        sym_win_frac = (len(improved) / len(eligible)) if eligible else 0.0
        # carriers not degraded at the adopt bucket
        car = arms["carriers"]
        car_pf_a = _pf(car["A"])
        car_pf_b = _pf(car[f"B{adopt_p}"])
        checks = {
            "1_dose_response_monotone": monotone,
            f"1b_tightest_valid(P{adopt_p})_PF-PFA>=0.15": b["PF"] - pf_a >= MIN_ADOPT_MARGIN,
            f"2_sample_guard_n>=200 (n={b['n']})": b["n"] >= MIN_BUCKET_TEST_N,
            "3_expectancy_B>A": b["expectancy"] > exp_a,
            f"4_symbol_win_frac>=0.60 ({sym_win_frac:.2f})": sym_win_frac >= MIN_SYMBOL_WIN_FRAC,
            f"5_carriers_not_degraded (A={car_pf_a:.2f} B={car_pf_b:.2f})": car_pf_b >= car_pf_a - MAX_CARRIER_DEGRADATION,
        }
        adopt = all(checks.values())
        if adopt:
            verdict = f"ADOPT (OSE overlay, rarest {adopt_p}%)"
        elif monotone and (b["PF"] - pf_a) >= MIN_ADOPT_MARGIN:
            verdict = "PARTIAL — dose-response present but a guard failed; NO CHANGE, re-examine"
        elif abs(b["PF"] - pf_a) < MIN_ADOPT_MARGIN:
            verdict = "NULL (statistical rarity immaterial; no dose-response edge)"
        else:
            verdict = "FAILED / NO CHANGE"

    return {
        "verdict": verdict,
        "pooled_PF_A": round(pf_a, 3),
        "pooled_expectancy_A": round(exp_a, 4),
        "buckets": buckets,
        "pf_sequence_20_10_5_2": pf_seq,
        "dose_response_monotone": monotone,
        "adopt_bucket_p": adopt_p,
        "checks": checks,
        "note": "Pre-registered rule (H025). Not to be edited to force a pass "
                "(rule 1). Measurement only; overlay stays OFF/frozen (rule 6).",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="H025 opportunity-scarcity A/B (pre-registered)")
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--step", type=int, default=8)
    args = ap.parse_args()
    symbols = ACTIVE_SYMBOLS if (args.all or not args.symbols) else args.symbols

    from core.data_loader import load_from_csv
    data_dir = Path("data")

    print("=" * 72)
    print("H025 — Opportunity Scarcity Engine A/B (rarity grid 20/10/5/2%)")
    print(f"Symbols: {len(symbols)} | Step: {args.step} | window: {RARITY_WINDOW_DAYS}d")
    print("=" * 72)

    scans, t0 = [], time.monotonic()
    for i, sym in enumerate(symbols, 1):
        csv = next((data_dir / f"{sym}_H1_{s}.csv"
                    for s in ["2y", "5y"] if (data_dir / f"{sym}_H1_{s}.csv").exists()), None)
        print(f"[{i:2}/{len(symbols)}] {sym:10} ... ", end="", flush=True)
        if not csv:
            print("❌ no CSV"); continue
        try:
            df = load_from_csv(str(csv))
            s = scan_symbol(sym, df, step=args.step)
            scans.append(s)
            rated = [t for t in s["trades"] if t.get("rarity") is not None]
            print(f"{len(rated):>3} rated trades ({time.monotonic()-t0:.0f}s)")
        except Exception as e:  # noqa: BLE001
            print(f"❌ {str(e)[:60]}")

    arms = build_arms(scans)
    decision = evaluate_decision(arms)

    print("\n" + "=" * 72)
    print("H025 DECISION (pooled TEST slice)")
    print("=" * 72)
    print(f"  arm A pooled TEST PF: {decision['pooled_PF_A']}  (expectancy {decision['pooled_expectancy_A']})")
    print(f"  rarity buckets (PF @ n):")
    for p in RARITY_GRID:
        b = decision["buckets"][p]
        print(f"    rarest {p:>2}% : PF={b['PF']:.3f}  n={b['n']:>4}  exp={b['expectancy']}")
    print(f"  dose-response monotone (20→2%): {decision['dose_response_monotone']}  seq={decision['pf_sequence_20_10_5_2']}")
    for k, v in decision["checks"].items():
        print(f"    [{'PASS' if v else 'FAIL'}] {k}")
    print(f"\n  VERDICT: {decision['verdict']}")

    out = {
        "hypothesis": "H025",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rarity_window_days": RARITY_WINDOW_DAYS,
        "rarity_grid_pct": RARITY_GRID,
        "train_frac": TRAIN_FRAC,
        "per_symbol": arms["per_symbol"],
        "decision": decision,
        "duration_sec": round(time.monotonic() - t0, 1),
    }
    p = Path("research/results/H025_opportunity_scarcity_ab.json")
    p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {p}")
    print("Next: freeze via scripts/revive_manifests.py, then record H025 verdict "
          "in registry.json + the evidence ledger (same care as any result).")


if __name__ == "__main__":
    main()
