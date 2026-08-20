#!/usr/bin/env python3
"""
research/experiments/H102_price_action_confluence_ab.py
-------------------------------------------------------------
H102 — Price Action engine as a confluence input (governance closure;
pre-registered research/results/registry.json / research/hypotheses/
H102_price_action_confluence.md). The DECISION RULE pre-exists this code
(CLAUDE.md rule 1); this runner only APPLIES it.

Falsification criteria (registry H102, verbatim): "Reject only if a
dedicated marginal-contribution A/B under the current voting system
contradicts H015 - TEST-slice mean dPF from removing price_action is
non-negative with OOS-win-fraction >=60% favoring removal." Equivalently:
PASS (keep price_action) unless the price_action-included arm's pooled PF
is NOT positive-and-favoured — i.e. this uses the identical dPF/win-
fraction mechanics as H101, just interpreted in H102's own wording below.

Design: same two-arm, same-bars A/B as H101_smc_structural_bias_ab.py —
Arm A = engines.enabled.price_action True (prod4), Arm B = False
(prod4-minus-price_action). Full 20-symbol universe (H015: subset
selection is universe-dependent noise, so a narrower universe would not
answer the registered question).

Usage (VPS, once data/*.csv exists via scripts/download_all_symbols.py):
    venv/bin/python -m research.experiments.H102_price_action_confluence_ab
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
RESULT_PATH = PROJECT_ROOT / "research" / "results" / "H102_price_action_confluence_ab.json"

ENGINE = "price_action"

# Same full 20-symbol universe as H101 — see that file's comment on why
# scripts/full_pipeline_backtest.py's ACTIVE_SYMBOLS (19, missing GBPJPY)
# isn't used here.
SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF",
    "XAUUSD", "XAGUSD", "USOIL",
    "US30", "NAS100", "SPX500", "BTCUSD", "ETHUSD",
]

TRAIN_FRAC = 0.65
STEP = 8
WARMUP = 220
MIN_TRADES_PER_SYMBOL = 10
MIN_TOTAL_OOS_TRADES = 300  # PROMOTION_CRITERIA floor (research/edge_gate.py)

DECISION = {
    "max_removal_dPF": 0.0,   # removal-favoring dPF (PF_without - PF_with) must be < this to keep the engine
    "min_win_fraction": 0.60,  # matches H024/H017's adoption bar
}


def _pf(trades: list[dict]) -> float:
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.0
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.0
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def backtest_symbol_arm(
    symbol: str, df, engine_enabled: bool,
    step: int = STEP, warmup: int = WARMUP,
) -> list[dict]:
    """Identical mechanics to H101_smc_structural_bias_ab.py's arm runner —
    toggles engines.enabled.price_action instead of smc."""
    from main import run_pipeline
    from scripts.download_all_symbols import ASSET_CLASS, DOLLAR_PER_POINT, PIP_SIZE
    from scripts.full_pipeline_backtest import build_config, calc_pnl, simulate_trade

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)

    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True
    cfg.setdefault("engines", {}).setdefault("enabled", {})[ENGINE] = engine_enabled

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
        trades.append({"i": i, "outcome": sim["outcome"], "pnl": pnl})

    return trades


def price_action_verdict(
    pooled_removal_dpf: float, win_fraction_removal: float, pooled_a_n: int,
) -> tuple[str, dict, list[str]]:
    """Apply the pre-registered H102 rule LITERALLY. Pure + unit-testable.

    pooled_removal_dpf = PF_B(price_action off) - PF_A(price_action on).
    win_fraction_removal = fraction of valid symbols where removal helped
    (PF_B > PF_A). Reject (demote) only if BOTH favor removal."""
    if pooled_a_n < MIN_TOTAL_OOS_TRADES:
        return ("INSUFFICIENT_DATA", {},
                [f"pooled arm-A (price_action-included) TEST n={pooled_a_n} < {MIN_TOTAL_OOS_TRADES}"])

    checks = {
        "1_removal_dPF>=0": pooled_removal_dpf >= DECISION["max_removal_dPF"],
        "2_win_fraction_favoring_removal>=0.60": win_fraction_removal >= DECISION["min_win_fraction"],
    }
    if all(checks.values()):
        return ("FAILED (removing price_action does not hurt — demote toward disable)", checks,
                [f"removal_dPF={round(pooled_removal_dpf, 3)}, "
                 f"win_fraction_favoring_removal={round(win_fraction_removal, 3)}"])
    return ("PASSED (price_action contributes positive marginal value — stays enabled, matches H015)", checks,
            [f"removal_dPF={round(pooled_removal_dpf, 3)}, "
             f"win_fraction_favoring_removal={round(win_fraction_removal, 3)}"])


def main() -> int:
    from research.controlled_hypothesis_run import require_controlled_execution
    require_controlled_execution("H102")

    parser = argparse.ArgumentParser(description="H102 price_action confluence A/B (pre-registered)")
    parser.add_argument("--step", type=int, default=STEP)
    args = parser.parse_args()

    from core.data_loader import load_from_csv
    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    t0 = time.monotonic()

    def discover_price(sym: str) -> Path | None:
        return next((DATA_DIR / f"{sym}_H1_{s}.csv"
                     for s in ["2y", "5y"] if (DATA_DIR / f"{sym}_H1_{s}.csv").exists()), None)

    arm_a: dict[str, list[dict]] = {}
    arm_b: dict[str, list[dict]] = {}
    splits: dict[str, int] = {}
    csvs: list[str] = []

    print("=" * 72)
    print("H102 — price_action confluence A/B (prod4 vs. prod4-minus-price_action)")
    print(f"Symbols: {len(SYMBOLS)} ({', '.join(SYMBOLS)})")
    print("=" * 72)

    for sym in SYMBOLS:
        price_csv = discover_price(sym)
        if not price_csv:
            print(f"{sym}: no price CSV — skipped "
                  f"(expected {sym}_H1_2y.csv or _H1_5y.csv from scripts/download_all_symbols.py)")
            continue

        df = load_from_csv(str(price_csv))
        splits[sym] = WARMUP + int((len(df) - WARMUP) * TRAIN_FRAC)
        csvs.append(str(price_csv))

        print(f"{sym}: running arm A (price_action on)...")
        a = backtest_symbol_arm(sym, df, True, step=args.step)
        print(f"{sym}: running arm B (price_action off)...")
        b = backtest_symbol_arm(sym, df, False, step=args.step)
        arm_a[sym], arm_b[sym] = a, b
        print(f"{sym}: arm_A_trades={len(a)} arm_B_trades={len(b)} "
              f"({time.monotonic() - t0:.0f}s)")

    if not arm_a:
        print("\nNo symbols had price data — nothing to evaluate.")
        return 1

    def test_slice(trades: list[dict], sym: str) -> list[dict]:
        return [t for t in trades if t["i"] >= splits[sym]]

    per_symbol_report: dict[str, dict] = {}
    valid_syms: list[str] = []
    for sym in arm_a:
        ta, tb = test_slice(arm_a[sym], sym), test_slice(arm_b[sym], sym)
        pfa, pfb = _pf(ta), _pf(tb)
        per_symbol_report[sym] = {
            "test_n_A": len(ta), "test_PF_A": round(pfa, 3),
            "test_n_B": len(tb), "test_PF_B": round(pfb, 3),
            "removal_dPF": round(pfb - pfa, 3),
        }
        if len(ta) >= MIN_TRADES_PER_SYMBOL and len(tb) >= MIN_TRADES_PER_SYMBOL:
            valid_syms.append(sym)

    pooled_a = [t for s in arm_a for t in test_slice(arm_a[s], s)]
    pooled_b = [t for s in arm_a for t in test_slice(arm_b[s], s)]
    pooled_pf_a, pooled_pf_b = _pf(pooled_a), _pf(pooled_b)
    pooled_removal_dpf = pooled_pf_b - pooled_pf_a
    removal_helped = [s for s in valid_syms if per_symbol_report[s]["test_PF_B"] > per_symbol_report[s]["test_PF_A"]]
    win_fraction_removal = len(removal_helped) / len(valid_syms) if valid_syms else 0.0

    verdict, checks, reasons = price_action_verdict(pooled_removal_dpf, win_fraction_removal, len(pooled_a))

    print("\n" + "=" * 72)
    print("H102 DECISION (chronological TEST slice)")
    print("=" * 72)
    for sym, r in per_symbol_report.items():
        print(f"  {sym}: A(on) PF={r['test_PF_A']} n={r['test_n_A']} | "
              f"B(off) PF={r['test_PF_B']} n={r['test_n_B']} | removal_dPF={r['removal_dPF']}")
    print(f"  Pooled: PF_A(on)={round(pooled_pf_a, 3)} n={len(pooled_a)} | "
          f"PF_B(off)={round(pooled_pf_b, 3)} n={len(pooled_b)} | "
          f"removal_dPF={round(pooled_removal_dpf, 3)} | "
          f"win_fraction_favoring_removal={round(win_fraction_removal, 3)}")
    print(f"  VERDICT: {verdict}")
    for r in reasons:
        print(f"    - {r}")

    result = {
        "hypothesis": "H102",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train_frac": TRAIN_FRAC,
        "decision_rule": DECISION,
        "per_symbol": per_symbol_report,
        "pooled": {
            "PF_A_price_action_on": round(pooled_pf_a, 3), "n_A": len(pooled_a),
            "PF_B_price_action_off": round(pooled_pf_b, 3), "n_B": len(pooled_b),
            "removal_dPF": round(pooled_removal_dpf, 3),
            "win_fraction_favoring_removal": round(win_fraction_removal, 3),
        },
        "verdict": verdict,
        "verdict_reasons": reasons,
        "checks": checks,
        "note": "Verdict from the pre-registered rule (registry H102 / "
                "research/hypotheses/H102_price_action_confluence.md) applied "
                "literally. Measurement only (rule 6) — engines.enabled."
                "price_action stays True in live config regardless of this "
                "result until a separate, explicit promotion/demotion decision.",
    }
    RESULT_PATH.write_text(json.dumps(result, indent=2, default=str))
    print(f"Saved: {RESULT_PATH}")

    manifest = build_manifest(
        kind="h102_price_action_confluence_ab",
        config=load_config(),
        params={"hypothesis": "H102", "symbols": SYMBOLS, "train_frac": TRAIN_FRAC,
                "decision_rule": DECISION, "step": args.step, "warmup": WARMUP},
        datasets=[dataset_fingerprint(Path(c)) for c in csvs],
        results={"verdict": verdict, "pooled": result["pooled"]},
    )
    outp = write_manifest(manifest, f"h102_price_action_confluence_ab_{time.strftime('%Y%m%d')}")
    print(f"Manifest: {outp}")
    print("\nNext (HUMAN steps): record the verdict in registry.json (H102) + "
          "the evidence ledger; commit result + manifest from a clean tree "
          "(rule 4). Regardless of verdict nothing live changes (rule 6) "
          "until a separate promotion/demotion decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
