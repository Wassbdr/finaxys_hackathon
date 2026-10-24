"""Tests for SHAP explanations of model alerts."""
from __future__ import annotations

import numpy as np
import pytest

from sentinel.detect.dataset import CLASS_INDEX, to_xy
from sentinel.detect.explain import PHRASES, ModelExplainer
from sentinel.detect.model import train_lgbm
from sentinel.features.extractor import FEATURE_NAMES


@pytest.fixture
def trained(synthetic_windows):
    train, val = synthetic_windows.iloc[:1600], synthetic_windows.iloc[1600:]
    return train_lgbm(train, val, seed=1), val


def test_every_feature_has_a_phrase_that_formats() -> None:
    assert set(PHRASES) == set(FEATURE_NAMES)
    for name, template in PHRASES.items():
        assert isinstance(template.format(v=0.5), str), name


def test_shap_values_add_up_to_the_raw_model_score(trained) -> None:
    booster, val = trained
    X = to_xy(val)[0][:25]
    explainer = ModelExplainer(booster, reference={n: 0.0 for n in FEATURE_NAMES})
    raw = booster.predict(X, raw_score=True)
    for kind in ("SPOOFING", "LAYERING", "WASH"):
        k = CLASS_INDEX[kind]
        contrib, base = explainer.contributions(X, k)
        assert np.allclose(contrib.sum(axis=1) + base, raw[:, k], atol=1e-4), kind


def test_shap_agrees_with_lightgbms_own_tree_shap(trained) -> None:
    booster, val = trained
    X = to_xy(val)[0][:25]
    explainer = ModelExplainer(booster, reference={n: 0.0 for n in FEATURE_NAMES})
    native = booster.predict(X, pred_contrib=True)
    width = len(FEATURE_NAMES) + 1
    for kind in ("SPOOFING", "WASH"):
        k = CLASS_INDEX[kind]
        contrib, base = explainer.contributions(X, k)
        assert np.allclose(contrib, native[:, k * width:k * width + len(FEATURE_NAMES)], atol=1e-4)
        assert np.allclose(base, native[:, k * width + len(FEATURE_NAMES)], atol=1e-4)


def test_explain_returns_top_positive_contributors_with_values_and_phrases(trained) -> None:
    booster, val = trained
    X, y = to_xy(val)
    idx = int(np.nonzero(y == CLASS_INDEX["LAYERING"])[0][0])
    reference = {n: float(np.median(val[n])) for n in FEATURE_NAMES}
    evidence = ModelExplainer(booster, reference).explain(X[idx], CLASS_INDEX["LAYERING"], top_k=5)
    assert 1 <= len(evidence) <= 5
    assert all(e.contribution > 0 for e in evidence)
    assert [e.contribution for e in evidence] == sorted((e.contribution for e in evidence), reverse=True)
    assert evidence[0].feature in ("max_live_levels_same_side", "max_cancel_burst_100ms")
    assert evidence[0].value == pytest.approx(X[idx][list(FEATURE_NAMES).index(evidence[0].feature)])
    assert evidence[0].reference == pytest.approx(reference[evidence[0].feature])
