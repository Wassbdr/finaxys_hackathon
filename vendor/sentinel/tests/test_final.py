"""Tests for turning engine alerts into flags and for the evaluation dict."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.final import ALL_KINDS, DETECTORS, engine_flags, evaluate


def synthetic_eval_inputs(n_episodes: int = 24, seed: int = 0):
    """A labeled window table, engine-style flags and scores for every detector."""
    rng = np.random.default_rng(seed)
    rows = []
    for e in range(n_episodes):
        kind = ("SPOOFING", "LAYERING", "WASH")[e % 3]
        level = ("easy", "medium", "hard")[(e // 3) % 3]
        for w in range(4):
            rows.append({"label": kind, "difficulty": level, "episode_id": e, "trader_id": 500 + e,
                         "instrument": "A", "window": w})
    for a in range(40):
        for w in range(6):
            rows.append({"label": "NORMAL", "difficulty": "none", "episode_id": -1, "trader_id": a,
                         "instrument": ("A", "B")[a % 2], "window": w})
    df = pd.DataFrame(rows)
    pos = (df["label"] != "NORMAL").to_numpy()
    flags, scores = {}, {}
    for i, det in enumerate(DETECTORS):
        strength = 0.3 + 0.15 * i
        base = np.where(pos, rng.random(len(df)) < strength, rng.random(len(df)) < 0.05)
        flags[det] = {k: (base & (df["label"] == k).to_numpy()) | (base & ~pos) if k != "ANY" else base
                      for k in ALL_KINDS}
    for det in ("LightGBM", "Isolation Forest"):
        scores[det] = {k: rng.random(len(df)) + pos * 0.5 for k in ALL_KINDS}
    return df, flags, scores


def test_engine_flags_maps_alerts_to_windows_by_detector_and_kind() -> None:
    df = pd.DataFrame({"trader_id": [1, 1, 2], "instrument": ["A", "A", "B"], "window": [0, 1, 0]})
    alerts = pd.DataFrame({"detector": ["model", "generic", "tuned", "textbook"],
                           "kind": ["WASH", "ANY", "SPOOFING", "LAYERING"],
                           "trader_id": [1, 1, 2, 2], "instrument": ["A", "A", "B", "B"], "window": [0, 1, 0, 0]})
    flags = engine_flags(df, alerts)
    assert list(flags["LightGBM"]["WASH"]) == [True, False, False] and list(flags["LightGBM"]["ANY"]) == [True, False, False]
    assert all(list(flags["generic rule"][k]) == [False, True, False] for k in ALL_KINDS)  # untyped: applies to every type
    assert list(flags["tuned rules"]["SPOOFING"]) == [False, False, True]
    assert list(flags["textbook rules"]["LAYERING"]) == [False, False, True]
    assert list(flags["textbook rules"]["ANY"]) == [False, False, True]


def test_engine_flags_rejects_alerts_for_unknown_windows() -> None:
    df = pd.DataFrame({"trader_id": [1], "instrument": ["A"], "window": [0]})
    alerts = pd.DataFrame({"detector": ["model"], "kind": ["WASH"], "trader_id": [9], "instrument": ["A"], "window": [0]})
    with pytest.raises(ValueError):
        engine_flags(df, alerts)


def test_evaluate_produces_the_full_results_structure() -> None:
    df, flags, scores = synthetic_eval_inputs()
    result = evaluate(df, flags, scores, n_instruments=2, sim_hours=1.0, n_boot=100, seed=1)
    assert result["detectors"] == list(DETECTORS)
    for kind in ALL_KINDS:
        cell = result["metrics"][kind]["LightGBM"]["f1"]
        assert cell["lo"] <= cell["point"] <= cell["hi"] or cell["lo"] <= cell["hi"]
        assert set(result["metrics"][kind]) == set(DETECTORS)
        assert {"curves", "ap", "points", "operating"} <= set(result["pr"][kind])
        assert set(result["pr"][kind]["curves"]) == {"LightGBM", "Isolation Forest"}
        assert result["alert_volume"][kind]["LightGBM"]["false_alerts"] >= 0
        assert {"a", "b", "metric", "diff", "lo", "hi"} <= set(result["paired"][kind][0])
    assert set(result["recall_by_difficulty"]["WASH"]["LightGBM"]) == {"easy", "medium", "hard"}
    assert result["metrics"]["WASH"]["LightGBM"]["episode_recall"]["point"] >= 0
