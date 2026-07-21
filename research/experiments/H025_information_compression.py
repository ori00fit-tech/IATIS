#!/usr/bin/env python3
"""
research/experiments/H025_information_compression.py
-----------------------------------------------------
H025 Stage-1 runner — information-compression test, TRAIN slice ONLY.

The DECISION RULE was pre-registered in research/results/registry.json on
2026-07-21 BEFORE this script existed (CLAUDE.md rule 1). This runner only
APPLIES it. Stage 2 (the complexity-gate A/B behind
features.complexity_gate) may be built ONLY if this stage passes; a
Stage-1 failure kills H025 without consuming any OOS data.

Pre-registered, FROZEN parameters (changing any of them = a NEW
hypothesis with a new ID):
  - Complexity (decision input): LZ76 (Kaspar-Schuster exhaustive-history
    parsing) of the binary close-to-close sign sequence, window 64 H4
    bars, normalized by n/log2(n).
  - Percentile reference: trailing 500 complexity values, per symbol,
    strictly prior bars only. Bottom quintile = percentile <= 0.20.
  - Forward move: M_t = (max(high) - min(low) over the NEXT 20 bars)
    / ATR14_t (simple 14-bar rolling mean of true range).
  - Stage-1 rule (ALL must hold, TRAIN slice pooled across the 20-symbol
    walk_forward_20260719 universe):
      (1) pooled bottom-quintile median M >= 1.10 x unconditional median M
      (2) block bootstrap p < 0.05 for ratio > 1 (1000 resamples;
          "per-symbol block" applied literally: each SYMBOL is the block —
          resample the symbols with replacement, recompute the pooled
          ratio; p = fraction of resamples with ratio <= 1.0)
      (3) ratio > 1.0 in >= 60% of symbols individually
      minimum: pooled bottom-quintile n >= 500 bars
  - Secondary measures (Shannon entropy 8-bin, zlib ratio) and the
    middle-ATR-tercile diagnostic are RECORDED ONLY — they cannot rescue
    a failed LZ76 and never feed the verdict.

Run on the VPS (uses the same deep-history CSVs as the walk-forward run):
    venv/bin/python -m scripts.download_deep_history --timeframes 4h
    venv/bin/python -m research.experiments.H025_information_compression
"""
from __future__ import annotations

import math
import sys
import time
import zlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

DATA_DIR = PROJECT_ROOT / "data"
RESULT_PATH = PROJECT_ROOT / "research" / "results" / "H025_information_compression.json"

# The walk_forward_20260719 universe — frozen at registration.
SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF",
    "XAUUSD", "XAGUSD", "USOIL", "US30", "NAS100", "SPX500",
    "BTCUSD", "ETHUSD",
]
CARRIERS = {"XAUUSD", "BTCUSD", "ETHUSD"}

TRAIN_FRACTION = 0.65        # H008c house standard; Stage 1 touches TRAIN only
WINDOW = 64                  # complexity window (H4 bars)
PCTL_LOOKBACK = 500          # trailing complexity values for the percentile
QUINTILE = 0.20              # "bottom complexity quintile"
FWD_BARS = 20                # forward-move horizon
ATR_PERIOD = 14
MIN_RATIO = 1.10             # rule (1)
MAX_P = 0.05                 # rule (2)
MIN_SYMBOL_FRACTION = 0.60   # rule (3)
MIN_POOLED_QUINTILE_N = 500  # pre-registered minimum sample
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 20260721    # fixed so the run is reproducible


# ---------------------------------------------------------------- pure fns

def lz76_complexity(seq: str) -> int:
    """Lempel-Ziv 1976 complexity of a string over any alphabet, via the
    Kaspar-Schuster exhaustive-history parsing. c('0'*n) == 2 for n >= 2;
    random binary sequences approach n/log2(n)."""
    n = len(seq)
    if n == 0:
        return 0
    if n == 1:
        return 1
    c, l, i, k, k_max = 1, 1, 0, 1, 1
    while True:
        if seq[i + k - 1] != seq[l + k - 1]:
            if k > k_max:
                k_max = k
            i += 1
            if i == l:
                c += 1
                l += k_max
                if l + 1 > n:
                    break
                i, k, k_max = 0, 1, 1
            else:
                k = 1
        else:
            k += 1
            if l + k > n:
                c += 1
                break
    return c


def normalized_lz76(bits: np.ndarray) -> float:
    """LZ76 normalized by the random-sequence expectation n/log2(n).
    ~1.0 for i.i.d. coin flips; << 1 for ordered sequences."""
    n = len(bits)
    if n < 2:
        return float("nan")
    s = "".join("1" if b else "0" for b in bits)
    return lz76_complexity(s) / (n / math.log2(n))


def shannon_entropy_8bin(returns: np.ndarray) -> float:
    """Secondary measure (recorded only): Shannon entropy of returns
    discretized into 8 equal-width bins over the window, in bits,
    normalized by log2(8) -> [0, 1]."""
    r = returns[np.isfinite(returns)]
    if len(r) < 2 or np.ptp(r) == 0:
        return 0.0
    counts, _ = np.histogram(r, bins=8)
    p = counts[counts > 0] / counts.sum()
    return float(-(p * np.log2(p)).sum() / 3.0)


def zlib_ratio(returns: np.ndarray) -> float:
    """Secondary measure (recorded only): zlib-compressed size of the
    8-bin symbol sequence over its raw size. Lower = more compressible."""
    r = returns[np.isfinite(returns)]
    if len(r) < 2 or np.ptp(r) == 0:
        return 0.0
    edges = np.histogram_bin_edges(r, bins=8)
    symbols = np.clip(np.digitize(r, edges[1:-1]), 0, 7).astype(np.uint8)
    raw = symbols.tobytes()
    return len(zlib.compress(raw, 9)) / len(raw)


def trailing_percentile(values: np.ndarray, lookback: int) -> np.ndarray:
    """percentile[t] = fraction of the previous `lookback` values that are
    <= values[t] (strictly prior window; NaN until it is full)."""
    n = len(values)
    out = np.full(n, np.nan)
    for t in range(lookback, n):
        window = values[t - lookback:t]
        if np.isnan(window).any() or np.isnan(values[t]):
            continue
        out[t] = float((window <= values[t]).mean())
    return out


def stage1_verdict(
    pooled_ratio: float,
    p_value: float,
    per_symbol_ratios: dict[str, float],
    pooled_quintile_n: int,
) -> tuple[str, list[str]]:
    """Apply the pre-registered H025 Stage-1 rule LITERALLY. Pure function,
    unit-tested so the verdict cannot drift from the registry text.

    Returns (verdict, reasons); verdict is PROCEED_TO_STAGE2, FAILED or
    INSUFFICIENT_DATA."""
    if pooled_quintile_n < MIN_POOLED_QUINTILE_N:
        return "INSUFFICIENT_DATA", [
            f"pooled bottom-quintile n={pooled_quintile_n} < {MIN_POOLED_QUINTILE_N}"
        ]
    reasons: list[str] = []
    if not pooled_ratio >= MIN_RATIO:
        reasons.append(f"pooled ratio {pooled_ratio:.4f} < {MIN_RATIO}")
    if not p_value < MAX_P:
        reasons.append(f"bootstrap p {p_value:.4f} >= {MAX_P}")
    finite = {s: r for s, r in per_symbol_ratios.items() if np.isfinite(r)}
    if finite:
        positive = sum(1 for r in finite.values() if r > 1.0)
        frac = positive / len(finite)
        if frac < MIN_SYMBOL_FRACTION:
            reasons.append(
                f"ratio > 1.0 in only {positive}/{len(finite)} symbols "
                f"({frac:.0%} < {MIN_SYMBOL_FRACTION:.0%})"
            )
    else:
        reasons.append("no symbol produced a finite per-symbol ratio")
    return ("FAILED", reasons) if reasons else ("PROCEED_TO_STAGE2", [])


# ------------------------------------------------------------- per symbol

def compute_symbol_frame(df: pd.DataFrame) -> pd.DataFrame:
    """All per-bar quantities for one symbol's TRAIN slice. Rows without a
    full complexity window, full percentile lookback, valid ATR, or 20
    forward bars are dropped — exactly the rows the registration excludes."""
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    n = len(df)

    signs = (np.diff(close) > 0)  # sign[i]: close[i+1] > close[i]
    returns = np.diff(close) / close[:-1]

    comp = np.full(n, np.nan)
    ent = np.full(n, np.nan)
    zr = np.full(n, np.nan)
    for t in range(WINDOW, n):
        # window of the last WINDOW sign/return observations ending at bar t
        comp[t] = normalized_lz76(signs[t - WINDOW:t])
        ent[t] = shannon_entropy_8bin(returns[t - WINDOW:t])
        zr[t] = zlib_ratio(returns[t - WINDOW:t])

    pctl = trailing_percentile(comp, PCTL_LOOKBACK)

    prev_close = np.concatenate([[np.nan], close[:-1]])
    tr = np.nanmax(
        np.column_stack([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ]),
        axis=1,
    )
    atr = pd.Series(tr).rolling(ATR_PERIOD).mean().to_numpy()
    atr_pctl = trailing_percentile(atr, PCTL_LOOKBACK)

    fwd = np.full(n, np.nan)
    for t in range(n - FWD_BARS):
        hi = high[t + 1:t + 1 + FWD_BARS].max()
        lo = low[t + 1:t + 1 + FWD_BARS].min()
        if atr[t] and np.isfinite(atr[t]) and atr[t] > 0:
            fwd[t] = (hi - lo) / atr[t]

    out = pd.DataFrame(
        {
            "complexity": comp,
            "complexity_pctl": pctl,
            "entropy": ent,
            "zlib_ratio": zr,
            "atr_pctl": atr_pctl,
            "fwd_move": fwd,
        },
        index=df.index,
    )
    return out.dropna(subset=["complexity_pctl", "fwd_move"])


def load_train_slice(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_H4_deep.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run: python -m scripts.download_deep_history "
            f"--timeframes 4h"
        )
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()
    split = int(len(df) * TRAIN_FRACTION)
    return df.iloc[:split]  # Stage 1 NEVER sees the TEST slice


def symbol_summary(frame: pd.DataFrame) -> dict:
    quintile = frame[frame["complexity_pctl"] <= QUINTILE]
    uncond_med = float(frame["fwd_move"].median())
    quint_med = float(quintile["fwd_move"].median()) if len(quintile) else float("nan")
    ratio = quint_med / uncond_med if uncond_med > 0 else float("nan")

    # Recorded-only diagnostic: same ratio inside the middle ATR tercile —
    # if the effect vanishes there, "low complexity" was just an ATR squeeze.
    mid = frame[(frame["atr_pctl"] >= 1 / 3) & (frame["atr_pctl"] <= 2 / 3)]
    mid_q = mid[mid["complexity_pctl"] <= QUINTILE]
    mid_med = float(mid["fwd_move"].median()) if len(mid) else float("nan")
    mid_ratio = (
        float(mid_q["fwd_move"].median()) / mid_med
        if len(mid_q) and mid_med and mid_med > 0
        else float("nan")
    )
    return {
        "bars_evaluated": int(len(frame)),
        "quintile_n": int(len(quintile)),
        "unconditional_median_move": round(uncond_med, 4),
        "quintile_median_move": round(quint_med, 4) if np.isfinite(quint_med) else None,
        "ratio": round(ratio, 4) if np.isfinite(ratio) else None,
        "diag_mid_atr_tercile_ratio": (
            round(mid_ratio, 4) if np.isfinite(mid_ratio) else None
        ),
        "diag_median_entropy_quintile": (
            round(float(quintile["entropy"].median()), 4) if len(quintile) else None
        ),
        "diag_median_zlib_quintile": (
            round(float(quintile["zlib_ratio"].median()), 4) if len(quintile) else None
        ),
    }


def pooled_ratio_from(frames: dict[str, pd.DataFrame]) -> tuple[float, int]:
    all_moves = pd.concat([f["fwd_move"] for f in frames.values()])
    quint_moves = pd.concat(
        [f.loc[f["complexity_pctl"] <= QUINTILE, "fwd_move"] for f in frames.values()]
    )
    uncond = float(all_moves.median())
    ratio = float(quint_moves.median()) / uncond if uncond > 0 else float("nan")
    return ratio, int(len(quint_moves))


def bootstrap_p(frames: dict[str, pd.DataFrame]) -> float:
    """Per-symbol block bootstrap, applied literally: the SYMBOL is the
    block. Resample the symbol set with replacement BOOTSTRAP_N times,
    recompute the pooled ratio; p = fraction of resamples with
    ratio <= 1.0 (the no-effect null)."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    names = list(frames)
    below = 0
    for _ in range(BOOTSTRAP_N):
        picked = rng.choice(names, size=len(names), replace=True)
        sample = {f"{s}#{i}": frames[s] for i, s in enumerate(picked)}
        ratio, _n = pooled_ratio_from(sample)
        if not np.isfinite(ratio) or ratio <= 1.0:
            below += 1
    return below / BOOTSTRAP_N


# ------------------------------------------------------------------ main

def main() -> int:
    import json

    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    cfg = load_config()
    frames: dict[str, pd.DataFrame] = {}
    per_symbol: dict[str, dict] = {}
    datasets = []
    missing: list[str] = []

    print("H025 Stage 1 — information-compression test (TRAIN slice only)\n")
    for symbol in SYMBOLS:
        t0 = time.time()
        try:
            train = load_train_slice(symbol)
        except FileNotFoundError as exc:
            print(f"{symbol}: DATA MISSING — {exc}")
            missing.append(symbol)
            continue
        frame = compute_symbol_frame(train)
        if frame.empty:
            print(f"{symbol}: no evaluable bars after warm-up windows — skipped")
            missing.append(symbol)
            continue
        frames[symbol] = frame
        per_symbol[symbol] = symbol_summary(frame)
        datasets.append(dataset_fingerprint(DATA_DIR / f"{symbol}_H4_deep.csv"))
        s = per_symbol[symbol]
        print(
            f"{symbol}: bars={s['bars_evaluated']} quintile_n={s['quintile_n']} "
            f"ratio={s['ratio']} mid-ATR diag={s['diag_mid_atr_tercile_ratio']} "
            f"({time.time() - t0:.0f}s)"
        )

    if not frames:
        print("\nNo data — nothing to evaluate.")
        return 1

    pooled_ratio, pooled_n = pooled_ratio_from(frames)
    p_value = bootstrap_p(frames)
    ratios = {s: (d["ratio"] if d["ratio"] is not None else float("nan"))
              for s, d in per_symbol.items()}
    verdict, reasons = stage1_verdict(pooled_ratio, p_value, ratios, pooled_n)

    positive = sum(1 for r in ratios.values() if np.isfinite(r) and r > 1.0)
    print(f"\nPooled: ratio={pooled_ratio:.4f} quintile_n={pooled_n} "
          f"bootstrap_p={p_value:.4f} positive_symbols={positive}/{len(ratios)}")
    print(f"VERDICT: {verdict}"
          + (f" — {'; '.join(reasons)}" if reasons else ""))

    results = {
        "stage1": {
            "verdict": verdict,
            "verdict_reasons": reasons,
            "pooled_ratio": round(pooled_ratio, 4),
            "pooled_quintile_n": pooled_n,
            "bootstrap_p": p_value,
            "positive_symbols": positive,
            "symbols_evaluated": len(ratios),
            "missing_symbols": missing,
            "per_symbol": per_symbol,
        }
    }
    RESULT_PATH.write_text(json.dumps(results, indent=1) + "\n")
    print(f"Result: {RESULT_PATH}")

    manifest = build_manifest(
        kind="h025_stage1",
        config=cfg,
        params={
            "hypothesis": "H025",
            "stage": 1,
            "train_fraction": TRAIN_FRACTION,
            "window": WINDOW,
            "pctl_lookback": PCTL_LOOKBACK,
            "quintile": QUINTILE,
            "fwd_bars": FWD_BARS,
            "atr_period": ATR_PERIOD,
            "bootstrap": {"n": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED,
                          "block": "symbol (cluster bootstrap)"},
            "rule": (
                f"PROCEED_TO_STAGE2 iff pooled quintile/unconditional median "
                f"ratio >= {MIN_RATIO} AND bootstrap p < {MAX_P} AND ratio > 1.0 "
                f"in >= {MIN_SYMBOL_FRACTION:.0%} of symbols, with pooled "
                f"quintile n >= {MIN_POOLED_QUINTILE_N} "
                "(pre-registered 2026-07-21, applied literally)"
            ),
        },
        datasets=datasets,
        results=results,
    )
    out = write_manifest(manifest, f"h025_stage1_{time.strftime('%Y%m%d')}")
    print(f"Manifest: {out}")
    print(
        "\nNext (HUMAN steps, per the registered rule): update registry.json "
        "H025 per this verdict. PROCEED_TO_STAGE2 -> build the complexity-gate "
        "A/B (features.complexity_gate, default false) on the H024 harness "
        "pattern. FAILED -> rejected ledger in "
        "docs/STRATEGY_EVIDENCE_2026-07.md; Stage 2 is never built."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
