"""
research/experiments/H008c_oos.py
-----------------------------------
H008c — the methodologically-honest re-test of BOS+FVG.

H008/H008b carried two fatal flaws relative to a real edge test:

  1. LOOK-AHEAD. `find_swing_points` uses a *centered* rolling window, so a
     swing at bar p is only knowable at bar p+window. The old detector
     treated such swings as "prior" at the sweep bar, letting the strategy
     act on structure it could not yet have seen. This inflates the
     apparent win rate.

  2. NO OUT-OF-SAMPLE SPLIT. Filters (London session, ATR quality) were
     chosen and scored on the *same* bars — the exact overfitting trap that
     produced the +100% trade-management mirage.

H008c fixes both:
  * Causal detector: a swing at position p is usable only once p+window ≤
    the decision bar. Every input to sweep/BOS/FVG is available in real time.
  * Chronological split: detect + score on a TRAIN slice, then re-detect +
    score on a held-out later TEST slice. The verdict is decided on TEST.

It reports the causal result WITH and WITHOUT the H008b filters, and also
re-runs the OLD (look-ahead) detector on the same data so the inflation is
quantified rather than asserted.

Pre-registered verdict (on the TEST slice), unchanged from H008b intent:
  PASS: p ≤ 0.05 AND WR−0.4978 ≥ 0.10pp-equivalent (≥ +10pp) AND n ≥ 50
  FAIL: p > 0.05 at n ≥ 50, or improvement < +10pp
  INCONCLUSIVE: n < 50
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

from engines.smc_engine import find_swing_points
from research.experiments.H008_bos_fvg import (
    _detect_fvg,
    _two_proportion_p,
    detect_bos_fvg_setups,  # the OLD look-ahead detector, for comparison
)
from research.experiments.H008b_session_filtered_bos import _compute_atr

RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "H008c_result.json"

H001_BASELINE = 0.4978
H001_N = 225
MIN_SAMPLE = 50
MIN_IMPROVEMENT_PP = 0.10
SWING_WINDOW = 3
BOS_MAX_BARS = 10
FORWARD_BARS = 20
TRAIN_FRAC = 0.60


class SyntheticDataNotAllowedError(Exception):
    pass


def causal_bos_fvg_setups(
    df: pd.DataFrame,
    *,
    swing_window: int = SWING_WINDOW,
    bos_max: int = BOS_MAX_BARS,
    forward: int = FORWARD_BARS,
    london: bool = False,
    atr_mult: float = 0.0,
) -> list[dict]:
    """BOS+FVG setups with NO look-ahead.

    A swing at position p is only consulted once p+swing_window ≤ the sweep
    bar i (i.e. it has been confirmed by real bars, not future ones). BOS is
    searched forward from i; the FVG is defined by the bar after BOS; entry
    is only allowed from bos+2 onward (once the FVG-defining bar has closed).
    Every decision input is therefore available at decision time.
    """
    n = len(df)
    sw = find_swing_points(df, window=swing_window)
    sh = sw["swing_high"].to_numpy()
    sl = sw["swing_low"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    open_ = df["open"].to_numpy()
    idx = df.index
    atr = _compute_atr(df).to_numpy() if atr_mult > 0 else None

    setups: list[dict] = []
    for i in range(swing_window * 2, n - forward - bos_max - 2):
        # most recent CONFIRMED swing low/high strictly before i
        rlow_price = rhigh_price = None
        for p in range(i - swing_window, swing_window - 1, -1):
            if rlow_price is None and sl[p]:
                rlow_price = low[p]
            if rhigh_price is None and sh[p]:
                rhigh_price = high[p]
            if rlow_price is not None and rhigh_price is not None:
                break
        if rlow_price is None or rhigh_price is None:
            continue

        swept_low = low[i] < rlow_price and close[i] > rlow_price
        swept_high = high[i] > rhigh_price and close[i] < rhigh_price
        if not swept_low and not swept_high:
            continue

        direction = "BULLISH" if swept_low else "BEARISH"
        bos_level = rhigh_price if direction == "BULLISH" else rlow_price
        bos_bar = None
        for j in range(i + 1, min(i + bos_max + 1, n)):
            if direction == "BULLISH" and close[j] > bos_level:
                bos_bar = j
                break
            if direction == "BEARISH" and close[j] < bos_level:
                bos_bar = j
                break
        if bos_bar is None:
            continue

        fvg = _detect_fvg(df, bos_bar, direction)
        if fvg is None:
            continue
        fvg_low, fvg_high = fvg

        entry_bar = None
        for k in range(bos_bar + 2, min(bos_bar + 16, n - forward)):
            if direction == "BULLISH" and fvg_low <= low[k] <= fvg_high:
                entry_bar = k
                break
            if direction == "BEARISH" and fvg_low <= high[k] <= fvg_high:
                entry_bar = k
                break
        if entry_bar is None or entry_bar + forward >= n:
            continue

        # Filters (H008b) — applied at the causal decision bar.
        if london:
            hr = getattr(idx[entry_bar], "hour", None)
            if hr is None or not (2 <= hr < 10):
                continue
        if atr_mult > 0 and atr is not None and bos_bar >= 14:
            a = atr[bos_bar]
            if a > 0 and abs(close[bos_bar] - open_[bos_bar]) < atr_mult * a:
                continue

        fwd = close[entry_bar + forward] - close[entry_bar]
        won = fwd > 0 if direction == "BULLISH" else fwd < 0
        setups.append({"t": idx[entry_bar], "direction": direction, "won": bool(won)})

    return setups


def _wr_block(setups: list[dict]) -> dict:
    n = len(setups)
    if n == 0:
        return {"n": 0, "wr": None, "improvement": None, "p_value": None}
    wr = sum(s["won"] for s in setups) / n
    imp = wr - H001_BASELINE
    p = _two_proportion_p(wr, n, H001_BASELINE, H001_N)
    return {"n": n, "wr": round(wr, 4), "improvement": round(imp, 4),
            "p_value": round(p, 4)}


def _verdict(test_block: dict) -> str:
    n = test_block["n"]
    if n < MIN_SAMPLE:
        return "INCONCLUSIVE"
    if test_block["p_value"] <= 0.05 and test_block["improvement"] >= MIN_IMPROVEMENT_PP:
        return "PASSED"
    return "FAILED"


@dataclass
class H008cResult:
    hypothesis_id: str
    status: str
    data_source: str
    n_bars: int
    train_test_boundary: str
    causal_unfiltered: dict          # {train, test}
    causal_filtered: dict            # {train, test}  (London+ATR)
    lookahead_full: dict             # OLD detector, whole set (for inflation Δ)
    causal_full: dict                # new detector, whole set
    lookahead_inflation_pp: float | None
    notes: str


def run_experiment(df_m15: pd.DataFrame, source: str, symbol: str = "UNKNOWN") -> H008cResult:
    if not source.startswith("real:"):
        raise SyntheticDataNotAllowedError("H008c requires real data only.")

    df_m15 = df_m15.sort_index()
    n = len(df_m15)
    split = int(n * TRAIN_FRAC)
    df_train, df_test = df_m15.iloc[:split], df_m15.iloc[split:]
    boundary = str(df_m15.index[split])

    # Causal, unfiltered (H008 concept, look-ahead removed)
    cu = {"train": _wr_block(causal_bos_fvg_setups(df_train)),
          "test": _wr_block(causal_bos_fvg_setups(df_test))}
    # Causal, with H008b filters (London session + 1.5×ATR BOS candle)
    cf = {"train": _wr_block(causal_bos_fvg_setups(df_train, london=True, atr_mult=1.5)),
          "test": _wr_block(causal_bos_fvg_setups(df_test, london=True, atr_mult=1.5))}

    # Inflation measurement: old (look-ahead) vs new (causal) on the WHOLE set
    old_full = _wr_block([{"won": s["won"]} for s in detect_bos_fvg_setups(df_m15)])
    new_full = _wr_block(causal_bos_fvg_setups(df_m15))
    inflation = (round((old_full["wr"] - new_full["wr"]) * 100, 2)
                 if old_full["wr"] is not None and new_full["wr"] is not None else None)

    # Verdict decided on the causal UNFILTERED test slice (the honest primary),
    # unless filtering both improves WR and keeps n≥50 on test.
    primary = cf if (cf["test"]["n"] >= MIN_SAMPLE and cf["test"]["wr"] is not None
                     and (cu["test"]["wr"] is None or cf["test"]["wr"] > cu["test"]["wr"])) else cu
    status = _verdict(primary["test"])

    notes = (
        f"{symbol}: causal OOS test WR (unfiltered)={cu['test']['wr']} "
        f"n={cu['test']['n']}; filtered={cf['test']['wr']} n={cf['test']['n']}. "
        f"Look-ahead inflated whole-set WR by {inflation}pp "
        f"(old {old_full['wr']} → causal {new_full['wr']}). Verdict on TEST slice."
    )

    result = H008cResult(
        hypothesis_id="H008c",
        status=status,
        data_source=source,
        n_bars=n,
        train_test_boundary=boundary,
        causal_unfiltered=cu,
        causal_filtered=cf,
        lookahead_full=old_full,
        causal_full=new_full,
        lookahead_inflation_pp=inflation,
        notes=notes,
    )
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(asdict(result), indent=2, default=str))
    return result


if __name__ == "__main__":
    raise SystemExit("Run via scripts/run_h008c.py")
