"""
scripts/full_system_check.py
-----------------------------
Comprehensive live diagnostic: every configured symbol × every implemented
engine, through the PRODUCTION data path (provider failover + resampling).

For each symbol it reports:
  - data health per timeframe (bars fetched, provider, starvation flags:
    decision-TF >= 210 for NNFX, D1 >= 50 for the MTF gate)
  - Market Quality Score and regime
  - every implemented engine's vote (bias / score / first reason),
    including dormant ones (run diagnostically — config is NOT changed)
  - the production confluence evaluation with the ENABLED engines only:
    vote, score, informative-weight share, and which gates would fail

Run on the VPS (full: Twelve Data + D1) or anywhere (degrades to Yahoo):

    python -m scripts.full_system_check                 # enabled symbols
    python -m scripts.full_system_check --all           # all 20 configured
    python -m scripts.full_system_check --symbols XAUUSD BTCUSD
    python -m scripts.full_system_check --json out.json

Read-only: no decisions are stored, no orders placed, no config modified.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback

from core.data_providers import fetch_multi_timeframe_with_failover
from core.market_quality import assess_market_quality
from confluence.score_calculator import calculate_score
from confluence.voting_system import (
    MIN_CONVICTION_SCORE, informative_weight_share, tally_votes,
)
from main import _ALL_ENGINES, _YF_ONLY
from regimes.regime_detector import detect_regime
from utils.helpers import load_config

NNFX_MIN_BARS = 210
MTF_MIN_D1_BARS = 50


def _td_symbol(internal: str, cfg_entry: dict) -> str:
    return (
        cfg_entry.get("symbol")
        or _YF_ONLY.get(internal)
        or (internal[:3] + "/" + internal[3:] if len(internal) == 6 else internal)
    )


def check_symbol(internal: str, cfg_entry: dict, config: dict) -> dict:
    timeframes = config["data"].get("timeframes", ["H4", "D1", "H1"])
    dtf = timeframes[0]
    bars = config["data"].get("bars_to_load", 3000)
    enabled_cfg = config.get("engines", {}).get("enabled", {})

    out: dict = {"symbol": internal, "status": "OK", "issues": [],
                 "engines": {}, "data": {}}

    # ── 1. Data through the production failover path ──
    try:
        t0 = time.monotonic()
        mtf = fetch_multi_timeframe_with_failover(
            _td_symbol(internal, cfg_entry), timeframes, outputsize=bars,
        )
        out["fetch_seconds"] = round(time.monotonic() - t0, 1)
    except Exception as exc:
        out["status"] = "FAIL"
        out["issues"].append(f"data fetch failed: {type(exc).__name__}: {exc}")
        return out

    for tf in timeframes:
        df = mtf.get(tf)
        out["data"][tf] = 0 if df is None else len(df)
    if out["data"].get(dtf, 0) < NNFX_MIN_BARS:
        out["issues"].append(
            f"STARVED: {out['data'].get(dtf, 0)} {dtf} bars < {NNFX_MIN_BARS} — NNFX mute")
    if "D1" in timeframes and dtf != "D1" and out["data"].get("D1", 0) < MTF_MIN_D1_BARS:
        out["issues"].append(
            f"STARVED: {out['data'].get('D1', 0)} D1 bars < {MTF_MIN_D1_BARS} — MTF gate inert")

    df_base = mtf[dtf]
    out["last_bar"] = str(df_base.index[-1])
    out["close"] = float(df_base["close"].iloc[-1])

    # ── 2. MQS + regime ──
    try:
        mq_cfg = config.get("market_quality", {})
        mqs = assess_market_quality(
            df=df_base, symbol=internal, timeframe=dtf,
            threshold_good=mq_cfg.get("threshold_good", 60),
            threshold_fair=mq_cfg.get("threshold_fair", 40),
        )
        out["mqs"] = {"score": mqs.score, "grade": mqs.grade,
                      "should_trade": mqs.should_trade}
    except Exception as exc:
        out["issues"].append(f"MQS failed: {exc}")
    try:
        reg = detect_regime(df_base)
        out["regime"] = {"state": reg.regime.value, "volatility": reg.volatility}
    except Exception as exc:
        out["issues"].append(f"regime failed: {exc}")

    # ── 3. ALL implemented engines, diagnostically ──
    enabled_outputs = []
    for key, cls in _ALL_ENGINES.items():
        try:
            eng = cls()
            eng.decision_tf = dtf
            res = eng.safe_analyze(mtf)
            rec = {
                "bias": res.bias.value,
                "score": round(res.score, 1),
                "enabled": bool(enabled_cfg.get(key, False)),
                "reason": (res.reasons[0][:70] if res.reasons else ""),
            }
            if any("insufficient" in r.lower() or "not enough" in r.lower()
                   for r in (res.reasons or [])):
                rec["starved"] = True
                out["issues"].append(f"{key}: {res.reasons[0][:60]}")
            out["engines"][key] = rec
            if rec["enabled"]:
                enabled_outputs.append(res)
        except Exception as exc:
            out["engines"][key] = {"bias": "ERROR", "score": 0,
                                   "enabled": bool(enabled_cfg.get(key, False)),
                                   "reason": f"{type(exc).__name__}: {exc}"}
            out["issues"].append(f"{key} raised: {type(exc).__name__}: {exc}")

    # ── 4. Production confluence view (enabled engines only) ──
    try:
        weights = config["confluence"]["weights"]
        vote = tally_votes(enabled_outputs, weights)
        score = calculate_score(enabled_outputs, weights, vote.winning_bias)
        info = informative_weight_share(enabled_outputs, weights)
        min_score = cfg_entry.get("min_score") or config["confluence"]["min_score_to_trade"]
        min_engines = config["confluence"]["min_engines_agreeing"]
        min_info = config["confluence"].get("min_informative_weight_share", 0.0)
        gates = []
        if score.final_score < min_score:
            gates.append(f"score {score.final_score} < {min_score}")
        if vote.agree_count < min_engines:
            gates.append(f"quorum {vote.agree_count} < {min_engines}")
        if min_info > 0 and info < min_info:
            gates.append(f"info_share {info:.0%} < {min_info:.0%}")
        out["confluence"] = {
            "bias": vote.winning_bias.value,
            "agree": vote.agree_count,
            "score": score.final_score,
            "informative_share": round(info, 3),
            "blocking_gates": gates,
        }
    except Exception as exc:
        out["issues"].append(f"confluence failed: {exc}")

    if any(i.startswith("STARVED") for i in out["issues"]):
        out["status"] = "WARN"
    if any("raised" in i or "failed" in i for i in out["issues"]):
        out["status"] = "FAIL"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="include disabled symbols too")
    ap.add_argument("--symbols", nargs="*", help="explicit symbol list")
    ap.add_argument("--json", metavar="PATH", help="write full JSON here")
    args = ap.parse_args()

    config = load_config()
    entries = config["data"].get("twelve_data_symbols", [])
    if args.symbols:
        chosen = [e for e in entries if e.get("internal") in set(args.symbols)]
    elif args.all:
        chosen = entries
    else:
        chosen = [e for e in entries if e.get("enabled")]

    print(f"Full system check — {len(chosen)} symbols × {len(_ALL_ENGINES)} engines "
          f"(decision TF {config['data']['timeframes'][0]}, "
          f"bars_to_load {config['data'].get('bars_to_load')})\n")

    results = []
    for e in chosen:
        internal = e.get("internal")
        try:
            r = check_symbol(internal, e, config)
        except Exception as exc:
            traceback.print_exc()
            r = {"symbol": internal, "status": "FAIL",
                 "issues": [f"unhandled: {exc}"], "engines": {}, "data": {}}
        results.append(r)
        icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[r["status"]]
        data_s = " ".join(f"{tf}:{n}" for tf, n in r.get("data", {}).items())
        conf = r.get("confluence", {})
        print(f"{icon} {internal:8s} [{data_s}] "
              f"conf={conf.get('bias','—'):8s} score={conf.get('score','—')} "
              f"agree={conf.get('agree','—')} info={conf.get('informative_share','—')}")
        for key, eng in r.get("engines", {}).items():
            flag = "•" if eng.get("enabled") else "·"
            starve = " [STARVED]" if eng.get("starved") else ""
            print(f"     {flag} {key:18s} {eng['bias']:8s} {eng['score']:>5} {starve} {eng['reason']}")
        for i in r.get("issues", []):
            print(f"     ! {i}")
        print()

    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    fail = sum(1 for r in results if r["status"] == "FAIL")
    print(f"═══ {ok} OK · {warn} WARN · {fail} FAIL of {len(results)} symbols ═══")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=1, default=str)
        print(f"JSON written: {args.json}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
