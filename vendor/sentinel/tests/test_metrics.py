"""Tests for the evaluation metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.metrics import any_metrics, binary_metrics, recall_by_difficulty, results_table, type_metrics


def test_binary_metrics_confusion_counts() -> None:
    y = np.array([1, 1, 1, 0, 0, 0, 0, 0], dtype=bool)
    p = np.array([1, 1, 0, 1, 0, 0, 0, 0], dtype=bool)
    m = binary_metrics(y, p)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 4)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3) and m["fpr"] == pytest.approx(1 / 5)


def test_binary_metrics_handles_empty_denominators() -> None:
    m = binary_metrics(np.zeros(3, dtype=bool), np.zeros(3, dtype=bool))
    assert m["precision"] == 0.0 and m["recall"] == 0.0 and m["f1"] == 0.0 and m["fpr"] == 0.0


def test_type_metrics_excludes_other_types() -> None:
    labels = pd.Series(["NORMAL", "NORMAL", "SPOOFING", "WASH", "SPOOFING"])
    flagged = np.array([False, True, True, True, False])
    m = type_metrics(labels, flagged, "SPOOFING")
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)  # the WASH row is ignored


def test_any_metrics_treats_every_type_as_positive() -> None:
    labels = pd.Series(["NORMAL", "SPOOFING", "WASH", "NORMAL"])
    m = any_metrics(labels, np.array([False, True, False, True]))
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)


def test_results_table_and_recall_by_difficulty() -> None:
    df = pd.DataFrame({"label": ["NORMAL", "SPOOFING", "SPOOFING", "SPOOFING"],
                       "difficulty": ["none", "easy", "easy", "hard"]})
    preds = {"det": {"SPOOFING": np.array([False, True, True, False])}}
    table = results_table(df["label"], preds)
    assert list(table.columns[:3]) == ["detector", "type", "precision"] and table.iloc[0]["recall"] == pytest.approx(2 / 3)
    rec = recall_by_difficulty(df, preds)
    assert rec.set_index(["detector", "type", "difficulty"]).loc[("det", "SPOOFING", "easy"), "recall"] == 1.0
    assert rec.set_index(["detector", "type", "difficulty"]).loc[("det", "SPOOFING", "hard"), "recall"] == 0.0
