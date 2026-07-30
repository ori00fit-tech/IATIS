"""
tests/test_feature_mining.py
--------------------------------
Feature Mining / Hypothesis Discovery Phase 1 (2026-07-30) — pure-function
tests for backtest/feature_mining.py, mirroring tests/test_meta_analysis.py's
structure. Uses hand-built TradeRecord fixtures (not real backtests) so
every bin's expected win_rate/mean_r/lift is known exactly.
"""
from __future__ import annotations

import pytest

from backtest.feature_mining import (
    MIN_BIN_SIZE,
    MIN_TRADES_FOR_FEATURE_MINING,
    compute_feature_mining,
    pool_feature_mining_results,
)
from backtest.metrics import TradeRecord


def _tr(is_win: bool, rr_actual: float, features: dict | None = None) -> TradeRecord:
    return TradeRecord(
        trade_id="t", symbol="EURUSD", direction="BUY",
        entry_time=None, exit_time=None, entry_price=1.0, exit_price=1.0,
        stop_loss=0.99, take_profit=1.02, position_size=1.0,
        is_win=is_win, rr_actual=rr_actual, features=features or {},
    )


class TestWholeSampleGate:
    def test_insufficient_data_below_min_trades(self):
        trades = [_tr(True, 1.0, {"atr_value": 1.0}) for _ in range(MIN_TRADES_FOR_FEATURE_MINING - 1)]
        result = compute_feature_mining(trades)
        assert result.insufficient_data is True
        assert result.n_features_tested == 0
        assert result.associations == []

    def test_sufficient_data_at_exactly_min_trades(self):
        trades = [_tr(i % 2 == 0, 1.0 if i % 2 == 0 else -1.0, {"atr_value": float(i)})
                  for i in range(MIN_TRADES_FOR_FEATURE_MINING)]
        result = compute_feature_mining(trades)
        assert result.insufficient_data is False


class TestNumericFeature:
    def test_quantile_binning_correct_win_rate_and_lift(self):
        # 40 trades: bottom half always loses (rr=-1), top half always wins
        # (rr=+2) — a feature with an obvious, exact, verifiable split.
        trades = []
        for i in range(40):
            value = float(i)
            is_win = i >= 20
            trades.append(_tr(is_win, 2.0 if is_win else -1.0, {"score": value}))
        result = compute_feature_mining(trades, n_quantiles=2, min_bin_size=10)
        assoc = next(a for a in result.associations if a.feature == "score")
        assert assoc.feature_type == "numeric"
        assert assoc.insufficient_data is False
        assert assoc.overall_win_rate == pytest.approx(0.5)
        assert len(assoc.bins) == 2
        low_bin = next(b for b in assoc.bins if b.bin_index == 0)
        high_bin = next(b for b in assoc.bins if b.bin_index == 1)
        assert low_bin.win_rate == pytest.approx(0.0)
        assert high_bin.win_rate == pytest.approx(1.0)
        assert high_bin.lift_win_rate == pytest.approx(2.0)  # 1.0 / 0.5
        assert low_bin.lift_win_rate == pytest.approx(0.0)

    def test_no_variance_feature_is_insufficient(self):
        trades = [_tr(i % 2 == 0, 1.0, {"constant": 5.0}) for i in range(40)]
        result = compute_feature_mining(trades)
        assoc = next(a for a in result.associations if a.feature == "constant")
        assert assoc.insufficient_data is True
        assert "variance" in assoc.note

    def test_bins_below_min_bin_size_are_dropped(self):
        # 40 evenly-spread trades over 4 quantiles -> each bin naturally
        # holds ~10 trades. Requiring min_bin_size=11 must drop every one
        # of them, even though the feature itself had plenty of trades.
        trades = [_tr(i % 2 == 0, 1.0, {"feat": float(i)}) for i in range(40)]
        result = compute_feature_mining(trades, n_quantiles=4, min_bin_size=11)
        assoc = next(a for a in result.associations if a.feature == "feat")
        assert assoc.insufficient_data is False
        assert assoc.n_observed == 40
        assert assoc.bins == []

    def test_p_value_none_when_std_zero(self):
        # Every trade in a bin has the identical R-multiple -> std_r == 0
        # -> trial_p_value returns None (never a fabricated p-value).
        trades = [_tr(True, 1.0, {"feat": float(i)}) for i in range(40)]
        result = compute_feature_mining(trades, n_quantiles=1, min_bin_size=10)
        assoc = next(a for a in result.associations if a.feature == "feat")
        for b in assoc.bins:
            assert b.p_value is None
            assert b.significance == "INSUFFICIENT_DATA"


class TestCategoricalFeature:
    def test_categorical_bins_grouped_by_exact_value(self):
        trades = []
        for i in range(40):
            regime = "TRENDING" if i < 20 else "RANGING"
            is_win = regime == "TRENDING"
            trades.append(_tr(is_win, 1.5 if is_win else -1.0, {"regime": regime}))
        result = compute_feature_mining(trades)
        assoc = next(a for a in result.associations if a.feature == "regime")
        assert assoc.feature_type == "categorical"
        labels = {b.bin_label for b in assoc.bins}
        assert labels == {"TRENDING", "RANGING"}
        trending_bin = next(b for b in assoc.bins if b.bin_label == "TRENDING")
        assert trending_bin.win_rate == pytest.approx(1.0)
        assert trending_bin.bin_index is None

    def test_boolean_feature_treated_as_categorical(self):
        trades = [_tr(i % 2 == 0, 1.0, {"flag": (i % 2 == 0)}) for i in range(40)]
        result = compute_feature_mining(trades)
        assoc = next(a for a in result.associations if a.feature == "flag")
        assert assoc.feature_type == "categorical"
        assert {b.bin_label for b in assoc.bins} == {"True", "False"}


class TestFeatureLevelInsufficientData:
    def test_feature_observed_below_min_trades_is_insufficient(self):
        # 40 total trades but only 5 carry this feature (e.g. from a run
        # where the source gate was on for only a few decisions).
        trades = [_tr(True, 1.0, {}) for _ in range(35)]
        trades += [_tr(True, 1.0, {"rare": 1.0}) for _ in range(5)]
        result = compute_feature_mining(trades)
        assoc = next(a for a in result.associations if a.feature == "rare")
        assert assoc.insufficient_data is True
        assert assoc.n_observed == 5


class TestBonferroniCorrection:
    def test_n_features_tested_excludes_insufficient_features(self):
        trades = [_tr(True, 1.0, {"common": float(i)}) for i in range(40)]
        # A second feature present on only 3 trades never clears the
        # whole-feature min_trades gate, so it must not count toward
        # n_features_tested / the Bonferroni denominator.
        for i in range(3):
            trades[i].features["rare"] = 1.0
        result = compute_feature_mining(trades)
        assert result.n_features_tested == 1
        from backtest.multiple_testing import bonferroni_alpha
        assert result.bonferroni_alpha == pytest.approx(bonferroni_alpha(1))


class TestPoolFeatureMiningResults:
    def test_pools_n_trades_and_reports_insufficient_when_all_empty(self):
        pooled = pool_feature_mining_results([{"insufficient_data": True}, {"insufficient_data": True}])
        assert pooled["insufficient_data"] is True
        assert pooled["associations"] == []

    def test_pools_two_symbols_combined_variance_not_averaged_p_values(self):
        # Two symbols' results for the SAME numeric feature/bin (bin_index=0):
        # symbol A: n=20, mean_r=1.0, std_r=0.5 (looks very significant alone)
        # symbol B: n=20, mean_r=-1.0, std_r=0.5 (looks very significant, opposite sign)
        # A naive average of p-values would stay "significant" (averaging two
        # small numbers). The correct pooled combination must recognize the
        # two samples DISAGREE (opposite means), inflating pooled variance
        # and yielding a much larger (less significant) p-value.
        def _blob(n, mean_r, std_r, p_value):
            return {
                "insufficient_data": False,
                "associations": [{
                    "feature": "score", "feature_type": "numeric", "n_observed": n,
                    "overall_win_rate": 0.5, "insufficient_data": False, "note": "",
                    "bins": [{
                        "feature": "score", "bin_label": "Q1 [0, 1]", "bin_index": 0,
                        "n_trades": n, "win_rate": 0.5, "mean_r": mean_r, "std_r": std_r,
                        "lift_win_rate": 1.0, "p_value": p_value, "significance": "SURVIVES_CORRECTION",
                    }],
                }],
                "n_trades_total": n, "n_features_tested": 1, "bonferroni_alpha": 0.05,
            }

        blob_a = _blob(20, 1.0, 0.5, 0.0001)
        blob_b = _blob(20, -1.0, 0.5, 0.0001)
        pooled = pool_feature_mining_results([blob_a, blob_b])

        assert pooled["insufficient_data"] is False
        assoc = next(a for a in pooled["associations"] if a["feature"] == "score")
        pooled_bin = assoc["bins"][0]
        assert pooled_bin["n_trades"] == 40
        assert pooled_bin["mean_r"] == pytest.approx(0.0)  # (20*1.0 + 20*-1.0) / 40
        # Naive p-value averaging would give ~0.0001 (still "significant").
        # The real combined-variance pooling must NOT do that — the two
        # disagreeing samples must produce a large (non-significant) p-value.
        assert pooled_bin["p_value"] is not None
        assert pooled_bin["p_value"] > 0.5
        assert pooled_bin["significance"] in ("NOT_SIGNIFICANT", "NOMINAL_ONLY")

    def test_pools_categorical_bins_by_literal_label(self):
        def _blob(win_rate, n):
            return {
                "insufficient_data": False,
                "associations": [{
                    "feature": "regime", "feature_type": "categorical", "n_observed": n,
                    "overall_win_rate": 0.5, "insufficient_data": False, "note": "",
                    "bins": [{
                        "feature": "regime", "bin_label": "TRENDING", "bin_index": None,
                        "n_trades": n, "win_rate": win_rate, "mean_r": 1.0, "std_r": 0.5,
                        "lift_win_rate": 1.0, "p_value": 0.5, "significance": "NOT_SIGNIFICANT",
                    }],
                }],
                "n_trades_total": n, "n_features_tested": 1, "bonferroni_alpha": 0.05,
            }

        pooled = pool_feature_mining_results([_blob(0.6, 10), _blob(0.4, 10)])
        assoc = next(a for a in pooled["associations"] if a["feature"] == "regime")
        assert len(assoc["bins"]) == 1
        assert assoc["bins"][0]["n_trades"] == 20
        assert assoc["bins"][0]["win_rate"] == pytest.approx(0.5)
