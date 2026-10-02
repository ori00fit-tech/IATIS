"""
data_trust/manifest.py
---------------------------------------
Phase 11 — dataset identity (a deterministic content hash) and the
DatasetManifest hand-off shape.

NON-NEGOTIABLE: this module never calls assess_data_trust() (data_trust.
quality's own orchestrator) -- build_manifest() only ever SNAPSHOTS an
already-computed `quality_result` dict handed to it by the caller. There
is no hidden re-evaluation here, and no side effect of any kind -- every
function in this module is a pure, total function of its own arguments.

dataset_hash doubles as the manifest's own identity (operator's own
accepted simplification) -- no separate, randomly-generated `dataset_id`
field exists alongside it. Re-loading the exact same OHLCV content from
a different provider, a different file, or a differently-ordered
DataFrame produces the SAME dataset_hash; any real difference in the
canonical (timestamp, open, high, low, close, volume) content produces a
different one.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd

LIVE_PROVIDER_DATA = "LIVE_PROVIDER_DATA"
INJECTED_REPLAY_DATA = "INJECTED_REPLAY_DATA"
VALID_PROVENANCE = (LIVE_PROVIDER_DATA, INJECTED_REPLAY_DATA)

SCHEMA_VERSION = "1"

_HASH_REQUIRED_COLUMNS = ("open", "high", "low", "close")
_HASH_LENGTH = 16


class DataTrustError(Exception):
    """Structural misuse only (an invalid provenance value, a DataFrame
    missing the columns a canonical hash requires) -- never raised for an
    ordinary data-quality finding, which is reported via `validation_status`/
    `reasons`, never an exception."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_dataset_hash(df: pd.DataFrame, symbol: str, timeframe: str) -> str:
    """A canonical, deterministic sha256 over the dataset's own real
    content -- never the raw provider payload, never pandas' own
    internal representation. Rows are sorted by timestamp (order-
    independent); each OHLCV value is formatted to a fixed numeric
    precision (so e.g. 1.1 and 1.10000000000000001 -- the same float
    value through two different code paths -- always hash identically);
    volume defaults to 0.0 when absent, matching core/data_providers.py's
    own established convention for a provider that doesn't report it.

    Raises DataTrustError if the required OHLC columns aren't present --
    there is no canonical content to hash without them."""
    missing = [c for c in _HASH_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataTrustError(
            f"compute_dataset_hash: missing required column(s) {missing} -- cannot compute a canonical hash."
        )
    sorted_df = df.sort_index()
    rows = []
    for ts, row in sorted_df.iterrows():
        volume = row["volume"] if "volume" in sorted_df.columns else 0.0
        rows.append([
            ts.isoformat(),
            f"{float(row['open']):.10g}", f"{float(row['high']):.10g}",
            f"{float(row['low']):.10g}", f"{float(row['close']):.10g}",
            f"{float(volume):.10g}",
        ])
    canonical = json.dumps(
        {"symbol": symbol, "timeframe": timeframe, "rows": rows}, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_HASH_LENGTH]


def build_manifest(
    df: pd.DataFrame, *, symbol: str, timeframe: str, provider: str, provenance: str,
    quality_result: dict[str, Any],
) -> dict[str, Any]:
    """Pure snapshot construction. `quality_result` is an ALREADY-COMPUTED
    result (the orchestrator in data_trust/quality.py produces it, shaped
    as {"validation_status", "reasons", "timezone", "missing_bars"}) --
    this function reads it, it never (re)computes it.

    dataset_hash falls back to None (never a crash, never a fabricated
    value) when the DataFrame is too malformed to hash at all (e.g.
    missing OHLC columns entirely) -- the real reason is already captured
    in quality_result's own `reasons`, via validate_ohlcv()'s own
    "Missing required columns" error."""
    if provenance not in VALID_PROVENANCE:
        raise DataTrustError(f"build_manifest: provenance must be one of {VALID_PROVENANCE}, got {provenance!r}.")

    try:
        dataset_hash = compute_dataset_hash(df, symbol, timeframe)
    except DataTrustError:
        dataset_hash = None

    missing_bars = quality_result.get("missing_bars")
    return {
        "dataset_hash": dataset_hash,
        "symbol": symbol,
        "timeframe": timeframe,
        "provider": provider,
        "start": str(df.index.min()) if len(df) else None,
        "end": str(df.index.max()) if len(df) else None,
        "row_count": len(df),
        "timezone": quality_result.get("timezone"),
        "provenance": provenance,
        "missing_bar_count": missing_bars["gaps_total"] if missing_bars else None,
        "validation_status": quality_result.get("validation_status"),
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
    }
