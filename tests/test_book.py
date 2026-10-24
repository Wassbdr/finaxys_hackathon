"""Tests for the price-time-priority matching engine."""
from __future__ import annotations

import random

from sentinel.sim.book import OrderBook
from sentinel.sim.types import ASK, BID, NO_QUOTE


def test_price_priority_best_ask_fills_first() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 101, 5)
    book.add_limit(2, 11, ASK, 100, 5)
    fills = book.add_market(3, 12, BID, 5)
    assert [(f.price, f.maker_order_id) for f in fills] == [(100, 2)]


def test_time_priority_within_a_price_level() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 100, 5)
    book.add_limit(2, 11, ASK, 100, 5)
    fills = book.add_market(3, 12, BID, 7)
    assert [(f.maker_order_id, f.qty) for f in fills] == [(1, 5), (2, 2)]


def test_partial_fill_leaves_remainder_resting() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 100, 10)
    fills = book.add_limit(2, 11, BID, 100, 4)
    assert sum(f.qty for f in fills) == 4
    assert book.snapshot()[1] == 100  # ask still resting
    assert book.snapshot()[3] == 6  # ask_qty1


def test_crossing_limit_rests_its_unfilled_remainder() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 100, 6)
    fills = book.add_limit(2, 11, BID, 100, 10)
    assert sum(f.qty for f in fills) == 6
    assert book.orders[2].qty == 4
    assert book.snapshot()[0] == 100  # our remainder is now the best bid


def test_cancel_removes_level_and_rejects_unknown_ids() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 100, 5)
    assert book.cancel(1) is True
    assert book.snapshot()[1] == NO_QUOTE
    assert book.cancel(1) is False


def test_cancel_of_filled_order_is_rejected() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 100, 5)
    book.add_market(2, 11, BID, 5)
    assert book.cancel(1) is False


def test_modify_down_keeps_queue_priority() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 100, 10)
    book.add_limit(2, 11, ASK, 100, 10)
    assert book.modify(1, 4) == []
    fills = book.add_market(3, 12, BID, 3)
    assert fills[0].maker_order_id == 1
    assert book.snapshot()[3] == 11  # 1 left on order 1, 10 on order 2


def test_modify_up_loses_queue_priority() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 100, 10)
    book.add_limit(2, 11, ASK, 100, 10)
    book.modify(1, 20)
    fills = book.add_market(3, 12, BID, 5)
    assert fills[0].maker_order_id == 2


def test_modify_price_into_the_spread_crosses() -> None:
    book = OrderBook()
    book.add_limit(1, 10, BID, 100, 5)
    book.add_limit(2, 11, ASK, 105, 5)
    fills = book.modify(1, 5, new_price=105)
    assert fills is not None and sum(f.qty for f in fills) == 5


def test_modify_unknown_order_returns_none() -> None:
    assert OrderBook().modify(99, 5) is None


def test_snapshot_reports_top_of_book_and_five_level_depth() -> None:
    book = OrderBook()
    for i, px in enumerate([100, 99, 98, 97, 96, 95, 94]):
        book.add_limit(i + 1, 10, BID, px, 10 + i)
    book.add_limit(50, 11, ASK, 101, 7)
    bb, ba, bq1, aq1, bd, ad = book.snapshot()
    assert (bb, ba, bq1, aq1) == (100, 101, 10, 7)
    assert bd == 10 + 11 + 12 + 13 + 14  # top five bid levels only
    assert ad == 7


def _check_invariants(book: OrderBook) -> None:
    bb, ba, *_ = book.snapshot()
    if bb != NO_QUOTE and ba != NO_QUOTE:
        assert bb < ba, "book is crossed"
    for side in (BID, ASK):
        resting = [o for o in book.orders.values() if o.side == side]
        assert book.total_resting(side) == sum(o.qty for o in resting)
        assert book.n_levels(side) == len({o.price for o in resting})
    assert all(o.alive and o.qty > 0 for o in book.orders.values())


def test_random_operations_preserve_invariants_and_conserve_quantity() -> None:
    rng = random.Random(5)
    book = OrderBook()
    next_id = 1
    for _ in range(5000):
        r = rng.random()
        if r < 0.5:
            side = rng.choice((BID, ASK))
            qty = rng.randint(1, 20)
            fills = book.add_limit(next_id, rng.randint(1, 5), side, 100 + rng.randint(-5, 5), qty)
            resting = book.orders[next_id].qty if next_id in book.orders else 0
            assert sum(f.qty for f in fills) + resting == qty
            next_id += 1
        elif r < 0.65:
            book.add_market(next_id, rng.randint(1, 5), rng.choice((BID, ASK)), rng.randint(1, 30))
            next_id += 1
        elif r < 0.85 and book.orders:
            book.cancel(rng.choice(list(book.orders)))
        elif book.orders:
            oid = rng.choice(list(book.orders))
            price = 100 + rng.randint(-5, 5) if rng.random() < 0.3 else None
            book.modify(oid, rng.randint(1, 25), new_price=price)
        _check_invariants(book)


def test_self_trade_prevention_cancels_the_resting_order_instead_of_trading() -> None:
    book = OrderBook()
    book.add_limit(1, 10, ASK, 100, 5)  # trader 10 rests an ask
    book.add_limit(2, 11, ASK, 100, 5)  # another trader queues behind it
    fills = book.add_market(3, 10, BID, 5)  # trader 10 buys: must skip its own order
    assert [(f.maker_order_id, f.qty) for f in fills] == [(2, 5)]
    assert [o.order_id for o in book.stp_cancels] == [1]
    assert 1 not in book.orders and book.snapshot()[1] == 0
