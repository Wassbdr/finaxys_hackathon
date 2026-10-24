"""Shared fixtures."""
from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from sentinel.detect.dataset import CLASSES
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.sim.agents import MarketMaker
from sentinel.sim.exchange import Exchange


@pytest.fixture
def market() -> Exchange:
    """An exchange whose book is seeded with six market makers' quotes."""
    ex = Exchange("T", 0)
    rng = random.Random(11)
    for i in range(6):
        MarketMaker(900 + i, rate=1.0).act(ex, 1_000 + i, rng)
    return ex


@pytest.fixture
def synthetic_windows() -> pd.DataFrame:
    """2,000 synthetic trader-windows where each manipulation class shifts a few features."""
    rng = np.random.default_rng(0)
    n = 2_000
    labels = rng.choice(len(CLASSES), size=n, p=[0.85, 0.05, 0.05, 0.05])
    X = rng.normal(size=(n, len(FEATURE_NAMES)))
    col = {name: i for i, name in enumerate(FEATURE_NAMES)}
    X[labels == 1, col["max_qty_over_best"]] += 4.0
    X[labels == 1, col["opposite_fill_share"]] += 3.0
    X[labels == 2, col["max_live_levels_same_side"]] += 4.0
    X[labels == 2, col["max_cancel_burst_100ms"]] += 3.0
    X[labels == 3, col["top_counterparty_share"]] += 4.0
    X[labels == 3, col["net_over_gross"]] -= 3.0
    df = pd.DataFrame(X, columns=list(FEATURE_NAMES))
    df["label"] = [CLASSES[i] for i in labels]
    df["difficulty"] = np.where(labels == 0, "none", "easy")
    return df
