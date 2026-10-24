"""Integration tests for the simulation runner."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandas.testing as pdt

from sentinel.sim.runner import SimConfig, simulate
from sentinel.sim.scenarios import ALL_COMBOS

SMALL = SimConfig(instruments=("A", "B"), events_per_instrument=20_000, episodes_per_instrument=20, seed=3)


def test_same_seed_gives_identical_output() -> None:
    first, second = simulate(SMALL), simulate(SMALL)
    pdt.assert_frame_equal(first.events, second.events)
    pdt.assert_frame_equal(first.episodes, second.episodes)
    assert np.array_equal(first.event_episode, second.event_episode)


def test_different_seed_gives_different_output() -> None:
    other = simulate(SimConfig(**{**SMALL.__dict__, "seed": 4}))
    assert not simulate(SMALL).events.equals(other.events)


def test_event_log_invariants() -> None:
    res = simulate(SMALL)
    ev = res.events
    assert len(ev) >= 2 * 20_000
    assert (ev.ts.diff().dropna() >= 0).all()  # merged log is time-ordered
    for _, group in ev.groupby("instrument", observed=True):
        assert (group.ts.diff().dropna() > 0).all()  # strictly increasing per instrument
        assert (group.event_type == "EXEC").sum() % 2 == 0  # two rows per fill
    quoted = (ev.best_bid > 0) & (ev.best_ask > 0)
    assert (ev.best_bid[quoted] < ev.best_ask[quoted]).all()  # never crossed
    assert (ev.qty > 0).all() and (ev.trader_id >= 0).all()
    assert (ev.price[ev.event_type == "EXEC"] > 0).all()
    assert set(ev.event_type.astype(str)) == {"NEW", "CANCEL", "MODIFY", "EXEC"}
    assert len(res.event_episode) == len(ev)


def test_all_scenario_combinations_are_injected_and_closed() -> None:
    res = simulate(SMALL)
    eps = res.episodes
    assert set(zip(eps.kind, eps.difficulty)) == set(ALL_COMBOS)
    assert (eps.t_end >= eps.t_start).all()
    tagged = res.event_episode[res.event_episode >= 0]
    assert set(tagged) <= set(eps.episode_id)


def test_market_makers_cancel_far_more_than_noise_traders() -> None:
    """The legitimate-high-cancel population that naive cancel-rate rules mis-flag."""
    ev = simulate(SMALL).events
    mm = ev[(ev.trader_id % 1000) < 6]
    other = ev[(ev.trader_id % 1000) >= 12]
    def cancel_rate(df: pd.DataFrame) -> float:
        return (df.event_type == "CANCEL").sum() / max(1, (df.event_type == "NEW").sum())
    assert cancel_rate(mm) > 0.9
    assert cancel_rate(other) < cancel_rate(mm) - 0.25


def _mid_at(events: pd.DataFrame, ts_array: np.ndarray, t: int) -> float:
    i = int(np.searchsorted(ts_array, t, side="right")) - 1
    best_bid, best_ask = events.best_bid.iat[i], events.best_ask.iat[i]
    return (best_bid + best_ask) / 2 if best_bid and best_ask else float("nan")


def test_spoofing_moves_the_mid_in_the_intended_direction_on_average() -> None:
    """The causal claim: fake size on one side pushes the price toward that side.

    Impact is measured from the mid when the episode starts to the mid just
    before the spoof order is cancelled (afterwards the price reverts).
    Mechanism: noise flow and market-maker quotes both lean with book imbalance.
    """
    cfg = SimConfig(instruments=("A",), events_per_instrument=100_000, episodes_per_instrument=80,
                    combos=(("SPOOFING", "easy"),), seed=21)
    res = simulate(cfg)
    ev, tags = res.events, res.event_episode
    ts = ev.ts.to_numpy()
    is_cancel = (ev.event_type == "CANCEL").to_numpy()
    impacts = []
    for row in res.episodes.itertuples():
        cancels = np.nonzero((tags == row.episode_id) & is_cancel)[0]
        if not len(cancels):
            continue
        start, before_cancel = _mid_at(ev, ts, row.t_start), _mid_at(ev, ts, ts[cancels[0]] - 1)
        if not (np.isnan(start) or np.isnan(before_cancel)):
            impacts.append(row.side * (before_cancel - start))
    mean, stderr = float(np.mean(impacts)), float(np.std(impacts) / np.sqrt(len(impacts)))
    assert len(impacts) >= 60
    assert mean > 3 * stderr  # more than three standard errors above zero (n is ~80 here)
