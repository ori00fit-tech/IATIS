#!/usr/bin/env python3
"""
research/experiments/H037_decision_delay.py
--------------------------------------------
H037 — Decision delay (pre-registered 2026-07-22, registry + doc:
research/hypotheses/H037_decision_delay.md). The DECISION RULE pre-exists
this code (CLAUDE.md rule 1); this runner only APPLIES it.

Design (frozen at registration):
  1. ONE pipeline pass on the H024/H033 harness captures arm A's decision
     set per symbol: (i, entry, sl, tp, direction) for every EXECUTE the
     frozen prod4 system emits under the standard no-overlap loop. The
     signal list is persisted (H037_signals.json) for --reuse-signals.
  2. Arm replays are pure geometry — no second pipeline pass:
       delay N: entry = close of bar i+N; SL/TP re-anchored with the
       ORIGINAL signal-time distances; simulation from i+N+1; same
       no-overlap rule (a signal whose delayed entry bar falls inside the
       previous trade's occupancy is dropped).
     No invalidation filter — the wait is the only mechanism.
  3. VALIDITY CHECK: the delay-0 replay must reproduce the captured arm-A
     trades exactly (same count, same outcomes, same total pnl). A
     mismatch aborts the run — the replay would not be measuring what the
     registration says it measures.
  4. Verdict per the pre-registered rule on the pooled chronological TEST
     slice (TRAIN 65% of bars, H024 split arithmetic):
       ADOPT smallest passing N iff ALL: dPF >= +0.15, retention >= 80%,
       >= 60% of symbols improved, carriers not degraded > 0.05, AND
       family-consistency (dPF > 0 for >= 2 of 3 delays);
       all |dPF| < 0.15 with retention held -> NULL;
       minimum pooled arm-A TEST n >= 300.

Usage (VPS):
    venv/bin/python -m research.experiments.H037_decision_delay --all
    venv/bin/python -m research.experiments.H037_decision_delay --reuse-signals
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
SIGNALS_PATH = PROJECT_ROOT / "research" / "results" / "H037_signals.json"
RESULT_PATH = PROJECT_ROOT / "research" / "results" / "H037_decision_delay.json"

# Pre-registered constants — changing any of these is a NEW hypothesis.
CARRIERS = {"XAUUSD", "BTCUSD", "ETHUSD"}
DELAYS = [1, 2, 3]
TRAIN_FRAC = 0.65
MIN_POOLED_A_TEST_TRADES = 300
DECISION = {
    "min_pooled_dPF": 0.15,
    "min_volume_retention": 0.80,     # stricter than gates: mechanism is unconditional
    "min_symbol_win_frac": 0.60,
    "max_carrier_degradation": 0.05,
    "min_family_positive": 2,         # dPF > 0 in >= 2 of the 3 delays
}
STEP = 8
WARMUP = 220


def _pf(trades: list[dict]) -> float:
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.0
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.0
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


# ------------------------------------------------------------ signal pass

def capture_signals(symbol: str, df, step: int = STEP, warmup: int = WARMUP) -> list[dict]:
    """Arm A's decision set under the standard no-overlap loop, with the
    signal geometry needed for delayed replays. Mirrors the H024/H033
    harness exactly."""
    from main import run_pipeline
    from scripts.full_pipeline_backtest import build_config, simulate_trade

    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True

    signals: list[dict] = []
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

        # occupancy bookkeeping identical to the harness (immediate entry);
        # outcome kept so the delay-0 replay can be checked trade-by-trade
        sim = simulate_trade(entry, sl, tp, direction, df, i + 1)
        open_until = i + sim["bars"]
        signals.append({
            "i": i,
            "entry": entry,
            "sl_dist": sl_dist,
            "tp_dist": abs(tp - entry),
            "direction": direction,
            "outcome_immediate": sim["outcome"],
        })
    return signals


# ---------------------------------------------------------------- replay

def replay_with_delay(signals: list[dict], df, delay: int, symbol: str,
                      asset_class: str, pip: float, dpp: float) -> list[dict]:
    """Pure geometry replay of the captured decision set with entry shifted
    to the close of bar i+delay, SL/TP re-anchored with the ORIGINAL
    distances, same no-overlap rule. delay=0 must reproduce arm A."""
    from scripts.full_pipeline_backtest import simulate_trade, calc_pnl

    n = len(df)
    close = df["close"]
    balance = 10_000.0
    trades: list[dict] = []
    open_until = -1
    for s in signals:
        j = s["i"] + delay          # entry bar of the delayed trade
        if j <= open_until:         # still inside the previous occupancy
            continue
        if j > n - 2:               # no bar left to simulate from
            continue
        direction = s["direction"]
        entry = s["entry"] if delay == 0 else float(close.iloc[j])
        sl = entry - direction * s["sl_dist"]
        tp = entry + direction * s["tp_dist"]
        sim = simulate_trade(entry, sl, tp, direction, df, j + 1)
        pnl = calc_pnl(entry, sim["exit"], direction, s["sl_dist"], balance,
                       symbol, asset_class, pip, dpp)
        balance += pnl
        open_until = j + sim["bars"]
        trades.append({
            "i": s["i"],            # split by SIGNAL bar, identical across arms
            "entry_bar": j,
            "outcome": sim["outcome"],
            "pnl": pnl,
        })
    return trades


# ---------------------------------------------------------------- verdict

def delay_verdict(
    per_delay: dict[int, dict],
    pooled_a_n: int,
) -> tuple[str, dict, list[str]]:
    """Apply the pre-registered H037 rule LITERALLY. Pure + unit-tested.

    per_delay[N] must contain: dpf, retention, symbol_win_frac,
    car_pf_a, car_pf_b."""
    if pooled_a_n < MIN_POOLED_A_TEST_TRADES:
        return ("INSUFFICIENT_DATA", {},
                [f"pooled arm-A TEST n={pooled_a_n} < {MIN_POOLED_A_TEST_TRADES}"])

    family_positive = sum(1 for d in per_delay.values() if d["dpf"] > 0)
    family_ok = family_positive >= DECISION["min_family_positive"]

    all_checks: dict[str, dict] = {}
    passing: list[int] = []
    for n in sorted(per_delay):
        d = per_delay[n]
        checks = {
            "1_pooled_dPF>=0.15": d["dpf"] >= DECISION["min_pooled_dPF"],
            "2_volume_retention>=0.80": d["retention"] >= DECISION["min_volume_retention"],
            "3_symbol_win_frac>=0.60": d["symbol_win_frac"] >= DECISION["min_symbol_win_frac"],
            "4_carriers_not_degraded": d["car_pf_b"] >= d["car_pf_a"] - DECISION["max_carrier_degradation"],
        }
        all_checks[f"delay_{n}"] = checks
        if all(checks.values()):
            passing.append(n)
    all_checks["5_family_dPF>0_in>=2_of_3"] = family_ok

    if passing and family_ok:
        n_star = min(passing)
        return (f"ADOPT (delay {n_star})", all_checks,
                [f"smallest passing delay N={n_star}; family positive {family_positive}/3"])
    reasons: list[str] = []
    if passing and not family_ok:
        reasons.append(
            f"delay {min(passing)} passes its guards but family-consistency fails "
            f"(dPF > 0 in only {family_positive}/3 delays) — treated as noise")
    if all(abs(d["dpf"]) < DECISION["min_pooled_dPF"] and
           d["retention"] >= DECISION["min_volume_retention"]
           for d in per_delay.values()):
        return ("NULL (entry timing immaterial at H4)", all_checks, reasons)
    if not reasons:
        worst = {n: round(d["dpf"], 3) for n, d in per_delay.items()}
        reasons.append(f"no delay passes; dPF by delay: {worst}")
    return ("FAILED / NO CHANGE", all_checks, reasons)


# ------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(description="H037 decision-delay replay (pre-registered)")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=STEP)
    parser.add_argument("--reuse-signals", action="store_true",
                        help="skip the pipeline pass; replay from the saved signal set")
    args = parser.parse_args()

    from core.data_loader import load_from_csv
    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config
    from scripts.full_pipeline_backtest import ACTIVE_SYMBOLS
    from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT

    symbols = ACTIVE_SYMBOLS if (args.all or not args.symbols) else args.symbols
    t0 = time.monotonic()

    def discover(sym: str) -> Path | None:
        return next((DATA_DIR / f"{sym}_H1_{s}.csv"
                     for s in ["2y", "5y"] if (DATA_DIR / f"{sym}_H1_{s}.csv").exists()), None)

    # ---------- phase 1: signal capture (or reuse) ----------
    if args.reuse_signals and SIGNALS_PATH.exists():
        saved = json.loads(SIGNALS_PATH.read_text())
        all_signals, csvs = saved["signals"], saved["datasets"]
        print(f"Reusing signals: {sum(len(v) for v in all_signals.values())} "
              f"across {len(all_signals)} symbols")
    else:
        all_signals: dict[str, list[dict]] = {}
        csvs: list[str] = []
        print("=" * 72)
        print("H037 — signal capture (ONE frozen-pipeline pass, arm A decision set)")
        print(f"Symbols: {len(symbols)} | Step: {args.step}")
        print("=" * 72)
        for idx, sym in enumerate(symbols, 1):
            csv = discover(sym)
            print(f"[{idx:2}/{len(symbols)}] {sym:10} ... ", end="", flush=True)
            if not csv:
                print("no CSV — skipped")
                continue
            try:
                df = load_from_csv(str(csv))
                sigs = capture_signals(sym, df, step=args.step)
                all_signals[sym] = sigs
                csvs.append(str(csv))
                print(f"signals={len(sigs)}  ({time.monotonic() - t0:.0f}s)")
            except Exception as e:  # noqa: BLE001
                print(f"ERROR {str(e)[:60]}")
        SIGNALS_PATH.write_text(json.dumps(
            {"signals": all_signals, "datasets": csvs,
             "generated_at": datetime.now(timezone.utc).isoformat()},
            indent=1, default=str))
        print(f"Signals saved: {SIGNALS_PATH}")

    # ---------- phase 2: replays ----------
    arms: dict[int, dict[str, list[dict]]] = {0: {}, **{n: {} for n in DELAYS}}
    splits: dict[str, int] = {}
    for sym, sigs in all_signals.items():
        csv = discover(sym)
        if not csv:
            continue
        df = load_from_csv(str(csv))
        splits[sym] = WARMUP + int((len(df) - WARMUP) * TRAIN_FRAC)
        pip = PIP_SIZE.get(sym, 0.0001)
        ac = ASSET_CLASS.get(sym, "forex")
        dpp = DOLLAR_PER_POINT.get(sym, 1.0)
        for n in [0, *DELAYS]:
            arms[n][sym] = replay_with_delay(sigs, df, n, sym, ac, pip, dpp)

    # ---------- validity check: delay-0 == captured arm A ----------
    # capture_signals kept only executable signals under the immediate
    # no-overlap loop, so the delay-0 replay must take every one of them.
    for sym, sigs in all_signals.items():
        a = arms[0].get(sym, [])
        if len(a) != len(sigs) or any(
                t["outcome"] != s["outcome_immediate"] for t, s in zip(a, sigs)):
            raise SystemExit(
                f"VALIDITY CHECK FAILED: {sym} delay-0 replay "
                f"({len(a)} trades) does not reproduce the captured arm A "
                f"({len(sigs)} signals) trade-by-trade — run is invalid "
                "(see registration §3).")
    print("Validity check passed: delay-0 replay reproduces arm A on every symbol.")

    # ---------- pooled TEST evaluation ----------
    def test_slice(trades: list[dict], sym: str) -> list[dict]:
        return [t for t in trades if t["i"] >= splits[sym]]

    pooled_a = [t for s in arms[0] for t in test_slice(arms[0][s], s)]
    pf_a = _pf(pooled_a)
    valid_syms = [s for s in arms[0] if len(test_slice(arms[0][s], s)) >= 10]

    per_delay: dict[int, dict] = {}
    per_delay_report: dict[str, dict] = {}
    for n in DELAYS:
        pooled_b = [t for s in arms[n] for t in test_slice(arms[n][s], s)]
        pf_b = _pf(pooled_b)
        improved = [s for s in valid_syms
                    if _pf(test_slice(arms[n][s], s)) > _pf(test_slice(arms[0][s], s))]
        car_a = [t for s in valid_syms if s in CARRIERS for t in test_slice(arms[0][s], s)]
        car_b = [t for s in valid_syms if s in CARRIERS for t in test_slice(arms[n][s], s)]
        per_delay[n] = {
            "dpf": pf_b - pf_a,
            "retention": len(pooled_b) / len(pooled_a) if pooled_a else 0.0,
            "symbol_win_frac": len(improved) / len(valid_syms) if valid_syms else 0.0,
            "car_pf_a": _pf(car_a),
            "car_pf_b": _pf(car_b),
        }
        per_delay_report[str(n)] = {
            "test_PF": round(pf_b, 3),
            "test_n": len(pooled_b),
            "dPF": round(pf_b - pf_a, 3),
            "retention": round(per_delay[n]["retention"], 3),
            "symbol_win_frac": round(per_delay[n]["symbol_win_frac"], 3),
            "symbols_improved": sorted(improved),
            "carriers_PF": round(per_delay[n]["car_pf_b"], 3),
        }

    verdict, checks, reasons = delay_verdict(per_delay, len(pooled_a))

    print("\n" + "=" * 72)
    print("H037 DECISION (pooled TEST slice)")
    print("=" * 72)
    print(f"  arm A (immediate): PF={round(pf_a, 3)}  n={len(pooled_a)}")
    for n in DELAYS:
        d = per_delay_report[str(n)]
        print(f"  delay {n}: PF={d['test_PF']} n={d['test_n']} dPF={d['dPF']} "
              f"retention={d['retention']} symbols={d['symbol_win_frac']} "
              f"carriers={d['carriers_PF']}")
    print(f"  VERDICT: {verdict}")
    for r in reasons:
        print(f"    - {r}")

    result = {
        "hypothesis": "H037",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train_frac": TRAIN_FRAC,
        "delays": DELAYS,
        "decision_rule": DECISION,
        "arm_A": {"test_PF": round(pf_a, 3), "test_n": len(pooled_a),
                  "carriers_PF": round(per_delay[DELAYS[0]]["car_pf_a"], 3)},
        "per_delay": per_delay_report,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "checks": checks,
        "note": "Verdict from the pre-registered rule (registry H037, "
                "2026-07-22) applied literally. Measurement only (rule 6).",
    }
    RESULT_PATH.write_text(json.dumps(result, indent=2, default=str))
    print(f"Saved: {RESULT_PATH}")

    manifest = build_manifest(
        kind="h037_decision_delay",
        config=load_config(),
        params={"hypothesis": "H037", "delays": DELAYS, "train_frac": TRAIN_FRAC,
                "decision_rule": DECISION, "step": args.step, "warmup": WARMUP},
        datasets=[dataset_fingerprint(Path(c)) for c in csvs],
        results={"verdict": verdict, "arm_A": result["arm_A"],
                 "per_delay": per_delay_report},
    )
    outp = write_manifest(manifest, f"h037_decision_delay_{time.strftime('%Y%m%d')}")
    print(f"Manifest: {outp}")
    print("\nNext (HUMAN steps): record the verdict in registry.json (H037) + "
          "the evidence ledger; commit result + signals + manifest from a "
          "clean tree (rule 4). Regardless of verdict nothing live changes "
          "(rule 6).")


if __name__ == "__main__":
    main()
