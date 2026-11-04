#!/usr/bin/env python3
"""
scripts/inspect_cell_trades.py
------------------------------
READ-ONLY diagnostic: re-runs ONE Matrix cell's Stage A backtest locally
(same inputs as backtest.matrix_orchestrator: the cell's own bundle /
engine_variants / confluence_overrides / risk preset, the same physical
dataset loader, backtest.optimizer.evaluate_point) and prints the trades
that dominate its R-multiple distribution. Writes NOTHING to D1 or to the
repo — run_mission()/record_trial() are never called.

Why (2026-11-04): XAUUSD quant H1 (family 75dfb7db8d71) recorded PF 1.73
over 955 trades but std_rr 4.77 (a 2R system should sit near 1.5),
largest_win $53,333 vs avg_win $1,010, skew 16.5, kurtosis 397, and 93
TP_GAP exits vs 6 SL_GAP. Per-trade records are not persisted anywhere,
so this is the only way to see which trades those are.

Usage (VPS):
    python3 -m scripts.inspect_cell_trades MATRIX-CELL-xxxx [--top 15] [--start ...] [--end ...]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    from backtest import research_matrix as rm
    from backtest.optimizer import evaluate_point
    from backtest.runner import load_symbol_data, physical_load_timeframe
    from storage import research_matrix as storage

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cell_id")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    cell = storage.get_cell(args.cell_id)
    if cell is None:
        print(f"Unknown cell {args.cell_id!r}.")
        return 1
    bundle = json.loads(cell["bundle_json"])
    point = {
        "timeframes": list(bundle.get("timeframes", [])),
        "engines": list(bundle.get("engines", [])),
        "indicators": list(bundle.get("indicators", [])),
        "context_filters": list(bundle.get("context_filters", [])),
        "engine_variants": json.loads(cell["engine_variants_json"]) if cell.get("engine_variants_json") else {},
        "confluence_overrides": json.loads(cell["confluence_overrides_json"]) if cell.get("confluence_overrides_json") else {},
        "risk_overrides": {k: v[0] for k, v in rm.risk_preset_to_grid(cell["risk_preset"]).items()},
    }
    phys_tf = physical_load_timeframe(tuple(point["timeframes"]) if point["timeframes"] else None)
    df = load_symbol_data(cell["symbol"], Path(args.data_dir), args.start, args.end, timeframe=phys_tf)
    print(f"cell={cell['cell_id']} {cell['symbol']} {bundle.get('engines')} {bundle.get('timeframes')} "
          f"preset={cell['risk_preset']} rows={len(df)} {df.index[0]} -> {df.index[-1]}")

    result = evaluate_point(cell["symbol"], df, point, min_trades=1, objective_metric="profit_factor", return_trades=True)
    m = result.metrics
    print(f"replayed: trades={m.total_trades} PF={m.profit_factor:.3f} avg_rr={m.avg_rr:.3f} std_rr={m.std_rr:.3f}")
    recorded = json.loads(cell["stage_a_metrics_json"]) if cell.get("stage_a_metrics_json") else {}
    if recorded:
        print(f"recorded: trades={recorded.get('total_trades')} PF={recorded.get('profit_factor')} "
              f"avg_rr={recorded.get('avg_rr')} std_rr={recorded.get('std_rr')}  "
              f"(a mismatch means data/code changed since Stage A ran)")

    trades = result.trade_records or []
    rrs = [t.rr_actual for t in trades]
    if not trades:
        print("No trades.")
        return 0

    def _risk(t):
        return abs(t.entry_price - t.stop_loss)

    risks = sorted(_risk(t) for t in trades)
    median_risk = risks[len(risks) // 2]
    print(f"\nstop distance (price units): min={risks[0]:.6g} median={median_risk:.6g} max={risks[-1]:.6g}")
    tiny = [t for t in trades if _risk(t) < 0.1 * median_risk]
    print(f"trades with stop < 10% of median stop: {len(tiny)}  exit reasons: {dict(Counter(t.exit_reason for t in tiny))}")
    print(f"|R| > 5: {sum(1 for r in rrs if abs(r) > 5)}   |R| > 20: {sum(1 for r in rrs if abs(r) > 20)}")

    by_reason: dict[str, list[float]] = {}
    for t in trades:
        by_reason.setdefault(t.exit_reason, []).append(t.rr_actual)
    print("\nper exit reason: n, mean R, min R, max R")
    for reason, vals in sorted(by_reason.items()):
        print(f"  {reason:8s} n={len(vals):4d} mean={sum(vals)/len(vals):8.2f} min={min(vals):8.2f} max={max(vals):8.2f}")

    print(f"\ntop {args.top} trades by |R|:")
    for t in sorted(trades, key=lambda t: abs(t.rr_actual), reverse=True)[: args.top]:
        print(f"  {t.entry_time} -> {t.exit_time} {t.direction:4s} {t.exit_reason:7s} R={t.rr_actual:9.2f} "
              f"usd={t.pnl_usd:11.2f} entry={t.entry_price:.5g} sl={t.stop_loss:.5g} tp={t.take_profit:.5g} "
              f"exit={t.exit_price:.5g} stop_dist={_risk(t):.5g} size={t.position_size}")

    trimmed = [r for r in rrs if abs(r) <= 5]
    if len(trimmed) >= 2:
        import statistics
        print(f"\nwithout |R|>5 trades: n={len(trimmed)} mean R={statistics.mean(trimmed):.3f} "
              f"std R={statistics.stdev(trimmed):.3f}  (diagnostic only — not a re-scored verdict)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
