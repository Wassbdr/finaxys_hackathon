"""Precision-recall curves, average precision and alert volume."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve

from sentinel.eval.metrics import any_metrics, type_metrics


def _subset(labels: pd.Series, kind: str) -> tuple[np.ndarray, np.ndarray]:
    """(mask of windows in this evaluation, boolean positives among them)."""
    if kind == "ANY":
        keep = np.ones(len(labels), dtype=bool)
        return keep, (labels != "NORMAL").to_numpy()
    keep = labels.isin([kind, "NORMAL"]).to_numpy()
    return keep, (labels.to_numpy() == kind)[keep]


def pr_curve(labels: pd.Series, scores: np.ndarray, kind: str, max_points: int = 300) -> dict[str, list[float]]:
    """Precision-recall curve of a score for one type (or ANY), thinned to at most ``max_points`` points."""
    keep, y = _subset(labels, kind)
    precision, recall, _ = precision_recall_curve(y, np.asarray(scores)[keep])
    if len(precision) > max_points:
        take = np.unique(np.linspace(0, len(precision) - 1, max_points).astype(int))
        precision, recall = precision[take], recall[take]
    return {"precision": [float(p) for p in precision], "recall": [float(r) for r in recall]}


def average_precision(labels: pd.Series, scores: np.ndarray, kind: str) -> float:
    """Average precision (area under the precision-recall curve) for one type (or ANY)."""
    keep, y = _subset(labels, kind)
    return float(average_precision_score(y, np.asarray(scores)[keep]))


def operating_point(labels: pd.Series, flagged: np.ndarray, kind: str) -> tuple[float, float]:
    """(precision, recall) of a set of boolean flags for one type (or ANY)."""
    m = any_metrics(labels, flagged) if kind == "ANY" else type_metrics(labels, flagged, kind)
    return m["precision"], m["recall"]


def alert_volume(labels: pd.Series, flagged: np.ndarray, kind: str, n_instruments: int,
                 sim_hours: float) -> dict[str, float]:
    """Analyst workload implied by a detector's flags."""
    m = any_metrics(labels, flagged) if kind == "ANY" else type_metrics(labels, flagged, kind)
    return {"false_alerts": m["fp"], "true_alerts": m["tp"],
            "false_alerts_per_1000_innocent_windows": 1000.0 * m["fpr"],
            "false_alerts_per_instrument_hour": m["fp"] / (n_instruments * sim_hours)}
