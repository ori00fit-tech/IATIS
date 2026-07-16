"""
research/replay.py
-------------------
Decision replay harness (institutional gap analysis S2).

Purpose: prove that a refactor did not change behavior, and answer
"why did decision X happen" from its own stored inputs — the regression
tool that makes behavior-frozen refactors (the CC-71 `run_pipeline`
split, the shared-indicator consolidation) safe to attempt.

How it works:
  persist_window(report, mtf_data, config)
      Called by main.run_pipeline (default: EXECUTE decisions only,
      system.persist_replay_windows: execute|all|off). Writes ONE
      self-contained JSON artifact per decision under
      storage/replay_windows/: the exact per-timeframe input frames
      (full float64 precision — Python json round-trips floats exactly),
      the effective config, the decision wall-clock time, and the
      original outcome (verdict / score / votes / provenance).

  replay(path)
      Loads the artifact, re-runs run_pipeline() on the stored frames
      (data.source=injected + _injected_mtf), at the stored wall-clock
      time (system._replay_now — the MQS session/day scoring is
      time-of-day dependent), in replay mode (system.replay_mode=true —
      run_pipeline skips ALL persistence, outcome logging and alerts),
      and diffs the fresh outputs against the stored ones.

Determinism boundary (deliberate, documented):
  - The news gate is disabled during replay: it fetches LIVE calendars
    and cannot reproduce the original news state. EXECUTE decisions were
    by definition not news-blocked, so this cannot flip an EXECUTE
    artifact's verdict; for 'all'-mode artifacts a news-blocked original
    will show a verdict diff, flagged with this note.
  - Everything else in the pipeline is deterministic from (frames,
    config, decision time): engines, confluence, meta and risk maths
    carry no clock or DB reads that affect the verdict.

CLI:
    python -m research.replay <artifact.json> [...]     # replay + diff
    python -m research.replay --all [--dir DIR]         # replay corpus
Exit code 0 = all identical, 1 = any diff.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "storage" / "replay_windows"

# Report keys whose values are compared on replay. Votes are compared
# separately (per engine).
_COMPARED_SCALARS = (
    ("final_verdict", lambda r: r.get("final_verdict")),
    ("confluence.score", lambda r: (r.get("confluence") or {}).get("score")),
    ("confluence.passed", lambda r: (r.get("confluence") or {}).get("passed")),
    ("risk.passed", lambda r: (r.get("risk") or {}).get("passed")),
    ("entry_price", lambda r: r.get("entry_price")),
    ("stop_loss", lambda r: r.get("stop_loss")),
    ("take_profit", lambda r: r.get("take_profit")),
)


# ---------------------------------------------------------------------------
# Frame (de)serialization — exact float64 round trip via Python json
# ---------------------------------------------------------------------------

def _frame_to_payload(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "provider": str(df.attrs.get("provider", "unknown")),
        "index": [str(ts) for ts in df.index],
        "columns": list(df.columns),
        "data": df.to_numpy().tolist(),
    }


def _frame_from_payload(payload: dict[str, Any]) -> pd.DataFrame:
    df = pd.DataFrame(
        payload["data"],
        columns=payload["columns"],
        index=pd.to_datetime(payload["index"]),
    )
    df.attrs["provider"] = payload.get("provider", "unknown")
    return df


def _strip_private(data_cfg: dict) -> dict:
    """Drop underscore-prefixed injection keys before persisting config."""
    return {k: v for k, v in data_cfg.items() if not str(k).startswith("_")}


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------

def persist_window(report: dict, mtf_data: dict, config: dict,
                   base_dir: Path | str | None = None) -> Path:
    """Write the self-contained replay artifact for one decision.

    Called through main._safe_store — may raise; the caller logs and
    continues (a lost window never blocks the decision itself).
    """
    base = Path(base_dir) if base_dir else DEFAULT_DIR
    base.mkdir(parents=True, exist_ok=True)

    symbol = report.get("symbol", "UNKNOWN")
    bar_time = str(report.get("bar_time", ""))
    stamp = re.sub(r"[^0-9A-Za-z]+", "", bar_time) or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    prov = report.get("provenance") or {}

    artifact = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # The original decision's wall-clock moment: replayed MQS scoring
        # must run at this time, not at replay time.
        "decision_wall_time": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "bar_time": bar_time,
        "original": {
            "final_verdict": report.get("final_verdict"),
            "confluence": {
                "score": (report.get("confluence") or {}).get("score"),
                "passed": (report.get("confluence") or {}).get("passed"),
            },
            "risk": {"passed": (report.get("risk") or {}).get("passed")},
            "entry_price": report.get("entry_price"),
            "stop_loss": report.get("stop_loss"),
            "take_profit": report.get("take_profit"),
            "engine_votes": {
                e.get("engine"): {"bias": e.get("bias"), "score": e.get("score")}
                for e in report.get("engine_outputs", [])
            },
            "provenance": prov,
        },
        "config": {**config, "data": _strip_private(config.get("data", {}))},
        "frames": {tf: _frame_to_payload(df) for tf, df in (mtf_data or {}).items()},
    }

    path = base / f"{symbol}_{stamp}_{prov.get('config_hash', 'nohash')[:8]}.json"
    path.write_text(json.dumps(artifact, default=str))
    logger.info(f"Replay window persisted: {path.name} "
                f"({sum(len(f['index']) for f in artifact['frames'].values())} bars total)")
    return path


# ---------------------------------------------------------------------------
# Replay + diff
# ---------------------------------------------------------------------------

@dataclass
class ReplayResult:
    artifact: str
    identical: bool
    diffs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    original_verdict: str | None = None
    replayed_verdict: str | None = None


def _diff_value(name: str, old: Any, new: Any, diffs: list[str]) -> None:
    if isinstance(old, float) and isinstance(new, float):
        if abs(old - new) > 1e-9:
            diffs.append(f"{name}: {old!r} → {new!r}")
    elif old != new:
        diffs.append(f"{name}: {old!r} → {new!r}")


def replay(path: Path | str) -> ReplayResult:
    """Re-run one stored decision and diff it against the original."""
    path = Path(path)
    artifact = json.loads(path.read_text())
    original = artifact["original"]

    frames = {tf: _frame_from_payload(p) for tf, p in artifact["frames"].items()}

    config = json.loads(json.dumps(artifact["config"]))  # deep copy
    config.setdefault("data", {})
    config["data"]["source"] = "injected"
    config["data"]["_injected_mtf"] = frames
    config.setdefault("system", {})
    config["system"]["replay_mode"] = True
    config["system"]["_replay_now"] = artifact.get("decision_wall_time")
    # News fetches live calendars — not reproducible (see module doc).
    config.setdefault("fundamentals", {})["news_filter_enabled"] = False
    # Telegram belt-and-braces: replay_mode already returns before alerts.
    config.setdefault("telegram", {})["enabled"] = False

    from main import run_pipeline
    report = run_pipeline(config)

    result = ReplayResult(
        artifact=path.name,
        identical=True,
        original_verdict=original.get("final_verdict"),
        replayed_verdict=report.get("final_verdict"),
        notes=["news gate neutralized in replay (live-fetch, not reproducible)"],
    )

    for name, getter in _COMPARED_SCALARS:
        old = _get_original_scalar(original, name)
        _diff_value(name, old, getter(report), result.diffs)

    old_votes = original.get("engine_votes") or {}
    new_votes = {
        e.get("engine"): {"bias": e.get("bias"), "score": e.get("score")}
        for e in report.get("engine_outputs", [])
    }
    for eng in sorted(set(old_votes) | set(new_votes)):
        _diff_value(f"vote[{eng}].bias",
                    (old_votes.get(eng) or {}).get("bias"),
                    (new_votes.get(eng) or {}).get("bias"), result.diffs)
        _diff_value(f"vote[{eng}].score",
                    (old_votes.get(eng) or {}).get("score"),
                    (new_votes.get(eng) or {}).get("score"), result.diffs)

    # Self-check that the injection was faithful: the replayed run's data
    # fingerprints must equal the stored ones (utils/provenance.py hashes
    # the frames the pipeline actually consumed).
    old_dv = (original.get("provenance") or {}).get("data_versions") or {}
    new_dv = (report.get("provenance") or {}).get("data_versions") or {}
    for tf in old_dv:
        _diff_value(f"data_versions[{tf}].sha256",
                    (old_dv.get(tf) or {}).get("sha256"),
                    (new_dv.get(tf) or {}).get("sha256"), result.diffs)

    result.identical = not result.diffs
    return result


def _get_original_scalar(original: dict, name: str) -> Any:
    if name == "final_verdict":
        return original.get("final_verdict")
    if name.startswith("confluence."):
        return (original.get("confluence") or {}).get(name.split(".", 1)[1])
    if name.startswith("risk."):
        return (original.get("risk") or {}).get(name.split(".", 1)[1])
    return original.get(name)


def replay_all(base_dir: Path | str | None = None) -> list[ReplayResult]:
    base = Path(base_dir) if base_dir else DEFAULT_DIR
    return [replay(p) for p in sorted(base.glob("*.json"))]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[2])
    ap.add_argument("artifacts", nargs="*", help="artifact JSON file(s)")
    ap.add_argument("--all", action="store_true", help="replay the whole corpus")
    ap.add_argument("--dir", default=None, help="corpus directory (default storage/replay_windows)")
    args = ap.parse_args()

    if args.all:
        results = replay_all(args.dir)
    elif args.artifacts:
        results = [replay(p) for p in args.artifacts]
    else:
        ap.error("give artifact path(s) or --all")

    any_diff = False
    for r in results:
        mark = "✅ IDENTICAL" if r.identical else "❌ DIFF"
        print(f"{mark}  {r.artifact}  "
              f"({r.original_verdict} → {r.replayed_verdict})")
        for d in r.diffs:
            any_diff = True
            print(f"    {d}")
    if not results:
        print("No artifacts found.")
    print(f"\n{len(results)} artifact(s), "
          f"{sum(1 for r in results if not r.identical)} with diffs.")
    return 1 if any_diff else 0


if __name__ == "__main__":
    raise SystemExit(main())
