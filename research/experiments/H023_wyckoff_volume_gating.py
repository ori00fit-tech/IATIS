#!/usr/bin/env python3
"""
research/experiments/H023_wyckoff_volume_gating.py
-----------------------------------------------------
H023 — Wyckoff volume gating by asset class (pre-registered 2026-07-18,
registry + doc: research/hypotheses/H023_wyckoff_volume_gating.md). The
DECISION RULE pre-exists this code (CLAUDE.md rule 1); this runner only
APPLIES it.

Mechanism under test:
    engines/wyckoff_engine.py's _volume_analysis() gates its volume-based
    signals on `window["volume"].sum() == 0`. Under the old Twelve Data
    Free feed, FX bars had zero volume, so this gate tripped and FX ran
    the engine's INTENDED price-only Wyckoff. cTrader (now the primary FX
    provider) returns nonzero tick-volume, so the gate no longer trips —
    Wyckoff silently started running full volume-spread analysis on FX
    tick-volume, contradicting the engine's own docstring. This asks
    whether that silent behavior change helps, hurts, or is noise.

Design (frozen at registration):
    Arm A = current live behavior (FX bars keep their real tick-volume,
        Wyckoff's gate behaves exactly as it does today).
    Arm B = asset-class gate: FX bars have their `volume` column zeroed
        BEFORE the pipeline sees them, forcing Wyckoff back to price-only
        for FX regardless of what cTrader reports. This is a HARNESS-ONLY
        intervention (see `_prepare_df`) — engines/wyckoff_engine.py and
        the live pipeline are untouched, so there is zero live-path change
        regardless of outcome (rule 6).
    Metals (XAUUSD, XAGUSD) and crypto (BTCUSD, ETHUSD) are controls —
    their volume is never touched in either arm — re-run under both to
    confirm arm B does not disturb them (a bug would show up as controls
    moving, since nothing about them should differ between arms).
    Primary metric = pooled TEST-slice profit factor of the FULL pipeline
    across the 7 FX symbols (not Wyckoff's internal sub-score alone).

Usage (VPS):
    venv/bin/python -m research.experiments.H023_wyckoff_volume_gating --all
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
RESULT_PATH = PROJECT_ROOT / "research" / "results" / "H023_wyckoff_volume_gating.json"

# Pre-registered constants — changing any of these is a NEW hypothesis.
FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "EURJPY", "GBPJPY", "AUDJPY"]
CONTROL_SYMBOLS = ["XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD"]
ALL_SYMBOLS = FX_SYMBOLS + CONTROL_SYMBOLS
TRAIN_FRAC = 0.65
STEP = 8
WARMUP = 220
DECISION = {
    "min_pooled_fx_dPF": 0.10,
    "min_pooled_fx_test_n": 100,
    "min_symbol_sign_frac": 5 / 7,   # >= 5 of 7 FX symbols must have dPF >= 0
    "max_control_degradation": 0.05,
}


def _pf(trades: list[dict]) -> float:
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.0
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.0
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def _prepare_df(df: pd.DataFrame, symbol: str, zero_fx_volume: bool) -> pd.DataFrame:
    """The ONLY difference between arm A and arm B: for FX symbols in arm
    B, zero the volume column before the pipeline ever sees it — forcing
    Wyckoff's `window["volume"].sum() == 0` gate to trip, exactly as it
    did under the old zero-volume feed. Controls (metals/crypto) and
    arm A are returned untouched. Pure function, no engine code touched."""
    if not zero_fx_volume or symbol not in FX_SYMBOLS or "volume" not in df.columns:
        return df
    out = df.copy()
    out["volume"] = 0
    return out


# ------------------------------------------------------------ backtest arm

def backtest_symbol_arm(
    symbol: str, df: pd.DataFrame, zero_fx_volume: bool,
    step: int = STEP, warmup: int = WARMUP,
) -> list[dict]:
    """One full arm (A or B) for one symbol. Mirrors
    scripts/full_pipeline_backtest.py::backtest_symbol's mechanics, but
    records the signal bar index `i` per trade (needed for the
    chronological TRAIN/TEST split) and applies `_prepare_df` before every
    injected pipeline pass — the only place arm A and arm B diverge."""
    from main import run_pipeline
    from scripts.download_all_symbols import ASSET_CLASS, DOLLAR_PER_POINT, PIP_SIZE
    from scripts.full_pipeline_backtest import build_config, calc_pnl, simulate_trade

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)

    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True

    prepared = _prepare_df(df, symbol, zero_fx_volume)

    balance = 10_000.0
    trades: list[dict] = []
    open_until = -1
    n = len(prepared)

    for i in range(warmup, n - 2, step):
        if i <= open_until:
            continue
        cfg["data"]["_injected_df"] = prepared.iloc[: i + 1]
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

        sim = simulate_trade(entry, sl, tp, direction, prepared, i + 1)
        pnl = calc_pnl(entry, sim["exit"], direction, sl_dist, balance, symbol, ac, pip, dpp)
        balance += pnl
        open_until = i + sim["bars"]
        trades.append({"i": i, "outcome": sim["outcome"], "pnl": pnl})

    return trades


# ---------------------------------------------------------------- verdict

def wyckoff_gate_verdict(
    pooled_fx_a: list[dict],
    pooled_fx_b: list[dict],
    per_symbol_dpf: dict[str, float],
    pooled_control_a: list[dict],
    pooled_control_b: list[dict],
) -> tuple[str, dict, list[str]]:
    """Apply the pre-registered H023 rule LITERALLY. Pure + unit-tested.

    per_symbol_dpf: {symbol: PF(B) - PF(A)} for each of the 7 FX symbols
    (caller computes this from per-symbol TEST slices)."""
    n_a = len(pooled_fx_a)
    if n_a < DECISION["min_pooled_fx_test_n"]:
        return ("INSUFFICIENT_DATA", {},
                [f"pooled FX TEST n={n_a} < {DECISION['min_pooled_fx_test_n']}"])

    pf_a = _pf(pooled_fx_a)
    pf_b = _pf(pooled_fx_b)
    dpf = pf_b - pf_a

    ctrl_a = _pf(pooled_control_a)
    ctrl_b = _pf(pooled_control_b)

    sign_frac = (sum(1 for v in per_symbol_dpf.values() if v >= 0) / len(per_symbol_dpf)
                 if per_symbol_dpf else 0.0)

    checks = {
        "1_pooled_fx_dPF>=0.10": dpf >= DECISION["min_pooled_fx_dPF"],
        "2_pooled_fx_test_n>=100": n_a >= DECISION["min_pooled_fx_test_n"],
        "3_symbol_sign_frac>=5/7": sign_frac >= DECISION["min_symbol_sign_frac"],
        "4_controls_not_degraded": ctrl_b >= ctrl_a - DECISION["max_control_degradation"],
    }

    if all(checks.values()):
        return ("ADOPT (FX forced to price-only Wyckoff)", checks,
                [f"pooled FX dPF={round(dpf, 3)}, sign_frac={round(sign_frac, 2)}, "
                 f"controls A={round(ctrl_a, 3)} B={round(ctrl_b, 3)}"])

    if abs(dpf) < DECISION["min_pooled_fx_dPF"] and checks["4_controls_not_degraded"]:
        return ("NULL (Wyckoff FX volume mode immaterial at system level)", checks,
                [f"pooled FX dPF={round(dpf, 3)} — below the +/-0.10 material threshold"])

    reasons = []
    for k, v in checks.items():
        if not v:
            reasons.append(f"{k} failed")
    return ("FAILED / NO CHANGE (current tick-volume behavior stays)", checks, reasons)


# ------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(description="H023 Wyckoff volume-gating A/B (pre-registered)")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=STEP)
    args = parser.parse_args()

    from core.data_loader import load_from_csv
    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    symbols = ALL_SYMBOLS if (args.all or not args.symbols) else args.symbols
    t0 = time.monotonic()

    def discover(sym: str) -> Path | None:
        # FX symbols MUST prefer the cTrader-sourced file (real tick-volume
        # — scripts/download_ctrader_fx_history.py). The Yahoo-sourced
        # *_H1_2y.csv/*_H1_5y.csv files have ZERO volume for every FX pair
        # (confirmed 2026-07-23 on the VPS: load_from_csv(...)['volume']
        # .describe() -> max 0.0), which made arm A and arm B identical by
        # construction (both already price-only Wyckoff) — a false NULL,
        # not a real measurement, on the first H023 run. Controls
        # (metals/crypto) keep the existing Yahoo files: those DO carry
        # real volume from Yahoo (futures/crypto, unlike FX).
        if sym in FX_SYMBOLS:
            ctrader_path = DATA_DIR / f"{sym}_H1_ctrader.csv"
            if ctrader_path.exists():
                return ctrader_path
            print(f"  WARNING: no {ctrader_path.name} — falling back to the "
                  f"Yahoo-sourced file, which has ZERO FX volume. This run "
                  f"cannot measure anything for {sym}; run "
                  f"scripts/download_ctrader_fx_history.py first.")
        return next((DATA_DIR / f"{sym}_H1_{s}.csv"
                     for s in ["2y", "5y"] if (DATA_DIR / f"{sym}_H1_{s}.csv").exists()), None)

    arm_a: dict[str, list[dict]] = {}
    arm_b: dict[str, list[dict]] = {}
    splits: dict[str, int] = {}
    csvs: list[str] = []

    print("=" * 72)
    print("H023 — Wyckoff volume gating by asset class (arm A vs arm B)")
    print(f"FX symbols: {FX_SYMBOLS}")
    print(f"Control symbols: {CONTROL_SYMBOLS}")
    print("=" * 72)

    for idx, sym in enumerate(symbols, 1):
        csv = discover(sym)
        print(f"[{idx:2}/{len(symbols)}] {sym:10} ... ", end="", flush=True)
        if not csv:
            print("no CSV — skipped")
            continue
        df = load_from_csv(str(csv))

        # Canary: if this is an FX symbol and its volume is still all/
        # mostly zero even from the cTrader-sourced file, arm A and arm B
        # would be identical again (the exact failure mode from the first
        # H023 run) — abort loudly instead of producing another false
        # NULL. Controls are exempt (Yahoo genuinely has zero volume for
        # some of them is not expected, but they aren't the subject here).
        if sym in FX_SYMBOLS and "volume" in df.columns:
            nonzero_frac = (df["volume"] > 0).mean()
            if nonzero_frac < 0.5:
                print(f"ABORT: {sym}'s volume is still mostly zero "
                      f"(nonzero fraction={nonzero_frac:.1%}) even from "
                      f"{csv.name} — arm A/B would be identical again. "
                      f"This symbol's data source does not actually carry "
                      f"the condition H023 tests; do not trust a run that "
                      f"includes it.")
                continue

        splits[sym] = WARMUP + int((len(df) - WARMUP) * TRAIN_FRAC)
        csvs.append(str(csv))

        a = backtest_symbol_arm(sym, df, zero_fx_volume=False, step=args.step)
        b = backtest_symbol_arm(sym, df, zero_fx_volume=True, step=args.step)
        arm_a[sym], arm_b[sym] = a, b
        print(f"arm_A_trades={len(a)} arm_B_trades={len(b)}  ({time.monotonic() - t0:.0f}s)")

    def test_slice(trades: list[dict], sym: str) -> list[dict]:
        return [t for t in trades if t["i"] >= splits.get(sym, 0)]

    fx_present = [s for s in FX_SYMBOLS if s in arm_a]
    ctrl_present = [s for s in CONTROL_SYMBOLS if s in arm_a]

    pooled_fx_a = [t for s in fx_present for t in test_slice(arm_a[s], s)]
    pooled_fx_b = [t for s in fx_present for t in test_slice(arm_b[s], s)]
    pooled_ctrl_a = [t for s in ctrl_present for t in test_slice(arm_a[s], s)]
    pooled_ctrl_b = [t for s in ctrl_present for t in test_slice(arm_b[s], s)]

    per_symbol_dpf: dict[str, float] = {}
    per_symbol_report: dict[str, dict] = {}
    for s in fx_present:
        ta, tb = test_slice(arm_a[s], s), test_slice(arm_b[s], s)
        pfa, pfb = _pf(ta), _pf(tb)
        per_symbol_dpf[s] = pfb - pfa
        per_symbol_report[s] = {
            "test_n_A": len(ta), "test_PF_A": round(pfa, 3),
            "test_n_B": len(tb), "test_PF_B": round(pfb, 3),
            "dPF": round(pfb - pfa, 3),
        }

    verdict, checks, reasons = wyckoff_gate_verdict(
        pooled_fx_a, pooled_fx_b, per_symbol_dpf, pooled_ctrl_a, pooled_ctrl_b,
    )

    print("\n" + "=" * 72)
    print("H023 DECISION (pooled FX TEST slice)")
    print("=" * 72)
    print(f"  pooled FX  arm A: PF={round(_pf(pooled_fx_a), 3)} n={len(pooled_fx_a)}")
    print(f"  pooled FX  arm B: PF={round(_pf(pooled_fx_b), 3)} n={len(pooled_fx_b)}")
    print(f"  controls   arm A: PF={round(_pf(pooled_ctrl_a), 3)} n={len(pooled_ctrl_a)}")
    print(f"  controls   arm B: PF={round(_pf(pooled_ctrl_b), 3)} n={len(pooled_ctrl_b)}")
    for s, r in per_symbol_report.items():
        print(f"  {s}: A PF={r['test_PF_A']} n={r['test_n_A']} | B PF={r['test_PF_B']} "
              f"n={r['test_n_B']} | dPF={r['dPF']}")
    print(f"  VERDICT: {verdict}")
    for r in reasons:
        print(f"    - {r}")

    result = {
        "hypothesis": "H023",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train_frac": TRAIN_FRAC,
        "decision_rule": DECISION,
        "pooled_fx_A": {"test_PF": round(_pf(pooled_fx_a), 3), "test_n": len(pooled_fx_a)},
        "pooled_fx_B": {"test_PF": round(_pf(pooled_fx_b), 3), "test_n": len(pooled_fx_b)},
        "pooled_controls_A": {"test_PF": round(_pf(pooled_ctrl_a), 3), "test_n": len(pooled_ctrl_a)},
        "pooled_controls_B": {"test_PF": round(_pf(pooled_ctrl_b), 3), "test_n": len(pooled_ctrl_b)},
        "per_symbol": per_symbol_report,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "checks": checks,
        "note": "Verdict from the pre-registered rule (registry H023, "
                "2026-07-18) applied literally. Measurement only (rule 6) — "
                "engines/wyckoff_engine.py and the live pipeline are "
                "untouched; arm B only zeroes FX volume in the harness's "
                "own injected DataFrame before run_pipeline() sees it.",
    }
    RESULT_PATH.write_text(json.dumps(result, indent=2, default=str))
    print(f"Saved: {RESULT_PATH}")

    manifest = build_manifest(
        kind="h023_wyckoff_volume_gating",
        config=load_config(),
        params={"hypothesis": "H023", "fx_symbols": FX_SYMBOLS,
                "control_symbols": CONTROL_SYMBOLS, "train_frac": TRAIN_FRAC,
                "decision_rule": DECISION, "step": args.step, "warmup": WARMUP},
        datasets=[dataset_fingerprint(Path(c)) for c in csvs],
        results={"verdict": verdict, "pooled_fx_A": result["pooled_fx_A"],
                 "pooled_fx_B": result["pooled_fx_B"], "per_symbol": per_symbol_report},
    )
    outp = write_manifest(manifest, f"h023_wyckoff_volume_gating_{time.strftime('%Y%m%d')}")
    print(f"Manifest: {outp}")
    print("\nNext (HUMAN steps): record the verdict in registry.json (H023) + "
          "the evidence ledger; commit result + manifest from a clean tree "
          "(rule 4). Regardless of verdict nothing live changes (rule 6) — "
          "H023 is FROZEN like H018 until the forward-demo milestone.")


if __name__ == "__main__":
    main()
