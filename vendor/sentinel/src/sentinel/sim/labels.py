"""Trader-window labels derived from hidden episode tags, and the dataset writer."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .runner import SimResult
from .types import NO_EPISODE, NS_PER_SEC

_KEY = ["trader_id", "instrument", "window"]


def build_labels(events: pd.DataFrame, event_episode: np.ndarray, episodes: pd.DataFrame,
                 window_ns: int) -> pd.DataFrame:
    """One row per (trader, instrument, window) in which the trader had any event.

    A window is labeled with the kind of the episode whose tagged events the
    trader has in that window (lowest episode id if there are several).
    Otherwise it is NORMAL with difficulty 'none'.
    """
    df = pd.DataFrame({
        "trader_id": events["trader_id"].to_numpy(),
        "instrument": events["instrument"].astype(str).to_numpy(),
        "window": events["ts"].to_numpy() // window_ns,
        "episode_id": event_episode,
    })
    out = df.groupby(_KEY, sort=True).size().rename("n_events").reset_index()
    tagged = df[df["episode_id"] >= 0]
    if tagged.empty:
        out["episode_id"] = NO_EPISODE
    else:
        owner = tagged.groupby(_KEY)["episode_id"].min().rename("episode_id").reset_index()
        out = out.merge(owner, on=_KEY, how="left")
        out["episode_id"] = out["episode_id"].fillna(NO_EPISODE).astype("int64")
    out = out.merge(episodes[["episode_id", "kind", "difficulty"]], on="episode_id", how="left")
    out["label"] = out["kind"].fillna("NORMAL")
    out["difficulty"] = out["difficulty"].fillna("none")
    out["window_start_ns"] = out["window"] * window_ns
    return out[["trader_id", "instrument", "window", "window_start_ns", "n_events",
                "label", "difficulty", "episode_id"]]


def write_dataset(result: SimResult, out_dir: Path, window_s: int) -> pd.DataFrame:
    """Write events.parquet, episodes.parquet and labels.parquet; returns the labels."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = build_labels(result.events, result.event_episode, result.episodes, window_s * NS_PER_SEC)
    result.events.to_parquet(out_dir / "events.parquet", index=False)
    result.episodes.to_parquet(out_dir / "episodes.parquet", index=False)
    labels.to_parquet(out_dir / "labels.parquet", index=False)
    return labels
