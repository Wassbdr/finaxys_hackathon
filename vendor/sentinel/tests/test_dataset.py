"""Tests for dataset loading, the leak-free split and the test-set guard."""
from __future__ import annotations

import pandas as pd
import pytest

from sentinel.detect import dataset
from sentinel.detect.dataset import CLASSES, load_sim, time_split, to_xy
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.sim.labels import write_dataset
from sentinel.sim.runner import SimConfig, simulate


def _windows(sim: str, episodes: dict[int, tuple[int, int]], n_windows: int = 100) -> pd.DataFrame:
    """One NORMAL row per window plus one row per window of each episode (id -> first, last window)."""
    rows = [{"sim": sim, "window": w, "episode_id": -1, "label": "NORMAL"} for w in range(n_windows)]
    for eid, (first, last) in episodes.items():
        rows += [{"sim": sim, "window": w, "episode_id": eid, "label": "SPOOFING"} for w in range(first, last + 1)]
    return pd.DataFrame(rows)


def test_split_is_time_ordered_with_boundary_windows_dropped() -> None:
    train, val = time_split(_windows("s", {}), train_frac=0.7)
    assert train.window.max() == 68 and val.window.min() == 71  # boundary is 70: windows 69, 70 dropped
    assert train.window.max() < val.window.min()


def test_episodes_never_appear_on_both_sides_and_straddlers_are_dropped() -> None:
    eps = {1: (10, 12), 2: (68, 72), 3: (69, 69), 4: (80, 82), 5: (70, 70)}
    train, val = time_split(_windows("s", eps), train_frac=0.7)
    in_train, in_val = set(train.episode_id) - {-1}, set(val.episode_id) - {-1}
    assert in_train == {1} and in_val == {4}  # 2 straddles; 3 and 5 sit in dropped boundary windows


def test_split_is_applied_per_simulation() -> None:
    both = pd.concat([_windows("a", {}, 100), _windows("b", {}, 50)])
    train, val = time_split(both, train_frac=0.7)
    assert train[train.sim == "b"].window.max() == 33 and val[val.sim == "b"].window.min() == 36


def test_to_xy_orders_columns_and_maps_labels() -> None:
    df = pd.DataFrame({name: [1.0, 2.0] for name in FEATURE_NAMES})
    df["label"] = ["NORMAL", "WASH"]
    X, y = to_xy(df)
    assert X.shape == (2, len(FEATURE_NAMES)) and list(y) == [0, 3] and CLASSES[3] == "WASH"


def test_load_sim_joins_features_and_labels_one_to_one(tmp_path) -> None:
    cfg = SimConfig(instruments=("A",), events_per_instrument=8_000, episodes_per_instrument=8, seed=2)
    write_dataset(simulate(cfg), tmp_path / "sim_seed2", cfg.window_s)
    df = load_sim(tmp_path / "sim_seed2")
    assert set(FEATURE_NAMES) <= set(df.columns) and {"label", "difficulty", "episode_id", "sim"} <= set(df.columns)
    assert df.sim.eq("sim_seed2").all() and set(df.label) >= {"NORMAL"}
    assert (tmp_path / "sim_seed2" / "features.parquet").exists()
    again = load_sim(tmp_path / "sim_seed2")  # second call reads the cache
    pd.testing.assert_frame_equal(df, again)


def test_test_simulations_are_locked_without_final_and_access_is_logged(tmp_path, monkeypatch) -> None:
    cfg = SimConfig(instruments=("A",), events_per_instrument=5_000, episodes_per_instrument=5, seed=3)
    write_dataset(simulate(cfg), tmp_path / "test_seed3", cfg.window_s)
    monkeypatch.setattr(dataset, "ACCESS_LOG", tmp_path / "test_access.log")
    with pytest.raises(PermissionError):
        load_sim(tmp_path / "test_seed3")
    assert not (tmp_path / "test_access.log").exists()
    load_sim(tmp_path / "test_seed3", final=True)
    assert "test_seed3" in (tmp_path / "test_access.log").read_text()


def test_load_events_uses_the_same_test_set_guard(tmp_path, monkeypatch) -> None:
    from sentinel.detect.dataset import load_events

    cfg = SimConfig(instruments=("A",), events_per_instrument=3_000, episodes_per_instrument=3, seed=4)
    write_dataset(simulate(cfg), tmp_path / "test_seed4", cfg.window_s)
    write_dataset(simulate(cfg), tmp_path / "rehearsal_seed4", cfg.window_s)
    monkeypatch.setattr(dataset, "ACCESS_LOG", tmp_path / "test_access.log")
    with pytest.raises(PermissionError):
        load_events(tmp_path / "test_seed4")
    assert len(load_events(tmp_path / "rehearsal_seed4")) > 0 and not (tmp_path / "test_access.log").exists()
    assert len(load_events(tmp_path / "test_seed4", final=True)) > 0
    assert "test_seed4" in (tmp_path / "test_access.log").read_text()
