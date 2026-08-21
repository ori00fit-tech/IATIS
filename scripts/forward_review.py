"""
scripts/forward_review.py
--------------------------
Pre-registered forward-evidence review (tier-2 gap #9: "the FX decision
keeps being deferred without a rule — one day it will be made under the
influence of the last good/bad week, which is exactly the bias this
project hunts").

The decision rules live in research/results/registry.json under
`_decision_rules`, written BEFORE the evidence exists. This script only
APPLIES them to the closed forward outcomes and prints verdicts — it
never invents thresholds at read time.

Usage (VPS):
    venv/bin/python -m scripts.forward_review

Output per rule: n so far, the metric, the pre-registered threshold, and
one of: VERDICT REACHED (act), INSUFFICIENT N (keep accumulating).
Shadow-book gate ledger is appended for context (never gated on — it is
hypothetical).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REGISTRY = Path(__file__).resolve().parent.parent / "research" / "results" / "registry.json"

FX = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
      "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF"}
CARRIERS = {"XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD"}


def _closed_outcomes() -> list[dict]:
    from storage import d1_client
    with d1_client.d1_connection() as con:
        rows = con.execute(
            "SELECT symbol, outcome, pnl_usd FROM outcomes "
            "WHERE outcome IN ('win','loss','breakeven')").fetchall()
    return [{k: r[k] for k in ("symbol", "outcome", "pnl_usd")} for r in rows]


def _bucket_stats(rows: list[dict], symbols: set[str]) -> dict:
    sel = [r for r in rows if r["symbol"] in symbols]
    wins = [r for r in sel if r["outcome"] == "win"]
    gross_w = sum(r["pnl_usd"] or 0 for r in sel if (r["pnl_usd"] or 0) > 0)
    gross_l = -sum(r["pnl_usd"] or 0 for r in sel if (r["pnl_usd"] or 0) < 0)
    return {
        "n": len(sel),
        "wr": round(100 * len(wins) / len(sel), 1) if sel else None,
        "pf": round(gross_w / gross_l, 3) if gross_l > 0 else (None if not sel else float("inf")),
    }


def evaluate_rules(rules: dict, buckets: dict) -> list[dict]:
    """Pure: applies every `_decision_rules` entry against the already-
    computed FX/carriers buckets, returning one structured verdict dict
    per rule — same comparison logic main()'s own loop always used,
    factored out so a caller other than this CLI (execution/
    post_trade_monitor.py's FORWARD_REVIEW_TRIGGERED scan) can consume
    real verdicts without re-deriving the threshold comparison. Never
    reads/writes the registry itself — `rules` is passed in, already
    loaded from `_decision_rules`, written BEFORE any evidence existed."""
    results: list[dict] = []
    for rule_id, rule in rules.items():
        if rule_id.startswith("_") or not isinstance(rule, dict):
            continue
        b = buckets.get(rule["bucket"])
        n = 0 if b is None else b["n"]
        if b is None or n < rule["min_n"]:
            results.append({
                "rule_id": rule_id, "statement": rule["statement"], "bucket": rule["bucket"],
                "n": n, "min_n": rule["min_n"], "metric": rule["metric"], "value": None,
                "op": rule["op"], "threshold": rule["threshold"], "triggered": False,
                "action": rule["action"], "insufficient_n": True,
            })
            continue
        metric = b.get(rule["metric"])
        triggered = (metric is not None
                     and ((rule["op"] == "<" and metric < rule["threshold"])
                          or (rule["op"] == ">=" and metric >= rule["threshold"])))
        results.append({
            "rule_id": rule_id, "statement": rule["statement"], "bucket": rule["bucket"],
            "n": n, "min_n": rule["min_n"], "metric": rule["metric"], "value": metric,
            "op": rule["op"], "threshold": rule["threshold"], "triggered": triggered,
            "action": rule["action"], "insufficient_n": False,
        })
    return results


def main() -> int:
    rules = json.loads(REGISTRY.read_text()).get("_decision_rules", {})
    if not rules:
        print("No _decision_rules block in the registry — nothing to review.")
        return 1

    try:
        rows = _closed_outcomes()
    except Exception as exc:
        print(f"✗ outcomes DB unreachable: {exc}", file=sys.stderr)
        return 2

    buckets = {"fx": _bucket_stats(rows, FX), "carriers": _bucket_stats(rows, CARRIERS)}
    print(f"Closed forward outcomes: {len(rows)} total | "
          f"FX n={buckets['fx']['n']} PF={buckets['fx']['pf']} WR={buckets['fx']['wr']}% | "
          f"carriers n={buckets['carriers']['n']} PF={buckets['carriers']['pf']} "
          f"WR={buckets['carriers']['wr']}%\n")

    any_reached = False
    for verdict in evaluate_rules(rules, buckets):
        print(f"── {verdict['rule_id']}: {verdict['statement']}")
        if verdict["insufficient_n"]:
            print(f"   INSUFFICIENT N ({verdict['n']}/{verdict['min_n']}) — keep accumulating.\n")
            continue
        print(f"   n={verdict['n']} {verdict['metric']}={verdict['value']} vs {verdict['op']} {verdict['threshold']}"
              f" → {'⚠ VERDICT REACHED: ' + verdict['action'] if verdict['triggered'] else 'rule not triggered — no action'}\n")
        any_reached = any_reached or verdict["triggered"]

    try:
        from storage.shadow_book import gate_ledger
        ledger = gate_ledger()
        print("── Shadow-book gate ledger (context only — hypothetical, never a gate):")
        for g in ledger["gates"]:
            print(f"   {g['primary_gate']:15s} n={g['n_closed']:4d} "
                  f"avg_r={g['avg_r']} → {g['verdict']}")
        if not ledger["gates"]:
            print(f"   no closed shadows yet ({ledger['open']} open)")
    except Exception:
        pass

    return 0 if not any_reached else 3   # 3 = a pre-registered verdict fired


if __name__ == "__main__":
    sys.exit(main())
