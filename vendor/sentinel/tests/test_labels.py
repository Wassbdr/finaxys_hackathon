"""Tests for window labels and the dataset writer."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.sim.labels import build_labels, write_dataset
from sentinel.sim.runner import SimConfig, simulate

WINDOW = 10 * 1_000_000_000


def _events(ts: list[int], traders: list[int], instrument: str = "A") -> pd.DataFrame:
    return pd.DataFrame({"ts": ts, "instrument": instrument, "trader_id": traders})


def _episodes() -> pd.DataFrame:
    return pd.DataFrame({"episode_id": [0, 1], "kind": ["SPOOFING", "WASH"], "difficulty": ["hard", "easy"]})


def test_windows_are_labeled_by_the_traders_own_tagged_events() -> None:
    events = _events([1, 2, WINDOW + 1, WINDOW + 2, 5], [7, 8, 7, 8, 7])
    tags = np.array([0, -1, -1, -1, 0])  # trader 7 has spoof events only in window 0
    out = build_labels(events, tags, _episodes(), WINDOW).set_index(["trader_id", "window"])
    assert out.loc[(7, 0), "label"] == "SPOOFING" and out.loc[(7, 0), "difficulty"] == "hard"
    assert out.loc[(7, 1), "label"] == "NORMAL" and out.loc[(7, 1), "difficulty"] == "none"
    assert out.loc[(8, 0), "label"] == "NORMAL"
    assert out.loc[(7, 0), "n_events"] == 2 and out.loc[(7, 0), "window_start_ns"] == 0


def test_one_row_per_active_trader_window_and_no_rows_for_inactive() -> None:
    events = _events([1, 2, 3], [7, 7, 8])
    out = build_labels(events, np.array([-1, -1, -1]), _episodes(), WINDOW)
    assert len(out) == 2 and set(out.label) == {"NORMAL"}


def test_window_with_two_episodes_takes_the_lowest_episode_id() -> None:
    events = _events([1, 2], [7, 7])
    out = build_labels(events, np.array([1, 0]), _episodes(), WINDOW)
    assert out.iloc[0].episode_id == 0 and out.iloc[0].label == "SPOOFING"


def test_labels_from_a_real_simulation_cover_every_episode() -> None:
    res = simulate(SimConfig(instruments=("A",), events_per_instrument=20_000, episodes_per_instrument=15, seed=9))
    labels = build_labels(res.events, res.event_episode, res.episodes, WINDOW)
    assert set(labels.label) == {"NORMAL", "SPOOFING", "LAYERING", "WASH"}
    positive = labels[labels.label != "NORMAL"]
    assert 0 < len(positive) < 0.2 * len(labels)
    assert set(positive.episode_id) <= set(res.episodes.episode_id)


def test_write_dataset_round_trips(tmp_path) -> None:
    cfg = SimConfig(instruments=("A",), events_per_instrument=5_000, episodes_per_instrument=5, seed=1)
    res = simulate(cfg)
    write_dataset(res, tmp_path, cfg.window_s)
    events = pd.read_parquet(tmp_path / "events.parquet")
    labels = pd.read_parquet(tmp_path / "labels.parquet")
    assert len(events) == len(res.events) and len(labels) > 0
    assert "episode" not in events.columns and "label" not in events.columns
    assert (tmp_path / "episodes.parquet").exists()
