"""H033 unit tests — the feature encoding and the pre-registered verdict
logic are pure functions; these pin them to the registry text."""
import numpy as np
import pytest

from research.experiments.H033_meta_confidence_gate import (
    FEATURE_COLUMNS,
    engine_agreement,
    feature_vector,
    meta_verdict,
    session_from_hour,
)


# ------------------------------------------------------ engine agreement

def test_engine_agreement_maps_agree_oppose_abstain():
    outputs = [
        {"engine": "smc", "bias": "BULLISH"},
        {"engine": "price_action", "bias": "BEARISH"},
        {"engine": "nnfx", "bias": "NEUTRAL"},
        # wyckoff missing entirely
    ]
    agr = engine_agreement(outputs, "BULLISH")
    assert agr == {"smc": 1, "price_action": -1, "nnfx": 0, "wyckoff": 0}


def test_engine_agreement_bearish_direction():
    outputs = [{"engine": "nnfx", "bias": "BEARISH"}]
    assert engine_agreement(outputs, "BEARISH")["nnfx"] == 1


# ------------------------------------------------------------- sessions

def test_session_boundaries():
    assert session_from_hour(7) == "london"
    assert session_from_hour(11) == "london"
    assert session_from_hour(12) == "ny"
    assert session_from_hour(16) == "ny"
    assert session_from_hour(17) == "other"
    assert session_from_hour(3) == "other"


# -------------------------------------------------------- feature vector

def _trade(**over):
    t = {
        "score": 62.0,
        "eng": {"smc": 1, "price_action": 1, "nnfx": 0, "wyckoff": -1},
        "regime": "TRENDING",
        "volatility": "high",
        "session": "london",
        "asset_class": "crypto",
        "atr_pctl": 0.8,
        "d1_confirming": True,
        "rr": 2.5,
    }
    t.update(over)
    return t


def test_feature_vector_matches_column_order():
    v = feature_vector(_trade())
    assert len(v) == len(FEATURE_COLUMNS)
    fx = dict(zip(FEATURE_COLUMNS, v))
    assert fx["score"] == 62.0
    assert fx["eng_wyckoff"] == -1.0
    assert fx["regime_trending"] == 1.0
    assert fx["vol_high"] == 1.0 and fx["vol_low"] == 0.0 and fx["vol_extreme"] == 0.0
    assert fx["sess_london"] == 1.0 and fx["sess_ny"] == 0.0
    assert fx["ac_crypto"] == 1.0 and fx["ac_metal"] == 0.0
    assert fx["atr_pctl"] == 0.8
    assert fx["d1_confirming"] == 1.0
    assert fx["rr"] == 2.5


def test_feature_vector_base_categories_encode_as_zeros():
    v = feature_vector(_trade(volatility="normal", session="other",
                              asset_class="forex", regime="RANGING",
                              d1_confirming=False))
    fx = dict(zip(FEATURE_COLUMNS, v))
    for col in ["vol_low", "vol_high", "vol_extreme", "sess_london", "sess_ny",
                "ac_metal", "ac_crypto", "ac_index", "ac_energy",
                "regime_trending", "d1_confirming"]:
        assert fx[col] == 0.0


def test_feature_vector_missing_atr_pctl_defaults_to_half():
    fx = dict(zip(FEATURE_COLUMNS, feature_vector(_trade(atr_pctl=None))))
    assert fx["atr_pctl"] == 0.5


# --------------------------------------------------------- verdict logic

OK = dict(auc=0.60, pf_a=1.10, pf_b=1.30, retention=0.70,
          symbol_win_frac=0.70, car_pf_a=1.30, car_pf_b=1.32,
          pooled_a_n=400, train_n=1500)


def _verdict(**over):
    d = {**OK, **over}
    return meta_verdict(d["auc"], d["pf_a"], d["pf_b"], d["retention"],
                        d["symbol_win_frac"], d["car_pf_a"], d["car_pf_b"],
                        d["pooled_a_n"], d["train_n"])


def test_adopt_when_everything_holds():
    v, checks, reasons = _verdict()
    assert v.startswith("ADOPT") and reasons == [] and all(checks.values())


def test_insufficient_data_short_circuits():
    v, _, _ = _verdict(train_n=999)
    assert v == "INSUFFICIENT_DATA"
    v, _, _ = _verdict(pooled_a_n=299)
    assert v == "INSUFFICIENT_DATA"


def test_auc_sanity_gate_kills_regardless_of_pf():
    v, checks, reasons = _verdict(auc=0.54, pf_b=9.9)
    assert v == "FAILED"
    assert checks == {"sanity_auc>=0.55": False}
    assert "luck" in reasons[0]


def test_null_when_dpf_immaterial_but_auc_ok():
    v, checks, _ = _verdict(pf_b=1.15)  # dPF = 0.05 < 0.15
    assert v.startswith("NULL")
    assert checks["sanity_auc>=0.55"] is True


def test_failed_when_gate_collapses_volume():
    # dPF large AND retention broken -> not ADOPT, not NULL
    v, _, reasons = _verdict(pf_b=1.50, retention=0.30)
    assert v == "FAILED / NO CHANGE"
    assert any("retention" in r for r in reasons)


def test_carrier_degradation_blocks_adopt():
    v, checks, reasons = _verdict(car_pf_b=1.20)  # -0.10 > 0.05 allowed
    assert not v.startswith("ADOPT")
    assert checks["4_carriers_not_degraded"] is False


# --------------------------------------------- frozen model spec smoke test

def test_frozen_spec_learns_a_separable_ranking():
    sklearn = pytest.importorskip("sklearn")
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(9)
    n = 800
    X = np.array([feature_vector(_trade(score=float(s), rr=float(r)))
                  for s, r in zip(rng.uniform(40, 90, n), rng.uniform(1.5, 4, n))])
    # outcome driven by score -> the exact registered spec must recover it
    logits = (X[:, FEATURE_COLUMNS.index("score")] - 65) / 8
    y = (rng.random(n) < 1 / (1 + np.exp(-logits))).astype(int)
    m = LogisticRegression(C=1.0, max_iter=1000).fit(X[:600], y[:600])
    auc = roc_auc_score(y[600:], m.predict_proba(X[600:])[:, 1])
    assert auc > 0.60
