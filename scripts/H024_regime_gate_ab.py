#!/usr/bin/env python3
"""
scripts/H024_regime_gate_ab.py
--------------------------------
H024 — Hard regime gate vs. current soft regime-weighting (pre-registered).

Two-arm A/B over the FROZEN prod4 pipeline on identical bars, differing ONLY in
one flag:
    arm A (control):   features.regime_gate = False  (current always-on system)
    arm B (treatment): features.regime_gate = True    (emit NO_TRADE on RANGING)

Everything else — engines, thresholds, entries, exits, stops, targets — is
byte-identical between arms, so any ΔPF is attributable solely to the gate.

Method (matches the house standard used by full_pipeline_backtest + the
walk_forward runs): calls the ACTUAL run_pipeline() at each bar, backtest_mode=True
(skips live D1 persistence), two-pass no-overlap trade simulation, chronological
TRAIN(65%)/TEST(35%) split per symbol. Verdict is computed on the pooled TEST
slice against the pre-registered decision rule in
research/hypotheses/H024_regime_gate.md — DO NOT edit the thresholds here to make
a result pass; that would violate CLAUDE.md rule 1.

Usage:
    python3 scripts/H024_regime_gate_ab.py --all --step 8
    python3 scripts/H024_regime_gate_ab.py --symbols XAUUSD BTCUSD USDCAD

This is measurement only. The gate ships OFF (features.regime_gate default false)
and stays frozen until the forward-demo milestone (CLAUDE.md rule 6).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Reuse the house machinery verbatim — no reimplementation, no divergence.
from scripts.full_pipeline_backtest import (
    ACTIVE_SYMBOLS,
    build_config,
    simulate_trade,
    calc_pnl,
)
from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT

# Pre-registered constants (H024_regime_gate.md). Changing these is changing the
# hypothesis — don't, mid-sample.
CARRIERS = {"XAUUSD", "BTCUSD", "ETHUSD"}
BLOCKED_REGIMES = ["RANGING"]        # arm B blocks these (trade only TRENDING)
TRAIN_FRAC = 0.65
MIN_POOLED_A_TEST_TRADES = 300
DECISION = {
    "min_pooled_dPF": 0.15,          # (1) beat control by a real margin
    "min_volume_retention": 0.50,    # (2) collapse guard
    "min_symbol_win_frac": 0.60,     # (3) H015 cherry-pick guard
    "max_carrier_degradation": 0.05, # (4) carriers not degraded
}


def _pf(trades: list[dict]) -> float:
    """Pooled profit factor over a trade list ({'pnl': float, ...})."""
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.0
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.0
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def run_arm(symbol: str, df, regime_gate: bool, step: int = 8, warmup: int = 220) -> list[dict]:
    """One full pass of the frozen pipeline over `df`, with the H024 gate on/off.

    Returns the list of taken trades, each tagged with its entry bar index `i`
    (for the chronological split) and the regime state at entry (diagnostics).
    Mirrors full_pipeline_backtest.backtest_symbol exactly except for the flag
    and the per-trade bookkeeping.
    """
    from main import run_pipeline

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)

    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True
    cfg.setdefault("features", {})
    cfg["features"]["regime_gate"] = regime_gate
    cfg["features"]["regime_gate_block"] = BLOCKED_REGIMES

    balance = 10_000.0
    trades: list[dict] = []
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

        if report.get("final_verdict") != "EXECUTE":
            continue

        entry = report.get("entry_price")
        sl = report.get("stop_loss")
        tp = report.get("take_profit")
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
        trades.append({
            "i": i,
            "outcome": sim["outcome"],
            "pnl": pnl,
            "regime": report.get("regime", {}).get("state", "UNKNOWN"),
        })
    return trades


def _split_test(trades: list[dict], split_idx: int) -> list[dict]:
    """Chronological TEST slice: trades whose entry bar is at/after split_idx."""
    return [t for t in trades if t["i"] >= split_idx]


def backtest_symbol_ab(symbol: str, df, step: int = 8, warmup: int = 220) -> dict:
    """Run both arms on the same bars and return TEST-slice A/B stats."""
    n = len(df)
    split_idx = warmup + int((n - warmup) * TRAIN_FRAC)

    trades_a = run_arm(symbol, df, regime_gate=False, step=step, warmup=warmup)
    trades_b = run_arm(symbol, df, regime_gate=True, step=step, warmup=warmup)

    test_a = _split_test(trades_a, split_idx)
    test_b = _split_test(trades_b, split_idx)

    return {
        "symbol": symbol,
        "asset_class": ASSET_CLASS.get(symbol, "forex"),
        "split_idx": split_idx,
        "test_trades_A": len(test_a),
        "test_trades_B": len(test_b),
        "test_PF_A": round(_pf(test_a), 3),
        "test_PF_B": round(_pf(test_b), 3),
        "test_WR_A": round(100 * sum(t["outcome"] == "win" for t in test_a) / len(test_a), 1) if test_a else None,
        "test_WR_B": round(100 * sum(t["outcome"] == "win" for t in test_b) / len(test_b), 1) if test_b else None,
        "_test_a": test_a,   # kept for pooling; stripped before JSON write
        "_test_b": test_b,
    }


def evaluate_decision(rows: list[dict]) -> dict:
    """Apply the pre-registered H024 falsification rule to the pooled TEST slice."""
    valid = [r for r in rows if r.get("test_trades_A", 0) >= 10]

    pooled_a = [t for r in valid for t in r["_test_a"]]
    pooled_b = [t for r in valid for t in r["_test_b"]]
    pf_a, pf_b = _pf(pooled_a), _pf(pooled_b)
    dpf = pf_b - pf_a

    retention = (len(pooled_b) / len(pooled_a)) if pooled_a else 0.0

    # per-symbol: fraction where B strictly beats A (among symbols with signal)
    improved = [r for r in valid if r["test_PF_B"] > r["test_PF_A"]]
    symbol_win_frac = (len(improved) / len(valid)) if valid else 0.0

    car = [r for r in valid if r["symbol"] in CARRIERS]
    car_a = [t for r in car for t in r["_test_a"]]
    car_b = [t for r in car for t in r["_test_b"]]
    car_pf_a, car_pf_b = _pf(car_a), _pf(car_b)

    checks = {
        "1_pooled_dPF>=0.15": dpf >= DECISION["min_pooled_dPF"],
        "2_volume_retention>=0.50": retention >= DECISION["min_volume_retention"],
        "3_symbol_win_frac>=0.60": symbol_win_frac >= DECISION["min_symbol_win_frac"],
        "4_carriers_not_degraded": car_pf_b >= car_pf_a - DECISION["max_carrier_degradation"],
        "n_guard_pooled_A>=300": len(pooled_a) >= MIN_POOLED_A_TEST_TRADES,
    }
    adopt = all(checks.values())
    null_result = (abs(dpf) < DECISION["min_pooled_dPF"]) and (retention >= DECISION["min_volume_retention"])

    if adopt:
        verdict = "ADOPT (arm B — hard regime gate)"
    elif null_result:
        verdict = "NULL (gate immaterial; soft weighting keeps edge)"
    else:
        verdict = "FAILED / NO CHANGE (soft weighting stays)"

    return {
        "verdict": verdict,
        "pooled_test_PF_A": round(pf_a, 3),
        "pooled_test_PF_B": round(pf_b, 3),
        "pooled_dPF": round(dpf, 3),
        "pooled_test_trades_A": len(pooled_a),
        "pooled_test_trades_B": len(pooled_b),
        "volume_retention": round(retention, 3),
        "symbol_win_frac": round(symbol_win_frac, 3),
        "symbols_improved": [r["symbol"] for r in improved],
        "carriers_test_PF_A": round(car_pf_a, 3),
        "carriers_test_PF_B": round(car_pf_b, 3),
        "checks": checks,
        "note": "Verdict is on the pooled chronological TEST slice. Pre-registered "
                "rule — not to be edited to force a pass (CLAUDE.md rule 1). "
                "Regardless of verdict this is measurement only; gate stays OFF "
                "and frozen until the forward-demo milestone (rule 6).",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="H024 regime-gate A/B (pre-registered)")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=8)
    args = parser.parse_args()

    symbols = ACTIVE_SYMBOLS if (args.all or not args.symbols) else args.symbols

    from core.data_loader import load_from_csv
    data_dir = Path("data")

    print("=" * 72)
    print("H024 — Hard regime gate A/B (arm A: gate OFF | arm B: block RANGING)")
    print(f"Symbols: {len(symbols)} | Step: {args.step} | TRAIN/TEST {int(TRAIN_FRAC*100)}/{int((1-TRAIN_FRAC)*100)}")
    print("=" * 72)

    rows: list[dict] = []
    t0 = time.monotonic()
    for idx, sym in enumerate(symbols, 1):
        csv = next((data_dir / f"{sym}_H1_{s}.csv"
                    for s in ["2y", "5y"] if (data_dir / f"{sym}_H1_{s}.csv").exists()), None)
        print(f"[{idx:2}/{len(symbols)}] {sym:10} ... ", end="", flush=True)
        if not csv:
            print("❌ no CSV")
            continue
        try:
            df = load_from_csv(str(csv))
            r = backtest_symbol_ab(sym, df, step=args.step)
            rows.append(r)
            print(f"A: n={r['test_trades_A']:>3} PF={r['test_PF_A']:.2f}  |  "
                  f"B: n={r['test_trades_B']:>3} PF={r['test_PF_B']:.2f}  "
                  f"({time.monotonic()-t0:.0f}s)")
        except Exception as e:  # noqa: BLE001
            print(f"❌ {str(e)[:60]}")

    decision = evaluate_decision(rows)

    print("\n" + "=" * 72)
    print("H024 DECISION (pooled TEST slice)")
    print("=" * 72)
    print(f"  arm A pooled TEST PF: {decision['pooled_test_PF_A']}  (n={decision['pooled_test_trades_A']})")
    print(f"  arm B pooled TEST PF: {decision['pooled_test_PF_B']}  (n={decision['pooled_test_trades_B']})")
    print(f"  ΔPF: {decision['pooled_dPF']}  | volume retained: {decision['volume_retention']}")
    print(f"  symbols where B>A: {decision['symbol_win_frac']} {decision['symbols_improved']}")
    print(f"  carriers PF  A={decision['carriers_test_PF_A']}  B={decision['carriers_test_PF_B']}")
    for k, v in decision["checks"].items():
        print(f"    [{'PASS' if v else 'FAIL'}] {k}")
    print(f"\n  VERDICT: {decision['verdict']}")

    # Strip the heavy per-trade lists before persisting the manifest-friendly JSON.
    for r in rows:
        r.pop("_test_a", None)
        r.pop("_test_b", None)

    out = {
        "hypothesis": "H024",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "arms": {"A": "regime_gate=False (control)", "B": f"regime_gate=True, block={BLOCKED_REGIMES}"},
        "train_frac": TRAIN_FRAC,
        "decision_rule": DECISION,
        "per_symbol": rows,
        "decision": decision,
        "duration_sec": round(time.monotonic() - t0, 1),
    }
    p = Path("research/results/H024_regime_gate_ab.json")
    p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {p}")
    print("Next: freeze via scripts/revive_manifests.py from a clean tree, then "
          "record the verdict in registry.json (H024) + the evidence ledger.")


if __name__ == "__main__":
    main()
