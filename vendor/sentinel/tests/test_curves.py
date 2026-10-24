"""Tests for PR curves, operating points and alert volume."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.curves import alert_volume, average_precision, operating_point, pr_curve


def _labels() -> pd.Series:
    return pd.Series(["NORMAL"] * 6 + ["WASH"] * 2 + ["SPOOFING"] * 2)


def test_perfect_scores_give_a_perfect_curve_and_ap() -> None:
    scores = np.array([0.1] * 6 + [0.9] * 2 + [0.5] * 2)
    curve = pr_curve(_labels(), scores, "WASH")
    assert 1.0 in curve["precision"] and curve["recall"][0] == 1.0 or max(curve["recall"]) == 1.0
    assert average_precision(_labels(), scores, "WASH") == pytest.approx(1.0)


def test_other_types_are_excluded_from_the_curve() -> None:
    scores = np.array([0.1] * 6 + [0.9] * 2 + [0.99] * 2)  # the SPOOFING rows score highest but must be ignored
    assert average_precision(_labels(), scores, "WASH") == pytest.approx(1.0)


def test_any_treats_every_type_as_positive() -> None:
    scores = np.array([0.1] * 6 + [0.9] * 4)
    assert average_precision(_labels(), scores, "ANY") == pytest.approx(1.0)


def test_curve_is_downsampled_to_max_points_and_keeps_the_endpoints() -> None:
    rng = np.random.default_rng(0)
    labels = pd.Series(np.where(rng.random(5_000) < 0.05, "WASH", "NORMAL"))
    curve = pr_curve(labels, rng.random(5_000), "WASH", max_points=100)
    assert len(curve["precision"]) == len(curve["recall"]) <= 100
    assert curve["recall"][-1] == 0.0 and curve["precision"][-1] == 1.0  # sklearn's final (recall 0, precision 1) point


def test_operating_point_is_precision_and_recall_of_the_flags() -> None:
    flagged = np.array([True] * 3 + [False] * 3 + [True, False, True, False])
    precision, recall = operating_point(_labels(), flagged, "WASH")
    assert precision == pytest.approx(1 / 4) and recall == pytest.approx(1 / 2)  # tp=1 (of 2), fp=3 of the normals


def test_alert_volume_converts_fpr_into_workload() -> None:
    labels = pd.Series(["NORMAL"] * 1000 + ["WASH"] * 10)
    flagged = np.array([True] * 20 + [False] * 980 + [True] * 5 + [False] * 5)
    v = alert_volume(labels, flagged, "WASH", n_instruments=4, sim_hours=2.0)
    assert v["false_alerts"] == 20 and v["true_alerts"] == 5
    assert v["false_alerts_per_1000_innocent_windows"] == pytest.approx(20.0)
    assert v["false_alerts_per_instrument_hour"] == pytest.approx(20 / 8)
