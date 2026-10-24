"""Precision, recall, F1 and false-positive rate, per manipulation type."""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


def binary_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Confusion counts and precision / recall / F1 / FPR (0.0 where undefined)."""
    y_true, y_pred = np.asarray(y_true, dtype=bool), np.asarray(y_pred, dtype=bool)
    tp = int((y_true & y_pred).sum())
    fp = int((~y_true & y_pred).sum())
    fn = int((y_true & ~y_pred).sum())
    tn = int((~y_true & ~y_pred).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "fpr": fpr, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def type_metrics(labels: pd.Series, flagged: np.ndarray, kind: str) -> dict[str, float]:
    """One type versus all NORMAL windows; windows of other types are excluded."""
    keep = labels.isin([kind, "NORMAL"]).to_numpy()
    return binary_metrics((labels.to_numpy() == kind)[keep], np.asarray(flagged)[keep])


def any_metrics(labels: pd.Series, flagged: np.ndarray) -> dict[str, float]:
    """Any manipulation type versus NORMAL."""
    return binary_metrics((labels != "NORMAL").to_numpy(), np.asarray(flagged))


def results_table(labels: pd.Series, preds: Mapping[str, Mapping[str, np.ndarray]]) -> pd.DataFrame:
    """One row per (detector, type). ``preds[detector][type]`` is a boolean flag array; type 'ANY' is allowed."""
    rows = []
    for detector, by_type in preds.items():
        for kind, flagged in by_type.items():
            m = any_metrics(labels, flagged) if kind == "ANY" else type_metrics(labels, flagged, kind)
            rows.append({"detector": detector, "type": kind, **m})
    return pd.DataFrame(rows)


def recall_by_difficulty(df: pd.DataFrame, preds: Mapping[str, Mapping[str, np.ndarray]]) -> pd.DataFrame:
    """Recall of each detector on each type's windows, split by difficulty."""
    rows = []
    for detector, by_type in preds.items():
        for kind, flagged in by_type.items():
            if kind == "ANY":
                continue
            flagged = np.asarray(flagged)
            for level in ("easy", "medium", "hard"):
                mask = ((df["label"] == kind) & (df["difficulty"] == level)).to_numpy()
                if mask.any():
                    rows.append({"detector": detector, "type": kind, "difficulty": level,
                                 "n": int(mask.sum()), "recall": float(flagged[mask].mean())})
    return pd.DataFrame(rows)
