"""Tests for the Exchange: logging, timestamps, snapshots, episode tags."""
from __future__ import annotations

from sentinel.sim.exchange import Exchange
from sentinel.sim.types import ASK, BID, NO_EPISODE


def test_cross_logs_new_rows_and_two_exec_rows() -> None:
    ex = Exchange("T", 0)
    a = ex.new_limit(1, ASK, 101, 10, ts=100)
    ex.new_limit(2, BID, 101, 4, ts=200)
    df, _ = ex.to_frames()
    assert list(df.event_type.astype(str)) == ["NEW", "NEW", "EXEC", "EXEC"]
    execs = df[df.event_type == "EXEC"]
    maker = execs[execs.trader_id == 1].iloc[0]
    taker = execs[execs.trader_id == 2].iloc[0]
    assert (maker.side, maker.order_id, maker.qty, maker.price) == (ASK, a, 4, 101)
    assert (taker.side, taker.qty) == (BID, 4)
    assert ex.last_price == 101


def test_timestamps_strictly_increase_even_with_equal_input_ts() -> None:
    ex = Exchange("T", 0)
    ex.new_limit(1, ASK, 101, 10, ts=100)
    ex.new_limit(2, BID, 101, 10, ts=100)
    ex.new_limit(3, BID, 99, 10, ts=50)  # in the past: gets bumped forward
    df, _ = ex.to_frames()
    assert (df.ts.diff().dropna() > 0).all()


def test_snapshot_is_recorded_after_the_event() -> None:
    ex = Exchange("T", 0)
    ex.new_limit(1, ASK, 101, 10, ts=1)
    ex.new_limit(2, BID, 101, 4, ts=2)
    df, _ = ex.to_frames()
    last = df.iloc[-1]
    assert (last.best_bid, last.best_ask, last.ask_qty1) == (0, 101, 6)


def test_cancel_logs_remaining_qty_and_rejects_dead_orders() -> None:
    ex = Exchange("T", 0)
    oid = ex.new_limit(1, ASK, 101, 10, ts=1)
    ex.new_market(2, BID, 3, ts=2)
    assert ex.cancel(oid, ts=3) is True
    n = ex.n_events
    assert ex.cancel(oid, ts=4) is False
    assert ex.n_events == n  # a rejected cancel logs nothing
    df, _ = ex.to_frames()
    cancel = df[df.event_type == "CANCEL"].iloc[0]
    assert cancel.qty == 7


def test_modify_logs_a_row_and_can_trade() -> None:
    ex = Exchange("T", 0)
    bid = ex.new_limit(1, BID, 100, 5, ts=1)
    ex.new_limit(2, ASK, 105, 5, ts=2)
    assert ex.modify(bid, 5, ts=3, new_price=105) is True
    df, _ = ex.to_frames()
    assert "MODIFY" in set(df.event_type.astype(str))
    assert (df.event_type == "EXEC").sum() == 2


def test_market_order_row_has_price_zero() -> None:
    ex = Exchange("T", 0)
    ex.new_limit(1, ASK, 101, 10, ts=1)
    ex.new_market(2, BID, 5, ts=2)
    df, _ = ex.to_frames()
    assert df.iloc[1].price == 0


def test_episode_tags_follow_orders_but_not_counterparties() -> None:
    ex = Exchange("T", 0)
    episode = ex.start_episode("SPOOFING", "easy", [1], BID, ts=0)
    ex.new_limit(1, ASK, 101, 10, ts=1, episode=episode.episode_id)
    ex.new_market(2, BID, 4, ts=2)  # normal trader hits the tagged order
    df, tags = ex.to_frames()
    assert "episode" not in df.columns
    assert list(tags) == [episode.episode_id, NO_EPISODE, episode.episode_id, NO_EPISODE]
    # rows: NEW(1, tagged), NEW(2), EXEC maker=1 (tagged), EXEC taker=2 (untagged)


def test_episode_bookkeeping_marks_traders_busy_until_end() -> None:
    ex = Exchange("T", 3)
    episode = ex.start_episode("WASH", "hard", [7, 8], 0, ts=10)
    assert ex.busy == {7, 8} and ex.active_episodes == 1
    assert episode.episode_id // 1_000_000 == 3
    ex.new_limit(7, ASK, 101, 1, ts=20)
    ex.end_episode(episode)
    assert ex.busy == set() and ex.active_episodes == 0
    assert episode.t_end == 20


def test_order_ids_are_unique_across_instruments() -> None:
    a, b = Exchange("A", 0), Exchange("B", 1)
    assert a.new_limit(1, BID, 100, 1, ts=1) != b.new_limit(1, BID, 100, 1, ts=1)


def test_to_frames_handles_an_empty_log() -> None:
    df, tags = Exchange("T", 0).to_frames()
    assert len(df) == 0 and len(tags) == 0


def test_self_trade_prevention_logs_a_cancel_row_for_the_resting_order() -> None:
    ex = Exchange("T", 0)
    own = ex.new_limit(1, ASK, 101, 5, ts=1)
    ex.new_limit(2, ASK, 101, 5, ts=2)
    ex.new_market(1, BID, 5, ts=3)
    df, _ = ex.to_frames()
    cancels = df[df.event_type == "CANCEL"]
    assert list(cancels.order_id) == [own] and cancels.iloc[0].qty == 5
    execs = df[df.event_type == "EXEC"]
    assert set(execs.trader_id) == {1, 2} and (execs.qty == 5).all()


def test_exec_rows_carry_the_counterparty_and_other_rows_carry_zero() -> None:
    ex = Exchange("T", 0)
    ex.new_limit(1, ASK, 101, 10, ts=100)
    ex.new_limit(2, BID, 101, 4, ts=200)
    df, _ = ex.to_frames()
    assert list(df[df.event_type != "EXEC"].counterparty_id) == [0, 0]
    execs = df[df.event_type == "EXEC"].set_index("trader_id")
    assert execs.loc[1, "counterparty_id"] == 2 and execs.loc[2, "counterparty_id"] == 1
    assert list(df.columns[:6]) == ["ts", "instrument", "event_type", "order_id", "trader_id", "counterparty_id"]
