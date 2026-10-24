"""Tests for the injected manipulation scenarios."""
from __future__ import annotations

import random

import pytest

from sentinel.sim.exchange import Exchange
from sentinel.sim.scenarios import DIFFICULTY, MIN_DEPTH, ScriptedEpisode, spawn_episode
from sentinel.sim.types import ASK, BID

POOL = [1, 2, 3, 4]


def _run(ex: Exchange, agent: ScriptedEpisode, start: int = 1_000_000_000) -> None:
    rng = random.Random(0)
    t = start + max(1, agent.first_delay)
    while (delay := agent.act(ex, t, rng)) is not None:
        t += delay


def _spawn(market: Exchange, kind: str, difficulty: str, seed: int = 5) -> ScriptedEpisode:
    return spawn_episode(market, random.Random(seed), kind, difficulty, POOL, now=1_000_000_000)


def test_difficulty_table_is_immutable_and_monotone() -> None:
    with pytest.raises(TypeError):
        DIFFICULTY["easy"] = DIFFICULTY["hard"]  # type: ignore[index]
    easy, medium, hard = (DIFFICULTY[k] for k in ("easy", "medium", "hard"))
    assert easy.size_mult[0] > medium.size_mult[1] and medium.size_mult[0] > hard.size_mult[1]
    assert easy.hold_s[1] <= medium.hold_s[0] and medium.hold_s[1] <= hard.hold_s[0]
    assert easy.n_dilution < medium.n_dilution < hard.n_dilution
    assert easy.n_layers > medium.n_layers > hard.n_layers
    assert easy.wash_qty_jitter < medium.wash_qty_jitter < hard.wash_qty_jitter


def test_spoofing_signature(market: Exchange) -> None:
    depth_before = {s: market.best_qty(s) for s in (BID, ASK)}
    agent = _spawn(market, "SPOOFING", "easy")
    episode = market.episodes[-1]
    side, trader = episode.side, episode.trader_ids[0]
    _run(market, agent)
    df, tags = market.to_frames()
    mine = df[df.trader_id == trader]
    news = mine[(mine.event_type == "NEW") & (mine.side == side)]
    spoof = news.iloc[0]
    assert spoof.qty >= 3 * max(depth_before[side], MIN_DEPTH)  # easy: >= 3x same-side best-level qty
    cancel = mine[(mine.event_type == "CANCEL") & (mine.order_id == spoof.order_id)]
    assert len(cancel) == 1
    real = mine[(mine.event_type == "EXEC") & (mine.side == -side)]
    assert len(real) >= 1 and real.index.min() < cancel.index.min()  # genuine trade, then cancel
    assert (tags[mine.index] == episode.episode_id).all()  # every row of the trader is tagged
    assert episode.t_end > episode.t_start and market.active_episodes == 0


def test_layering_signature(market: Exchange) -> None:
    agent = _spawn(market, "LAYERING", "easy")
    episode = market.episodes[-1]
    side, trader = episode.side, episode.trader_ids[0]
    _run(market, agent)
    df, _ = market.to_frames()
    mine = df[df.trader_id == trader]
    layers = mine[(mine.event_type == "NEW") & (mine.side == side) & (mine.price > 0)]
    assert len(layers) == DIFFICULTY["easy"].n_layers
    assert layers.price.nunique() == len(layers)  # one order per price level
    cancelled = set(mine[mine.event_type == "CANCEL"].order_id)
    assert set(layers.order_id) <= cancelled


def test_wash_trades_stay_between_the_pair_and_net_out(market: Exchange) -> None:
    agent = _spawn(market, "WASH", "easy")
    episode = market.episodes[-1]
    a, b = episode.trader_ids
    _run(market, agent)
    df, tags = market.to_frames()
    execs = df[(df.event_type == "EXEC") & df.trader_id.isin([a, b])]
    assert len(execs) >= 4  # at least two legs, two rows each
    assert (tags[execs.index] == episode.episode_id).all()
    net = {t: int((execs[execs.trader_id == t].side * execs[execs.trader_id == t].qty).sum()) for t in (a, b)}
    assert net[a] == -net[b]  # every wash fill is between the pair
    largest_leg = int(execs.qty.max())
    assert abs(net[a]) <= largest_leg  # position is (near) flat vs. total volume traded
    assert execs.qty.sum() >= 4 * largest_leg * 0.5


def test_dilution_orders_are_tagged_and_present_when_hard(market: Exchange) -> None:
    agent = _spawn(market, "SPOOFING", "hard")
    episode = market.episodes[-1]
    _run(market, agent)
    df, tags = market.to_frames()
    mine = df[df.trader_id == episode.trader_ids[0]]
    news = mine[mine.event_type == "NEW"]
    assert len(news) >= 1 + 1 + DIFFICULTY["hard"].n_dilution  # spoof + genuine + dilution
    assert (tags[mine.index] == episode.episode_id).all()


def test_spawn_skips_busy_traders_and_fails_when_none_free(market: Exchange) -> None:
    market.busy.update({1, 2, 3})
    agent = _spawn(market, "SPOOFING", "easy")
    assert market.episodes[-1].trader_ids == (4,)
    with pytest.raises(RuntimeError):
        _spawn(market, "WASH", "easy")
    del agent


def test_layer_sizes_are_jittered(market: Exchange) -> None:
    agent = _spawn(market, "LAYERING", "easy")
    episode = market.episodes[-1]
    _run(market, agent)
    df, _ = market.to_frames()
    mine = df[(df.trader_id == episode.trader_ids[0]) & (df.event_type == "NEW") & (df.side == episode.side) & (df.price > 0)]
    assert mine.qty.nunique() > 1
