"""tests/test_shadow_effective_sample.py -- tests for backtest/
shadow_effective_sample.py (Within-Hypothesis Dependence: Effective
Sample Size, Design Gate locked 2026-10): connected-components
clustering over exposure_windows with inclusive (<=) boundary overlap,
deterministic representative selection (earliest bar_time, request_id
ASC tie-break), k_eff counting representatives only, fail-closed on a
missing representative outcome lookup, and structural isolation from
every production/statistical layer."""
from __future__ import annotations

import random

import pytest

from backtest import shadow_effective_sample as ses
from backtest.shadow_outcome_resolver import SL_HIT, TP_HIT


def _window(request_id: str, bar_time: str, resolved_bar_time: str) -> dict:
    return {"request_id": request_id, "bar_time": bar_time, "resolved_bar_time": resolved_bar_time}


# ---------- cluster_exposure_windows() --------------------------------------


def test_empty_input_returns_empty_list():
    assert ses.cluster_exposure_windows([]) == []


def test_single_window_forms_its_own_cluster():
    windows = [_window("R1", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00")]
    clusters = ses.cluster_exposure_windows(windows)
    assert clusters == [{"representative_request_id": "R1", "member_request_ids": ["R1"]}]


def test_non_overlapping_windows_each_form_their_own_cluster():
    """n_eff == n_t exactly when there is no overlap at all -- the
    locked consistency invariant, not an ordinary case."""
    windows = [
        _window("R1", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00"),
        _window("R2", "2026-09-01T08:00:00+00:00", "2026-09-01T12:00:00+00:00"),
        _window("R3", "2026-09-01T16:00:00+00:00", "2026-09-01T20:00:00+00:00"),
    ]
    clusters = ses.cluster_exposure_windows(windows)
    n_t = len(windows)
    assert len(clusters) == n_t  # n_eff == n_t
    assert {c["representative_request_id"] for c in clusters} == {"R1", "R2", "R3"}
    for cluster in clusters:
        assert len(cluster["member_request_ids"]) == 1


def test_direct_overlap_merges_into_one_cluster():
    windows = [
        _window("R1", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00"),
        _window("R2", "2026-09-01T02:00:00+00:00", "2026-09-01T06:00:00+00:00"),
    ]
    clusters = ses.cluster_exposure_windows(windows)
    assert len(clusters) == 1
    assert sorted(clusters[0]["member_request_ids"]) == ["R1", "R2"]


def test_transitive_overlap_forms_one_component_even_without_direct_overlap():
    """A overlaps B, B overlaps C, but A does NOT directly overlap C --
    all three must still end up in the SAME connected component."""
    a = _window("A", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00")
    b = _window("B", "2026-09-01T03:00:00+00:00", "2026-09-01T08:00:00+00:00")
    c = _window("C", "2026-09-01T07:00:00+00:00", "2026-09-01T10:00:00+00:00")

    # Sanity: A and C do NOT directly overlap (confirms the test is meaningful).
    assert not ses._windows_overlap(
        (ses._parse_utc(a["bar_time"]), ses._parse_utc(a["resolved_bar_time"])),
        (ses._parse_utc(c["bar_time"]), ses._parse_utc(c["resolved_bar_time"])),
    )

    clusters = ses.cluster_exposure_windows([a, b, c])
    assert len(clusters) == 1
    assert sorted(clusters[0]["member_request_ids"]) == ["A", "B", "C"]


def test_boundary_touching_counts_as_overlap():
    """Locked, deliberately conservative choice: a window ending at the
    EXACT instant another begins is treated as overlapping (<=), not
    independent."""
    windows = [
        _window("R1", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00"),
        _window("R2", "2026-09-01T04:00:00+00:00", "2026-09-01T08:00:00+00:00"),
    ]
    clusters = ses.cluster_exposure_windows(windows)
    assert len(clusters) == 1
    assert sorted(clusters[0]["member_request_ids"]) == ["R1", "R2"]


def test_deterministic_representative_is_earliest_bar_time():
    windows = [
        _window("R2", "2026-09-01T02:00:00+00:00", "2026-09-01T06:00:00+00:00"),
        _window("R1", "2026-09-01T00:00:00+00:00", "2026-09-01T10:00:00+00:00"),
        _window("R3", "2026-09-01T05:00:00+00:00", "2026-09-01T09:00:00+00:00"),
    ]
    clusters = ses.cluster_exposure_windows(windows)
    assert len(clusters) == 1
    assert clusters[0]["representative_request_id"] == "R1"  # earliest bar_time


def test_tie_on_bar_time_breaks_by_request_id_ascending():
    windows = [
        _window("R2", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00"),
        _window("R1", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00"),
    ]
    clusters = ses.cluster_exposure_windows(windows)
    assert len(clusters) == 1
    assert clusters[0]["representative_request_id"] == "R1"  # lexicographically smaller


def test_clustering_is_independent_of_input_order():
    windows = [
        _window("A", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00"),
        _window("B", "2026-09-01T03:00:00+00:00", "2026-09-01T08:00:00+00:00"),
        _window("C", "2026-09-01T07:00:00+00:00", "2026-09-01T10:00:00+00:00"),
        _window("D", "2026-09-02T00:00:00+00:00", "2026-09-02T04:00:00+00:00"),
    ]
    baseline = ses.cluster_exposure_windows(windows)
    shuffled = list(windows)
    random.Random(42).shuffle(shuffled)
    assert ses.cluster_exposure_windows(shuffled) == baseline


# ---------- compute_effective_sample() --------------------------------------


def test_compute_effective_sample_empty_clusters_is_zero_zero():
    assert ses.compute_effective_sample([], {}) == {"n_eff": 0, "k_eff": 0}


def test_n_eff_equals_cluster_count():
    clusters = [
        {"representative_request_id": "R1", "member_request_ids": ["R1"]},
        {"representative_request_id": "R2", "member_request_ids": ["R2", "R3"]},
    ]
    outcomes = {"R1": SL_HIT, "R2": SL_HIT}
    assert ses.compute_effective_sample(clusters, outcomes)["n_eff"] == 2


def test_k_eff_counts_only_tp_hit_representatives():
    clusters = [
        {"representative_request_id": "R1", "member_request_ids": ["R1"]},
        {"representative_request_id": "R2", "member_request_ids": ["R2"]},
        {"representative_request_id": "R3", "member_request_ids": ["R3"]},
    ]
    outcomes = {"R1": TP_HIT, "R2": SL_HIT, "R3": TP_HIT}
    result = ses.compute_effective_sample(clusters, outcomes)
    assert result == {"n_eff": 3, "k_eff": 2}


def test_k_eff_depends_only_on_representative_outcome():
    """Works correctly even when outcomes_by_request_id contains ONLY
    the representatives -- k_eff never needs a non-representative
    member's outcome at all."""
    clusters = [
        {"representative_request_id": "R1", "member_request_ids": ["R1", "R2", "R3"]},
    ]
    outcomes = {"R1": TP_HIT}  # R2, R3 (non-representative members) absent entirely
    assert ses.compute_effective_sample(clusters, outcomes) == {"n_eff": 1, "k_eff": 1}


def test_non_representative_member_outcomes_do_not_affect_k_eff():
    """If (incorrectly) consulted, the non-representative members'
    outcomes below would flip the result -- this proves they are not."""
    clusters = [
        {"representative_request_id": "R1", "member_request_ids": ["R1", "R2", "R3"]},
    ]
    # Representative R1 is SL_HIT; every non-representative member is TP_HIT.
    # A correct implementation must still report k_eff=0 for this cluster.
    outcomes = {"R1": SL_HIT, "R2": TP_HIT, "R3": TP_HIT}
    assert ses.compute_effective_sample(clusters, outcomes) == {"n_eff": 1, "k_eff": 0}


def test_missing_representative_outcome_raises_explicitly():
    clusters = [{"representative_request_id": "R-missing", "member_request_ids": ["R-missing"]}]
    with pytest.raises(ses.ShadowEffectiveSampleError, match="R-missing"):
        ses.compute_effective_sample(clusters, {})


# ---------- end-to-end: cluster + compute composed --------------------------


def test_end_to_end_non_overlapping_matches_raw_counts():
    """With no overlap at all, n_eff/k_eff must equal the raw n_t/tp_count
    a caller would have computed without any clustering at all."""
    windows = [
        _window("R1", "2026-09-01T00:00:00+00:00", "2026-09-01T04:00:00+00:00"),
        _window("R2", "2026-09-01T08:00:00+00:00", "2026-09-01T12:00:00+00:00"),
        _window("R3", "2026-09-01T16:00:00+00:00", "2026-09-01T20:00:00+00:00"),
    ]
    outcomes = {"R1": TP_HIT, "R2": TP_HIT, "R3": SL_HIT}
    clusters = ses.cluster_exposure_windows(windows)
    result = ses.compute_effective_sample(clusters, outcomes)
    assert result == {"n_eff": 3, "k_eff": 2}  # matches raw n_t=3, tp_count=2


# ---------- structural: no production/statistical wiring --------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(ses)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_performs_no_io():
    body = _source_without_docstrings()
    forbidden = ("import storage", "d1_client", "fetch_with_failover", "open(", "requests.")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_effective_sample unexpectedly references {pattern!r}"


def test_module_is_isolated_from_production_and_statistical_layers():
    body = _source_without_docstrings()
    forbidden = (
        "shadow_outcome_evidence", "shadow_record", "shadow_integration",
        "shadow_divergence_membership", "compute_catastrophic_divergence_p_value",
        "binomial_lower_tail_p_value", "bonferroni_alpha", "classify_significance",
        "build_shadow_record", "record_family_membership_if_attempted",
        "promotion_gate", "policy_health", "execution_attribution",
        "trade_executor", "scheduler", "main.py",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_effective_sample unexpectedly references {pattern!r}"
