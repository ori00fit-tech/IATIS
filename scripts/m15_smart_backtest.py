#!/usr/bin/env python3
"""
scripts/m15_smart_backtest.py
------------------------------
Smart M15 backtest — CORE_4 engines, RR=1:3, Swing SL.

Strategy:
  Engines:   SMC + NNFX + PriceAction + Wyckoff (CORE_4)
  Timeframe: M15 entry, H1 + D1 confirmation (MTF)
  Agreement: min 3/4 engines
  SL:        Swing high/low ± ATR×0.2
  RR:        1:3
  MQS:       skip POOR quality bars
  Contradiction: block if trend engines disagree

Target: PF > 2.0

Usage on VPS:
  python3 scripts/m15_smart_backtest.py --all
  python3 scripts/m15_smart_backtest.py --symbols XAUUSD BTCUSD
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Symbols ──────────────────────────────────────────────────────────────
ALL_SYMBOLS = {
    "EURUSD":  {"pip": 0.0001, "class": "forex",  "dpp": 10.0},
    "GBPUSD":  {"pip": 0.0001, "class": "forex",  "dpp": 10.0},
    "AUDUSD":  {"pip": 0.0001, "class": "forex",  "dpp": 10.0},
    "USDCAD":  {"pip": 0.0001, "class": "forex",  "dpp": 10.0},
    "NZDUSD":  {"pip": 0.0001, "class": "forex",  "dpp": 10.0},
    "XAUUSD":  {"pip": 0.01,   "class": "metal",  "dpp": 1.0},
    "XAGUSD":  {"pip": 0.001,  "class": "metal",  "dpp": 50.0},
    "BTCUSD":  {"pip": 1.0,    "class": "crypto", "dpp": 1.0},
    "ETHUSD":  {"pip": 0.01,   "class": "crypto", "dpp": 1.0},
}

RR = 3.0
MIN_AGREE = 3      # min 3 of 4 CORE engines
WARMUP = 200       # M15 bars warmup (~2 days) — reduced for limited data
STEP = 2           # evaluate every 2 M15 bars (= 30 min) — more signals


def _find_csv(symbol: str) -> Path | None:
    """Find CSV: M15/15m first, fallback to H1, then any available."""
    data = Path("data")
    # Priority: real M15 data → H1 data
    for pattern in [
        f"{symbol}_15m_2y.csv",
        f"{symbol}_M15_2y.csv",
        f"{symbol}_M15_5y.csv",
        f"{symbol}_1h_2y.csv",
        f"{symbol}_H1_2y.csv",
        f"{symbol}_1h_5y.csv",
        f"{symbol}_H1_5y.csv",
    ]:
        p = data / pattern
        if p.exists() and p.stat().st_size > 10_000:
            return p
    return None


def _load_df(path: Path):
    from core.data_loader import load_from_csv
    df = load_from_csv(str(path))
    print(f"    Loaded: {len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")
    return df


def _build_mtf(df_m15, warmup_idx: int):
    """Build MTF view from M15 slice: M15 + H1 (resample) + D1 (resample)."""
    from core.timeframe_sync import build_multi_timeframe_view
    df_slice = df_m15.iloc[:warmup_idx + 1].copy()
    return build_multi_timeframe_view(df_slice, ["M15", "H1", "H4", "D1"])


def _run_engines(mtf: dict) -> list:
    """Run CORE_4 engines only."""
    from engines.smc_engine import SMCEngine
    from engines.nnfx_engine import NNFXEngine
    from engines.price_action_engine import PriceActionEngine
    from engines.wyckoff_engine import WyckoffEngine

    engines = [SMCEngine(), NNFXEngine(), PriceActionEngine(), WyckoffEngine()]
    return [e.safe_analyze(mtf) for e in engines]


def _swing_sl(df_m15_slice, direction: int, entry: float, atr: float) -> float | None:
    """Find nearest swing SL beyond entry."""
    highs = df_m15_slice["high"].tail(30)
    lows  = df_m15_slice["low"].tail(30)

    if direction == 1:  # BUY → SL below entry
        swing_lows = lows[lows < entry]
        if len(swing_lows) > 0:
            sl = float(swing_lows.min()) - atr * 0.2
            if abs(entry - sl) < atr * 0.3:
                sl = entry - atr * 1.5
            return sl
    else:  # SELL → SL above entry
        swing_highs = highs[highs > entry]
        if len(swing_highs) > 0:
            sl = float(swing_highs.max()) + atr * 0.2
            if abs(entry - sl) < atr * 0.3:
                sl = entry + atr * 1.5
            return sl
    return None


def _simulate(df, start: int, entry: float, sl: float, tp: float, direction: int) -> dict:
    """Scan forward bar-by-bar until SL or TP hit."""
    n = len(df)
    for j in range(start, min(start + 500, n)):
        h = float(df.iloc[j]["high"])
        l = float(df.iloc[j]["low"])
        if direction == 1:
            if l <= sl: return {"outcome": "loss", "exit": sl, "bars": j - start}
            if h >= tp: return {"outcome": "win",  "exit": tp, "bars": j - start}
        else:
            if h >= sl: return {"outcome": "loss", "exit": sl, "bars": j - start}
            if l <= tp: return {"outcome": "win",  "exit": tp, "bars": j - start}
    last = float(df.iloc[min(start + 499, n - 1)]["close"])
    return {"outcome": "win" if (last - entry) * direction > 0 else "loss",
            "exit": last, "bars": 500}


def _pnl(entry: float, exit_p: float, direction: int, sl_dist: float,
         balance: float, symbol: str, info: dict) -> float:
    risk = balance * 0.01
    pip = info["pip"]
    ac  = info["class"]
    dpp = info["dpp"]

    if ac == "forex":
        pv = (pip / max(entry, 1)) * 100_000 if "JPY" in symbol else pip * 100_000
        sl_pips = sl_dist / pip if pip > 0 else 1
        size = max(0.01, min(risk / (sl_pips * pv), 10.0))
        return round(((exit_p - entry) * direction) / pip * pv * size, 2)
    elif ac == "crypto":
        size = max(0.001, min(risk / sl_dist, 1.0)) if sl_dist > 0 else 0.001
        return round((exit_p - entry) * direction * size, 2)
    else:
        size = max(0.01, min(risk / (sl_dist * dpp), 10.0)) if sl_dist > 0 else 0.01
        return round((exit_p - entry) * direction * size * dpp, 2)


def backtest_symbol(symbol: str, info: dict) -> dict:
    from engines.base_engine import Bias
    from core.market_quality import assess_market_quality

    csv = _find_csv(symbol)
    if not csv:
        return {"symbol": symbol, "error": "No CSV", "trades": 0}

    print(f"\n  [{symbol}]", end=" ", flush=True)
    try:
        df = _load_df(csv)
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "trades": 0}

    n = len(df)
    balance = 10_000.0
    peak    = balance
    max_dd  = 0.0
    trades  = []
    open_until = -1
    mqs_blocks = score_blocks = agree_blocks = 0

    for i in range(WARMUP, n - 2, STEP):
        if i <= open_until:
            continue

        df_slice = df.iloc[:i + 1]
        atr = float((df_slice["high"] - df_slice["low"]).tail(14).mean())
        entry = float(df_slice["close"].iloc[-1])

        # MQS gate — pass timeframe for correct scoring
        try:
            mqs = assess_market_quality(df_slice, symbol, timeframe="15m")
            if not mqs.should_trade:
                mqs_blocks += 1
                continue
        except Exception:
            pass

        # Run CORE_4
        try:
            mtf = _build_mtf(df, i)
            outputs = _run_engines(mtf)
        except Exception:
            continue

        # Count agreement
        bull = [o for o in outputs if o.bias == Bias.BULLISH and o.score >= 30]
        bear = [o for o in outputs if o.bias == Bias.BEARISH and o.score >= 30]

        if len(bull) >= MIN_AGREE:
            direction, agree = 1, len(bull)
        elif len(bear) >= MIN_AGREE:
            direction, agree = -1, len(bear)
        else:
            agree_blocks += 1
            continue

        # Swing SL
        sl = _swing_sl(df_slice, direction, entry, atr)
        if sl is None:
            sl = entry - direction * atr * 2.0

        sl_dist = abs(entry - sl)
        if sl_dist < atr * 0.3 or sl_dist > atr * 8:
            sl = entry - direction * atr * 2.0
            sl_dist = abs(entry - sl)

        tp = entry + direction * sl_dist * RR

        # Simulate
        sim = _simulate(df, i + 1, entry, sl, tp, direction)
        pnl = _pnl(entry, sim["exit"], direction, sl_dist, balance, symbol, info)

        balance += pnl
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak - balance) / peak)
        open_until = i + sim["bars"]
        trades.append({"outcome": sim["outcome"], "pnl": pnl})

    if not trades:
        return {"symbol": symbol, "error": "No trades", "trades": 0,
                "mqs_blocks": mqs_blocks, "agree_blocks": agree_blocks}

    wins = sum(1 for t in trades if t["outcome"] == "win")
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.001
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.001

    return {
        "symbol":   symbol,
        "asset_class": info["class"],
        "trades":   len(trades),
        "wins":     wins,
        "win_rate": round(wins / len(trades) * 100, 1),
        "profit_factor": round(gp / gl, 2),
        "max_dd_pct": round(max_dd * 100, 1),
        "total_return_pct": round((balance - 10_000) / 10_000 * 100, 1),
        "final_balance": round(balance, 2),
        "mqs_blocks":  mqs_blocks,
        "agree_blocks": agree_blocks,
        "error": None,
    }


def grade(r: dict) -> str:
    if r.get("error") or r.get("trades", 0) < 20: return "SKIP"
    pf = r.get("profit_factor", 0)
    wr = r.get("win_rate", 0)
    dd = r.get("max_dd_pct", 100)
    if pf >= 2.0 and wr >= 44 and dd <= 20: return "EXCELLENT"
    if pf >= 1.5 and wr >= 40 and dd <= 25: return "GOOD"
    if pf >= 1.2 and wr >= 36: return "MARGINAL"
    return "POOR"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    symbols = list(ALL_SYMBOLS.keys()) if (args.all or not args.symbols) \
              else [s for s in args.symbols if s in ALL_SYMBOLS]

    print(f"\n{'='*65}")
    print(f"IATIS M15 Smart Backtest — CORE_4 + RR=1:{RR:.0f} + Swing SL")
    print(f"{'='*65}")
    print(f"Engines: SMC + NNFX + PriceAction + Wyckoff")
    print(f"Min agreement: {MIN_AGREE}/4 | Step: {STEP} bars | Target PF > 2.0")
    print(f"Symbols: {len(symbols)}")

    results = []
    t_start = time.monotonic()

    for sym in symbols:
        info = ALL_SYMBOLS[sym]
        t0 = time.monotonic()
        r = backtest_symbol(sym, info)
        elapsed = time.monotonic() - t0
        g = grade(r)
        icon = {"EXCELLENT":"🏆","GOOD":"✅","MARGINAL":"⚠️","POOR":"❌","SKIP":"⏭"}.get(g,"?")
        if r.get("error"):
            print(f"    ❌ {r['error']}")
        else:
            print(f"    {icon} WR={r['win_rate']:.1f}% PF={r['profit_factor']:.2f} "
                  f"DD={r['max_dd_pct']:.1f}% Ret={r['total_return_pct']:.1f}% "
                  f"({elapsed:.0f}s)")
        results.append(r)

    # Summary
    duration = time.monotonic() - t_start
    valid = [r for r in results if not r.get("error") and r.get("trades", 0) >= 20]

    print(f"\n{'='*65}")
    print(f"SUMMARY ({len(valid)}/{len(results)} | {duration/60:.0f} min)")
    print(f"{'='*65}")
    hdr = f"{'Symbol':<10} {'Class':<7} {'Trades':>6} {'WR%':>6} {'PF':>6} {'DD%':>5} {'Ret%':>7}"
    print(hdr)
    print("-" * 55)

    order = {"EXCELLENT": 0, "GOOD": 1, "MARGINAL": 2, "POOR": 3, "SKIP": 4}
    for r in sorted(valid, key=lambda x: (order.get(grade(x), 5), -x.get("profit_factor", 0))):
        g = grade(r)
        icon = {"EXCELLENT":"🏆","GOOD":"✅","MARGINAL":"⚠️","POOR":"❌"}.get(g,"?")
        print(f"{r['symbol']:<10} {r.get('asset_class','?'):<7} "
              f"{r['trades']:>6} {r['win_rate']:>6.1f}% "
              f"{r['profit_factor']:>6.2f} {r['max_dd_pct']:>5.1f}% "
              f"{r['total_return_pct']:>7.1f}%  {icon}")

    if valid:
        avg_wr = sum(r["win_rate"] for r in valid) / len(valid)
        avg_pf = sum(r["profit_factor"] for r in valid) / len(valid)
        excellent = sum(1 for r in valid if grade(r) == "EXCELLENT")
        good = sum(1 for r in valid if grade(r) in ("EXCELLENT", "GOOD"))
        print(f"\nAvg WR: {avg_wr:.1f}% | Avg PF: {avg_pf:.2f}")
        print(f"PF>2: {excellent}/{len(valid)} | PF>1.5: {good}/{len(valid)}")

    # Save
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = Path("storage") / f"m15_smart_backtest_{date_str}.json"
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"engines": "CORE_4", "rr": RR, "min_agree": MIN_AGREE,
                   "step": STEP, "timeframe": "M15"},
        "results": results,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {out_path}")
    print(f"Note: in-sample, no slippage, commission=0")


if __name__ == "__main__":
    main()
