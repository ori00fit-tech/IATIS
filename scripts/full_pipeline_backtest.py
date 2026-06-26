#!/usr/bin/env python3
"""
scripts/full_pipeline_backtest.py
-----------------------------------
Comprehensive backtest using the FULL IATIS pipeline on all symbols.

Unlike backtest_engine.py (which tests the confluence score only),
this tests the COMPLETE pipeline including:
  ✅ Market Quality Score (MQS) filter
  ✅ MTF Confirmation (D1 vs H1)
  ✅ Correlation Filter (max 2 per group)
  ✅ Group Contradiction H013 (reversal engines)
  ✅ News blackout (skipped — historical data only)
  ✅ Risk gate
  ✅ All 9 engines with real weights
  ✅ Regime-aware weights

This is the most realistic backtest possible without live trading.

Usage:
    python3 scripts/full_pipeline_backtest.py
    python3 scripts/full_pipeline_backtest.py --symbols EURUSD GBPUSD
    python3 scripts/full_pipeline_backtest.py --step 8 --years 2
    python3 scripts/full_pipeline_backtest.py --all --step 4

Output:
    storage/full_pipeline_backtest_YYYY-MM-DD.json
    Console summary table
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_all_symbols import (
    ALL_SYMBOLS, PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT
)

# Active symbols from config (19 symbols)
ACTIVE_SYMBOLS = [
    "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
    "EURJPY","AUDJPY","EURGBP","EURCHF",
    "XAUUSD","XAGUSD","USOIL",
    "US30","NAS100","SPX500",
    "BTCUSD","ETHUSD",
]


def build_config(symbol: str) -> dict:
    """Build pipeline config for a symbol."""
    from utils.helpers import load_config
    cfg = load_config()
    cfg["data"]["symbol"] = symbol
    cfg["data"]["twelve_data_symbol"] = symbol
    # Disable news filter for backtest (no historical news data)
    cfg["fundamentals"] = {"news_filter_enabled": False}
    # Disable telegram for backtest
    cfg["telegram"] = {"enabled": False}
    return cfg


def backtest_symbol_full_pipeline(
    symbol: str,
    df: "pd.DataFrame",
    step: int = 4,
    warmup: int = 220,
) -> dict:
    """
    Run full IATIS pipeline on historical data, bar by bar.
    
    Returns performance metrics dict.
    """
    import pandas as pd
    from backtesting.backtest_engine import BacktestConfig
    from core.market_quality import assess_market_quality
    from confluence.contradiction_engine import check_contradictions
    from risk.correlation_engine import check_correlation

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)
    
    balance = 10_000.0
    peak_balance = balance
    max_dd = 0.0
    trades = []
    
    mqs_blocks = 0
    contradiction_blocks = 0
    correlation_blocks = 0
    score_blocks = 0
    total_runs = 0
    execute_signals_this_run: list[str] = []

    n = len(df)
    
    for i in range(warmup, n - 1, step):
        total_runs += 1
        df_slice = df.iloc[:i+1].copy()
        
        # Reset correlation tracking each "run" (simulates scheduler run)
        if total_runs % 19 == 0:  # ~19 symbols per run
            execute_signals_this_run = []
        
        # Build multi-timeframe view
        from core.timeframe_sync import build_multi_timeframe_view
        mtf = build_multi_timeframe_view(df_slice, ["M15","H1","H4","D1"])
        df_h1 = mtf.get("H1", df_slice)
        
        if len(df_h1) < 50:
            continue
        
        # 1. Market Quality Score
        bar_time = df_h1.index[-1]
        if hasattr(bar_time, 'to_pydatetime'):
            bar_time = bar_time.to_pydatetime()
        
        from core.market_quality import assess_market_quality
        mqs = assess_market_quality(df_h1, symbol, now=bar_time)
        if not mqs.should_trade:
            mqs_blocks += 1
            continue
        
        # 2. Regime detection
        from regimes.regime_detector import detect_regime
        from regimes.volatility_classifier import classify_volatility
        from confluence.regime_weights import apply_regime_weights

        regime_result = detect_regime(df_h1)
        regime_state = "TRENDING"
        if regime_result is not None:
            try:
                regime_state = regime_result.regime.value
            except Exception:
                pass

        volatility = "normal"
        try:
            vol_result = classify_volatility(df_h1)
            if vol_result is not None and not isinstance(vol_result, Exception):
                # vol_result may be a Series or object
                if hasattr(vol_result, 'level'):
                    volatility = str(vol_result.level)
                elif hasattr(vol_result, 'iloc'):
                    # It's a pandas Series — get last value
                    volatility = str(vol_result.iloc[-1]) if len(vol_result) > 0 else "normal"
        except Exception:
            pass
        
        # 3. Run engines
        cfg = build_config(symbol)
        base_weights = cfg["confluence"]["weights"]
        active_weights = apply_regime_weights(base_weights, regime_state, volatility)
        
        from research.edge_gate import check_edge_gate, EdgeNotProvenError
        enabled_engines_cfg = cfg.get("engines", {}).get("enabled", {})
        try:
            check_edge_gate(enabled_engines_cfg)
        except EdgeNotProvenError:
            continue
        
        from main import build_active_engines
        try:
            active_engines = build_active_engines(cfg)
        except Exception:
            continue
        
        outputs = [e.safe_analyze(mtf) for e in active_engines]
        
        # 4. Confluence
        from confluence.voting_system import tally_votes
        from confluence.score_calculator import calculate_score
        from confluence.contradiction_engine import check_contradictions
        from confluence.mtf_confirmation import check_mtf_confirmation
        
        vote_result = tally_votes(outputs)
        score_result = calculate_score(outputs, active_weights)
        contradiction_result = check_contradictions(outputs)
        
        # MTF adjustment
        mtf_result = check_mtf_confirmation(vote_result.winning_bias.value, mtf)
        adjusted_score = max(0, min(100, score_result.final_score + mtf_result.score_adjustment))
        
        min_score = cfg["confluence"]["min_score_to_trade"]
        min_engines = cfg["confluence"]["min_engines_agreeing"]
        
        if contradiction_result.blocked:
            contradiction_blocks += 1
            continue
        if adjusted_score < min_score:
            score_blocks += 1
            continue
        if vote_result.agree_count < min_engines:
            score_blocks += 1
            continue
        
        # 5. Correlation filter
        corr = check_correlation(symbol, execute_signals_this_run)
        if not corr.allowed:
            correlation_blocks += 1
            continue
        
        # 6. Risk gate
        from engines.base_engine import Bias
        entry = float(df_h1["close"].iloc[-1])
        next_bar = df.iloc[i+1] if i+1 < n else None
        if next_bar is None:
            continue
        
        atr = float((df_h1["high"] - df_h1["low"]).tail(14).mean())
        direction = 1 if vote_result.winning_bias == Bias.BULLISH else -1

        # Swing-based SL — same as backtest_engine.py (gives WR=56-68%)
        # BUY: SL below nearest swing low | SELL: SL above nearest swing high
        try:
            from engines.smc_engine import find_swing_points
            swings = find_swing_points(df_h1, window=3)
            sl = None
            if direction == 1:  # BUY → SL below last swing low
                swing_lows = df_h1["low"][swings["swing_low"]].tail(10)
                if len(swing_lows) >= 1:
                    # SL = recent swing low minus small buffer
                    sl = float(swing_lows.iloc[-1]) - atr * 0.3
            else:  # SELL → SL above last swing high
                swing_highs = df_h1["high"][swings["swing_high"]].tail(10)
                if len(swing_highs) >= 1:
                    sl = float(swing_highs.iloc[-1]) + atr * 0.3

            # Fallback to ATR if no swing found
            if sl is None or abs(entry - sl) < atr * 0.5:
                sl_mult = cfg.get("risk", {}).get("sl_atr_multiplier", 2.5)
                sl = entry - direction * atr * sl_mult

        except Exception:
            sl_mult = cfg.get("risk", {}).get("sl_atr_multiplier", 2.5)
            sl = entry - direction * atr * sl_mult

        sl_dist = abs(entry - sl)
        if sl_dist <= 0 or sl_dist > atr * 8:  # reject unrealistic SL
            continue

        tp = entry + direction * sl_dist * cfg["risk"]["min_risk_reward"]

        # Position sizing
        risk_usd = balance * 0.01
        
        if ac == "forex":
            if pip == 0.01:  # JPY
                pip_val_per_lot = (pip / max(entry, 1)) * 100_000
            else:
                pip_val_per_lot = pip * 100_000
            sl_pips = sl_dist / pip
            size = max(0.01, min(risk_usd / (sl_pips * pip_val_per_lot), 10.0))
        else:
            size = max(0.01, min(risk_usd / (sl_dist * dpp), 10.0))
        
        # 7. Simulate trade on next H1 bars
        execute_signals_this_run.append(symbol)
        outcome = None
        exit_price = None

        # Use the original df (H1 data) for trade simulation
        df_for_sim = df_slice  # already H1
        sim_start = len(df_for_sim)

        for j in range(sim_start, min(sim_start + 200, n)):
            try:
                future_bar = df.iloc[j]
                h = float(future_bar["high"])
                l = float(future_bar["low"])
            except Exception:
                continue

            if direction == 1:  # BUY
                if bool(l <= sl):
                    outcome = "loss"
                    exit_price = sl
                    break
                if bool(h >= tp):
                    outcome = "win"
                    exit_price = tp
                    break
            else:  # SELL
                if bool(h >= sl):
                    outcome = "loss"
                    exit_price = sl
                    break
                if bool(l <= tp):
                    outcome = "win"
                    exit_price = tp
                    break
        
        if outcome is None:
            # Force close at last price
            exit_price = float(df.iloc[min(i+200, n-1)]["close"])
            price_diff = (exit_price - entry) * direction
            outcome = "win" if price_diff > 0 else "loss"
        
        # Calculate P&L
        price_diff = (exit_price - entry) * direction
        if ac == "forex":
            pips = price_diff / pip
            pnl = pips * (pip_val_per_lot if pip == 0.01 else pip * 100_000) * size
        else:
            pnl = price_diff * size * dpp
        
        balance += pnl
        peak_balance = max(peak_balance, balance)
        dd = (peak_balance - balance) / peak_balance
        max_dd = max(max_dd, dd)
        
        trades.append({
            "outcome": outcome,
            "pnl": pnl,
            "entry": entry,
            "exit": exit_price,
        })
    
    if not trades:
        return {
            "symbol": symbol, "trades": 0, "error": "No trades generated",
            "mqs_blocks": mqs_blocks, "contradiction_blocks": contradiction_blocks,
        }
    
    wins = sum(1 for t in trades if t["outcome"] == "win")
    losses = sum(1 for t in trades if t["outcome"] == "loss")
    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.001
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.001
    
    return {
        "symbol": symbol,
        "asset_class": ac,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(trades) * 100, 1),
        "profit_factor": round(gross_profit / gross_loss, 2),
        "max_drawdown_pct": round(max_dd * 100, 1),
        "total_return_pct": round((balance - 10_000) / 10_000 * 100, 1),
        "final_balance": round(balance, 2),
        # Filter statistics
        "total_bars_evaluated": total_runs,
        "mqs_blocks": mqs_blocks,
        "contradiction_blocks": contradiction_blocks,
        "correlation_blocks": correlation_blocks,
        "score_blocks": score_blocks,
        "execute_rate_pct": round(len(trades) / max(total_runs, 1) * 100, 1),
        "error": None,
    }


def grade(r: dict) -> str:
    if r.get("error") or r.get("trades", 0) < 10:
        return "SKIP"
    pf = r.get("profit_factor", 0)
    wr = r.get("win_rate", 0)
    dd = r.get("max_drawdown_pct", 100)
    if pf >= 1.5 and wr >= 50 and dd <= 15:
        return "GOOD"
    if pf >= 1.2 and wr >= 45:
        return "MARGINAL"
    return "POOR"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=6)
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    symbols = ACTIVE_SYMBOLS if args.all or args.symbols is None else args.symbols

    print(f"\n{'='*70}")
    print(f"IATIS Full Pipeline Backtest v0.4")
    print(f"{'='*70}")
    print(f"Strategy: 9 engines + MQS + MTF + Correlation + H013 + Risk gate")
    print(f"Symbols: {len(symbols)} | Step: {args.step} bars | Period: {args.years}yr H1")
    print(f"Risk: 1% per trade | RR: 1:3 minimum")
    print()

    from core.data_loader import load_from_csv
    import pandas as pd

    data_dir = Path("data")
    results = []
    t_start = time.monotonic()

    for i, symbol in enumerate(symbols, 1):
        # Find CSV
        csv_file = None
        for pattern in [f"{symbol}_H1_{args.years}y.csv", f"{symbol}_H1_2y.csv",
                        f"{symbol}_H1_5y.csv"]:
            p = data_dir / pattern
            if p.exists():
                csv_file = p
                break

        print(f"[{i:2}/{len(symbols)}] {symbol:10} ... ", end="", flush=True)

        if not csv_file:
            print("❌ no CSV — run: python3 scripts/download_all_symbols.py")
            results.append({"symbol": symbol, "error": "No CSV data", "trades": 0})
            continue

        t0 = time.monotonic()
        try:
            df = load_from_csv(str(csv_file))
            result = backtest_symbol_full_pipeline(symbol, df, step=args.step)
            elapsed = time.monotonic() - t0
            g = grade(result)
            icon = {"GOOD": "✅", "MARGINAL": "⚠️", "POOR": "❌", "SKIP": "⏭"}.get(g, "?")
            print(
                f"{icon} {result.get('trades',0):>3} trades "
                f"WR={result.get('win_rate',0):.0f}% "
                f"PF={result.get('profit_factor',0):.2f} "
                f"DD={result.get('max_drawdown_pct',0):.0f}% "
                f"MQS_block={result.get('mqs_blocks',0)} "
                f"({elapsed:.0f}s)"
            )
        except Exception as exc:
            print(f"❌ ERROR: {str(exc)[:60]}")
            result = {"symbol": symbol, "error": str(exc)[:100], "trades": 0}

        results.append(result)

    duration = time.monotonic() - t_start
    valid = [r for r in results if not r.get("error") and r.get("trades", 0) >= 10]

    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY ({len(valid)}/{len(results)} symbols | {duration/60:.0f} min)")
    print(f"{'='*70}")
    print(f"{'Symbol':<10} {'Class':<7} {'Trades':>6} {'WR%':>6} {'PF':>6} "
          f"{'DD%':>6} {'Return%':>8} {'MQS_blk':>8} {'Grade'}")
    print("-" * 70)

    for r in sorted(valid, key=lambda x: (
        0 if grade(x)=="GOOD" else 1 if grade(x)=="MARGINAL" else 2, -x.get("profit_factor",0)
    )):
        g = grade(r)
        print(f"{r['symbol']:<10} {r.get('asset_class','?'):<7} "
              f"{r.get('trades',0):>6} "
              f"{r.get('win_rate',0):>6.1f} "
              f"{r.get('profit_factor',0):>6.2f} "
              f"{r.get('max_drawdown_pct',0):>6.1f} "
              f"{r.get('total_return_pct',0):>8.1f} "
              f"{r.get('mqs_blocks',0):>8} "
              f"  {g}")

    # Stats
    if valid:
        avg_wr = sum(r.get("win_rate",0) for r in valid) / len(valid)
        avg_pf = sum(r.get("profit_factor",0) for r in valid) / len(valid)
        goods = sum(1 for r in valid if grade(r)=="GOOD")
        total_mqs = sum(r.get("mqs_blocks",0) for r in valid)
        total_trades = sum(r.get("trades",0) for r in valid)
        total_runs = sum(r.get("total_bars_evaluated",0) for r in valid)
        print(f"\nAvg WR: {avg_wr:.1f}% | Avg PF: {avg_pf:.2f} | GOOD: {goods}/{len(valid)}")
        print(f"MQS filtered: {total_mqs:,} bars ({total_mqs/max(total_runs,1)*100:.0f}% of evaluations)")
        print(f"Total signals executed: {total_trades:,}")

    # Save
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "v0.4_full_pipeline",
        "strategy": "IATIS 9-engine + MQS + MTF + Correlation + H013",
        "step_bars": args.step,
        "duration_sec": round(duration, 1),
        "results": results,
        "summary": {
            "total_symbols": len(symbols),
            "completed": len(valid),
            "good": sum(1 for r in valid if grade(r)=="GOOD"),
            "marginal": sum(1 for r in valid if grade(r)=="MARGINAL"),
            "poor": sum(1 for r in valid if grade(r)=="POOR"),
        }
    }

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = Path("storage") / f"full_pipeline_backtest_{date_str}.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {out_path}")
    print(f"\n⚠️  Caveats: in-sample, no slippage, no real news filter, commission=0")


if __name__ == "__main__":
    main()
