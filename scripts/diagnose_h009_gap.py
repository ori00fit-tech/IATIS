#!/usr/bin/env python3
"""
scripts/diagnose_h009_gap.py
------------------------------
Diagnoses the gap between H009 (PF=3.08) and current system (PF=1.2).

Tests 4 configurations on same data to isolate the cause:
  A: H009 original (6 engines, count voting, step=4, SL=ATR×1.5, RR=3)
  B: Same + weight-based voting
  C: Same + 9 engines
  D: Current v0.5 (9 engines, weight, step=8, SL=swing, RR=2)

Run: python3 scripts/diagnose_h009_gap.py
"""
from __future__ import annotations
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.data_loader import load_from_csv, load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias
from utils.helpers import load_config


def run_simple_backtest(
    symbol: str,
    df,
    n_engines: int = 6,
    use_weights: bool = False,
    step: int = 4,
    rr: float = 3.0,
    sl_mult: float = 1.5,
    min_agree: int = 4,
    warmup: int = 210,
) -> dict:
    """Minimal backtest without full pipeline — isolates engine config effects."""
    from main import build_active_engines
    from confluence.voting_system import tally_votes
    from confluence.score_calculator import calculate_score

    cfg = load_config()
    cfg['data']['symbol'] = symbol
    cfg['data']['source'] = 'synthetic'

    # Build engines subset
    all_engines = build_active_engines(cfg)
    engines = all_engines[:n_engines]  # first N engines

    pip = {'EURUSD':0.0001,'BTCUSD':1.0,'XAUUSD':0.01}.get(symbol, 0.0001)
    weights = cfg['confluence']['weights']

    balance = 10_000.0
    peak = balance
    max_dd = 0.0
    trades = []
    open_until = -1
    n = len(df)

    for i in range(warmup, n-2, step):
        if i <= open_until:
            continue

        df_slice = df.iloc[:i+1].copy()
        mtf = build_multi_timeframe_view(df_slice, ['H1','H4','D1'])

        outputs = [e.safe_analyze(mtf) for e in engines]

        if use_weights:
            score_r = calculate_score(outputs, weights)
            vote_bias = Bias.BULLISH if score_r.final_score > 0 else Bias.BEARISH
            agree = sum(1 for o in outputs if o.bias == vote_bias and o.score >= 30)
        else:
            bullish = sum(1 for o in outputs if o.bias == Bias.BULLISH)
            bearish = sum(1 for o in outputs if o.bias == Bias.BEARISH)
            if bullish > bearish:
                vote_bias, agree = Bias.BULLISH, bullish
            else:
                vote_bias, agree = Bias.BEARISH, bearish

        if agree < min_agree:
            continue

        # SL/TP
        atr = float((df_slice['high'] - df_slice['low']).tail(14).mean())
        entry = float(df_slice['close'].iloc[-1])
        direction = 1 if vote_bias == Bias.BULLISH else -1
        sl = entry - direction * atr * sl_mult
        tp = entry + direction * atr * sl_mult * rr

        # Simulate
        outcome = None
        bars_held = 0
        for j in range(i+1, min(i+300, n)):
            h = float(df.iloc[j]['high'])
            l = float(df.iloc[j]['low'])
            bars_held = j - i
            if direction == 1:
                if l <= sl: outcome = 'loss'; break
                if h >= tp: outcome = 'win'; break
            else:
                if h >= sl: outcome = 'loss'; break
                if l <= tp: outcome = 'win'; break

        if outcome is None:
            last = float(df.iloc[min(i+299, n-1)]['close'])
            outcome = 'win' if (last-entry)*direction > 0 else 'loss'

        # P&L
        exit_p = tp if outcome == 'win' else sl
        sl_dist = abs(entry - sl)
        risk = balance * 0.01
        if 'JPY' in symbol:
            pv = (pip/entry)*100_000
        else:
            pv = pip * 100_000
        sl_pips = sl_dist/pip if pip > 0 else 1
        size = max(0.01, min(risk/(sl_pips*pv), 10.0)) if sl_pips > 0 and pv > 0 else 0.01
        pnl = ((exit_p-entry)*direction)/pip*pv*size if pip > 0 else 0

        balance += pnl
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak-balance)/peak)
        open_until = i + bars_held
        trades.append({'outcome': outcome, 'pnl': round(pnl, 2)})

    if not trades:
        return {'trades': 0, 'wr': 0, 'pf': 0, 'dd': 0, 'return': 0}

    wins = sum(1 for t in trades if t['outcome'] == 'win')
    gp = sum(t['pnl'] for t in trades if t['pnl'] > 0) or 0.001
    gl = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0)) or 0.001

    return {
        'trades': len(trades),
        'wr': round(wins/len(trades)*100, 1),
        'pf': round(gp/gl, 2),
        'dd': round(max_dd*100, 1),
        'return': round((balance-10000)/10000*100, 1),
    }


def main():
    print("\n" + "="*65)
    print("H009 GAP DIAGNOSIS — Isolating performance drop")
    print("="*65)
    print()

    # Load data
    symbol = "EURUSD"
    csv = Path("data/EURUSD_H1_2y.csv")
    if csv.exists():
        df = load_from_csv(str(csv))
        print(f"Data: {symbol} H1 2y ({len(df)} bars)")
    else:
        df = load_synthetic(3000, seed=42)
        print(f"Data: synthetic ({len(df)} bars)")

    configs = [
        {"label": "A: H009 original   (6eng, count, step=4, RR=3, SL=1.5×ATR)",
         "n_engines": 6, "use_weights": False, "step": 4, "rr": 3.0, "sl_mult": 1.5},
        {"label": "B: + weight voting (6eng, weight, step=4, RR=3, SL=1.5×ATR)",
         "n_engines": 6, "use_weights": True,  "step": 4, "rr": 3.0, "sl_mult": 1.5},
        {"label": "C: + 9 engines     (9eng, weight, step=4, RR=3, SL=1.5×ATR)",
         "n_engines": 9, "use_weights": True,  "step": 4, "rr": 3.0, "sl_mult": 1.5},
        {"label": "D: + RR=2          (9eng, weight, step=4, RR=2, SL=1.5×ATR)",
         "n_engines": 9, "use_weights": True,  "step": 4, "rr": 2.0, "sl_mult": 1.5},
        {"label": "E: + step=8        (9eng, weight, step=8, RR=2, SL=1.5×ATR)",
         "n_engines": 9, "use_weights": True,  "step": 8, "rr": 2.0, "sl_mult": 1.5},
        {"label": "F: + swing SL      (9eng, weight, step=8, RR=2, SL=2.5×ATR)",
         "n_engines": 9, "use_weights": True,  "step": 8, "rr": 2.0, "sl_mult": 2.5},
        {"label": "G: OPTIMAL?        (6eng, count, step=4, RR=2, SL=2.0×ATR)",
         "n_engines": 6, "use_weights": False, "step": 4, "rr": 2.0, "sl_mult": 2.0},
    ]

    print(f"\n{'Config':<55} {'Trades':>6} {'WR%':>6} {'PF':>6} {'DD%':>5} {'Ret%':>6}")
    print("-"*85)

    best = None
    for cfg in configs:
        t0 = time.monotonic()
        r = run_simple_backtest(
            symbol, df,
            n_engines=cfg['n_engines'],
            use_weights=cfg['use_weights'],
            step=cfg['step'],
            rr=cfg['rr'],
            sl_mult=cfg['sl_mult'],
        )
        elapsed = time.monotonic() - t0
        pf_str = f"{r['pf']:.2f}"
        flag = "✅" if r['pf'] >= 1.5 and r['wr'] >= 44 else "⚠️" if r['pf'] >= 1.2 else "❌"
        print(f"{cfg['label']:<55} {r['trades']:>6} {r['wr']:>6.1f}% {pf_str:>6} {r['dd']:>5.1f}% {r['return']:>5.1f}%  {flag} ({elapsed:.0f}s)")
        if best is None or r['pf'] > best[1]:
            best = (cfg['label'], r['pf'], r)

    print()
    print(f"Best config: {best[0].split('(')[0].strip()} → PF={best[1]:.2f}")
    print()
    print("Diagnosis:")
    print("  Compare A vs B: effect of weight-based voting")
    print("  Compare B vs C: effect of adding 3 more engines")
    print("  Compare C vs D: effect of RR 3→2")
    print("  Compare D vs E: effect of step 4→8")
    print("  Compare E vs F: effect of SL method (1.5×→2.5×ATR)")
    print("  Compare A vs G: H009-style but with optimal RR+SL")


if __name__ == "__main__":
    main()
