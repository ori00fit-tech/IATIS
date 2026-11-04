#!/usr/bin/env python3
"""
research/experiments/H038_stage_b_power.py
------------------------------------------
H038 — Stage B power repair (pre-registered 2026-11-04 in
research/results/registry.json BEFORE this script was ever run).

Synthetic-only by design: the motivation for H038 came from seeing real
candidates (ETHUSD price_action H1, BTCUSD nnfx H1) fail Stage B, so the
hypothesis is judged ONLY on trade streams whose true edge is known.

For every (true PF, preset, n) cell, 2000 i.i.d. fixed-RR trade streams
are generated; dollar P&L compounds on a 10,000 balance exactly like
backtesting.backtest_engine (risk_amount = balance * risk_per_trade); the
stream is split into 3 equal chronological windows (ValidationConfig.
wf_windows=3). Profit factor, window PF and max drawdown are computed on
dollar P&L the same way backtest.metrics.calculate_metrics does (additive
equity from initial capital, drawdown as % of running peak); the Monte
Carlo p95 drawdown shuffles the same stream's P&L order like
backtest.monte_carlo.run_monte_carlo.

    v1 pass = pooled PF >= 1.25 AND every window PF >= 1.25 AND max DD <= 10%
    v2 pass = pooled PF >= 1.25 AND every window PF >= 1.0  AND max DD <= MC p95 max DD

Usage:
    python3 -m research.experiments.H038_stage_b_power [--out research/results/H038_stage_b_power.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

TRUE_PFS = (1.0, 1.1, 1.2, 1.3, 1.5)
PRESETS = {"conservative": (2.5, 0.005), "balanced": (2.0, 0.01), "aggressive": (1.5, 0.02)}
N_TRADES = (400, 1000)
N_WINDOWS = 3
N_STREAMS = 2000
N_SHUFFLES = 200
INITIAL = 10_000.0
POOLED_PF_BAR = 1.25
V1_WINDOW_PF, V1_MAX_DD = 1.25, 10.0
V2_WINDOW_PF = 1.0
SEED = 38


def _pf(pnls: np.ndarray) -> float:
    gp = pnls[pnls > 0].sum()
    gl = -pnls[pnls < 0].sum()
    return float(gp / gl) if gl > 0 else float("inf")


def _max_dd_pct(pnls: np.ndarray) -> np.ndarray:
    """pnls: (..., n). Additive equity from INITIAL, drawdown % of running peak."""
    equity = INITIAL + np.cumsum(pnls, axis=-1)
    equity = np.concatenate([np.full(equity.shape[:-1] + (1,), INITIAL), equity], axis=-1)
    peak = np.maximum.accumulate(equity, axis=-1)
    return ((peak - equity) / peak * 100).max(axis=-1)


def _stream(rng: np.random.Generator, win_prob: float, rr: float, risk: float, n: int) -> np.ndarray:
    wins = rng.random(n) < win_prob
    pnls = np.empty(n)
    balance = INITIAL
    for i in range(n):
        risk_amount = balance * risk
        pnls[i] = rr * risk_amount if wins[i] else -risk_amount
        balance += pnls[i]
    return pnls


def run_cell(rng: np.random.Generator, true_pf: float, rr: float, risk: float, n: int) -> dict:
    win_prob = true_pf / (rr + true_pf)  # PF = w*rr / (1-w) for fixed-RR exits
    v1 = v2 = 0
    for _ in range(N_STREAMS):
        pnls = _stream(rng, win_prob, rr, risk, n)
        pooled = _pf(pnls)
        if pooled < POOLED_PF_BAR:
            continue
        windows = [_pf(w) for w in np.array_split(pnls, N_WINDOWS)]
        dd = float(_max_dd_pct(pnls))
        if min(windows) >= V1_WINDOW_PF and dd <= V1_MAX_DD:
            v1 += 1
        if min(windows) >= V2_WINDOW_PF:
            shuffled = np.stack([rng.permutation(pnls) for _ in range(N_SHUFFLES)])
            p95 = float(np.percentile(_max_dd_pct(shuffled), 95))
            if dd <= p95:
                v2 += 1
    return {"true_pf": true_pf, "rr": rr, "risk": risk, "n": n,
            "v1_pass_rate": round(v1 / N_STREAMS, 4), "v2_pass_rate": round(v2 / N_STREAMS, 4)}


def evaluate(cells: list[dict]) -> dict:
    def at(pf: float) -> list[dict]:
        return [c for c in cells if c["true_pf"] == pf]

    c1 = all(c["v2_pass_rate"] <= 0.05 for c in at(1.0))
    c2 = all(c["v2_pass_rate"] >= 0.40 for c in at(1.3))
    c3 = all((c["v2_pass_rate"] >= 2 * c["v1_pass_rate"]) if c["v1_pass_rate"] > 0 else (c["v2_pass_rate"] >= 0.40)
             for c in at(1.3))
    c4 = all(c["v2_pass_rate"] <= 0.20 for c in at(1.1))
    return {
        "condition_1_null_false_pass_le_0.05": c1,
        "condition_2_power_at_1.3_ge_0.40": c2,
        "condition_3_power_at_1.3_ge_2x_v1": c3,
        "condition_4_marginal_1.1_pass_le_0.20": c4,
        "decision": "ADOPT" if (c1 and c2 and c3 and c4) else "REJECTED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="research/results/H038_stage_b_power.json")
    args = parser.parse_args()

    rng = np.random.default_rng(SEED)
    cells = []
    for preset, (rr, risk) in PRESETS.items():
        for n in N_TRADES:
            for true_pf in TRUE_PFS:
                cell = {"preset": preset, **run_cell(rng, true_pf, rr, risk, n)}
                cells.append(cell)
                print(f"{preset:12s} n={n:4d} PF={true_pf:.1f}  v1={cell['v1_pass_rate']:.3f}  v2={cell['v2_pass_rate']:.3f}", flush=True)

    verdict = evaluate(cells)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except Exception:  # noqa: BLE001
        commit, dirty = None, None
    out = {
        "hypothesis": "H038", "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit, "git_dirty": dirty, "seed": SEED,
        "n_streams_per_cell": N_STREAMS, "n_shuffles": N_SHUFFLES, "n_windows": N_WINDOWS,
        "cells": cells, "verdict": verdict,
    }
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
