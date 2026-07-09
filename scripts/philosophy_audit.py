"""
scripts/philosophy_audit.py
---------------------------
System Philosophy Audit — 8 axes, ~27 automated checks against the live
decisions database (Cloudflare D1 via storage.d1_client).

Run on the VPS (needs D1_WORKER_URL / D1_PROXY_TOKEN in .env):

    python -m scripts.philosophy_audit            # full report
    python -m scripts.philosophy_audit --json     # machine-readable

Axes (see docs/PHILOSOPHY_AUDIT_2026-07_ADDENDUM_LIVE.md for rationale):
  1. Hierarchy      — can a verdict contradict its gates?
  2. Independence   — are engine votes actually independent?
  3. Phantom engines — which engines never inform decisions, and why
  4. Engine impact  — would deleting an engine change any decision?
  5. Gate bottleneck — which condition kills most candidates, and who blocks
  6. Discontinuity  — score jumps near thresholds / vote-vs-score side flips
  7. Selectivity    — is the EXECUTE rate philosophy or bug?
  8. No-information confluence — quorum met only because half the panel is mute

Every check prints PASS / FAIL / WARN / INFO plus the evidence rows.
Exit code 1 if any FAIL.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from storage import d1_client

# Engines currently enabled in config.yaml — kept in sync manually so the
# audit also runs against historical rows written under this config.
ENABLED_ENGINES = ("SMC", "PriceAction", "NNFX", "Wyckoff")
MIN_ENGINES_AGREEING = 2
NNFX_MIN_BARS = 210          # engines/nnfx_engine.py:61
MTF_MIN_D1_BARS = 50         # confluence/mtf_confirmation.py:77
MIN_CONVICTION_SCORE = 20    # confluence/voting_system.py:31


@dataclass
class Check:
    axis: int
    name: str
    status: str          # PASS / FAIL / WARN / INFO
    detail: str
    evidence: list = field(default_factory=list)


RESULTS: list[Check] = []


def add(axis: int, name: str, status: str, detail: str, evidence: list | None = None):
    RESULTS.append(Check(axis, name, status, detail, evidence or []))


def _rows(con, sql: str, params: tuple = ()) -> list:
    return con.execute(sql, params).fetchall()


# ───────────────────────── Axis 1 — Hierarchy ─────────────────────────

def axis1_hierarchy(con):
    # 1.1 EXECUTE while the risk gate said no — must be zero.
    bad = _rows(con, "SELECT id, ts, symbol, cf_score, risk_passed FROM decisions "
                     "WHERE verdict='EXECUTE' AND risk_passed=0")
    add(1, "EXECUTE with risk_passed=0", "FAIL" if bad else "PASS",
        f"{len(bad)} violation(s). Code path (_final_verdict) makes this impossible; "
        "any hit means storage writes a different decision than the pipeline made.",
        [tuple(r) for r in bad[:10]])

    # 1.2 EXECUTE with a recorded fail_reason — contradiction in the record.
    bad = _rows(con, "SELECT id, ts, symbol, fail_reason FROM decisions "
                     "WHERE verdict='EXECUTE' AND fail_reason IS NOT NULL AND fail_reason<>''")
    add(1, "EXECUTE carrying a fail_reason", "FAIL" if bad else "PASS",
        f"{len(bad)} row(s).", [tuple(r) for r in bad[:10]])

    # 1.3 NO_TRADE with all gates green (cf passed + risk passed) and no
    #     fail_reason — only legitimate causes are the regime filter and the
    #     meta layer; those must appear in summary/raw_json.
    rows = _rows(con, "SELECT id, ts, symbol, summary FROM decisions "
                      "WHERE verdict='NO_TRADE' AND risk_passed=1 "
                      "AND (fail_reason IS NULL OR fail_reason='')")
    unexplained = [r for r in rows
                   if not any(k in (r[3] or "").lower()
                              for k in ("regime filter", "meta", "news", "blackout"))]
    add(1, "NO_TRADE with every recorded gate green", "WARN" if unexplained else "PASS",
        f"{len(rows)} row(s) total, {len(unexplained)} without a visible downgrade reason "
        "(regime filter / meta / news). Silent-downgrade decisions break auditability.",
        [tuple(r) for r in unexplained[:10]])

    # 1.4 risk_passed populated on rows where confluence failed — the risk
    #     gate is never evaluated there (main.py:_risk_gate returns None),
    #     so a non-NULL value would be fabricated.
    bad = _rows(con, "SELECT COUNT(*) FROM decisions WHERE risk_passed IS NOT NULL "
                     "AND fail_reason LIKE '%engine(s) agree%'")
    n = bad[0][0] if bad else 0
    add(1, "risk_passed fabricated on confluence-failed rows", "WARN" if n else "PASS",
        f"{n} row(s) have a risk verdict although the risk gate never ran for them.")


# ─────────────────────── Axis 2 — Independence ────────────────────────

def axis2_independence(con):
    votes = _rows(con, "SELECT decision_id, engine, bias, score FROM engine_votes")
    by_dec: dict[int, dict[str, tuple[str, float]]] = defaultdict(dict)
    for did, eng, bias, score in votes:
        by_dec[did][eng] = (bias, score or 0.0)

    pairs = [("SMC", "PriceAction"), ("SMC", "NNFX"), ("PriceAction", "NNFX"),
             ("SMC", "Wyckoff"), ("PriceAction", "Wyckoff"), ("NNFX", "Wyckoff")]
    for a, b in pairs:
        both = agree = 0
        for d in by_dec.values():
            if a in d and b in d:
                ba, bb = d[a][0], d[b][0]
                if ba != "NEUTRAL" and bb != "NEUTRAL":
                    both += 1
                    agree += (ba == bb)
        if both < 30:
            add(2, f"vote agreement {a}|{b}", "INFO",
                f"only {both} co-voting decisions — not enough live data yet "
                "(backtest reference: ablation_20260703.json pairwise matrix).")
            continue
        pct = 100.0 * agree / both
        status = "FAIL" if pct >= 80 or pct <= 20 else ("WARN" if pct >= 70 else "PASS")
        add(2, f"vote agreement {a}|{b}", status,
            f"{pct:.1f}% over {both} co-voting decisions "
            f"(>=80% = same engine twice; <=20% = mirrored negation; "
            f"backtest pooled reference: SMC|PA 49.7%, SMC|NNFX 73.6%, "
            f"wyckoff|sentiment 100%).")


# ────────────────────── Axis 3 — Phantom engines ──────────────────────

def axis3_phantom(con):
    rows = _rows(con, "SELECT engine, "
                      "SUM(CASE WHEN bias='NEUTRAL' THEN 0 ELSE 1 END) AS voted, "
                      "COUNT(*) AS n, AVG(score) AS avg_score "
                      "FROM engine_votes GROUP BY engine")
    for eng, voted, n, avg_score in rows:
        if n == 0:
            continue
        rate = 100.0 * (voted or 0) / n
        # An enabled engine that votes on <10% of decisions is a phantom:
        # it occupies a quorum seat but almost never brings information.
        status = "FAIL" if (eng in ENABLED_ENGINES and rate < 10) else (
                 "WARN" if (eng in ENABLED_ENGINES and rate < 30) else "INFO")
        add(3, f"{eng} participation", status,
            f"votes on {rate:.1f}% of {n} decisions, avg score {avg_score:.1f}. "
            "Backtest reference vote rates (full-history H1): NNFX 100%, SMC 76%, "
            "PA 74%, Wyckoff 32%. A large live-vs-backtest gap = the engine is "
            "data-starved live, not 'weak'.")

    # NNFX starvation root cause: the reasons string is stored inside raw_json.
    starved = _rows(con, "SELECT COUNT(*) FROM decisions "
                         "WHERE raw_json LIKE '%Insufficient data for NNFX%'")
    n = starved[0][0] if starved else 0
    add(3, "NNFX 'Insufficient data' occurrences", "FAIL" if n else "PASS",
        f"{n} decision(s) carry the literal reason 'Insufficient data for NNFX "
        f"analysis (need 210+ bars for EMA200)'. Root cause: Twelve Data Free "
        f"returns 403 on native H4/D1, so H4 is resampled from a 500-bar H1 "
        f"window ≈ 125 H4 bars < {NNFX_MIN_BARS} (core/data_loader.py:270-279). "
        "Fix data depth; do not tune weights around a starved engine.")

    mtf_dead = _rows(con, "SELECT COUNT(*) FROM decisions "
                          "WHERE raw_json LIKE '%D1 data unavailable%' "
                          "   OR raw_json LIKE '%no MTF adjustment%'")
    n = mtf_dead[0][0] if mtf_dead else 0
    add(3, "MTF D1-confirmation inert", "WARN" if n else "INFO",
        f"{n} decision(s) where the MTF gate applied no adjustment. The same "
        f"resampling gives ~21 D1 bars < {MTF_MIN_D1_BARS} required "
        "(confluence/mtf_confirmation.py:77) — the one gate with positive "
        "indirect evidence is likely inert in live operation.")


# ─────────────────────── Axis 4 — Engine impact ───────────────────────

def axis4_impact(con):
    """Counterfactual replay: for each decision, drop one engine and
    recompute (a) the 2-engine quorum and (b) the majority direction.
    Pure vote arithmetic — score/MTF/veto are not replayed, so this is a
    lower bound on decision changes."""
    votes = _rows(con, "SELECT decision_id, engine, bias, score FROM engine_votes")
    by_dec: dict[int, list] = defaultdict(list)
    for did, eng, bias, score in votes:
        by_dec[did].append((eng, bias, score or 0.0))

    for target in ENABLED_ENGINES:
        changed = total = 0
        for d, evs in by_dec.items():
            active = [(e, b) for e, b, s in evs
                      if b != "NEUTRAL" and s >= MIN_CONVICTION_SCORE]
            if not active:
                continue
            total += 1
            cnt = Counter(b for _, b in active)
            majority = cnt.most_common(1)[0][0]
            quorum = cnt[majority] >= MIN_ENGINES_AGREEING

            active2 = [(e, b) for e, b in active if e != target]
            cnt2 = Counter(b for _, b in active2)
            majority2 = cnt2.most_common(1)[0][0] if cnt2 else None
            quorum2 = bool(cnt2) and cnt2[majority2] >= MIN_ENGINES_AGREEING

            if majority != majority2 or quorum != quorum2:
                changed += 1
        pct = 100.0 * changed / total if total else 0.0
        status = "WARN" if pct < 2 else "INFO"
        add(4, f"decisions changed if {target} removed", status,
            f"{changed}/{total} ({pct:.1f}%). Below ~2% the engine is decoration: "
            "it changes nothing — remove it or fix its inputs. "
            "(Backtest LOO reference: no engine's removal was statistically "
            "significant; removing SMC improved PF on 7/10 symbols.)")


# ────────────────────── Axis 5 — Gate bottleneck ──────────────────────

def axis5_bottleneck(con):
    rows = _rows(con, "SELECT fail_reason, COUNT(*) FROM decisions "
                      "WHERE verdict='NO_TRADE' AND fail_reason IS NOT NULL "
                      "AND fail_reason<>'' GROUP BY fail_reason ORDER BY 2 DESC")
    top = [(r[0][:90], r[1]) for r in rows[:8]]
    add(5, "rejection ranking", "INFO",
        "Top fail_reasons by count.", top)

    # Who blocks the quorum? For quorum-failed decisions, find the mute/
    # dissenting engines.
    qdecs = _rows(con, "SELECT id FROM decisions WHERE fail_reason LIKE '%engine(s) agree%'")
    ids = {r[0] for r in qdecs}
    votes = _rows(con, "SELECT decision_id, engine, bias, score FROM engine_votes")
    mute = Counter()
    dissent = Counter()
    for did, eng, bias, score in votes:
        if did not in ids:
            continue
        if bias == "NEUTRAL" or (score or 0) < MIN_CONVICTION_SCORE:
            mute[eng] += 1
        else:
            dissent[eng] += 1
    add(5, "quorum blockers (mute engines)", "INFO",
        f"On {len(ids)} 'Only 1 engine agrees' rejections, engines were mute "
        f"(NEUTRAL or score<{MIN_CONVICTION_SCORE}) this many times.",
        mute.most_common())
    add(5, "quorum blockers (dissenting engines)", "INFO",
        "…and actively voted against the lone agreeing engine this many times. "
        "Mute ≫ dissent means the bottleneck is missing information (starved "
        "engines), not disagreement — fix data before touching thresholds.",
        dissent.most_common())


# ────────────────────── Axis 6 — Discontinuity ────────────────────────

def axis6_discontinuity(con):
    # 6.1 Vote-vs-score side flip: majority-by-conviction (voting_system)
    #     and majority-by-count (score_calculator) can disagree; the stored
    #     cf_score then describes the OPPOSITE side of the verdict direction.
    votes = _rows(con, "SELECT decision_id, engine, bias, score FROM engine_votes")
    by_dec: dict[int, list] = defaultdict(list)
    for did, eng, bias, score in votes:
        by_dec[did].append((eng, bias, score or 0.0))
    ties = flips = 0
    for d, evs in by_dec.items():
        bulls = [(e, s) for e, b, s in evs if b == "BULLISH" and s >= MIN_CONVICTION_SCORE]
        bears = [(e, s) for e, b, s in evs if b == "BEARISH" and s >= MIN_CONVICTION_SCORE]
        if bulls and bears:
            ties += (len(bulls) == len(bears))
    add(6, "count-tie decisions (score side chosen by avg)", "WARN" if ties else "PASS",
        f"{ties} decision(s) had equal bull/bear counts: score_calculator then "
        "reports whichever side has the higher average — a 1-point change in "
        "one engine flips the reported score between the two sides' averages "
        "(e.g. 80 ↔ 45). This is the BTC-39 vs ETH-80 class of jump.")

    # 6.2 Conviction cliff at score=20: engines counted in the score but
    #     silenced in the vote (or vice versa).
    cliff = sum(1 for evs in by_dec.values()
                for _, b, s in evs if b != "NEUTRAL" and 15 <= s < MIN_CONVICTION_SCORE)
    add(6, f"votes inside the {MIN_CONVICTION_SCORE}-point conviction cliff",
        "WARN" if cliff else "PASS",
        f"{cliff} engine-vote(s) sat at 15–19 points: NEUTRAL for the quorum "
        "(voting_system) yet still included by score_calculator, which reads "
        "raw bias without the threshold — two layers disagree about whether "
        "the engine voted at all.")

    # 6.3 Score distribution around the min_score threshold — a healthy
    #     continuous score shows no cliff exactly at the gate.
    hist = _rows(con, "SELECT CAST(cf_score/5 AS INT)*5 AS bucket, COUNT(*) "
                      "FROM decisions WHERE cf_score IS NOT NULL "
                      "GROUP BY bucket ORDER BY bucket")
    add(6, "cf_score histogram (5-pt buckets)", "INFO",
        "Inspect mass just below 55–60: a spike there means the threshold, "
        "not the market, shapes the score.", [tuple(r) for r in hist])


# ─────────────────────── Axis 7 — Selectivity ─────────────────────────

def axis7_selectivity(con):
    tot = _rows(con, "SELECT COUNT(*) FROM decisions")[0][0]
    ex = _rows(con, "SELECT COUNT(*) FROM decisions WHERE verdict='EXECUTE'")[0][0]
    rate = 100.0 * ex / tot if tot else 0.0
    # Validated reference: the H4 backtest config takes ~902 trades per
    # ~5,900 evaluated decision points ≈ 15%. An order of magnitude below
    # that is not "philosophy", it is the starved-engine bug compounding
    # the quorum gate.
    status = "FAIL" if rate < 5 else ("WARN" if rate < 8 else "PASS")
    add(7, "EXECUTE rate vs validated selectivity", status,
        f"live {ex}/{tot} = {rate:.2f}% vs ≈15% for the same config on full "
        "data (h4_backtest_20260705). If the two engines + MTF found starved "
        "in Axis 3 were fed, most of this gap should close; re-run after the "
        "data fix before touching min_engines_agreeing or min_score.")

    byrsn = _rows(con, "SELECT COUNT(*) FROM decisions "
                       "WHERE fail_reason LIKE '%exposure%'")
    n = byrsn[0][0] if byrsn else 0
    add(7, "exposure-cap rejections", "WARN" if n else "INFO",
        f"{n} rejection(s) from 'Projected total exposure exceeds max'. With "
        "risk_per_trade_max=0.01 and max_exposure=0.05, five open paper "
        "positions saturate the book; auto-close only resolves on scheduler "
        "ticks at the latest close (outcome_tracker.py), so stale open trades "
        "block new signals. Verify open-outcome hygiene before reading this "
        "as real risk appetite.")


# ─────────────── Axis 8 — No-information confluence ───────────────────

def axis8_no_information(con):
    votes = _rows(con, "SELECT decision_id, engine, bias, score FROM engine_votes")
    by_dec: dict[int, list] = defaultdict(list)
    for did, eng, bias, score in votes:
        by_dec[did].append((eng, bias, score or 0.0))
    ex_ids = {r[0] for r in _rows(con, "SELECT id FROM decisions WHERE verdict='EXECUTE'")}

    thin = 0
    for d in ex_ids:
        evs = by_dec.get(d, [])
        informative = [e for e, b, s in evs
                       if b != "NEUTRAL" and s >= MIN_CONVICTION_SCORE]
        if len(informative) <= 2 and len(evs) >= 4:
            thin += 1
    add(8, "EXECUTE on a 2-of-4 quorum with half the panel mute",
        "WARN" if thin else "PASS",
        f"{thin}/{len(ex_ids)} EXECUTE decision(s) were carried by exactly 2 "
        "informative engines while ≥2 enabled engines were NEUTRAL/mute. "
        "min_engines_agreeing=2 was calibrated for a 4-voice panel; with two "
        "voices structurally silent (Axis 3) it degenerates into 'SMC and "
        "PriceAction both said so' — measured backtest agreement between "
        "those two is ~50%, i.e. a coin-flip co-signature, not confluence.")


# ────────────────────────────── main ──────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        with d1_client.d1_connection() as con:
            axis1_hierarchy(con)
            axis2_independence(con)
            axis3_phantom(con)
            axis4_impact(con)
            axis5_bottleneck(con)
            axis6_discontinuity(con)
            axis7_selectivity(con)
            axis8_no_information(con)
    except Exception as exc:
        print(f"ERROR: cannot reach the decisions DB ({exc}).\n"
              "Run on the VPS with D1_WORKER_URL / D1_PROXY_TOKEN set.",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([c.__dict__ for c in RESULTS], indent=1, default=str))
    else:
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ ", "INFO": "ℹ️ "}
        cur = None
        for c in RESULTS:
            if c.axis != cur:
                cur = c.axis
                print(f"\n━━━ Axis {c.axis} ━━━")
            print(f"{icon[c.status]} [{c.status}] {c.name}\n    {c.detail}")
            for ev in c.evidence[:12]:
                print(f"      {ev}")
        fails = sum(1 for c in RESULTS if c.status == "FAIL")
        warns = sum(1 for c in RESULTS if c.status == "WARN")
        print(f"\n{len(RESULTS)} checks — {fails} FAIL, {warns} WARN.")
    return 1 if any(c.status == "FAIL" for c in RESULTS) else 0


if __name__ == "__main__":
    sys.exit(main())
