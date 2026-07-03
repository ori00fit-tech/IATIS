#!/usr/bin/env python3
"""
scripts/engine_ablation.py
--------------------------
Answers: "Does each engine add value, and are the engines actually
independent experts?" — with measurements, not opinion.

Two studies per symbol:

1. VOTE INDEPENDENCE MATRIX
   Evaluates every engine on the same rolling windows and records its
   bias. Reports pairwise agreement (conditioned on both engines being
   non-neutral) and each engine's counter-trend rate vs the regime
   detector. Two "independent experts" that agree ~100% of the time are
   one signal counted twice — which invalidates consensus mechanisms
   (H013) built on them.

2. LEAVE-ONE-OUT (LOO) ABLATION
   Runs the production-parity walk-forward backtest once with the
   baseline engine set, then once per engine with that engine disabled
   (weights renormalize via score_calculator's participating-engine
   logic). Also runs add-one-in variants for research engines and an
   H013-off variant. Reports Δ profit factor / Δ win rate / Δ trades vs
   baseline. A positive ΔPF when an engine is REMOVED is evidence the
   engine subtracts value.

Integrity notes (per IATIS research protocol):
- No gate other than the ablated component is toggled; everything else
  stays at production parity (BacktestConfig defaults).
- Every result row is labeled with its exact variant. Ablation results
  must never be quoted as system performance.
- Synthetic data validates MECHANICS only. Edge conclusions require the
  real cached H1 datasets ({SYMBOL}_H1_*.csv) on the VPS.

Usage:
    python3 scripts/engine_ablation.py --symbols EURUSD GBPUSD \
        --data-dir data --output results/ablation.json
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402


# ── Engine registry (mirrors backtesting.backtest_engine._ENGINE_MAP) ──

def _engine_map() -> dict:
    from engines.divergence_engine import DivergenceEngine
    from engines.ict_engine import ICTEngine
    from engines.market_structure_engine import MarketStructureEngine
    from engines.nnfx_engine import NNFXEngine
    from engines.price_action_engine import PriceActionEngine
    from engines.quant_engine import QuantEngine
    from engines.sentiment_engine import SentimentEngine
    from engines.smc_engine import SMCEngine
    from engines.wyckoff_engine import WyckoffEngine
    return {
        "smc": SMCEngine, "price_action": PriceActionEngine,
        "ict": ICTEngine, "nnfx": NNFXEngine,
        "quant": QuantEngine, "wyckoff": WyckoffEngine,
        "divergence": DivergenceEngine,
        "market_structure": MarketStructureEngine,
        "sentiment": SentimentEngine,
    }


# ── Study 1: vote independence ──────────────────────────────────────────

def vote_independence(
    df: pd.DataFrame,
    timeframes: list[str],
    warmup: int = 220,
    step: int = 8,
) -> dict:
    """Record every engine's bias on identical rolling windows; report
    pairwise agreement and counter-trend rates.

    Agreement is conditioned on BOTH engines voting non-neutral: neutral
    abstentions say nothing about signal correlation.
    """
    from core.timeframe_sync import build_multi_timeframe_view
    from regimes.regime_detector import detect_regime

    engines = {k: cls() for k, cls in _engine_map().items()}
    votes: dict[str, list[str]] = {k: [] for k in engines}
    trend_dir: list[str] = []

    n = len(df)
    for i in range(warmup, n - 1, step):
        window = df.iloc[: i + 1]
        mtf = build_multi_timeframe_view(window, timeframes)

        regime = detect_regime(window)
        strength = getattr(regime, "trend_strength", 0.0) or 0.0
        trend_dir.append(
            "BULLISH" if strength > 0 else "BEARISH" if strength < 0 else "NEUTRAL"
        )

        for key, eng in engines.items():
            out = eng.safe_analyze(mtf)
            votes[key].append(out.bias.value)

    samples = len(trend_dir)
    keys = list(engines)

    # Pairwise agreement, conditioned on both non-neutral.
    agreement: dict[str, dict] = {}
    for a, b in itertools.combinations(keys, 2):
        both = [
            (va, vb) for va, vb in zip(votes[a], votes[b])
            if va != "NEUTRAL" and vb != "NEUTRAL"
        ]
        if len(both) < 10:
            agreement[f"{a}|{b}"] = {"n": len(both), "agree_pct": None}
            continue
        agree = sum(1 for va, vb in both if va == vb)
        agreement[f"{a}|{b}"] = {
            "n": len(both),
            "agree_pct": round(100.0 * agree / len(both), 1),
        }

    # Per-engine activity and counter-trend rate.
    per_engine: dict[str, dict] = {}
    for key in keys:
        active = [
            (v, t) for v, t in zip(votes[key], trend_dir)
            if v != "NEUTRAL" and t != "NEUTRAL"
        ]
        nonneutral = sum(1 for v in votes[key] if v != "NEUTRAL")
        counter = sum(1 for v, t in active if v != t)
        per_engine[key] = {
            "samples": samples,
            "vote_rate_pct": round(100.0 * nonneutral / samples, 1) if samples else 0.0,
            "counter_trend_pct": (
                round(100.0 * counter / len(active), 1) if active else None
            ),
        }

    return {"per_engine": per_engine, "pairwise_agreement": agreement}


# ── Study 2: leave-one-out ablation ─────────────────────────────────────

@dataclass
class VariantResult:
    variant: str
    trades: int
    win_rate: float          # fraction 0–1 (engine convention)
    profit_factor: float
    max_drawdown_pct: float
    expectancy_usd: float
    gate_rejections: dict = field(default_factory=dict)
    delta_pf: float | None = None
    delta_wr_pp: float | None = None
    delta_trades: int | None = None


def _run_variant(
    df: pd.DataFrame,
    symbol: str,
    engine_config: dict,
    use_reversal_veto: bool = True,
    bt_step: int = 4,
) -> VariantResult:
    from backtesting.backtest_engine import BacktestConfig, run_backtest

    bt_cfg = BacktestConfig.from_profile(
        symbol, use_reversal_veto=use_reversal_veto, step_bars=bt_step
    )
    res = run_backtest(df, config=bt_cfg, engine_config=engine_config)
    closed = [t for t in res.trades if t.exit_bar >= 0]
    expectancy = (
        round(sum(t.pnl_usd for t in closed) / len(closed), 2) if closed else 0.0
    )
    return VariantResult(
        variant="",  # filled by caller
        trades=res.execute_count,
        win_rate=res.win_rate,                       # fraction
        profit_factor=round(res.profit_factor, 3),
        max_drawdown_pct=round(res.max_drawdown_pct * 100, 2),  # frac → %
        expectancy_usd=expectancy,
        gate_rejections=dict(res.gate_rejections),
    )


def loo_ablation(df: pd.DataFrame, symbol: str, base_config: dict,
                 bt_step: int = 4) -> list[dict]:
    """Baseline + leave-one-out + add-one-in + H013-off variants."""
    enabled = base_config.get("engines", {}).get("enabled", {})
    active = [k for k, v in enabled.items() if v]
    research_pool = [
        k for k in ("divergence", "market_structure", "sentiment")
        if not enabled.get(k)
    ]

    rows: list[VariantResult] = []

    base = _run_variant(df, symbol, base_config, bt_step=bt_step)
    base.variant = "BASELINE (" + "+".join(sorted(active)) + ")"
    rows.append(base)

    def _with(enabled_overrides: dict) -> dict:
        cfg = copy.deepcopy(base_config)
        cfg.setdefault("engines", {}).setdefault("enabled", {}).update(
            enabled_overrides
        )
        return cfg

    for key in sorted(active):
        r = _run_variant(df, symbol, _with({key: False}), bt_step=bt_step)
        r.variant = f"LOO: −{key}"
        rows.append(r)

    for key in sorted(research_pool):
        r = _run_variant(df, symbol, _with({key: True}), bt_step=bt_step)
        r.variant = f"ADD: +{key}"
        rows.append(r)

    r = _run_variant(df, symbol, base_config, use_reversal_veto=False,
                     bt_step=bt_step)
    r.variant = "ABLATE: H013 reversal veto OFF"
    rows.append(r)

    out = []
    for r in rows:
        if r.variant != base.variant:
            r.delta_pf = round(r.profit_factor - base.profit_factor, 3)
            r.delta_wr_pp = round((r.win_rate - base.win_rate) * 100, 1)
            r.delta_trades = r.trades - base.trades
        out.append(vars(r))
    return out


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> None:
    from backtest.runner import find_symbol_csv, load_symbol_data
    from utils.helpers import load_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--data-dir", default="data", type=Path)
    parser.add_argument("--output", default=None,
                        help="Write JSON results here")
    parser.add_argument("--skip-independence", action="store_true")
    parser.add_argument("--bars", type=int, default=None,
                        help="Use only the last N bars (speed cap)")
    parser.add_argument("--bt-step", type=int, default=4,
                        help="Backtest pipeline stride in bars")
    parser.add_argument("--step", type=int, default=8,
                        help="Vote-sampling stride for study 1")
    parser.add_argument("--synthetic-label", action="store_true",
                        help="Mark results as synthetic-data mechanics "
                             "validation (NOT edge evidence)")
    args = parser.parse_args()

    base_config = load_config()
    results: dict = {
        "study": "engine_ablation",
        "data_integrity": (
            "SYNTHETIC — mechanics validation only, NOT edge evidence"
            if args.synthetic_label else
            "historical CSV — walk-forward, production gate parity"
        ),
        "symbols": {},
    }

    for symbol in args.symbols:
        print(f"\n=== {symbol} ===")
        path = find_symbol_csv(symbol, args.data_dir)
        df = load_symbol_data(symbol, args.data_dir)
        if args.bars and len(df) > args.bars:
            df = df.iloc[-args.bars:]
        sym_res: dict = {"csv": str(path), "bars": len(df)}

        if not args.skip_independence:
            print("[1/2] Vote independence matrix …")
            indep = vote_independence(
                df, base_config["data"]["timeframes"], step=args.step
            )
            sym_res["independence"] = indep
            print(f"{'engine':<18}{'vote%':>8}{'counter-trend%':>16}")
            for k, v in indep["per_engine"].items():
                ct = v["counter_trend_pct"]
                print(f"{k:<18}{v['vote_rate_pct']:>7.1f}%"
                      f"{(f'{ct:.1f}%' if ct is not None else 'n/a'):>16}")
            print("\nHighest pairwise agreement (both non-neutral):")
            pairs = sorted(
                ((p, d) for p, d in indep["pairwise_agreement"].items()
                 if d["agree_pct"] is not None),
                key=lambda x: -x[1]["agree_pct"],
            )[:6]
            for p, d in pairs:
                print(f"  {p:<28} {d['agree_pct']:>5.1f}%  (n={d['n']})")

        print("[2/2] Leave-one-out ablation …")
        rows = loo_ablation(df, symbol, base_config, bt_step=args.bt_step)
        sym_res["ablation"] = rows
        print(f"{'variant':<38}{'trades':>7}{'WR':>8}{'PF':>7}"
              f"{'ΔPF':>8}{'ΔWR(pp)':>9}")
        for r in rows:
            dpf = "" if r["delta_pf"] is None else f"{r['delta_pf']:+.3f}"
            dwr = "" if r["delta_wr_pp"] is None else f"{r['delta_wr_pp']:+.1f}"
            print(f"{r['variant']:<38}{r['trades']:>7}"
                  f"{r['win_rate']:>7.1%}{r['profit_factor']:>7.2f}"
                  f"{dpf:>8}{dwr:>9}")

        results["symbols"][symbol] = sym_res

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2))
        print(f"\nResults written: {out}")


if __name__ == "__main__":
    main()
