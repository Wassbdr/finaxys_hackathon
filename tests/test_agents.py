"""Tests for the background agents."""
from __future__ import annotations

import random

import pytest

from sentinel.sim.agents import MarketMaker, MomentumTrader, NoiseTrader, lognormal_qty
from sentinel.sim.exchange import Exchange
from sentinel.sim.types import ASK, BID


def test_lognormal_qty_is_positive_integer_with_requested_median() -> None:
    rng = random.Random(1)
    qtys = sorted(lognormal_qty(rng, 100, 0.8) for _ in range(4001))
    assert all(isinstance(q, int) and q >= 1 for q in qtys)
    assert 85 <= qtys[2000] <= 118


def test_market_maker_quotes_both_sides_and_requotes() -> None:
    ex = Exchange("T", 0)
    rng = random.Random(1)
    mm = MarketMaker(1, rate=1.0)
    assert mm.act(ex, 1_000, rng) > 0
    bb, ba, *_ = ex.snap
    assert 0 < bb < ba
    first = set(ex.book.orders)
    mm.act(ex, 2_000, rng)
    live = [o for o in ex.book.orders.values() if o.trader_id == 1]
    assert len(live) == 2 and first.isdisjoint(ex.book.orders)


def test_mixed_flow_never_crosses_the_book_and_produces_all_event_types() -> None:
    ex = Exchange("T", 0)
    rng = random.Random(2)
    agents = [MarketMaker(i, 8.0) for i in range(3)]
    agents += [NoiseTrader(10 + i, 2.0) for i in range(10)]
    agents += [MomentumTrader(30, 0.5)]
    t = 0
    for step in range(6000):
        agent = agents[step % len(agents)]
        t += 1_000_000
        agent.act(ex, t, rng)
        bb, ba, *_ = ex.snap
        assert not (bb and ba) or bb < ba
    df, _ = ex.to_frames()
    assert set(df.event_type.astype(str)) == {"NEW", "CANCEL", "MODIFY", "EXEC"}


def test_momentum_bursts_share_a_side() -> None:
    ex = Exchange("T", 0)
    rng = random.Random(3)
    for i in range(4):
        MarketMaker(900 + i, 1.0).act(ex, 100 + i, rng)
    trader = MomentumTrader(99, rate=0.5)
    delays = [trader.act(ex, 1_000_000 * (k + 1), rng) for k in range(3)]
    df, _ = ex.to_frames()
    mine = df[(df.trader_id == 99) & (df.event_type == "NEW")]
    assert mine.side.nunique() == 1  # first burst has at least 3 orders, all one side
    assert delays[0] <= 300_000_000  # still inside the burst


def test_noise_trader_leans_with_book_imbalance() -> None:
    ex = Exchange("T", 0)
    ex.new_limit(500, BID, 9_999, 100_000, ts=1)  # huge bid stack at the touch
    ex.new_limit(501, ASK, 10_001, 100, ts=2)
    rng = random.Random(4)
    sides = []
    for k in range(400):
        trader = NoiseTrader(1 + k, 1.0)
        n0 = ex.n_events
        trader.act(ex, 10 + k, rng)
        df, _ = ex.to_frames()
        new_rows = df.iloc[n0:]
        new_rows = new_rows[new_rows.event_type == "NEW"]
        sides += list(new_rows.side)
    assert sides.count(BID) > sides.count(ASK)


@pytest.mark.parametrize("p_improve, expect_some", [(0.0, False), (1.0, True)])
def test_noise_traders_only_post_inside_the_spread_when_price_improvement_is_enabled(
        monkeypatch, p_improve: float, expect_some: bool) -> None:
    import sentinel.sim.agents as agents

    monkeypatch.setattr(agents, "P_IMPROVE", p_improve)
    rng = random.Random(9)
    improving = 0
    for _ in range(400):
        ex = Exchange("T", 0)
        ex.new_limit(900, BID, 9_999, 100, ts=1)
        ex.new_limit(901, ASK, 10_001, 100, ts=2)  # one-tick-wide interior: only price 10,000 improves
        NoiseTrader(1, 1.0).act(ex, 10, rng)
        df, _ = ex.to_frames()
        mine = df[(df.trader_id == 1) & (df.event_type == "NEW") & (df.price > 0)]
        improving += int((mine.price == 10_000).sum())  # strictly inside the 9,999 / 10,001 spread
    assert (improving > 0) == expect_some
