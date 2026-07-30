"""
backtest/feature_mining.py
-----------------------------
Feature Mining / Hypothesis Discovery Phase 1 (2026-07-30) — retrospective,
computed-on-read, non-ML association analysis between TradeRecord.features
(backtest/metrics.py) and trade outcome (is_win / rr_actual).

Deliberately NOT a trained model: no logistic regression, no AUC, no
meta-model — the exact technique family CLAUDE.md's dead list already
killed (H033: "Meta-model self-confidence gating" — TEST AUC 0.5071,
floor 0.55, walk-forward sign-flipped 0.548->0.474 = noise not signal).
This module answers "which factor correlates with the outcome" one
feature at a time via quantile/category bins, reusing
backtest/multiple_testing.py's existing p-value + Bonferroni machinery
(no new statistics engine, no scipy/sklearn dependency). Every result is
retrospective, descriptive, non-causal, and explicitly not a verdict —
see FEATURE_MINING_CAVEAT, which every response carries.

Mirrors backtest/meta_analysis.py's exact conventions: pure functions, no
D1 access inside this module, frozen dataclasses with to_dict(), an
explicit minimum-sample gate before anything is reported.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backtest.metrics import TradeRecord
from backtest.multiple_testing import bonferroni_alpha, classify_significance, trial_p_value

MIN_TRADES_FOR_FEATURE_MINING = 30   # whole-sample gate, mirrors meta_analysis's insufficient_data pattern
MIN_BIN_SIZE = 10                     # per-bin/per-category minimum before it's reported
DEFAULT_N_QUANTILES = 4
_DEFAULT_FAMILY_ALPHA = 0.05

FEATURE_MINING_CAVEAT = (
    "Retrospective, descriptive, non-ML association analysis over already-"
    "closed trades — NOT a trained model, NOT causal, NOT a verdict. A lift "
    "ratio here means 'this bin/category won more often in this sample,' "
    "nothing more. See CLAUDE.md's dead list (H033) for why this "
    "deliberately stops short of a classifier."
)


@dataclass(frozen=True)
class FeatureBinResult:
    feature: str
    bin_label: str
    # Ordinal position for numeric quantile bins (Q1=0, Q2=1, ...) — the
    # pooling key pool_feature_mining_results() matches across symbols,
    # since each symbol's own quantile edges sit on a different numeric
    # scale. None for categorical bins, which pool by bin_label instead
    # (a category name like "TRENDING" IS comparable across symbols).
    bin_index: int | None
    n_trades: int
    win_rate: float
    mean_r: float
    # Sample std (ddof=1) of this bin's R-multiples; None if n_trades < 2.
    # Kept (not just p_value) so pool_feature_mining_results() can combine
    # bins across symbols via the standard combined-variance formula
    # instead of ever averaging pre-computed p-values (invalid).
    std_r: float | None
    lift_win_rate: float | None   # bin win_rate / this feature's overall win_rate; None if overall == 0
    p_value: float | None
    significance: str   # multiple_testing.classify_significance(...)

    def to_dict(self) -> dict:
        return {
            "feature": self.feature, "bin_label": self.bin_label, "bin_index": self.bin_index,
            "n_trades": self.n_trades, "win_rate": round(self.win_rate, 4),
            "mean_r": round(self.mean_r, 4),
            "std_r": round(self.std_r, 4) if self.std_r is not None else None,
            "lift_win_rate": round(self.lift_win_rate, 3) if self.lift_win_rate is not None else None,
            "p_value": self.p_value, "significance": self.significance,
        }


@dataclass(frozen=True)
class FeatureAssociation:
    feature: str
    feature_type: str          # "numeric" | "categorical"
    n_observed: int            # trades where this feature was present (non-None)
    overall_win_rate: float    # win rate across every trade where this feature was observed (the lift denominator)
    bins: list[FeatureBinResult] = field(default_factory=list)
    insufficient_data: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "feature": self.feature, "feature_type": self.feature_type,
            "n_observed": self.n_observed, "overall_win_rate": round(self.overall_win_rate, 4),
            "bins": [b.to_dict() for b in self.bins],
            "insufficient_data": self.insufficient_data, "note": self.note,
        }


@dataclass(frozen=True)
class FeatureMiningResult:
    n_trades_total: int
    n_features_tested: int
    bonferroni_alpha: float | None
    associations: list[FeatureAssociation] = field(default_factory=list)
    caveat: str = FEATURE_MINING_CAVEAT
    insufficient_data: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "n_trades_total": self.n_trades_total, "n_features_tested": self.n_features_tested,
            "bonferroni_alpha": self.bonferroni_alpha,
            "associations": [a.to_dict() for a in self.associations],
            "caveat": self.caveat, "insufficient_data": self.insufficient_data, "note": self.note,
        }


def _bin_result(
    feature: str, bin_label: str, bin_index: int | None, bucket: list[TradeRecord],
    overall_win_rate: float, n_features_tested: int, family_alpha: float,
) -> FeatureBinResult:
    n = len(bucket)
    win_rate = sum(1 for t in bucket if t.is_win) / n
    rr_values = [t.rr_actual for t in bucket]
    mean_r = float(np.mean(rr_values))
    std_r = float(np.std(rr_values, ddof=1)) if n >= 2 else None
    lift = (win_rate / overall_win_rate) if overall_win_rate > 0 else None
    p_value = trial_p_value(mean_r, std_r or 0.0, n)
    # n_features_tested >= 1 is guaranteed here: a feature only reaches
    # _bin_result via a caller that already passed the whole-feature
    # min_trades gate, which is exactly what counts toward n_features_tested.
    significance = classify_significance(p_value, n_features_tested, family_alpha)
    return FeatureBinResult(
        feature=feature, bin_label=bin_label, bin_index=bin_index, n_trades=n,
        win_rate=win_rate, mean_r=mean_r, std_r=std_r, lift_win_rate=lift,
        p_value=p_value, significance=significance,
    )


def _numeric_feature_association(
    feature: str, pairs: list[tuple[Any, TradeRecord]], n_quantiles: int, min_bin_size: int,
    n_features_tested: int, family_alpha: float,
) -> FeatureAssociation:
    overall_win_rate = sum(1 for _, t in pairs if t.is_win) / len(pairs)
    values = np.array([float(v) for v, _ in pairs], dtype=float)

    raw_edges = np.quantile(values, [i / n_quantiles for i in range(n_quantiles + 1)])
    edges = sorted({round(float(e), 10) for e in raw_edges})
    if len(edges) < 2:
        return FeatureAssociation(
            feature=feature, feature_type="numeric", n_observed=len(pairs),
            overall_win_rate=overall_win_rate, bins=[], insufficient_data=True,
            note="No variance in this feature's values for this sample.",
        )

    interior = edges[1:-1]
    n_bins = len(edges) - 1
    buckets: list[list[TradeRecord]] = [[] for _ in range(n_bins)]
    for value, t in pairs:
        idx = min(bisect.bisect_right(interior, float(value)), n_bins - 1)
        buckets[idx].append(t)

    bins: list[FeatureBinResult] = []
    for i, bucket in enumerate(buckets):
        if len(bucket) < min_bin_size:
            continue
        bin_label = f"Q{i + 1} [{edges[i]:.4g}, {edges[i + 1]:.4g}]"
        bins.append(_bin_result(feature, bin_label, i, bucket, overall_win_rate, n_features_tested, family_alpha))

    return FeatureAssociation(
        feature=feature, feature_type="numeric", n_observed=len(pairs),
        overall_win_rate=overall_win_rate, bins=bins, insufficient_data=False,
    )


def _categorical_feature_association(
    feature: str, pairs: list[tuple[Any, TradeRecord]], min_bin_size: int,
    n_features_tested: int, family_alpha: float,
) -> FeatureAssociation:
    overall_win_rate = sum(1 for _, t in pairs if t.is_win) / len(pairs)
    groups: dict[str, list[TradeRecord]] = {}
    for value, t in pairs:
        groups.setdefault(str(value), []).append(t)

    bins: list[FeatureBinResult] = []
    for key in sorted(groups):
        bucket = groups[key]
        if len(bucket) < min_bin_size:
            continue
        bins.append(_bin_result(feature, key, None, bucket, overall_win_rate, n_features_tested, family_alpha))

    return FeatureAssociation(
        feature=feature, feature_type="categorical", n_observed=len(pairs),
        overall_win_rate=overall_win_rate, bins=bins, insufficient_data=False,
    )


def compute_feature_mining(
    trades: list[TradeRecord],
    min_trades: int = MIN_TRADES_FOR_FEATURE_MINING,
    min_bin_size: int = MIN_BIN_SIZE,
    n_quantiles: int = DEFAULT_N_QUANTILES,
    family_alpha: float = _DEFAULT_FAMILY_ALPHA,
) -> FeatureMiningResult:
    """`trades`: real TradeRecord objects (e.g. from
    backtest.optimizer.evaluate_point(..., return_trades=True)). Discovers
    every feature name present on >=1 trade's .features dict (the union —
    different gate configs populate different subsets), then dispatches
    numeric (quantile-binned) vs categorical (one bin per value) per
    feature. Bonferroni correction is applied across n_features_tested
    (the number of features that actually cleared the min_trades gate),
    matching mission_significance_summary()'s own family-wise framing."""
    n_total = len(trades)
    if n_total < min_trades:
        return FeatureMiningResult(
            n_trades_total=n_total, n_features_tested=0, bonferroni_alpha=None,
            associations=[], insufficient_data=True,
            note=(
                f"Only {n_total} trade(s) — need at least {min_trades} before "
                "any feature association means anything more than noise."
            ),
        )

    feature_names = sorted({k for t in trades for k in t.features})

    observed: dict[str, list[tuple[Any, TradeRecord]]] = {}
    feature_types: dict[str, str] = {}
    for name in feature_names:
        pairs = [(t.features[name], t) for t in trades if t.features.get(name) is not None]
        observed[name] = pairs
        if pairs and isinstance(pairs[0][0], bool):
            feature_types[name] = "categorical"
        elif pairs and isinstance(pairs[0][0], (int, float)):
            feature_types[name] = "numeric"
        else:
            feature_types[name] = "categorical"

    n_features_tested = sum(1 for name in feature_names if len(observed[name]) >= min_trades)

    associations: list[FeatureAssociation] = []
    for name in feature_names:
        pairs = observed[name]
        if len(pairs) < min_trades:
            associations.append(FeatureAssociation(
                feature=name, feature_type=feature_types[name], n_observed=len(pairs),
                overall_win_rate=0.0, bins=[], insufficient_data=True,
                note=f"Only {len(pairs)} trade(s) observed this feature — need at least {min_trades}.",
            ))
            continue
        if feature_types[name] == "numeric":
            assoc = _numeric_feature_association(name, pairs, n_quantiles, min_bin_size, n_features_tested, family_alpha)
        else:
            assoc = _categorical_feature_association(name, pairs, min_bin_size, n_features_tested, family_alpha)
        associations.append(assoc)

    return FeatureMiningResult(
        n_trades_total=n_total, n_features_tested=n_features_tested,
        bonferroni_alpha=bonferroni_alpha(n_features_tested, family_alpha) if n_features_tested > 0 else None,
        associations=associations, insufficient_data=False,
    )


def _pool_bins(bin_list: list[dict], pooled_overall_win_rate: float, n_features_tested: int, family_alpha: float) -> dict | None:
    total_n = sum(b["n_trades"] for b in bin_list)
    if total_n == 0:
        return None
    pooled_mean = sum(b["n_trades"] * b["mean_r"] for b in bin_list) / total_n

    # Combined-variance formula for merging independent samples (within-
    # group variance + between-group mean spread) — mathematically
    # equivalent to recomputing over the concatenated raw R-multiples,
    # without needing to persist/transmit raw trade-level data at this
    # layer. NEVER average pre-computed p-values (statistically invalid).
    numer = 0.0
    for b in bin_list:
        n_i = b["n_trades"]
        var_i = (b["std_r"] ** 2) if b.get("std_r") is not None else 0.0
        numer += (n_i - 1) * var_i + n_i * (b["mean_r"] - pooled_mean) ** 2
    pooled_var = numer / (total_n - 1) if total_n > 1 else 0.0
    pooled_std = math.sqrt(pooled_var) if pooled_var > 0 else None

    total_wins = sum(b["win_rate"] * b["n_trades"] for b in bin_list)
    pooled_win_rate = total_wins / total_n
    lift = (pooled_win_rate / pooled_overall_win_rate) if pooled_overall_win_rate > 0 else None
    p_value = trial_p_value(pooled_mean, pooled_std or 0.0, total_n)
    significance = classify_significance(p_value, n_features_tested, family_alpha)

    sample = bin_list[0]
    # A numeric bin's literal numeric range differs per symbol (e.g.
    # EURUSD's ATR scale vs XAUUSD's) — label it ordinally rather than
    # reusing one symbol's range, which would misleadingly imply a single
    # shared threshold across symbols.
    bin_label = (
        f"Q{sample['bin_index'] + 1} (each symbol's own quartile)"
        if sample.get("bin_index") is not None else sample["bin_label"]
    )
    return {
        "feature": sample["feature"], "bin_label": bin_label, "bin_index": sample.get("bin_index"),
        "n_trades": total_n, "win_rate": round(pooled_win_rate, 4), "mean_r": round(pooled_mean, 4),
        "std_r": round(pooled_std, 4) if pooled_std is not None else None,
        "lift_win_rate": round(lift, 3) if lift is not None else None,
        "p_value": p_value, "significance": significance,
    }


def pool_feature_mining_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregates multiple per-symbol FeatureMiningResult.to_dict() blobs
    (e.g. every symbol result of one mission_validator.py validation run)
    into one pooled view. See _pool_bins for the pooling method and why
    p-values are never averaged. Numeric bins pool by ordinal bin_index
    (Q1..Qn); categorical bins pool by their literal value (comparable
    across symbols, e.g. "TRENDING")."""
    non_empty = [r for r in results if not r.get("insufficient_data")]
    if not non_empty:
        return {
            "n_trades_total": 0, "n_features_tested": 0, "bonferroni_alpha": None,
            "associations": [], "caveat": FEATURE_MINING_CAVEAT,
            "insufficient_data": True, "note": "No symbol result has enough data for feature mining yet.",
        }

    n_trades_total = sum(r["n_trades_total"] for r in non_empty)

    assocs_by_feature: dict[str, list[dict]] = {}
    for r in non_empty:
        for assoc in r.get("associations", []):
            if assoc.get("insufficient_data"):
                continue
            assocs_by_feature.setdefault(assoc["feature"], []).append(assoc)

    n_features_tested = len(assocs_by_feature)
    alpha = _DEFAULT_FAMILY_ALPHA
    bonf_alpha = bonferroni_alpha(n_features_tested, alpha) if n_features_tested > 0 else None

    associations = []
    for feature, assoc_list in assocs_by_feature.items():
        feature_type = assoc_list[0]["feature_type"]
        total_observed = sum(a["n_observed"] for a in assoc_list)
        total_wins = sum(a["overall_win_rate"] * a["n_observed"] for a in assoc_list)
        pooled_overall_win_rate = (total_wins / total_observed) if total_observed else 0.0

        keyed: dict[Any, list[dict]] = {}
        for a in assoc_list:
            for b in a.get("bins", []):
                key = b["bin_index"] if feature_type == "numeric" else b["bin_label"]
                keyed.setdefault(key, []).append(b)

        pooled_bins = [
            pb for pb in (
                _pool_bins(bin_list, pooled_overall_win_rate, n_features_tested, alpha)
                for bin_list in keyed.values()
            ) if pb is not None
        ]
        if feature_type == "numeric":
            pooled_bins.sort(key=lambda b: b["bin_index"] if b["bin_index"] is not None else 0)
        else:
            pooled_bins.sort(key=lambda b: b["bin_label"])

        associations.append({
            "feature": feature, "feature_type": feature_type,
            "n_observed": total_observed, "overall_win_rate": round(pooled_overall_win_rate, 4),
            "bins": pooled_bins, "insufficient_data": False, "note": "",
        })

    return {
        "n_trades_total": n_trades_total, "n_features_tested": n_features_tested,
        "bonferroni_alpha": bonf_alpha, "associations": associations,
        "caveat": FEATURE_MINING_CAVEAT, "insufficient_data": False, "note": "",
    }
