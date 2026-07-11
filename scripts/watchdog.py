"""
scripts/watchdog.py
--------------------
Independent health watchdog (production-audit item M7 / tier-1 gap #3:
"nothing watches the watcher"). Runs from its own systemd timer every 10
minutes — deliberately NOT inside the scheduler or API process, so it
survives their deaths and reports them.

Checks (all local, no market data, no D1):
  1. SCHEDULER LIVENESS — storage/decisions.jsonl must have been touched
     within WATCHDOG_MAX_SILENCE_MIN (default 300 = 2.5 x the 120-min
     scheduler interval). The scheduler appends a decision line for every
     symbol every run, so a stale file means the scheduler is dead, hung,
     or failing before persistence.
  2. API LIVENESS — GET /health on 127.0.0.1 must return 200.
  3. DISK — usage of the repo filesystem above WATCHDOG_DISK_WARN_PCT
     (default 85) alerts before "no space left" kills persistence.

Alerting: Telegram via the existing bot, with a per-condition cooldown
(default 6h) persisted in storage/watchdog_state.json so a broken night
produces two messages, not thirty-six. A recovery notice is sent once
when a previously-alerted condition clears.

Exit code: 0 all-clear, 1 at least one condition firing (visible in
`systemctl status iatis-watchdog`).

Install (see scripts/setup_service_user.sh or manually):
    cp iatis-watchdog.service iatis-watchdog.timer /etc/systemd/system/
    systemctl daemon-reload && systemctl enable --now iatis-watchdog.timer
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "storage" / "watchdog_state.json"
DECISIONS_JSONL = PROJECT_ROOT / "storage" / "decisions.jsonl"

MAX_SILENCE_MIN = float(os.environ.get("WATCHDOG_MAX_SILENCE_MIN", 300))
DISK_WARN_PCT = float(os.environ.get("WATCHDOG_DISK_WARN_PCT", 85))
COOLDOWN_H = float(os.environ.get("WATCHDOG_ALERT_COOLDOWN_H", 6))
HEALTH_URL = os.environ.get("WATCHDOG_HEALTH_URL", "http://127.0.0.1:8000/health")


def _check_scheduler() -> str | None:
    if not DECISIONS_JSONL.exists():
        return f"decisions.jsonl missing at {DECISIONS_JSONL} — scheduler has never persisted (or wrong path)"
    age_min = (time.time() - DECISIONS_JSONL.stat().st_mtime) / 60
    if age_min > MAX_SILENCE_MIN:
        return (f"scheduler silent: no decision persisted for {age_min:.0f} min "
                f"(threshold {MAX_SILENCE_MIN:.0f}) — check `systemctl status iatis-scheduler`")
    return None


def _check_api() -> str | None:
    import requests
    try:
        r = requests.get(HEALTH_URL, timeout=10)
        if r.status_code != 200:
            return f"API /health returned {r.status_code} — check `systemctl status iatis-api`"
    except Exception as exc:
        return f"API unreachable at {HEALTH_URL}: {type(exc).__name__} — check `systemctl status iatis-api`"
    return None


def _check_disk() -> str | None:
    usage = shutil.disk_usage(PROJECT_ROOT)
    pct = usage.used / usage.total * 100
    if pct >= DISK_WARN_PCT:
        free_mb = usage.free / 1e6
        return (f"disk {pct:.0f}% full ({free_mb:.0f} MB free) — persistence "
                f"(decisions.jsonl, backups, td_cache) will start failing")
    return None


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=1))
    except Exception:
        pass  # a watchdog must never die on its own bookkeeping


def _notify(text: str) -> None:
    try:
        from execution.telegram_bot import send_raw
        send_raw(text)
    except Exception as exc:
        print(f"watchdog: telegram notify failed: {exc}", file=sys.stderr)


def main() -> int:
    checks = {
        "scheduler": _check_scheduler,
        "api": _check_api,
        "disk": _check_disk,
    }
    state = _load_state()
    now = time.time()
    firing = False

    for name, fn in checks.items():
        try:
            problem = fn()
        except Exception as exc:  # a check crashing is itself a finding
            problem = f"watchdog check '{name}' crashed: {type(exc).__name__}: {exc}"

        entry = state.get(name, {})
        if problem:
            firing = True
            print(f"[FAIL] {name}: {problem}")
            last_alert = entry.get("alerted_at", 0)
            if now - last_alert >= COOLDOWN_H * 3600:
                _notify(f"🚨 <b>IATIS watchdog — {name}</b>\n{problem}")
                entry["alerted_at"] = now
            entry["failing_since"] = entry.get("failing_since") or now
            entry["problem"] = problem
        else:
            print(f"[ ok ] {name}")
            if entry.get("failing_since"):
                down_min = (now - entry["failing_since"]) / 60
                _notify(f"✅ <b>IATIS watchdog — {name} recovered</b> "
                        f"(was failing for {down_min:.0f} min)")
            state[name] = {}
            continue
        state[name] = entry

    _save_state(state)
    return 1 if firing else 0


if __name__ == "__main__":
    sys.exit(main())
