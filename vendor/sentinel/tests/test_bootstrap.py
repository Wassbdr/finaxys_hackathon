"""Tests for the cluster bootstrap."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.bootstrap import (METRICS, bootstrap_detectors, bootstrap_recall_by_difficulty,
                                     build_clusters, interval, paired_difference)
from sentinel.eval.metrics import type_metrics


def _table(n_episodes: int = 20, windows_per_episode: int = 5, n_accounts: int = 30, normal_per_account: int = 10,
           kind: str = "WASH") -> pd.DataFrame:
    rows = []
    for e in range(n_episodes):
        level = ("easy", "medium", "hard")[e % 3]
        for w in range(windows_per_episode):
            rows.append({"label": kind, "difficulty": level, "episode_id": e, "trader_id": 1000 + e,
                         "instrument": "A", "window": w})
    for a in range(n_accounts):
        for w in range(normal_per_account):
            rows.append({"label": "NORMAL", "difficulty": "none", "episode_id": -1, "trader_id": a,
                         "instrument": "A" if a % 2 else "B", "window": w})
    return pd.DataFrame(rows)


def test_build_clusters_counts_episodes_and_accounts() -> None:
    df = _table()
    c = build_clusters(df, "WASH")
    assert c.n_ep == 20 and c.n_acct == 30 and c.keep.all()
    other = pd.concat([df, df.iloc[:5].assign(label="SPOOFING", episode_id=99)], ignore_index=True)
    assert build_clusters(other, "WASH").keep.sum() == len(df)  # other types are excluded
    assert build_clusters(other, "ANY").n_ep == 21


def test_point_estimates_equal_the_plain_metrics() -> None:
    df = _table()
    rng = np.random.default_rng(0)
    flagged = rng.random(len(df)) < 0.3
    boot = bootstrap_detectors(df, {"d": flagged}, "WASH", n_boot=50, seed=1)
    plain = type_metrics(df["label"], flagged, "WASH")
    for metric in ("precision", "recall", "f1", "fpr"):
        assert boot.point["d"][metric] == pytest.approx(plain[metric])


def test_perfect_and_silent_detectors_have_degenerate_intervals() -> None:
    df = _table()
    perfect, silent = (df["label"] == "WASH").to_numpy(), np.zeros(len(df), dtype=bool)
    boot = bootstrap_detectors(df, {"perfect": perfect, "silent": silent}, "WASH", n_boot=200, seed=2)
    for metric in ("precision", "recall", "f1", "episode_recall"):
        assert interval(boot.reps["perfect"][metric]) == (1.0, 1.0)
    assert interval(boot.reps["perfect"]["fpr"]) == (0.0, 0.0)
    assert interval(boot.reps["silent"]["recall"]) == (0.0, 0.0) and boot.point["silent"]["precision"] == 0.0


def test_resampling_by_episode_widens_the_interval_versus_windows() -> None:
    """Two 10-window episodes, one fully caught, one fully missed: recall is 0, 0.5 or 1 per replicate."""
    df = _table(n_episodes=2, windows_per_episode=10, n_accounts=5)
    flagged = ((df["label"] == "WASH") & (df["episode_id"] == 0)).to_numpy()
    boot = bootstrap_detectors(df, {"d": flagged}, "WASH", n_boot=500, seed=3)
    lo, hi = interval(boot.reps["d"]["recall"])
    assert (lo, hi) == (0.0, 1.0) and boot.point["d"]["recall"] == 0.5


def test_intervals_cover_the_true_episode_recall_at_about_the_nominal_rate() -> None:
    rng = np.random.default_rng(7)
    hits = 0
    worlds = 150
    for w in range(worlds):
        df = _table(n_episodes=80, windows_per_episode=3, n_accounts=20, normal_per_account=5)
        caught = rng.random(80) < 0.6  # true episode-level recall is 0.6
        flagged = np.where(df["label"] == "WASH", caught[df["episode_id"].clip(lower=0).to_numpy()], False)
        boot = bootstrap_detectors(df, {"d": flagged}, "WASH", n_boot=300, seed=w)
        lo, hi = interval(boot.reps["d"]["episode_recall"])
        hits += lo <= 0.6 <= hi
    print(f"observed coverage: {hits / worlds:.4f} ({hits}/{worlds})")
    assert 0.88 <= hits / worlds <= 1.0


def test_paired_difference_is_zero_for_identical_detectors_and_positive_for_a_better_one() -> None:
    df = _table()
    truth = (df["label"] == "WASH").to_numpy()
    rng = np.random.default_rng(1)
    noisy = truth & (rng.random(len(df)) < 0.5)
    boot = bootstrap_detectors(df, {"a": truth, "b": truth.copy(), "c": noisy}, "WASH", n_boot=300, seed=4)
    assert paired_difference(boot, "a", "b", "f1") == (0.0, 0.0, 0.0)
    diff, lo, hi = paired_difference(boot, "a", "c", "recall")
    assert diff > 0 and lo > 0


def test_recall_by_difficulty_uses_episodes_within_each_level() -> None:
    df = _table(n_episodes=30)
    flagged = (df["label"] == "WASH").to_numpy() & (df["difficulty"] == "easy").to_numpy()
    out = bootstrap_recall_by_difficulty(df, {"d": flagged}, "WASH", n_boot=200, seed=5)["d"]
    assert out["easy"]["point"] == 1.0 and out["medium"]["point"] == 0.0 and out["hard"]["point"] == 0.0
    assert out["easy"]["n"] == 50 and out["easy"]["n_episodes"] == 10
    assert out["hard"]["lo"] == 0.0 and out["hard"]["hi"] == 0.0


def test_no_positives_raises() -> None:
    with pytest.raises(ValueError):
        build_clusters(_table(kind="SPOOFING"), "WASH")
    assert set(METRICS) == {"precision", "recall", "f1", "fpr", "episode_recall"}
