"""Tests for the LightGBM classifier, alert thresholds and the Isolation Forest."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.detect.dataset import to_xy
from sentinel.detect.model import (IsolationForestDetector, choose_thresholds, class_weights, load_model,
                                   predict_proba, predict_types, save_model, train_lgbm)


def _split(df):
    return df.iloc[:1600], df.iloc[1600:]


def test_class_weights_upweight_rare_classes_and_respect_the_cap() -> None:
    y = np.array([0] * 90 + [1] * 10)
    w = class_weights(y)
    assert w[1] > w[0] and w[1] == pytest.approx(100 / (4 * 10))
    assert class_weights(np.array([0] * 999 + [1]))[1] == 20.0  # capped
    assert class_weights(y)[3] == 20.0  # absent class gets the cap


def test_lgbm_learns_separable_classes_and_returns_valid_probabilities(synthetic_windows) -> None:
    train, val = _split(synthetic_windows)
    booster = train_lgbm(train, val, seed=1)
    X, y = to_xy(val)
    proba = predict_proba(booster, X)
    assert proba.shape == (len(val), 4) and np.allclose(proba.sum(axis=1), 1.0)
    assert (proba.argmax(axis=1) == y).mean() > 0.9


def test_booster_is_truncated_to_the_best_iteration(synthetic_windows) -> None:
    booster = train_lgbm(*_split(synthetic_windows), seed=1)
    assert booster.num_trees() % 4 == 0 and booster.num_trees() <= 4 * 500
    assert booster.best_iteration in (0, -1) or booster.best_iteration == booster.num_trees() // 4


def test_training_is_deterministic(synthetic_windows) -> None:
    train, val = _split(synthetic_windows)
    X = to_xy(val)[0]
    a = predict_proba(train_lgbm(train, val, seed=5), X)
    b = predict_proba(train_lgbm(train, val, seed=5), X)
    assert np.allclose(a, b)


def test_thresholds_maximize_per_type_f1_and_predict_types_takes_the_highest_probability() -> None:
    labels = pd.Series(["NORMAL"] * 6 + ["SPOOFING"] * 2 + ["LAYERING"] * 2 + ["WASH"] * 2)
    proba = np.full((12, 4), 0.02)
    proba[:6, 0] = 0.9
    proba[6:8, 1] = 0.9
    proba[8:10, 2] = 0.8
    proba[10:12, 3] = 0.7
    thresholds = choose_thresholds(proba, labels)
    assert set(thresholds) == {"SPOOFING", "LAYERING", "WASH"}
    assert (predict_types(proba, thresholds) == np.array([0] * 6 + [1] * 2 + [2] * 2 + [3] * 2)).all()
    both = np.array([[0.1, 0.6, 0.7, 0.0]])
    assert predict_types(both, {"SPOOFING": 0.5, "LAYERING": 0.5, "WASH": 0.5})[0] == 2
    none = np.array([[0.9, 0.05, 0.05, 0.0]])
    assert predict_types(none, {"SPOOFING": 0.5, "LAYERING": 0.5, "WASH": 0.5})[0] == 0


def test_save_and_load_round_trip(synthetic_windows, tmp_path) -> None:
    train, val = _split(synthetic_windows)
    booster = train_lgbm(train, val, seed=2)
    thresholds = {"SPOOFING": 0.4, "LAYERING": 0.5, "WASH": 0.6}
    save_model(booster, thresholds, tmp_path)
    loaded, loaded_thresholds = load_model(tmp_path)
    X = to_xy(val)[0]
    assert np.allclose(predict_proba(booster, X), predict_proba(loaded, X)) and loaded_thresholds == thresholds


def test_isolation_forest_scores_outliers_higher_and_picks_an_f1_threshold(synthetic_windows) -> None:
    train, val = _split(synthetic_windows)
    detector = IsolationForestDetector(seed=1).fit(to_xy(train)[0])
    Xv = to_xy(val)[0]
    scores = detector.score(Xv)
    is_manip = (val["label"] != "NORMAL").to_numpy()
    assert scores[is_manip].mean() > scores[~is_manip].mean()
    thr = detector.choose_threshold(scores, val["label"])
    flagged = detector.predict(Xv, thr)
    assert flagged.shape == (len(val),) and flagged[is_manip].mean() > flagged[~is_manip].mean()


def test_isolation_forest_save_and_load_round_trip(synthetic_windows, tmp_path) -> None:
    train = synthetic_windows.iloc[:1600]
    detector = IsolationForestDetector(seed=1).fit(to_xy(train)[0])
    detector.save(tmp_path / "iforest.joblib")
    loaded = IsolationForestDetector.load(tmp_path / "iforest.joblib")
    X = to_xy(synthetic_windows.iloc[1600:])[0]
    assert np.allclose(detector.score(X), loaded.score(X))
