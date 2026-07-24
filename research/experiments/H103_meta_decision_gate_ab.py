#!/usr/bin/env python3
"""
research/experiments/H103_meta_decision_gate_ab.py
------------------------------------------------------
H103 — Does confluence/meta_decision.py's live confidence-gate (BLOCK when
confidence < 40, downgrades EXECUTE -> NO_TRADE) improve OOS PF, or does
it just double-count already-gated information and cost good trades for
free? (research/hypotheses/H103_meta_decision_gate_removal.md,
research/results/registry.json). The DECISION RULE pre-exists this code
(CLAUDE.md rule 1); this runner only APPLIES it.

Design (frozen at registration, matches the H024/H033 harness pattern):
  Two arms, same bars, FROZEN prod4 pipeline, differing ONLY in one flag
  (main.py's features.meta_decision_gate, wired 2026-07-24):
    arm A (control):   meta_decision_gate = True  (current live behavior —
                        a BLOCK verdict downgrades EXECUTE -> NO_TRADE)
    arm B (treatment):  meta_decision_gate = False (meta is still computed
                        and logged for comparison; BLOCK never downgrades)
  Entries/exits/thresholds/every other gate are byte-identical between
  arms, so any dPF is attributable solely to this one gate — same
  discipline as H017/H024/H037.

  Chronological TRAIN(65%)/TEST(35%) split (H008c standard). Verdict on
  the pooled TEST slice against the SYMMETRIC decision rule (this gate is
  already live — the null result is "keep current behavior," not "do
  nothing," unlike a promotion-style ADOPT-only test):
    REMOVE the gate only if ALL hold: (1) PF(B) >= PF(A) - 0.03; (2)
    carriers-only PF(B) >= PF(A) - 0.03; (3) trade count(B) > trade
    count(A) (confirms the gate was actually blocking something).
    KEEP the gate (FAILED) if PF(B) drops by more than 0.03 pooled or on
    carriers specifically. |dPF| < 0.03 with no material n difference =
    NULL (keep as-is, not worth the governance churn). Minimum: pooled
    arm-A TEST n >= 300.

Usage (VPS):
    venv/bin/python -m research.experiments.H103_meta_decision_gate_ab --all --step 8
    venv/bin/python -m research.experiments.H103_meta_decision_gate_ab --symbols XAUUSD BTCUSD

Measurement only, on the backtest harness. main.py's live gate stays
default-True (today's actual behavior) regardless of how long this takes
to run — only a promoted ADOPT verdict, applied deliberately and
separately, would ever flip the live default (CLAUDE.md rule 6).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Reuse the house machinery verbatim — no reimplementation, no divergence.
from scripts.full_pipeline_backtest import (
    ACTIVE_SYMBOLS,
    build_config,
    simulate_trade,
    calc_pnl,
)
from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT

# Pre-registered constants (H103_meta_decision_gate_removal.md). Changing
# these is changing the hypothesis — don't, mid-sample.
CARRIERS = {"XAUUSD", "BTCUSD", "ETHUSD"}
TRAIN_FRAC = 0.65
MIN_POOLED_A_TEST_TRADES = 300
DECISION = {
    "max_pf_degradation": 0.03,   # PF(B) must not drop by more than this
    "max_carrier_degradation": 0.03,
}


def _pf(trades: list[dict]) -> float:
    """Pooled profit factor over a trade list ({'pnl': float, ...})."""
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.0
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.0
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def run_arm(symbol: str, df, meta_decision_gate: bool, step: int = 8, warmup: int = 220) -> list[dict]:
    """One full pass of the frozen pipeline over `df`, with H103's gate
    flag on/off. Mirrors H024's run_arm exactly except for the flag."""
    from main import run_pipeline

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)

    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True
    cfg.setdefault("features", {})
    cfg["features"]["meta_decision_gate"] = meta_decision_gate

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
        trades.append({"i": i, "outcome": sim["outcome"], "pnl": pnl})
    return trades


def _split_test(trades: list[dict], split_idx: int) -> list[dict]:
    """Chronological TEST slice: trades whose entry bar is at/after split_idx."""
    return [t for t in trades if t["i"] >= split_idx]


def backtest_symbol_ab(symbol: str, df, step: int = 8, warmup: int = 220) -> dict:
    """Run both arms on the same bars and return TEST-slice A/B stats."""
    n = len(df)
    split_idx = warmup + int((n - warmup) * TRAIN_FRAC)

    trades_a = run_arm(symbol, df, meta_decision_gate=True, step=step, warmup=warmup)
    trades_b = run_arm(symbol, df, meta_decision_gate=False, step=step, warmup=warmup)

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
        "_test_a": test_a,   # kept for pooling; stripped before JSON write
        "_test_b": test_b,
    }


def evaluate_decision(rows: list[dict]) -> dict:
    """Apply the pre-registered H103 SYMMETRIC rule to the pooled TEST
    slice. This gate is already live — the default outcome is KEEP, not
    a neutral 'no action'; removal is the one-sided change that needs a
    passing case, not merely the absence of a failing one."""
    valid = [r for r in rows if r.get("test_trades_A", 0) >= 5]

    pooled_a = [t for r in valid for t in r["_test_a"]]
    pooled_b = [t for r in valid for t in r["_test_b"]]

    if len(pooled_a) < MIN_POOLED_A_TEST_TRADES:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "pooled_test_trades_A": len(pooled_a),
            "note": f"pooled arm-A TEST n={len(pooled_a)} < required "
                    f"{MIN_POOLED_A_TEST_TRADES} — no verdict.",
        }

    pf_a, pf_b = _pf(pooled_a), _pf(pooled_b)
    dpf = pf_b - pf_a

    car = [r for r in valid if r["symbol"] in CARRIERS]
    car_a = [t for r in car for t in r["_test_a"]]
    car_b = [t for r in car for t in r["_test_b"]]
    car_pf_a, car_pf_b = _pf(car_a), _pf(car_b)
    car_dpf = car_pf_b - car_pf_a

    checks = {
        "1_pooled_PF_not_worse_than_0.03": dpf >= -DECISION["max_pf_degradation"],
        "2_carriers_PF_not_worse_than_0.03": car_dpf >= -DECISION["max_carrier_degradation"],
        "3_trade_count_increased": len(pooled_b) > len(pooled_a),
    }
    adopt = all(checks.values())
    degraded = (dpf < -DECISION["max_pf_degradation"]) or (car_dpf < -DECISION["max_carrier_degradation"])
    material_n_increase = len(pooled_b) > len(pooled_a)
    null_result = (abs(dpf) < DECISION["max_pf_degradation"]) and not material_n_increase and not degraded

    if adopt:
        verdict = "ADOPT (remove the meta_decision gate)"
    elif degraded:
        verdict = "FAILED (keep the gate — it is doing real filtering)"
    elif null_result:
        verdict = "NULL (keep as-is; gate is inert-but-harmless, not worth the governance churn)"
    else:
        verdict = "FAILED (keep the gate — default when no removal case passes)"

    return {
        "verdict": verdict,
        "pooled_test_PF_A": round(pf_a, 3),
        "pooled_test_PF_B": round(pf_b, 3),
        "pooled_dPF": round(dpf, 3),
        "pooled_test_trades_A": len(pooled_a),
        "pooled_test_trades_B": len(pooled_b),
        "carriers_test_PF_A": round(car_pf_a, 3),
        "carriers_test_PF_B": round(car_pf_b, 3),
        "carriers_dPF": round(car_dpf, 3),
        "checks": checks,
        "note": "Verdict is on the pooled chronological TEST slice. Pre-registered "
                "rule (research/hypotheses/H103_meta_decision_gate_removal.md) — "
                "not to be edited to force a pass (CLAUDE.md rule 1). This gate is "
                "already live: the default outcome is KEEP unless ADOPT's three "
                "conditions all pass. Regardless of verdict this is measurement "
                "only; main.py's meta_decision_gate stays default TRUE (today's "
                "live behavior) until a promoted ADOPT is applied deliberately "
                "(CLAUDE.md rule 6).",
    }


def _discover_csv(symbol: str, data_dir: Path) -> Path | None:
    return next((data_dir / f"{symbol}_H1_{s}.csv"
                 for s in ["2y", "5y"] if (data_dir / f"{symbol}_H1_{s}.csv").exists()), None)


def write_h103_manifest(result: dict, data_dir: Path) -> Path:
    """Reproducibility manifest (research/manifest.py house format)."""
    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    datasets = []
    for row in result.get("per_symbol", []):
        csv = _discover_csv(row["symbol"], data_dir)
        if csv:
            datasets.append(dataset_fingerprint(csv))
    manifest = build_manifest(
        kind="h103_meta_decision_gate_ab",
        config=load_config(),
        params={
            "hypothesis": "H103",
            "train_frac": result.get("train_frac", TRAIN_FRAC),
            "arms": result.get("arms"),
            "decision_rule": result.get("decision_rule", DECISION),
            "result_generated_at": result.get("generated_at"),
        },
        datasets=datasets,
        results={"decision": result.get("decision"), "per_symbol": result.get("per_symbol")},
    )
    return write_manifest(manifest, f"h103_meta_decision_gate_ab_{time.strftime('%Y%m%d')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="H103 meta_decision-gate A/B (pre-registered)")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=8)
    parser.add_argument("--manifest-only", action="store_true",
                        help="write the reproducibility manifest for the existing "
                             "result JSON without re-running the A/B")
    args = parser.parse_args()

    if args.manifest_only:
        rp = Path("research/results/H103_meta_decision_gate_ab.json")
        if not rp.exists():
            raise SystemExit(f"{rp} not found — nothing to backfill")
        result = json.loads(rp.read_text())
        out = write_h103_manifest(result, Path("data"))
        print(f"Manifest (backfilled from existing result, no re-run): {out}")
        return

    symbols = ACTIVE_SYMBOLS if (args.all or not args.symbols) else args.symbols

    from core.data_loader import load_from_csv
    data_dir = Path("data")

    print("=" * 76)
    print("H103 — meta_decision gate A/B (arm A: gate ON | arm B: gate OFF)")
    print(f"Symbols: {len(symbols)} | Step: {args.step} | TRAIN/TEST {int(TRAIN_FRAC*100)}/{int((1-TRAIN_FRAC)*100)}")
    print("=" * 76)

    rows: list[dict] = []
    t0 = time.monotonic()
    for idx, sym in enumerate(symbols, 1):
        csv = _discover_csv(sym, data_dir)
        print(f"[{idx:2}/{len(symbols)}] {sym:10} ... ", end="", flush=True)
        if not csv:
            print("no CSV")
            continue
        try:
            df = load_from_csv(str(csv))
            r = backtest_symbol_ab(sym, df, step=args.step)
            rows.append(r)
            print(f"A: n={r['test_trades_A']:>3} PF={r['test_PF_A']:.2f}  |  "
                  f"B: n={r['test_trades_B']:>3} PF={r['test_PF_B']:.2f}  "
                  f"({time.monotonic()-t0:.0f}s)")
        except Exception as e:  # noqa: BLE001
            print(f"FAILED: {str(e)[:60]}")

    decision = evaluate_decision(rows)

    print("\n" + "=" * 76)
    print("H103 DECISION (pooled TEST slice)")
    print("=" * 76)
    if decision["verdict"] == "INSUFFICIENT_DATA":
        print(f"  {decision['note']}")
    else:
        print(f"  arm A pooled TEST PF: {decision['pooled_test_PF_A']}  (n={decision['pooled_test_trades_A']})")
        print(f"  arm B pooled TEST PF: {decision['pooled_test_PF_B']}  (n={decision['pooled_test_trades_B']})")
        print(f"  dPF: {decision['pooled_dPF']}")
        print(f"  carriers PF  A={decision['carriers_test_PF_A']}  B={decision['carriers_test_PF_B']}  "
              f"dPF={decision['carriers_dPF']}")
        for k, v in decision["checks"].items():
            print(f"    [{'PASS' if v else 'FAIL'}] {k}")
    print(f"\n  VERDICT: {decision['verdict']}")

    # Strip the heavy per-trade lists before persisting the manifest-friendly JSON.
    for r in rows:
        r.pop("_test_a", None)
        r.pop("_test_b", None)

    out = {
        "hypothesis": "H103",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "arms": {"A": "meta_decision_gate=True (control, current live behavior)",
                 "B": "meta_decision_gate=False (meta computed+logged, never downgrades)"},
        "train_frac": TRAIN_FRAC,
        "decision_rule": DECISION,
        "per_symbol": rows,
        "decision": decision,
        "duration_sec": round(time.monotonic() - t0, 1),
    }
    p = Path("research/results/H103_meta_decision_gate_ab.json")
    p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {p}")
    mp = write_h103_manifest(out, data_dir)
    print(f"Manifest: {mp}")
    print("Next: record the verdict in registry.json (H103) + the evidence "
          "ledger; commit result + manifest from a clean tree (rule 4).")


if __name__ == "__main__":
    main()
