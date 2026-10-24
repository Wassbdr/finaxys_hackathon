"""Load feature+label tables, split them without leakage, and guard the final test set."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from sentinel.features.extractor import FEATURE_NAMES, extract_features

CLASSES = ("NORMAL", "SPOOFING", "LAYERING", "WASH")
CLASS_INDEX = {name: i for i, name in enumerate(CLASSES)}
ACCESS_LOG = Path("data/test_access.log")
_KEY = ["trader_id", "instrument", "window"]
_LABEL_COLUMNS = ["window_start_ns", "n_events", "label", "difficulty", "episode_id"]


def _log_access(sim_dir: Path) -> None:
    """Append a timestamped line to the test-set access log."""
    ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with ACCESS_LOG.open("a", encoding="utf-8") as log:
        log.write(f"{stamp}\t{sim_dir}\n")


def _guard(sim_dir: Path, final: bool) -> None:
    """test_* directories need final=True and every load is logged."""
    if sim_dir.name.startswith("test_"):
        if not final:
            raise PermissionError(f"{sim_dir.name} is the final test set; pass final=True (logged) to load it")
        _log_access(sim_dir)


def load_events(sim_dir: Path, final: bool = False) -> pd.DataFrame:
    """Raw event log of a simulation directory, behind the same guard as ``load_sim``."""
    sim_dir = Path(sim_dir)
    _guard(sim_dir, final)
    return pd.read_parquet(sim_dir / "events.parquet")


def load_sim(sim_dir: Path, window_s: int = 10, final: bool = False, rebuild: bool = False) -> pd.DataFrame:
    """Features joined with labels for one simulation directory.

    Directories named ``test_*`` are the fresh final test simulation: they can
    only be loaded with ``final=True`` and every load is appended to the access
    log, so single use is auditable. Features are cached in features.parquet
    and rebuilt if the cache is missing, stale (columns differ) or ``rebuild``.
    """
    sim_dir = Path(sim_dir)
    _guard(sim_dir, final)
    cache = sim_dir / "features.parquet"
    feats = None
    if cache.exists() and not rebuild:
        cached = pd.read_parquet(cache)
        if list(cached.columns) == ["trader_id", "instrument", "window", *FEATURE_NAMES]:
            feats = cached
    if feats is None:
        feats = extract_features(pd.read_parquet(sim_dir / "events.parquet"), window_s)
        feats.to_parquet(cache, index=False)
    labels = pd.read_parquet(sim_dir / "labels.parquet")
    df = feats.merge(labels[_KEY + _LABEL_COLUMNS], on=_KEY, how="inner", validate="one_to_one")
    if not (len(df) == len(feats) == len(labels)):
        raise ValueError("feature rows and label rows do not match one-to-one")
    df["sim"] = sim_dir.name
    return df


def time_split(df: pd.DataFrame, train_frac: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Leak-free train/validation split, applied per simulation.

    Boundary B = int(train_frac * n_windows). Train gets windows <= B-2 and
    validation windows >= B+1, so the two windows at the boundary are dropped.
    Any episode with rows in both parts, or in the dropped windows, is removed
    from both, so no episode contributes to more than one side.
    """
    train_parts, val_parts = [], []
    for _, g in df.groupby("sim", sort=False):
        boundary = int(train_frac * (g["window"].max() + 1))
        train, val = g[g["window"] <= boundary - 2], g[g["window"] >= boundary + 1]
        gap = g[g["window"].isin([boundary - 1, boundary])]
        drop = (set(train["episode_id"]) & set(val["episode_id"])) | set(gap["episode_id"])
        drop.discard(-1)
        train_parts.append(train[~train["episode_id"].isin(drop)])
        val_parts.append(val[~val["episode_id"].isin(drop)])
    return pd.concat(train_parts, ignore_index=True), pd.concat(val_parts, ignore_index=True)


def to_xy(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Feature matrix (columns in FEATURE_NAMES order) and integer class labels."""
    return df[list(FEATURE_NAMES)].to_numpy(dtype=np.float64), df["label"].map(CLASS_INDEX).to_numpy()
