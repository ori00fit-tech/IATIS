#!/usr/bin/env python3
"""
research/experiments/H019_crypto_positioning_ab.py
------------------------------------------------------
H019 — Crypto positioning/sentiment as an internal confluence modulator
(pre-registered research/results/registry.json; feasibility resolved
2026-07-24 — funding rate on Binance ~6y, Fear & Greed since 2018, open
interest DROPPED per the hypothesis's own pre-registered fallback). The
DECISION RULE pre-exists this code (CLAUDE.md rule 1); this runner only
APPLIES it.

Design:
  Two arms on IDENTICAL price bars for BTCUSD and ETHUSD (the only two
  eligible symbols per H019's own decision rule — n=2, not the 4+ used
  elsewhere in this registry):
    Arm A = current live behavior: engines.crypto_positioning_modulator
        stays False. No context is ever injected. Byte-identical to
        prod4 today.
    Arm B = identical EXCEPT the modulator flag is True AND a causal
        context (confluence.crypto_positioning_modulator.causal_context_at)
        is injected into config["data"]["_crypto_positioning_context"]
        before every run_pipeline() call, built from the funding-rate/
        Fear-Greed CSVs scripts/download_crypto_positioning_history.py
        produces. Every value used at bar i is drawn STRICTLY from
        timestamps before bar i's close (causal_context_at is where that
        guard is actually enforced — see its own docstring).
  Entries/exits/thresholds/every other gate are byte-identical between
  arms — isolates only the modulator, same discipline as H013/H017/H024.

Usage (VPS):
    venv/bin/python -m research.experiments.H019_crypto_positioning_ab
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
RESULT_PATH = PROJECT_ROOT / "research" / "results" / "H019_crypto_positioning_ab.json"

# Pre-registered constants — changing any of these is a NEW hypothesis.
SYMBOLS = ["BTCUSD", "ETHUSD"]
TRAIN_FRAC = 0.65
STEP = 8
WARMUP = 220
DECISION = {
    "min_mean_dPF": 0.05,
    "max_losing_symbols": 0,  # n=2 is too small for the ">=1 losing symbol OK" tolerance used elsewhere
}


def _pf(trades: list[dict]) -> float:
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.0
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.0
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def _load_positioning_data(symbol: str):
    """Returns (funding_df, fear_greed_df) or (None, None) if the funding
    file doesn't exist yet (scripts/download_crypto_positioning_history.py
    not run). Fear & Greed is shared across both symbols; missing it
    degrades causal_context_at to 'no amplification', never blocks the
    funding-rate leg — only a missing funding file is fatal for a symbol."""
    funding_path = DATA_DIR / f"{symbol}_funding_rate.csv"
    if not funding_path.exists():
        return None, None
    funding_df = load_from_csv_positioning(funding_path)

    fg_path = DATA_DIR / "fear_greed_index.csv"
    fear_greed_df = load_from_csv_positioning(fg_path) if fg_path.exists() else None
    return funding_df, fear_greed_df


def load_from_csv_positioning(path: Path):
    """Positioning CSVs (funding rate / Fear & Greed) have their own
    schema (settlement_ts_ms / published_ts_s columns) — core.data_loader.
    load_from_csv assumes an OHLCV shape, so this reads them directly."""
    import pandas as pd
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
    return df


def backtest_symbol_arm(
    symbol: str, df, enable_modulator: bool, funding_df, fear_greed_df,
    step: int = STEP, warmup: int = WARMUP,
) -> list[dict]:
    """One arm for one symbol. Mirrors research/experiments/
    H023_wyckoff_volume_gating.py::backtest_symbol_arm's mechanics exactly
    (same injected-df pattern, same no-overlap loop), adding the causal
    positioning-context injection as the ONLY extra step for arm B."""
    from main import run_pipeline
    from confluence.crypto_positioning_modulator import causal_context_at
    from scripts.download_all_symbols import ASSET_CLASS, DOLLAR_PER_POINT, PIP_SIZE
    from scripts.full_pipeline_backtest import build_config, calc_pnl, simulate_trade

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "crypto")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)

    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True
    cfg.setdefault("engines", {})["crypto_positioning_modulator"] = enable_modulator

    balance = 10_000.0
    trades: list[dict] = []
    open_until = -1
    n = len(df)

    for i in range(warmup, n - 2, step):
        if i <= open_until:
            continue

        if enable_modulator and funding_df is not None:
            as_of_ms = int(df.index[i].timestamp() * 1000)
            ctx = causal_context_at(funding_df, fear_greed_df, as_of_ms)
            if ctx is not None:
                cfg["data"]["_crypto_positioning_context"] = ctx
            else:
                cfg["data"].pop("_crypto_positioning_context", None)

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


def positioning_verdict(
    per_symbol_dpf: dict[str, float],
) -> tuple[str, dict, list[str]]:
    """Apply the pre-registered H019 rule LITERALLY. Pure + unit-tested.
    n=2 is too small for the ">=1 losing symbol tolerated" pattern used
    on larger symbol sets elsewhere in this registry — the rule is
    stricter by construction: BOTH symbols must improve, zero exceptions."""
    losing = [s for s, d in per_symbol_dpf.items() if d < 0]
    mean_dpf = sum(per_symbol_dpf.values()) / len(per_symbol_dpf) if per_symbol_dpf else 0.0

    checks = {
        "1_mean_dPF>=0.05": mean_dpf >= DECISION["min_mean_dPF"],
        "2_zero_losing_symbols": len(losing) <= DECISION["max_losing_symbols"],
    }
    if all(checks.values()):
        return ("ADOPT (crypto positioning modulator)", checks,
                [f"mean dPF={round(mean_dpf, 3)}, both symbols improved"])
    reasons = [f"{k} failed" for k, v in checks.items() if not v]
    if losing:
        reasons.append(f"losing symbol(s): {losing}")
    return ("FAILED / NO CHANGE (modulator stays off)", checks, reasons)


def main() -> int:
    parser = argparse.ArgumentParser(description="H019 crypto positioning A/B (pre-registered)")
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
    print("H019 — crypto positioning A/B (funding rate + Fear & Greed)")
    print(f"Symbols: {SYMBOLS}")
    print("=" * 72)

    for sym in SYMBOLS:
        price_csv = discover_price(sym)
        if not price_csv:
            print(f"{sym}: no price CSV — skipped "
                  f"(expected {sym}_H1_2y.csv or _H1_5y.csv from scripts/download_all_symbols.py)")
            continue
        funding_df, fear_greed_df = _load_positioning_data(sym)
        if funding_df is None:
            print(f"{sym}: no funding-rate CSV — skipped "
                  f"(run scripts/download_crypto_positioning_history.py first)")
            continue

        df = load_from_csv(str(price_csv))
        splits[sym] = WARMUP + int((len(df) - WARMUP) * TRAIN_FRAC)
        csvs.append(str(price_csv))

        print(f"{sym}: running arm A...")
        a = backtest_symbol_arm(sym, df, False, funding_df, fear_greed_df, step=args.step)
        print(f"{sym}: running arm B...")
        b = backtest_symbol_arm(sym, df, True, funding_df, fear_greed_df, step=args.step)
        arm_a[sym], arm_b[sym] = a, b
        print(f"{sym}: arm_A_trades={len(a)} arm_B_trades={len(b)} "
              f"({time.monotonic() - t0:.0f}s)")

    if not arm_a:
        print("\nNo symbols had both price and funding-rate data — nothing to evaluate.")
        return 1

    def test_slice(trades: list[dict], sym: str) -> list[dict]:
        return [t for t in trades if t["i"] >= splits[sym]]

    per_symbol_dpf: dict[str, float] = {}
    per_symbol_report: dict[str, dict] = {}
    for sym in arm_a:
        ta, tb = test_slice(arm_a[sym], sym), test_slice(arm_b[sym], sym)
        pfa, pfb = _pf(ta), _pf(tb)
        per_symbol_dpf[sym] = pfb - pfa
        per_symbol_report[sym] = {
            "test_n_A": len(ta), "test_PF_A": round(pfa, 3),
            "test_n_B": len(tb), "test_PF_B": round(pfb, 3),
            "dPF": round(pfb - pfa, 3),
        }

    verdict, checks, reasons = positioning_verdict(per_symbol_dpf)

    print("\n" + "=" * 72)
    print("H019 DECISION (chronological TEST slice)")
    print("=" * 72)
    for sym, r in per_symbol_report.items():
        print(f"  {sym}: A PF={r['test_PF_A']} n={r['test_n_A']} | B PF={r['test_PF_B']} "
              f"n={r['test_n_B']} | dPF={r['dPF']}")
    print(f"  VERDICT: {verdict}")
    for r in reasons:
        print(f"    - {r}")

    result = {
        "hypothesis": "H019",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train_frac": TRAIN_FRAC,
        "decision_rule": DECISION,
        "per_symbol": per_symbol_report,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "checks": checks,
        "note": "Verdict from the pre-registered rule (registry H019) "
                "applied literally. Measurement only (rule 6) — "
                "engines.crypto_positioning_modulator stays False in "
                "live config regardless of this result until a separate, "
                "explicit promotion decision.",
    }
    RESULT_PATH.write_text(json.dumps(result, indent=2, default=str))
    print(f"Saved: {RESULT_PATH}")

    manifest = build_manifest(
        kind="h019_crypto_positioning_ab",
        config=load_config(),
        params={"hypothesis": "H019", "symbols": SYMBOLS, "train_frac": TRAIN_FRAC,
                "decision_rule": DECISION, "step": args.step, "warmup": WARMUP},
        datasets=[dataset_fingerprint(Path(c)) for c in csvs],
        results={"verdict": verdict, "per_symbol": per_symbol_report},
    )
    outp = write_manifest(manifest, f"h019_crypto_positioning_ab_{time.strftime('%Y%m%d')}")
    print(f"Manifest: {outp}")
    print("\nNext (HUMAN steps): record the verdict in registry.json (H019) + "
          "the evidence ledger; commit result + manifest from a clean tree "
          "(rule 4). Regardless of verdict nothing live changes (rule 6).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
