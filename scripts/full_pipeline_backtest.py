#!/usr/bin/env python3
"""
scripts/full_pipeline_backtest.py
-----------------------------------
Full IATIS v0.4 pipeline backtest — correct architecture.

Two-pass design (same as backtest_engine.py):
  Pass 1 (Signal Generation): For each bar i, run full pipeline.
                               If EXECUTE → record signal + SL/TP.
  Pass 2 (Trade Simulation):  For each signal, scan forward bars
                               until SL or TP hit (no step skip).
                               Only one open trade per symbol at a time.

This eliminates:
  - Multiple overlapping trades from step-based scanning
  - SL/TP being missed between steps
  - Wrong P&L from partial simulation

Usage:
    python3 scripts/full_pipeline_backtest.py --all --step 8
    python3 scripts/full_pipeline_backtest.py --symbols EURUSD BTCUSD --step 6
"""
from __future__ import annotations

import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_all_symbols import (
    ALL_SYMBOLS, PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT
)

ACTIVE_SYMBOLS = [
    "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
    "EURJPY","AUDJPY","EURGBP","EURCHF",
    "XAUUSD","XAGUSD","USOIL",
    "US30","NAS100","SPX500",
    "BTCUSD","ETHUSD",
]


def build_config(symbol: str) -> dict:
    from utils.helpers import load_config
    cfg = load_config()
    cfg["data"]["symbol"] = symbol
    cfg["data"]["twelve_data_symbol"] = symbol
    cfg["fundamentals"] = {"news_filter_enabled": False}
    cfg["telegram"] = {"enabled": False}
    return cfg


def run_pipeline_at_bar(symbol: str, df_slice, cfg: dict) -> dict | None:
    """Run full IATIS pipeline at one bar. Returns signal dict or None."""
    try:
        from core.timeframe_sync import build_multi_timeframe_view
        from core.market_quality import assess_market_quality
        from regimes.regime_detector import detect_regime
        from confluence.regime_weights import apply_regime_weights
        from confluence.voting_system import tally_votes
        from confluence.score_calculator import calculate_score
        from confluence.contradiction_engine import check_contradictions
        from confluence.mtf_confirmation import check_mtf_confirmation
        from risk.correlation_engine import check_correlation
        from research.edge_gate import check_edge_gate, EdgeNotProvenError
        from main import build_active_engines
        from engines.base_engine import Bias
        from engines.smc_engine import find_swing_points

        mtf = build_multi_timeframe_view(df_slice, ["M15","H1","H4","D1"])
        df_h1 = mtf.get("H1", df_slice)

        if len(df_h1) < 50:
            return None

        # MQS
        bar_time = df_h1.index[-1]
        if hasattr(bar_time, 'to_pydatetime'):
            bar_time = bar_time.to_pydatetime()
        mqs = assess_market_quality(df_h1, symbol, now=bar_time)
        if not mqs.should_trade:
            return {"blocked": "mqs"}

        # News blackout
        try:
            from scripts.download_historical_news import get_events_in_window
            events = get_events_in_window(symbol, bar_time, 60)
            if events and any(abs(e.get("minutes_until", 999)) <= 30 for e in events):
                return {"blocked": "news"}
        except Exception:
            pass

        # Regime
        regime_result = detect_regime(df_h1)
        regime_state = "TRENDING"
        if regime_result is not None:
            try:
                regime_state = regime_result.regime.value
            except Exception:
                pass

        volatility = "normal"
        try:
            from regimes.volatility_classifier import classify_volatility
            vr = classify_volatility(df_h1)
            if vr is not None:
                if hasattr(vr, 'level'):
                    volatility = str(vr.level)
                elif hasattr(vr, 'iloc'):
                    volatility = str(vr.iloc[-1]) if len(vr) > 0 else "normal"
        except Exception:
            pass

        base_weights = cfg["confluence"]["weights"]
        active_weights = apply_regime_weights(base_weights, regime_state, volatility)

        # Edge gate
        try:
            check_edge_gate(cfg.get("engines", {}).get("enabled", {}))
        except EdgeNotProvenError:
            return None

        # Run engines
        active_engines = build_active_engines(cfg)
        outputs = [e.safe_analyze(mtf) for e in active_engines]

        # Confluence
        vote_result = tally_votes(outputs)
        score_result = calculate_score(outputs, active_weights)
        contradiction_result = check_contradictions(outputs)
        mtf_result = check_mtf_confirmation(vote_result.winning_bias.value, mtf)

        adjusted_score = max(0, min(100, score_result.final_score + mtf_result.score_adjustment))
        min_score = cfg["confluence"]["min_score_to_trade"]
        min_engines = cfg["confluence"]["min_engines_agreeing"]

        if contradiction_result.blocked:
            return {"blocked": "contradiction"}
        if adjusted_score < min_score or vote_result.agree_count < min_engines:
            return {"blocked": "score"}

        # SL/TP calculation — correct swing-based
        atr = float((df_h1["high"] - df_h1["low"]).tail(14).mean())
        direction = 1 if vote_result.winning_bias == Bias.BULLISH else -1
        entry = float(df_h1["close"].iloc[-1])

        sl = None
        try:
            swings = find_swing_points(df_h1, window=3)
            if direction == 1:  # BUY → SL below entry
                lows = df_h1["low"][swings["swing_low"]].tail(10)
                valid = lows[lows < entry]
                if len(valid) > 0:
                    sl = float(valid.iloc[-1]) - atr * 0.3
            else:  # SELL → SL above entry
                highs = df_h1["high"][swings["swing_high"]].tail(10)
                valid = highs[highs > entry]
                if len(valid) > 0:
                    sl = float(valid.iloc[-1]) + atr * 0.3
        except Exception:
            pass

        sl_mult = cfg.get("risk", {}).get("sl_atr_multiplier", 2.5)
        if sl is None or abs(entry - sl) < atr * 0.5 or abs(entry - sl) > atr * 8:
            sl = entry - direction * atr * sl_mult

        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return None

        tp = entry + direction * sl_dist * cfg["risk"]["min_risk_reward"]

        return {
            "blocked": None,
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "sl_dist": sl_dist,
            "atr": atr,
            "score": adjusted_score,
            "bar_index": len(df_slice) - 1,
        }

    except Exception:
        return None


def simulate_trade(signal: dict, df: "import pandas; pandas.DataFrame",
                   start_idx: int, ac: str, pip: float, dpp: float) -> dict:
    """Simulate one trade forward from start_idx."""
    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]
    direction = signal["direction"]
    n = len(df)

    for j in range(start_idx, min(start_idx + 300, n)):
        try:
            bar = df.iloc[j]
            h = float(bar["high"])
            l = float(bar["low"])
        except Exception:
            continue

        if direction == 1:  # BUY
            if bool(l <= sl):
                return {"outcome": "loss", "exit": sl, "bars": j - start_idx}
            if bool(h >= tp):
                return {"outcome": "win", "exit": tp, "bars": j - start_idx}
        else:  # SELL
            if bool(h >= sl):
                return {"outcome": "loss", "exit": sl, "bars": j - start_idx}
            if bool(l <= tp):
                return {"outcome": "win", "exit": tp, "bars": j - start_idx}

    # Force close
    try:
        last_close = float(df.iloc[min(start_idx + 299, n - 1)]["close"])
    except Exception:
        last_close = entry
    price_diff = (last_close - entry) * direction
    return {
        "outcome": "win" if price_diff > 0 else "loss",
        "exit": last_close,
        "bars": 300,
    }


def calc_pnl(signal: dict, sim: dict, balance: float,
             ac: str, pip: float, dpp: float) -> float:
    """Calculate P&L in USD with correct pip values per asset class."""
    entry = signal["entry"]
    exit_p = sim["exit"]
    direction = signal["direction"]
    sl_dist = signal["sl_dist"]
    symbol = signal.get("symbol", "")

    risk_usd = balance * 0.01

    if ac == "forex":
        # JPY pairs: pip value depends on current price
        # USDJPY at 150: 1 pip = $0.01/150 × 100,000 = $6.67 per lot
        # EUR pairs: 1 pip = $0.0001 × 100,000 = $10.00 per lot
        if "JPY" in symbol:
            pip_val_per_lot = (pip / max(entry, 1)) * 100_000
        else:
            pip_val_per_lot = pip * 100_000  # = $10 for standard lot

        sl_pips = sl_dist / pip if pip > 0 else 1
        if sl_pips <= 0:
            return 0.0
        size = max(0.01, min(risk_usd / (sl_pips * pip_val_per_lot), 10.0))
        pnl = ((exit_p - entry) * direction) / pip * pip_val_per_lot * size

    elif ac == "crypto":
        size = max(0.001, min(risk_usd / sl_dist, 1.0)) if sl_dist > 0 else 0.001
        pnl = (exit_p - entry) * direction * size

    else:  # metal, index, energy
        size = max(0.01, min(risk_usd / (sl_dist * dpp), 10.0)) if sl_dist > 0 else 0.01
        pnl = (exit_p - entry) * direction * size * dpp

    return round(pnl, 2)


def backtest_symbol_full_pipeline(
    symbol: str, df, step: int = 4, warmup: int = 220
) -> dict:
    """Two-pass backtest: signal generation + trade simulation."""
    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)
    cfg = build_config(symbol)

    balance = 10_000.0
    peak_balance = balance
    max_dd = 0.0
    trades = []

    mqs_blocks = 0
    news_blocks = 0
    contradiction_blocks = 0
    score_blocks = 0
    total_runs = 0
    n = len(df)

    # Track open trade to prevent overlapping
    open_until_bar = -1

    for i in range(warmup, n - 2, step):
        total_runs += 1

        # Skip if trade still open
        if i <= open_until_bar:
            continue

        df_slice = df.iloc[:i+1].copy()
        signal = run_pipeline_at_bar(symbol, df_slice, cfg)

        if signal is None:
            continue

        blocked = signal.get("blocked")
        if blocked == "mqs":
            mqs_blocks += 1
            continue
        if blocked == "news":
            news_blocks += 1
            continue
        if blocked == "contradiction":
            contradiction_blocks += 1
            continue
        if blocked == "score":
            score_blocks += 1
            continue
        if blocked is not None:
            continue

        # Valid EXECUTE signal — simulate trade
        sim = simulate_trade(signal, df, i + 1, ac, pip, dpp)
        pnl = calc_pnl(signal, sim, balance, ac, pip, dpp)

        balance += pnl
        peak_balance = max(peak_balance, balance)
        dd = (peak_balance - balance) / peak_balance
        max_dd = max(max_dd, dd)

        # Mark bars as occupied
        open_until_bar = i + sim["bars"]

        trades.append({"outcome": sim["outcome"], "pnl": pnl})

    if not trades:
        return {
            "symbol": symbol, "trades": 0, "error": "No trades",
            "mqs_blocks": mqs_blocks, "news_blocks": news_blocks,
            "contradiction_blocks": contradiction_blocks,
        }

    wins = sum(1 for t in trades if t["outcome"] == "win")
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.001
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.001

    return {
        "symbol": symbol, "asset_class": ac,
        "trades": len(trades), "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(wins / len(trades) * 100, 1),
        "profit_factor": round(gp / gl, 2),
        "max_drawdown_pct": round(max_dd * 100, 1),
        "total_return_pct": round((balance - 10_000) / 10_000 * 100, 1),
        "final_balance": round(balance, 2),
        "total_bars_evaluated": total_runs,
        "mqs_blocks": mqs_blocks,
        "news_blocks": news_blocks,
        "contradiction_blocks": contradiction_blocks,
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
    if pf >= 1.5 and wr >= 50 and dd <= 20:
        return "GOOD"
    if pf >= 1.2 and wr >= 44:
        return "MARGINAL"
    return "POOR"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=6)
    args = parser.parse_args()

    symbols = ACTIVE_SYMBOLS if (args.all or args.symbols is None) else args.symbols

    print(f"\n{'='*70}")
    print(f"IATIS Full Pipeline Backtest v0.4 (Two-Pass Design)")
    print(f"{'='*70}")
    print(f"Symbols: {len(symbols)} | Step: {args.step} bars | No overlapping trades")
    print()

    from core.data_loader import load_from_csv
    data_dir = Path("data")
    results = []
    t_start = time.monotonic()

    for i, symbol in enumerate(symbols, 1):
        csv_file = None
        for pattern in [f"{symbol}_H1_2y.csv", f"{symbol}_H1_5y.csv"]:
            p = data_dir / pattern
            if p.exists():
                csv_file = p
                break

        print(f"[{i:2}/{len(symbols)}] {symbol:10} ... ", end="", flush=True)

        if not csv_file:
            print("❌ no CSV")
            results.append({"symbol": symbol, "error": "No CSV", "trades": 0})
            continue

        t0 = time.monotonic()
        try:
            df = load_from_csv(str(csv_file))
            result = backtest_symbol_full_pipeline(symbol, df, step=args.step)
            elapsed = time.monotonic() - t0
            g = grade(result)
            icon = {"GOOD":"✅","MARGINAL":"⚠️","POOR":"❌","SKIP":"⏭"}.get(g,"?")
            print(
                f"{icon} {result.get('trades',0):>3} trades "
                f"WR={result.get('win_rate',0):.0f}% "
                f"PF={result.get('profit_factor',0):.2f} "
                f"DD={result.get('max_drawdown_pct',0):.0f}% "
                f"news_blk={result.get('news_blocks',0)} "
                f"({elapsed:.0f}s)"
            )
        except Exception as exc:
            print(f"❌ {str(exc)[:60]}")
            result = {"symbol": symbol, "error": str(exc)[:100], "trades": 0}

        results.append(result)

    duration = time.monotonic() - t_start
    valid = [r for r in results if not r.get("error") and r.get("trades", 0) >= 10]

    print(f"\n{'='*70}")
    print(f"SUMMARY ({len(valid)}/{len(results)} symbols | {duration/60:.0f} min)")
    print(f"{'='*70}")
    print(f"{'Symbol':<10} {'Class':<7} {'Trades':>6} {'WR%':>6} {'PF':>6} "
          f"{'DD%':>6} {'Return%':>8} {'News_blk':>9} {'Grade'}")
    print("-" * 70)

    for r in sorted(valid, key=lambda x: (
        0 if grade(x)=="GOOD" else 1 if grade(x)=="MARGINAL" else 2,
        -x.get("profit_factor", 0)
    )):
        g = grade(r)
        print(f"{r['symbol']:<10} {r.get('asset_class','?'):<7} "
              f"{r.get('trades',0):>6} "
              f"{r.get('win_rate',0):>6.1f} "
              f"{r.get('profit_factor',0):>6.2f} "
              f"{r.get('max_drawdown_pct',0):>6.1f} "
              f"{r.get('total_return_pct',0):>8.1f} "
              f"{r.get('news_blocks',0):>9} "
              f"  {g}")

    if valid:
        avg_wr = sum(r.get("win_rate",0) for r in valid) / len(valid)
        avg_pf = sum(r.get("profit_factor",0) for r in valid) / len(valid)
        goods = sum(1 for r in valid if grade(r)=="GOOD")
        total_news = sum(r.get("news_blocks",0) for r in valid)
        total_contra = sum(r.get("contradiction_blocks",0) for r in valid)
        print(f"\nAvg WR: {avg_wr:.1f}% | Avg PF: {avg_pf:.2f} | GOOD: {goods}/{len(valid)}")
        print(f"News blocked: {total_news:,} | Contradiction blocked: {total_contra:,}")

    date_str = datetime.now().strftime("%Y-%m-%d")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "v0.4_two_pass",
        "design": "Two-pass: signal generation + trade simulation (no step skip in sim)",
        "step_bars": args.step,
        "duration_sec": round(duration, 1),
        "results": results,
        "summary": {
            "total": len(symbols), "completed": len(valid),
            "good": sum(1 for r in valid if grade(r)=="GOOD"),
            "marginal": sum(1 for r in valid if grade(r)=="MARGINAL"),
            "poor": sum(1 for r in valid if grade(r)=="POOR"),
        }
    }
    out_path = Path("storage") / f"full_pipeline_backtest_{date_str}.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {out_path}")
    print(f"\n⚠️  Caveats: in-sample, no slippage, commission=0")


if __name__ == "__main__":
    main()
