"""tests/test_provenance.py — decision provenance fingerprints (M2).

utils/provenance.py fingerprint semantics, decision_db persistence
(including pre-migration fallback), and the philosophy-audit invariant.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils import provenance


def _frame(n=50, seed=7, provider="twelve_data") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = 1.10 + rng.normal(0, 0.001, n).cumsum()
    df = pd.DataFrame({
        "open": close + 0.0001,
        "high": close + 0.0005,
        "low": close - 0.0005,
        "close": close,
        "volume": rng.integers(100, 1000, n).astype(float),
    }, index=idx)
    df.attrs["provider"] = provider
    return df


# ---------------------------------------------------------------------------
# Fingerprint semantics
# ---------------------------------------------------------------------------

def test_config_hash_ignores_dict_ordering():
    a = {"risk": {"rr": 2.0, "max": 0.05}, "data": {"symbol": "EURUSD"}}
    b = {"data": {"symbol": "EURUSD"}, "risk": {"max": 0.05, "rr": 2.0}}
    assert provenance.config_hash(a) == provenance.config_hash(b)


def test_config_hash_changes_on_threshold_change():
    a = {"confluence": {"min_score_to_trade": 58}}
    b = {"confluence": {"min_score_to_trade": 60}}
    assert provenance.config_hash(a) != provenance.config_hash(b)


def test_data_version_stable_for_identical_bars():
    v1 = provenance.data_versions({"H4": _frame()})["H4"]
    v2 = provenance.data_versions({"H4": _frame()})["H4"]
    assert v1["sha256"] == v2["sha256"]
    assert v1["row_count"] == 50
    assert v1["provider"] == "twelve_data"


def test_data_version_changes_when_bars_change():
    v1 = provenance.data_versions({"H4": _frame(seed=7)})["H4"]
    v2 = provenance.data_versions({"H4": _frame(seed=8)})["H4"]
    assert v1["sha256"] != v2["sha256"]


def test_data_version_exposes_starvation():
    """The July 2026 incident detector: a truncated window is visible as
    row_count in every decision row."""
    v = provenance.data_versions({"H4": _frame(n=125)})["H4"]
    assert v["row_count"] == 125  # < 210 → NNFX starved, and now provable


def test_data_versions_never_raise(monkeypatch):
    class Broken:
        columns = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        def __len__(self):
            return 5
        index = [1]
    out = provenance.data_versions({"H4": Broken()})
    assert "error" in out["H4"]


def test_git_commit_env_override(monkeypatch):
    monkeypatch.setattr(provenance, "_git_commit_cache", None)
    monkeypatch.setenv("IATIS_GIT_COMMIT", "deadbeef1234")
    assert provenance.git_commit() == "deadbeef1234"
    monkeypatch.setattr(provenance, "_git_commit_cache", None)


def test_build_provenance_shape():
    block = provenance.build_provenance({"a": 1}, {"H4": _frame()})
    assert set(block) == {"git_commit", "config_hash", "data_versions"}
    assert block["config_hash"] == provenance.config_hash({"a": 1})


# ---------------------------------------------------------------------------
# decision_db persistence
# ---------------------------------------------------------------------------

def _report_with_provenance() -> dict:
    return {
        "symbol": "EURUSD",
        "final_verdict": "NO_TRADE",
        "summary": "test",
        "confluence": {"score": 40, "engines_participating": 4,
                       "fail_reasons": ["score too low"]},
        "risk": {},
        "regime": {"state": "TRENDING", "volatility": "normal"},
        "engine_outputs": [],
        "provenance": {
            "git_commit": "abc123def456",
            "config_hash": "0123456789abcdef",
            "data_versions": {"H4": {"provider": "twelve_data", "row_count": 750}},
        },
    }


def test_decision_row_carries_fingerprints(fake_d1):
    from storage.decision_db import log_decision_db
    log_decision_db(_report_with_provenance())
    row = fake_d1.execute(
        "SELECT git_commit, config_hash, data_versions FROM decisions"
    ).fetchone()
    assert row["git_commit"] == "abc123def456"
    assert row["config_hash"] == "0123456789abcdef"
    assert '"row_count": 750' in row["data_versions"]


def test_decision_without_provenance_writes_nulls(fake_d1):
    from storage.decision_db import log_decision_db
    rep = _report_with_provenance()
    del rep["provenance"]
    log_decision_db(rep)
    row = fake_d1.execute("SELECT git_commit, config_hash FROM decisions").fetchone()
    assert row["git_commit"] is None and row["config_hash"] is None


def test_premigration_table_falls_back_to_legacy_insert(fake_d1):
    """A production table without the provenance columns (migration 2 not
    yet applied) must still receive the decision — fingerprints dropped,
    row kept."""
    fake_d1.execute("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, symbol TEXT NOT NULL DEFAULT '',
            verdict TEXT NOT NULL, regime TEXT, volatility TEXT,
            trend_str REAL, cf_score REAL, cf_engines INTEGER,
            risk_passed INTEGER, fail_reason TEXT, summary TEXT, raw_json TEXT
        )""")
    fake_d1.commit()

    from storage.decision_db import log_decision_db
    log_decision_db(_report_with_provenance())
    row = fake_d1.execute("SELECT verdict, symbol FROM decisions").fetchone()
    assert row is not None and row["symbol"] == "EURUSD"


# ---------------------------------------------------------------------------
# Philosophy-audit invariant (Axis 9)
# ---------------------------------------------------------------------------

def test_axis9_flags_mid_sample_config_change(fake_d1):
    from storage import migrations
    migrations.apply_migrations()
    fake_d1.execute(
        "INSERT INTO decisions (ts, verdict, git_commit, config_hash) "
        "VALUES ('2026-07-01T00:00:00', 'NO_TRADE', 'aaa', 'hash1')")
    fake_d1.execute(
        "INSERT INTO decisions (ts, verdict, git_commit, config_hash) "
        "VALUES ('2026-07-10T00:00:00', 'NO_TRADE', 'bbb', 'hash2')")
    fake_d1.commit()

    from scripts.philosophy_audit import RESULTS, axis9_provenance
    from storage import d1_client
    RESULTS.clear()
    with d1_client.d1_connection() as con:
        axis9_provenance(con)
    by_name = {c.name: c for c in RESULTS}
    assert by_name["Distinct code+config fingerprints in the sample"].status == "WARN"
    assert by_name["Every decision fingerprinted since provenance began"].status == "PASS"


def test_axis9_passes_on_homogeneous_sample(fake_d1):
    from storage import migrations
    migrations.apply_migrations()
    for day in (1, 2, 3):
        fake_d1.execute(
            "INSERT INTO decisions (ts, verdict, git_commit, config_hash) "
            f"VALUES ('2026-07-0{day}T00:00:00', 'NO_TRADE', 'aaa', 'hash1')")
    fake_d1.commit()

    from scripts.philosophy_audit import RESULTS, axis9_provenance
    from storage import d1_client
    RESULTS.clear()
    with d1_client.d1_connection() as con:
        axis9_provenance(con)
    assert all(c.status == "PASS" for c in RESULTS if c.axis == 9)


def test_axis9_tolerates_premigration_schema(fake_d1):
    fake_d1.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, ts TEXT, verdict TEXT)")
    fake_d1.commit()
    from scripts.philosophy_audit import RESULTS, axis9_provenance
    from storage import d1_client
    RESULTS.clear()
    with d1_client.d1_connection() as con:
        axis9_provenance(con)
    assert RESULTS and RESULTS[0].status == "INFO"
