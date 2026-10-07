#!/usr/bin/env python3
"""
scripts/run_shadow_observe_cycle.py
------------------------------------------
External Shadow Observe Runner (operator's own locked Design Gate,
2026-10, "External Scheduler/Runner", Phase: External Shadow Observe
Runner). The ONE standalone entry point that wires the whole SHADOW
observation chain together for a single cycle:

    storage.live_roster.list_active_entries()
            |
            v
    backtest.shadow_roster_composer.compose_shadow_observe_entries(
        evidence_resolver=backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence)
            |
            v
    backtest.shadow_observe_orchestrator.run_shadow_observe_cycle()
            |
            v
    research_shadow_decision_snapshots (captured, if anything observed)

RESEARCH-ONLY, NO LIVE DECISION PATH (same framing as the already-
existing iatis-marketaux-collect.service/.timer pair, reused verbatim
for this script's own systemd units): this script never imports
backtest.promotion_gate itself (the composer it calls does, for the
SHADOW-stage eligibility check ONLY -- never LIMITED/ACTIVE), never
imports execution.authorization/execution.trade_executor, and never
touches scheduler.py or main.py. Cadence is entirely owned by the
systemd timer that fires this script (iatis-shadow-observe.timer) --
this script contains no clock/sleep/loop of its own; one process
invocation == one observe cycle, then exit.

"CYCLE COMPLETED" != "EVIDENCE PRODUCED" (operator's own locked
correction to this Design Gate's own earlier phrasing): a successful
exit (code 0) means the cycle ran to completion without raising -- it
does NOT mean anything was observed or captured. `captured_count == 0`
on a successful exit is an ordinary, expected outcome (e.g. an empty
roster) -- never treated as, logged as, or reported as a failure here.

FAILURE HANDLING (operator's own locked decision, this Design Gate):
on any exception, logs it (logger.exception) and exits non-zero --
systemd then marks the unit `failed`, and the journal is the sole
observability mechanism for this phase. Deliberately NO Telegram/
alerting integration here (unlike main.py/scheduler.py's own live
operational alerts) -- this is a research-only observation path, kept
isolated from live-operational alerting infrastructure by design.

Usage (VPS -- scheduled via iatis-shadow-observe.timer; `systemctl
enable --now iatis-shadow-observe.timer`, don't run the .service
directly except for manual testing):

    python3 -m scripts.run_shadow_observe_cycle
"""
from __future__ import annotations

from utils.logger import get_logger

logger = get_logger(__name__)


def main() -> int:
    from backtest.shadow_live_evidence_resolver import resolve_live_shadow_evidence
    from backtest.shadow_observe_orchestrator import run_shadow_observe_cycle
    from backtest.shadow_roster_composer import compose_shadow_observe_entries
    from storage.live_roster import list_active_entries
    from utils.helpers import load_config

    base_config = load_config()
    roster_entries = list_active_entries()
    logger.info(f"shadow observe cycle starting: {len(roster_entries)} active roster entr(y/ies) found")

    entries = compose_shadow_observe_entries(roster_entries, evidence_resolver=resolve_live_shadow_evidence)
    result = run_shadow_observe_cycle(entries=entries, base_config=base_config)

    logger.info(
        f"shadow observe cycle complete: entries_count={result['entries_count']} "
        f"observed_count={result['observed_count']} captured_count={result['captured_count']}"
    )
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        logger.exception("shadow observe cycle failed")
        raise SystemExit(1)
    raise SystemExit(exit_code)
