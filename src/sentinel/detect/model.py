"""LightGBM multiclass classifier, per-type alert thresholds, and an Isolation Forest."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from sentinel.detect.dataset import CLASS_INDEX, CLASSES, to_xy
from sentinel.features.extractor import FEATURE_NAMES

_THRESHOLD_GRID = np.round(np.arange(0.05, 0.96, 0.01), 2)


def class_weights(y: np.ndarray, cap: float = 20.0) -> np.ndarray:
    """Inverse-frequency weight per class, N / (K * n_class), capped so tiny classes cannot dominate."""
    counts = np.bincount(y, minlength=len(CLASSES)).astype(float)
    weights = np.full(len(CLASSES), cap)
    present = counts > 0
    weights[present] = np.minimum(cap, len(y) / (len(CLASSES) * counts[present]))
    return weights


def train_lgbm(train: pd.DataFrame, val: pd.DataFrame, seed: int = 0, max_rounds: int = 500) -> lgb.Booster:
    """Train a small multiclass gradient-boosted model with early stopping on validation log-loss.

    The returned booster contains only the trees up to the best iteration, so
    prediction, SHAP and any exported copy all see exactly the same model.
    """
    X_train, y_train = to_xy(train)
    X_val, y_val = to_xy(val)
    weights = class_weights(y_train)
    names = list(FEATURE_NAMES)
    dtrain = lgb.Dataset(X_train, y_train, weight=weights[y_train], feature_name=names)
    dval = lgb.Dataset(X_val, y_val, weight=weights[y_val], feature_name=names, reference=dtrain)
    params = {
        "objective": "multiclass", "num_class": len(CLASSES), "metric": "multi_logloss",
        "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 10, "feature_fraction": 0.8,
        "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0, "seed": seed,
        "deterministic": True, "force_row_wise": True, "num_threads": 4, "verbosity": -1,
    }
    booster = lgb.train(params, dtrain, num_boost_round=max_rounds, valid_sets=[dval],
                        callbacks=[lgb.early_stopping(50, verbose=False)])
    return lgb.Booster(model_str=booster.model_to_string(num_iteration=booster.best_iteration))


def predict_proba(booster: lgb.Booster, X: np.ndarray) -> np.ndarray:
    """Class probabilities, shape (n, 4), columns in CLASSES order."""
    return booster.predict(X)


def choose_thresholds(proba: np.ndarray, labels: pd.Series) -> dict[str, float]:
    """Per-type alert threshold maximizing that type's F1 (type vs. NORMAL) on validation data.

    Ties go to higher precision, then to the higher threshold (fewer alerts).
    """
    thresholds: dict[str, float] = {}
    for kind in CLASSES[1:]:
        keep = labels.isin([kind, "NORMAL"]).to_numpy()
        y = (labels.to_numpy() == kind)[keep]
        p = proba[keep, CLASS_INDEX[kind]]
        best_key, best_t = (-1.0, -1.0, -1.0), float(_THRESHOLD_GRID[0])
        for t in _THRESHOLD_GRID:
            flagged = p >= t
            tp = int((flagged & y).sum())
            fp = int((flagged & ~y).sum())
            fn = int(y.sum()) - tp
            f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
            precision = tp / (tp + fp) if tp + fp else 0.0
            key = (f1, precision, float(t))
            if key > best_key:
                best_key, best_t = key, float(t)
        thresholds[kind] = best_t
    return thresholds


def predict_types(proba: np.ndarray, thresholds: dict[str, float]) -> np.ndarray:
    """Alert type per row (class index): among types whose probability meets its threshold, the most probable; else NORMAL (0)."""
    out = np.zeros(len(proba), dtype=int)
    best = np.full(len(proba), -1.0)
    for kind in CLASSES[1:]:
        k = CLASS_INDEX[kind]
        fires = (proba[:, k] >= thresholds[kind]) & (proba[:, k] > best)
        out[fires] = k
        best[fires] = proba[fires, k]
    return out


def save_model(booster: lgb.Booster, thresholds: dict[str, float], out_dir: Path) -> None:
    """Write lgbm.txt (LightGBM text model) and thresholds.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "lgbm.txt"))
    (out_dir / "thresholds.json").write_text(json.dumps(thresholds, indent=2), encoding="utf-8")


def load_model(model_dir: Path) -> tuple[lgb.Booster, dict[str, float]]:
    """Inverse of save_model."""
    model_dir = Path(model_dir)
    booster = lgb.Booster(model_file=str(model_dir / "lgbm.txt"))
    return booster, json.loads((model_dir / "thresholds.json").read_text(encoding="utf-8"))


class IsolationForestDetector:
    """Unsupervised anomaly detector: manipulation windows should be easier to isolate than normal ones."""

    def __init__(self, seed: int = 0, n_estimators: int = 200) -> None:
        self._forest = IsolationForest(n_estimators=n_estimators, random_state=seed, n_jobs=1)

    def fit(self, X: np.ndarray) -> "IsolationForestDetector":
        """Fit without labels."""
        self._forest.fit(X)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        """Anomaly score; higher means more anomalous."""
        return -self._forest.score_samples(X)

    def choose_threshold(self, scores: np.ndarray, labels: pd.Series) -> float:
        """Threshold maximizing F1 for 'any manipulation vs NORMAL' on validation data."""
        y = (labels != "NORMAL").to_numpy()
        best_key, best_t = (-1.0, -1.0), float(scores.min())
        for t in np.unique(np.quantile(scores, np.linspace(0.5, 0.999, 200))):
            flagged = scores >= t
            tp = int((flagged & y).sum())
            fp = int((flagged & ~y).sum())
            f1 = 2 * tp / (2 * tp + fp + (int(y.sum()) - tp)) if tp else 0.0
            key = (f1, float(t))
            if key > best_key:
                best_key, best_t = key, float(t)
        return best_t

    def predict(self, X: np.ndarray, threshold: float) -> np.ndarray:
        """Boolean flags at ``threshold``."""
        return self.score(X) >= threshold

    def save(self, path: Path) -> None:
        """Persist the fitted forest."""
        joblib.dump(self._forest, path)

    @classmethod
    def load(cls, path: Path) -> "IsolationForestDetector":
        """Inverse of ``save``."""
        detector = cls()
        detector._forest = joblib.load(path)
        return detector
