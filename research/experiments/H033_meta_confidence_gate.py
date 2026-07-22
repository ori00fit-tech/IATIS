#!/usr/bin/env python3
"""
research/experiments/H033_meta_confidence_gate.py
--------------------------------------------------
H033 — Meta-confidence gate (pre-registered 2026-07-21, registry + doc:
research/hypotheses/H033_meta_confidence_gate.md). The DECISION RULE
pre-exists this code (CLAUDE.md rule 1); this runner only APPLIES it.

Design (frozen at registration):
  1. ONE arm-A pass of the FROZEN prod4 pipeline (the exact H024 harness:
     run_pipeline at each bar, backtest_mode=True, no-overlap simulation,
     H024's data discovery and step). The H024 runner strips per-trade
     data before saving, so this pass re-produces the arm-A ledger WITH
     the decision-time feature vector attached to every trade. No flag
     differs from the live frozen system — this IS arm A.
  2. Chronological split per symbol (TRAIN 65% of bars, H008c standard,
     identical split arithmetic to H024). Fit ONE pooled L2 logistic
     regression (sklearn, C=1.0, defaults; max_iter raised to 1000 — a
     numerical-convergence knob, not a statistical hyperparameter) on
     TRAIN trades only. FREEZE it.
  3. Score TEST trades. Arm B = TEST ledger minus trades whose score is
     below the 30th percentile of the TRAIN score distribution (the gate
     only ever skips, so arm B is a filtered subset — no re-simulation,
     no second backtest, no path dependence introduced).
  4. Verdict per the pre-registered rule (sanity gate first):
       AUC(TEST) >= 0.55, else FAILED regardless of PF;
       ADOPT only if ALL: dPF >= +0.15, retention >= 50%,
       improvement in >= 60% of symbols, carriers not degraded > 0.05;
       |dPF| < 0.15 with AUC >= 0.55 -> NULL.
       Minimums: pooled TRAIN n >= 1000, pooled arm-A TEST n >= 300.
  5. Walk-forward refit (two TEST half-windows, refit before window 2) is
     RECORDED ONLY — a robustness read, never a verdict input.

Interpretation choices fixed BEFORE any result (documented here so they
cannot drift at read time):
  - "confluence score (raw)" = report confluence.score (the adjusted
    score the EXECUTE decision is actually made on).
  - Sessions from the decision bar's UTC hour: London 07:00-11:59,
    NewYork 12:00-16:59, else Other.
  - Label: outcome of the no-overlap simulation (win=1 / loss=0; the
    harness resolves 300-bar timeouts by sign of the open PnL).
  - ATR(14) percentile vs trailing 500 bars (strictly prior), same
    trailing-percentile definition H025 used.

Usage (VPS):
    venv/bin/pip install scikit-learn   # research-only dependency
    venv/bin/python -m research.experiments.H033_meta_confidence_gate --all
    # after a completed pass, the featured ledger is saved; re-apply the
    # fit/verdict phase without re-backtesting via --reuse-ledger
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

import numpy as np

DATA_DIR = PROJECT_ROOT / "data"
LEDGER_PATH = PROJECT_ROOT / "research" / "results" / "H033_trade_ledger.json"
RESULT_PATH = PROJECT_ROOT / "research" / "results" / "H033_meta_confidence_gate.json"

# Pre-registered constants — changing any of these is a NEW hypothesis.
CARRIERS = {"XAUUSD", "BTCUSD", "ETHUSD"}
PROD4 = ["smc", "price_action", "nnfx", "wyckoff"]
TRAIN_FRAC = 0.65
SKIP_PERCENTILE = 30          # skip TEST trades below this TRAIN-score pctl
MIN_TRAIN_TRADES = 1000
MIN_POOLED_A_TEST_TRADES = 300
MIN_AUC = 0.55
DECISION = {
    "min_pooled_dPF": 0.15,
    "min_volume_retention": 0.50,
    "min_symbol_win_frac": 0.60,
    "max_carrier_degradation": 0.05,
}
ATR_PERIOD = 14
ATR_PCTL_LOOKBACK = 500

# Fixed feature column order — the model spec, frozen.
FEATURE_COLUMNS = [
    "score",
    "eng_smc", "eng_price_action", "eng_nnfx", "eng_wyckoff",
    "regime_trending",
    "vol_low", "vol_high", "vol_extreme",          # base: normal
    "sess_london", "sess_ny",                      # base: other
    "ac_metal", "ac_crypto", "ac_index", "ac_energy",  # base: forex
    "atr_pctl",
    "d1_confirming",
    "rr",
]


# ---------------------------------------------------------------- pure fns

def _pf(trades: list[dict]) -> float:
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.0
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.0
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def engine_agreement(engine_outputs: list[dict], winning_bias: str) -> dict[str, int]:
    """Per prod4 engine: +1 agrees with the final direction, -1 opposes,
    0 neutral/abstaining/missing."""
    opposite = {"BULLISH": "BEARISH", "BEARISH": "BULLISH"}.get(winning_bias)
    out = {}
    by_name = {o.get("engine"): o.get("bias") for o in engine_outputs}
    for eng in PROD4:
        bias = by_name.get(eng)
        if bias == winning_bias:
            out[eng] = 1
        elif bias == opposite and opposite is not None:
            out[eng] = -1
        else:
            out[eng] = 0
    return out


def session_from_hour(hour_utc: int) -> str:
    if 7 <= hour_utc < 12:
        return "london"
    if 12 <= hour_utc < 17:
        return "ny"
    return "other"


def feature_vector(t: dict) -> list[float]:
    """Trade record -> fixed-order numeric vector (FEATURE_COLUMNS)."""
    vol = t.get("volatility", "normal")
    sess = t.get("session", "other")
    ac = t.get("asset_class", "forex")
    return [
        float(t["score"]),
        float(t["eng"]["smc"]), float(t["eng"]["price_action"]),
        float(t["eng"]["nnfx"]), float(t["eng"]["wyckoff"]),
        1.0 if t.get("regime") == "TRENDING" else 0.0,
        1.0 if vol == "low" else 0.0,
        1.0 if vol == "high" else 0.0,
        1.0 if vol == "extreme" else 0.0,
        1.0 if sess == "london" else 0.0,
        1.0 if sess == "ny" else 0.0,
        1.0 if ac in ("metal", "metals") else 0.0,
        1.0 if ac == "crypto" else 0.0,
        1.0 if ac in ("index", "indices") else 0.0,
        1.0 if ac == "energy" else 0.0,
        float(t.get("atr_pctl") if t.get("atr_pctl") is not None else 0.5),
        1.0 if t.get("d1_confirming") else 0.0,
        float(t.get("rr", 2.0)),
    ]


def meta_verdict(
    auc: float,
    pf_a: float,
    pf_b: float,
    retention: float,
    symbol_win_frac: float,
    car_pf_a: float,
    car_pf_b: float,
    pooled_a_n: int,
    train_n: int,
) -> tuple[str, dict, list[str]]:
    """Apply the pre-registered H033 rule LITERALLY. Pure + unit-tested."""
    if train_n < MIN_TRAIN_TRADES or pooled_a_n < MIN_POOLED_A_TEST_TRADES:
        return (
            "INSUFFICIENT_DATA",
            {},
            [f"TRAIN n={train_n} (need >= {MIN_TRAIN_TRADES}); "
             f"pooled arm-A TEST n={pooled_a_n} (need >= {MIN_POOLED_A_TEST_TRADES})"],
        )
    if not auc >= MIN_AUC:
        return (
            "FAILED",
            {"sanity_auc>=0.55": False},
            [f"TEST AUC {auc:.4f} < {MIN_AUC} — the model cannot rank its own "
             "trades out of sample; any PF delta is luck, not self-knowledge"],
        )
    dpf = pf_b - pf_a
    checks = {
        "sanity_auc>=0.55": True,
        "1_pooled_dPF>=0.15": dpf >= DECISION["min_pooled_dPF"],
        "2_volume_retention>=0.50": retention >= DECISION["min_volume_retention"],
        "3_symbol_win_frac>=0.60": symbol_win_frac >= DECISION["min_symbol_win_frac"],
        "4_carriers_not_degraded": car_pf_b >= car_pf_a - DECISION["max_carrier_degradation"],
    }
    reasons = [k for k, v in checks.items() if not v]
    if all(checks.values()):
        return "ADOPT (meta-confidence gate)", checks, []
    if abs(dpf) < DECISION["min_pooled_dPF"] and retention >= DECISION["min_volume_retention"]:
        return ("NULL (model ranks but the ranking is not monetizable at this threshold)",
                checks, reasons)
    return "FAILED / NO CHANGE", checks, reasons


# ------------------------------------------------------------ ledger build

def atr_percentiles(df) -> np.ndarray:
    from research.experiments.H025_information_compression import trailing_percentile

    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    prev_close = np.concatenate([[np.nan], close[:-1]])
    tr = np.nanmax(
        np.column_stack([high - low, np.abs(high - prev_close), np.abs(low - prev_close)]),
        axis=1,
    )
    import pandas as pd
    atr = pd.Series(tr).rolling(ATR_PERIOD).mean().to_numpy()
    return trailing_percentile(atr, ATR_PCTL_LOOKBACK)


def run_arm_a_with_features(symbol: str, df, step: int = 8, warmup: int = 220) -> list[dict]:
    """The H024 arm-A pass (frozen system, no gate flag set) with the
    pre-registered decision-time feature set captured per trade."""
    from main import run_pipeline
    from scripts.full_pipeline_backtest import build_config, simulate_trade, calc_pnl
    from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)

    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True

    atr_pctl = atr_percentiles(df)

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

        conf = report.get("confluence", {})
        vote = conf.get("vote", {})
        mtf = conf.get("mtf", {})
        ts = df.index[i]
        trades.append({
            "symbol": symbol,
            "i": i,
            "bar_time": str(ts),
            "outcome": sim["outcome"],
            "label": 1 if sim["outcome"] == "win" else 0,
            "pnl": pnl,
            # -------- decision-time features (registered list) --------
            "score": conf.get("score"),
            "eng": engine_agreement(report.get("engine_outputs", []),
                                    vote.get("winning_bias", "")),
            "regime": report.get("regime", {}).get("state", "UNKNOWN"),
            "volatility": report.get("regime", {}).get("volatility", "normal"),
            "session": session_from_hour(int(getattr(ts, "hour", 0))),
            "asset_class": ac,
            "atr_pctl": (None if np.isnan(atr_pctl[i]) else round(float(atr_pctl[i]), 4)),
            "d1_confirming": bool(mtf.get("confirming", False)),
            "rr": round(abs(tp - entry) / sl_dist, 3),
        })
    return trades


# --------------------------------------------------------------- fit phase

def fit_and_judge(ledger: list[dict], splits: dict[str, int]) -> dict:
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "scikit-learn is required (research-only dependency): "
            "venv/bin/pip install scikit-learn"
        ) from exc

    train = [t for t in ledger if t["i"] < splits[t["symbol"]]]
    test = [t for t in ledger if t["i"] >= splits[t["symbol"]]]

    X_tr = np.array([feature_vector(t) for t in train])
    y_tr = np.array([t["label"] for t in train])
    X_te = np.array([feature_vector(t) for t in test])
    y_te = np.array([t["label"] for t in test])

    model = LogisticRegression(C=1.0, max_iter=1000)
    model.fit(X_tr, y_tr)

    p_tr = model.predict_proba(X_tr)[:, 1]
    p_te = model.predict_proba(X_te)[:, 1]
    threshold = float(np.percentile(p_tr, SKIP_PERCENTILE))

    auc = float(roc_auc_score(y_te, p_te)) if len(set(y_te)) > 1 else 0.5

    kept = [t for t, p in zip(test, p_te) if p >= threshold]
    pf_a, pf_b = _pf(test), _pf(kept)
    retention = len(kept) / len(test) if test else 0.0

    # per-symbol improvement fraction (symbols with >= 10 arm-A TEST trades,
    # mirroring H024's evaluate_decision)
    per_symbol = {}
    for t, p in zip(test, p_te):
        per_symbol.setdefault(t["symbol"], {"a": [], "b": []})
        per_symbol[t["symbol"]]["a"].append(t)
        if p >= threshold:
            per_symbol[t["symbol"]]["b"].append(t)
    valid = {s: d for s, d in per_symbol.items() if len(d["a"]) >= 10}
    improved = [s for s, d in valid.items() if _pf(d["b"]) > _pf(d["a"])]
    symbol_win_frac = len(improved) / len(valid) if valid else 0.0

    car_a = [t for s, d in valid.items() if s in CARRIERS for t in d["a"]]
    car_b = [t for s, d in valid.items() if s in CARRIERS for t in d["b"]]
    car_pf_a, car_pf_b = _pf(car_a), _pf(car_b)

    verdict, checks, reasons = meta_verdict(
        auc, pf_a, pf_b, retention, symbol_win_frac,
        car_pf_a, car_pf_b, len(test), len(train),
    )

    # ---- walk-forward refit: RECORDED ONLY, never a verdict input ----
    wf = []
    by_i = sorted(test, key=lambda t: (t["symbol"], t["i"]))
    half = {}
    for s, d in per_symbol.items():
        idx = sorted(t["i"] for t in d["a"])
        half[s] = idx[len(idx) // 2] if idx else 0
    w1 = [t for t in by_i if t["i"] < half.get(t["symbol"], 0)]
    w2 = [t for t in by_i if t["i"] >= half.get(t["symbol"], 0)]
    for name, fit_set, eval_set in [("W1_fit_TRAIN", train, w1),
                                    ("W2_fit_TRAIN+W1", train + w1, w2)]:
        if len(eval_set) < 30 or len(fit_set) < 100:
            wf.append({"window": name, "skipped": "too few trades"})
            continue
        m = LogisticRegression(C=1.0, max_iter=1000)
        m.fit(np.array([feature_vector(t) for t in fit_set]),
              np.array([t["label"] for t in fit_set]))
        pw = m.predict_proba(np.array([feature_vector(t) for t in eval_set]))[:, 1]
        yw = np.array([t["label"] for t in eval_set])
        thr = float(np.percentile(
            m.predict_proba(np.array([feature_vector(t) for t in fit_set]))[:, 1],
            SKIP_PERCENTILE))
        keptw = [t for t, p in zip(eval_set, pw) if p >= thr]
        wf.append({
            "window": name,
            "n": len(eval_set),
            "auc": round(float(roc_auc_score(yw, pw)), 4) if len(set(yw)) > 1 else None,
            "pf_a": round(_pf(eval_set), 3),
            "pf_b": round(_pf(keptw), 3),
            "retention": round(len(keptw) / len(eval_set), 3),
        })

    return {
        "verdict": verdict,
        "verdict_reasons": reasons,
        "checks": checks,
        "test_auc": round(auc, 4),
        "train_trades": len(train),
        "pooled_test_trades_A": len(test),
        "pooled_test_trades_B": len(kept),
        "pooled_test_PF_A": round(pf_a, 3),
        "pooled_test_PF_B": round(pf_b, 3),
        "pooled_dPF": round(pf_b - pf_a, 3),
        "volume_retention": round(retention, 3),
        "skip_threshold_train_p30": round(threshold, 4),
        "symbol_win_frac": round(symbol_win_frac, 3),
        "symbols_improved": sorted(improved),
        "carriers_test_PF_A": round(car_pf_a, 3),
        "carriers_test_PF_B": round(car_pf_b, 3),
        "model_coefficients": dict(zip(FEATURE_COLUMNS,
                                       [round(float(c), 4) for c in model.coef_[0]])),
        "model_intercept": round(float(model.intercept_[0]), 4),
        "walk_forward_recorded_only": wf,
        "note": "Verdict from the pre-registered rule (registry H033, "
                "2026-07-21) applied literally. Measurement only: "
                "features.meta_gate does not exist and nothing live changes "
                "regardless of verdict (rule 6).",
    }


# ------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(description="H033 meta-confidence gate (pre-registered)")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=8)
    parser.add_argument("--reuse-ledger", action="store_true",
                        help="skip the backtest pass; refit from the saved ledger")
    args = parser.parse_args()

    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config
    from scripts.full_pipeline_backtest import ACTIVE_SYMBOLS

    symbols = ACTIVE_SYMBOLS if (args.all or not args.symbols) else args.symbols
    warmup = 220
    t0 = time.monotonic()

    if args.reuse_ledger and LEDGER_PATH.exists():
        saved = json.loads(LEDGER_PATH.read_text())
        ledger, splits, csvs = saved["trades"], saved["splits"], saved["datasets"]
        print(f"Reusing ledger: {len(ledger)} trades, {len(splits)} symbols")
    else:
        from core.data_loader import load_from_csv

        ledger: list[dict] = []
        splits: dict[str, int] = {}
        csvs: list[str] = []
        print("=" * 72)
        print("H033 — arm-A ledger build (frozen pipeline + feature capture)")
        print(f"Symbols: {len(symbols)} | Step: {args.step} | split TRAIN "
              f"{int(TRAIN_FRAC * 100)}/{int((1 - TRAIN_FRAC) * 100)}")
        print("=" * 72)
        for idx, sym in enumerate(symbols, 1):
            csv = next((DATA_DIR / f"{sym}_H1_{s}.csv"
                        for s in ["2y", "5y"] if (DATA_DIR / f"{sym}_H1_{s}.csv").exists()), None)
            print(f"[{idx:2}/{len(symbols)}] {sym:10} ... ", end="", flush=True)
            if not csv:
                print("no CSV — skipped")
                continue
            try:
                df = load_from_csv(str(csv))
                trades = run_arm_a_with_features(sym, df, step=args.step, warmup=warmup)
                splits[sym] = warmup + int((len(df) - warmup) * TRAIN_FRAC)
                ledger.extend(trades)
                csvs.append(str(csv))
                print(f"n={len(trades)}  ({time.monotonic() - t0:.0f}s)")
            except Exception as e:  # noqa: BLE001
                print(f"ERROR {str(e)[:60]}")
        LEDGER_PATH.write_text(json.dumps(
            {"trades": ledger, "splits": splits, "datasets": csvs,
             "generated_at": datetime.now(timezone.utc).isoformat()},
            indent=1, default=str))
        print(f"Ledger saved: {LEDGER_PATH} ({len(ledger)} trades)")

    decision = fit_and_judge(ledger, splits)

    print("\n" + "=" * 72)
    print("H033 DECISION (pooled TEST slice)")
    print("=" * 72)
    print(f"  TEST AUC: {decision['test_auc']}  (sanity floor {MIN_AUC})")
    print(f"  arm A pooled TEST PF: {decision['pooled_test_PF_A']}  (n={decision['pooled_test_trades_A']})")
    print(f"  arm B pooled TEST PF: {decision['pooled_test_PF_B']}  (n={decision['pooled_test_trades_B']})")
    print(f"  dPF: {decision['pooled_dPF']}  | retention: {decision['volume_retention']}")
    print(f"  symbols improved: {decision['symbol_win_frac']} {decision['symbols_improved']}")
    print(f"  carriers PF  A={decision['carriers_test_PF_A']}  B={decision['carriers_test_PF_B']}")
    for k, v in decision.get("checks", {}).items():
        print(f"    [{'PASS' if v else 'FAIL'}] {k}")
    for w in decision["walk_forward_recorded_only"]:
        print(f"  WF {w}")
    print(f"\n  VERDICT: {decision['verdict']}")

    out = {
        "hypothesis": "H033",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train_frac": TRAIN_FRAC,
        "skip_percentile": SKIP_PERCENTILE,
        "feature_columns": FEATURE_COLUMNS,
        "decision_rule": {**DECISION, "min_auc": MIN_AUC,
                          "min_train_trades": MIN_TRAIN_TRADES,
                          "min_pooled_A_test_trades": MIN_POOLED_A_TEST_TRADES},
        "decision": decision,
        "duration_sec": round(time.monotonic() - t0, 1),
    }
    RESULT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"Saved: {RESULT_PATH}")

    manifest = build_manifest(
        kind="h033_meta_gate",
        config=load_config(),
        params={"hypothesis": "H033", "train_frac": TRAIN_FRAC,
                "skip_percentile": SKIP_PERCENTILE,
                "model": "sklearn LogisticRegression C=1.0 max_iter=1000",
                "features": FEATURE_COLUMNS},
        datasets=[dataset_fingerprint(Path(c)) for c in csvs],
        results={"decision": decision},
    )
    outp = write_manifest(manifest, f"h033_meta_gate_{time.strftime('%Y%m%d')}")
    print(f"Manifest: {outp}")
    print("\nNext (HUMAN steps): record the verdict in registry.json (H033) + "
          "the evidence ledger; commit result + ledger + manifest from a clean "
          "tree (rule 4). Regardless of verdict nothing live changes (rule 6).")


if __name__ == "__main__":
    main()
