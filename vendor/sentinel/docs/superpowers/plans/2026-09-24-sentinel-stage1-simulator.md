# Sentinel Stage 1: Market Simulator and Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit rule:** every commit message ends with the line `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` (use a heredoc, as in Task 0).

**Goal:** Generate 1.6M+ labeled limit-order-book events (normal flow plus injected spoofing, layering and wash trading at three difficulty levels) with trader-window labels, and validate the simulator's statistics against LOBSTER samples.

**Architecture:** A price-time-priority `OrderBook` is wrapped by an `Exchange` that logs every event with a post-event book snapshot and tags manipulation orders with a hidden episode id (kept out of the event log). Agent classes (market makers, noise traders, momentum traders) generate background flow from Poisson clocks; noise traders lean toward the heavier side of the book, which is what makes a fake large order move the price. Scenario episodes are scripted timelines that run through the same exchange. A runner simulates each instrument independently, merges by timestamp, and a label builder turns hidden episode tags into `(trader, instrument, window)` labels.

**Tech Stack:** Python 3.12, numpy, pandas, pyarrow, pytest. Standard-library `random` for scalar draws (faster than numpy scalars and deterministic given a seed).

Spec: `docs/superpowers/specs/2026-09-24-sentinel-simulator-design.md`

**Deliberate refinements of the spec (decided while planning):**
- Snapshot columns are `best_bid, best_ask, bid_qty1, ask_qty1, bid_depth5, ask_depth5` (total quantity in the top five levels), not per-level columns.
- Default window is 10 s and default size is 400k events per instrument (4 instruments = 1.6M). The simulated market runs at ~330 events/s per instrument, so 10 s windows hold enough events per trader and manipulation holds of 0.2-10 s span tens to thousands of events.
- Spoof and layer orders sit one or more ticks *behind* the touch, so the spoofer's own genuine trade doesn't match its own spoof order.

---

## File Structure

```
pyproject.toml
.gitignore
src/sentinel/__init__.py
src/sentinel/sim/__init__.py
src/sentinel/sim/types.py        constants: event codes, sides, id strides
src/sentinel/sim/book.py         OrderBook, Order, Fill (matching only, no logging)
src/sentinel/sim/exchange.py     Exchange (book + event log + episodes), Episode
src/sentinel/sim/agents.py       MarketMaker, NoiseTrader, MomentumTrader, lognormal_qty
src/sentinel/sim/scenarios.py    DIFFICULTY table, ScriptedEpisode, spawn_episode
src/sentinel/sim/runner.py       SimConfig, SimResult, simulate_instrument, simulate
src/sentinel/sim/labels.py       build_labels, write_dataset
src/sentinel/sim/validate.py     LOBSTER comparison statistics
scripts/generate_data.py         CLI: generate and write the dataset
scripts/lobster_compare.py       CLI: write docs/lobster_comparison.md
tests/conftest.py                shared fixtures
tests/test_book.py
tests/test_exchange.py
tests/test_agents.py
tests/test_scenarios.py
tests/test_runner.py
tests/test_labels.py
tests/test_validate.py
tests/test_scale.py              marked slow
```

---

### Task 0: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/sentinel/__init__.py`, `src/sentinel/sim/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "sentinel"
version = "0.1.0"
description = "Trade surveillance engine for spoofing, layering and wash trading"
requires-python = ">=3.11"
dependencies = ["numpy>=2.0", "pandas>=2.2", "pyarrow>=15"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
markers = ["slow: long-running scale tests, run with `pytest -m slow`"]
addopts = "-m 'not slow'"
```

- [ ] **Step 2: Write `.gitignore`**

```
data/
__pycache__/
*.egg-info/
.pytest_cache/
.venv/
```

- [ ] **Step 3: Create the empty package files**

`src/sentinel/__init__.py`:
```python
"""Sentinel: trade surveillance for spoofing, layering and wash trading."""
```
`src/sentinel/sim/__init__.py`:
```python
"""Limit order book simulator with labeled manipulation scenarios."""
```
`tests/__init__.py`: empty file.

- [ ] **Step 4: Install and check pytest runs**

Run: `python -m pip install -e ".[dev]"` then `python -m pytest`
Expected: install succeeds; pytest reports `no tests ran`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "$(cat <<'EOF'
chore: scaffold sentinel package

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1: Constants and the matching engine (`OrderBook`)

**Concept to explain to the user:** a limit order book holds resting buy orders (bids) and sell orders (asks). An incoming order that reaches the other side's best price *matches*. Ties at one price are broken by arrival time (price-time priority), so cancelling and re-entering costs you your place in the queue.

**Files:**
- Create: `src/sentinel/sim/types.py`, `src/sentinel/sim/book.py`
- Test: `tests/test_book.py`

- [ ] **Step 1: Write `types.py`**

```python
"""Shared constants for the simulator: event codes, sides and id strides."""
from __future__ import annotations

NEW, CANCEL, MODIFY, EXEC = 0, 1, 2, 3
EVENT_TYPES = ("NEW", "CANCEL", "MODIFY", "EXEC")

BID, ASK = 1, -1  # side of an order; -side is the opposite side

NS_PER_SEC = 1_000_000_000
NO_QUOTE = 0  # best price reported when a side of the book is empty
NO_EPISODE = -1  # episode tag for normal (non-manipulation) orders
DEPTH_LEVELS = 5  # number of price levels summed into the depth snapshot

ORDER_ID_STRIDE = 1_000_000_000  # order ids are index * stride + counter
TRADER_ID_STRIDE = 1_000  # trader ids are index * stride + local id
EPISODE_ID_STRIDE = 1_000_000  # episode ids are index * stride + counter
```

- [ ] **Step 2: Write the failing tests**

`tests/test_book.py`:
```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_book.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.sim.book'`

- [ ] **Step 4: Write `book.py`**

```python
"""Price-time-priority limit order book (matching only, no logging).

Data structures, and why:
* ``_prices[side]``: sorted list of live price levels. Levels are few (tens),
  so bisect insertion is cheap and best-price lookup is O(1).
* ``_levels[side][price]``: deque of orders in arrival order (the queue).
* Cancels are lazy: a cancelled order is flagged ``alive=False`` and skipped
  when it reaches the front of its queue, which makes cancel O(1) instead of
  O(queue length). Per-level live quantity and live count are kept exactly,
  so depth queries never see dead orders.
"""
from __future__ import annotations

from bisect import bisect_left, insort
from collections import deque
from dataclasses import dataclass

from .types import ASK, BID, DEPTH_LEVELS, NO_QUOTE

# (best_bid, best_ask, bid_qty1, ask_qty1, bid_depth5, ask_depth5)
Snapshot = tuple[int, int, int, int, int, int]

_MAX_PRICE = 2**62  # a market buy accepts any ask up to this price


@dataclass(slots=True)
class Order:
    """A resting limit order. ``qty`` is the remaining (unfilled) quantity."""

    order_id: int
    trader_id: int
    side: int
    price: int
    qty: int
    alive: bool = True


@dataclass(frozen=True, slots=True)
class Fill:
    """One match between a resting (maker) order and an incoming (taker) order."""

    price: int
    qty: int
    maker_order_id: int
    maker_trader_id: int
    taker_order_id: int
    taker_trader_id: int
    taker_side: int


class OrderBook:
    """A single-instrument limit order book with price-time priority.

    Prices are integer ticks and quantities are integer shares, so all
    arithmetic is exact.
    """

    def __init__(self) -> None:
        self.orders: dict[int, Order] = {}
        self._prices: dict[int, list[int]] = {BID: [], ASK: []}
        self._levels: dict[int, dict[int, deque[Order]]] = {BID: {}, ASK: {}}
        self._qty: dict[int, dict[int, int]] = {BID: {}, ASK: {}}
        self._n: dict[int, dict[int, int]] = {BID: {}, ASK: {}}

    # -- public API ---------------------------------------------------------

    def add_limit(self, order_id: int, trader_id: int, side: int, price: int, qty: int) -> list[Fill]:
        """Submit a limit order: match what crosses, rest the remainder."""
        if qty <= 0 or price <= 0:
            raise ValueError("limit orders need positive price and qty")
        fills, remaining = self._match(order_id, trader_id, side, price, qty)
        if remaining > 0:
            self._rest(Order(order_id, trader_id, side, price, remaining))
        return fills

    def add_market(self, order_id: int, trader_id: int, side: int, qty: int) -> list[Fill]:
        """Submit a market order: match at any price; the unfilled remainder is dropped."""
        if qty <= 0:
            raise ValueError("market orders need positive qty")
        limit = _MAX_PRICE if side == BID else 0
        fills, _ = self._match(order_id, trader_id, side, limit, qty)
        return fills

    def cancel(self, order_id: int) -> bool:
        """Cancel a live order. Returns False if it is unknown or already filled/cancelled."""
        order = self.orders.get(order_id)
        if order is None:
            return False
        self._unrest(order)
        return True

    def modify(self, order_id: int, new_qty: int, new_price: int | None = None) -> list[Fill] | None:
        """Change an order's quantity and/or price.

        Reducing quantity at the same price keeps queue priority (the order
        keeps its place). Anything else (a size increase or a price change) is
        cancel-and-replace, so the order goes to the back of the queue and may
        cross the spread. Returns None if the order is not live.
        """
        order = self.orders.get(order_id)
        if order is None:
            return None
        if new_qty <= 0:
            raise ValueError("modify needs positive qty; use cancel instead")
        price = order.price if new_price is None else new_price
        if price == order.price and new_qty <= order.qty:
            self._qty[order.side][order.price] -= order.qty - new_qty
            order.qty = new_qty
            return []
        trader_id, side = order.trader_id, order.side
        self._unrest(order)
        return self.add_limit(order_id, trader_id, side, price, new_qty)

    def snapshot(self) -> Snapshot:
        """Top of book and five-level depth. Empty sides report NO_QUOTE / 0."""
        bp, ap = self._prices[BID], self._prices[ASK]
        bq, aq = self._qty[BID], self._qty[ASK]
        best_bid = bp[-1] if bp else NO_QUOTE
        best_ask = ap[0] if ap else NO_QUOTE
        bid_qty1 = bq[best_bid] if bp else 0
        ask_qty1 = aq[best_ask] if ap else 0
        bid_depth = sum(bq[p] for p in bp[-DEPTH_LEVELS:])
        ask_depth = sum(aq[p] for p in ap[:DEPTH_LEVELS])
        return best_bid, best_ask, bid_qty1, ask_qty1, bid_depth, ask_depth

    def total_resting(self, side: int) -> int:
        """Total live quantity on one side (used by invariant tests)."""
        return sum(self._qty[side].values())

    def n_levels(self, side: int) -> int:
        """Number of live price levels on one side."""
        return len(self._prices[side])

    # -- internals ----------------------------------------------------------

    def _match(self, order_id: int, trader_id: int, side: int, limit: int, qty: int) -> tuple[list[Fill], int]:
        fills: list[Fill] = []
        opp = -side
        prices = self._prices[opp]
        levels = self._levels[opp]
        level_qty = self._qty[opp]
        level_n = self._n[opp]
        while qty > 0 and prices:
            best = prices[0] if opp == ASK else prices[-1]
            if (side == BID and best > limit) or (side == ASK and best < limit):
                break
            queue = levels[best]
            while qty > 0 and queue:
                maker = queue[0]
                if not maker.alive:
                    queue.popleft()
                    continue
                traded = min(qty, maker.qty)
                maker.qty -= traded
                level_qty[best] -= traded
                qty -= traded
                fills.append(Fill(best, traded, maker.order_id, maker.trader_id, order_id, trader_id, side))
                if maker.qty == 0:
                    maker.alive = False
                    queue.popleft()
                    del self.orders[maker.order_id]
                    level_n[best] -= 1
            if level_n[best] == 0:
                self._drop_level(opp, best)
        return fills, qty

    def _rest(self, order: Order) -> None:
        side, price = order.side, order.price
        queue = self._levels[side].get(price)
        if queue is None:
            self._levels[side][price] = deque([order])
            self._qty[side][price] = order.qty
            self._n[side][price] = 1
            insort(self._prices[side], price)
        else:
            queue.append(order)
            self._qty[side][price] += order.qty
            self._n[side][price] += 1
        self.orders[order.order_id] = order

    def _unrest(self, order: Order) -> None:
        order.alive = False
        del self.orders[order.order_id]
        side, price = order.side, order.price
        self._qty[side][price] -= order.qty
        self._n[side][price] -= 1
        if self._n[side][price] == 0:
            self._drop_level(side, price)

    def _drop_level(self, side: int, price: int) -> None:
        del self._levels[side][price]
        del self._qty[side][price]
        del self._n[side][price]
        prices = self._prices[side]
        del prices[bisect_left(prices, price)]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_book.py -v`
Expected: 12 passed

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/sim/types.py src/sentinel/sim/book.py tests/test_book.py
git commit -m "$(cat <<'EOF'
feat(sim): price-time-priority order book with lazy cancels

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `Exchange` (event log, snapshots, episode tags)

**Concept to explain:** every action becomes one or more log rows. A fill produces *two* EXEC rows (one per party) so each trader's own history is complete. Each row carries the book snapshot *after* the operation, and each timestamp is forced strictly increasing so event order is exact.

**Files:**
- Create: `src/sentinel/sim/exchange.py`
- Test: `tests/test_exchange.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_exchange.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_exchange.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.sim.exchange'`

- [ ] **Step 3: Write `exchange.py`**

```python
"""Exchange: an OrderBook plus the event log, clock discipline and episode bookkeeping.

Everything that happens in a simulated market goes through this class, so the
log is complete by construction. The manipulation ground truth (which orders
belong to an injected episode) is stored *separately* from the event columns
and returned by ``to_frames`` as a parallel array, so detectors that read the
event log can never see the labels.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .book import Fill, OrderBook, Snapshot
from .types import (
    ASK,
    BID,
    CANCEL,
    EPISODE_ID_STRIDE,
    EVENT_TYPES,
    EXEC,
    MODIFY,
    NEW,
    NO_EPISODE,
    ORDER_ID_STRIDE,
)

INITIAL_PRICE = 10_000  # starting reference price, in ticks
_FLUSH_ROWS = 50_000  # rows buffered as Python tuples before packing into numpy

_COLUMNS = (
    "ts", "event_type", "order_id", "trader_id", "side", "price", "qty", "episode",
    "best_bid", "best_ask", "bid_qty1", "ask_qty1", "bid_depth5", "ask_depth5",
)


@dataclass
class Episode:
    """Ground truth for one injected manipulation scenario."""

    episode_id: int
    kind: str
    difficulty: str
    instrument: str
    trader_ids: tuple[int, ...]
    side: int  # side of the spoof/layer orders; 0 for wash trading
    t_start: int
    t_end: int = -1


class Exchange:
    """One instrument's market: order book, event log and episode registry."""

    def __init__(self, instrument: str, index: int, initial_price: int = INITIAL_PRICE) -> None:
        self.instrument = instrument
        self.index = index
        self.book = OrderBook()
        self.last_price = initial_price
        self.last_ts = -1
        self.snap: Snapshot = self.book.snapshot()
        self.episodes: list[Episode] = []
        self.busy: set[int] = set()
        self.active_episodes = 0
        self._rows: list[tuple[int, ...]] = []
        self._chunks: list[np.ndarray] = []
        self._flushed = 0
        self._next_order = index * ORDER_ID_STRIDE + 1
        self._order_episode: dict[int, int] = {}

    # -- market state used by agents ---------------------------------------

    @property
    def n_events(self) -> int:
        """Number of rows logged so far."""
        return self._flushed + len(self._rows)

    def mid(self) -> float:
        """Mid-price; falls back to the last traded price if a side is empty."""
        best_bid, best_ask = self.snap[0], self.snap[1]
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return float(self.last_price)

    def best(self, side: int) -> int:
        """Best price on ``side`` (0 if that side is empty)."""
        return self.snap[0] if side == BID else self.snap[1]

    def depth5(self, side: int) -> int:
        """Total quantity in the top five levels of ``side``."""
        return self.snap[4] if side == BID else self.snap[5]

    def imbalance(self) -> float:
        """(bid depth - ask depth) / (bid depth + ask depth), in [-1, 1]."""
        bid_depth, ask_depth = self.snap[4], self.snap[5]
        total = bid_depth + ask_depth
        return (bid_depth - ask_depth) / total if total else 0.0

    def is_live(self, order_id: int) -> bool:
        """True if the order is still resting in the book."""
        return order_id in self.book.orders

    # -- actions ------------------------------------------------------------

    def new_limit(self, trader_id: int, side: int, price: int, qty: int, ts: int,
                  episode: int = NO_EPISODE) -> int:
        """Submit a limit order; returns its order id."""
        order_id = self._alloc(episode)
        fills = self.book.add_limit(order_id, trader_id, side, price, qty)
        self._record(ts, NEW, order_id, trader_id, side, price, qty, episode, fills)
        return order_id

    def new_market(self, trader_id: int, side: int, qty: int, ts: int,
                   episode: int = NO_EPISODE) -> int:
        """Submit a market order (logged with price 0); returns its order id."""
        order_id = self._alloc(episode)
        fills = self.book.add_market(order_id, trader_id, side, qty)
        self._record(ts, NEW, order_id, trader_id, side, 0, qty, episode, fills)
        return order_id

    def cancel(self, order_id: int, ts: int) -> bool:
        """Cancel an order. A rejected cancel (order not live) logs nothing."""
        order = self.book.orders.get(order_id)
        if order is None:
            return False
        trader_id, side, price, qty = order.trader_id, order.side, order.price, order.qty
        self.book.cancel(order_id)
        episode = self._order_episode.get(order_id, NO_EPISODE)
        self._record(ts, CANCEL, order_id, trader_id, side, price, qty, episode, ())
        return True

    def modify(self, order_id: int, new_qty: int, ts: int, new_price: int | None = None) -> bool:
        """Modify an order; returns False (and logs nothing) if it is not live."""
        order = self.book.orders.get(order_id)
        if order is None:
            return False
        trader_id, side = order.trader_id, order.side
        price = order.price if new_price is None else new_price
        fills = self.book.modify(order_id, new_qty, new_price)
        episode = self._order_episode.get(order_id, NO_EPISODE)
        self._record(ts, MODIFY, order_id, trader_id, side, price, new_qty, episode, fills or ())
        return True

    # -- episodes -----------------------------------------------------------

    def start_episode(self, kind: str, difficulty: str, traders: list[int], side: int, ts: int) -> Episode:
        """Register a manipulation episode and mark its traders busy."""
        episode = Episode(self.index * EPISODE_ID_STRIDE + len(self.episodes), kind, difficulty,
                          self.instrument, tuple(traders), side, ts)
        self.episodes.append(episode)
        self.busy.update(traders)
        self.active_episodes += 1
        return episode

    def end_episode(self, episode: Episode) -> None:
        """Close an episode at the current clock and free its traders."""
        episode.t_end = self.last_ts
        self.busy.difference_update(episode.trader_ids)
        self.active_episodes -= 1

    # -- export -------------------------------------------------------------

    def to_frames(self) -> tuple[pd.DataFrame, np.ndarray]:
        """Return (event log, per-row episode tags). Tags are ground truth, not features."""
        self._flush()
        arr = np.concatenate(self._chunks) if self._chunks else np.empty((0, len(_COLUMNS)), dtype=np.int64)
        df = pd.DataFrame(arr, columns=list(_COLUMNS))
        tags = df.pop("episode").to_numpy()
        df["event_type"] = pd.Categorical.from_codes(df["event_type"].to_numpy(), categories=list(EVENT_TYPES))
        df.insert(1, "instrument", pd.Categorical([self.instrument] * len(df)))
        return df, tags

    # -- internals ----------------------------------------------------------

    def _alloc(self, episode: int) -> int:
        order_id = self._next_order
        self._next_order += 1
        if episode != NO_EPISODE:
            self._order_episode[order_id] = episode
        return order_id

    def _stamp(self, ts: int) -> int:
        ts = ts if ts > self.last_ts else self.last_ts + 1
        self.last_ts = ts
        return ts

    def _emit(self, ts: int, etype: int, order_id: int, trader_id: int, side: int, price: int,
              qty: int, episode: int, snap: Snapshot) -> None:
        self._rows.append((self._stamp(ts), etype, order_id, trader_id, side, price, qty, episode) + snap)
        if len(self._rows) >= _FLUSH_ROWS:
            self._flush()

    def _record(self, ts: int, etype: int, order_id: int, trader_id: int, side: int, price: int,
                qty: int, episode: int, fills: list[Fill] | tuple[()]) -> None:
        if fills:
            self.last_price = fills[-1].price
        snap = self.book.snapshot()
        self.snap = snap
        self._emit(ts, etype, order_id, trader_id, side, price, qty, episode, snap)
        for f in fills:
            self._emit(ts, EXEC, f.maker_order_id, f.maker_trader_id, -f.taker_side, f.price, f.qty,
                       self._order_episode.get(f.maker_order_id, NO_EPISODE), snap)
            self._emit(ts, EXEC, f.taker_order_id, f.taker_trader_id, f.taker_side, f.price, f.qty,
                       episode, snap)

    def _flush(self) -> None:
        if self._rows:
            self._chunks.append(np.array(self._rows, dtype=np.int64))
            self._flushed += len(self._rows)
            self._rows = []
```

Note: `_flushed` counts flushed rows; `n_events` adds the buffered rows, so it stays correct across flushes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_exchange.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/sim/exchange.py tests/test_exchange.py
git commit -m "$(cat <<'EOF'
feat(sim): exchange with event log, snapshots and hidden episode tags

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Background agents

**Concept to explain:** market makers earn the bid-ask spread by quoting both sides and constantly refreshing quotes, so they cancel a very high fraction of their orders *legitimately*. That is the classic false-positive trap for naive cancel-rate rules. Noise traders lean toward the heavier side of the book (`IMBALANCE_SENSITIVITY`). That reaction is real in markets and is exactly the channel through which a spoofer's fake size moves the price.

**Files:**
- Create: `src/sentinel/sim/agents.py`, `tests/conftest.py`
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write the shared fixture**

`tests/conftest.py`:
```python
"""Shared fixtures."""
from __future__ import annotations

import random

import pytest

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
```

- [ ] **Step 2: Write the failing tests**

`tests/test_agents.py`:
```python
"""Tests for the background agents."""
from __future__ import annotations

import random

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
    ex.new_limit(500, BID, 9_990, 100_000, ts=1)  # huge bid stack
    ex.new_limit(501, ASK, 10_010, 100, ts=2)
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_agents.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.sim.agents'`

- [ ] **Step 4: Write `agents.py`**

```python
"""Background agents. Each agent acts when its Poisson clock fires.

``act`` performs the agent's actions on the exchange and returns the delay in
nanoseconds until the agent's next action (None means "never again").
"""
from __future__ import annotations

import math
import random
from typing import Protocol

from .exchange import Exchange
from .types import ASK, BID, NS_PER_SEC

P_CANCEL = 0.30  # noise trader: chance an action is a cancel (if it has live orders)
P_MODIFY = 0.05  # noise trader: chance an action is a size reduction
P_MARKET = 0.20  # noise trader: chance a new order is a market order
MAX_LIVE = 20  # noise trader: cap on simultaneously resting orders
IMBALANCE_SENSITIVITY = 0.5  # how strongly noise flow follows book imbalance


class Agent(Protocol):
    """Anything the runner can schedule."""

    def act(self, ex: Exchange, now: int, rng: random.Random) -> int | None:
        """Act at time ``now``; return ns until the next action, or None to retire."""
        ...


def lognormal_qty(rng: random.Random, median: float, sigma: float) -> int:
    """Heavy-tailed order size: lognormal with the given median, at least 1 share."""
    return max(1, int(rng.lognormvariate(math.log(median), sigma)))


def _delay(rng: random.Random, rate: float) -> int:
    """Exponential inter-arrival time (a Poisson process), in ns."""
    return max(1, int(rng.expovariate(rate) * NS_PER_SEC))


class MarketMaker:
    """Quotes both sides around the mid and refreshes every quote each time it acts.

    Because it cancels its old quotes on every refresh, its cancel rate is
    close to 100% and completely legitimate.
    """

    def __init__(self, trader_id: int, rate: float) -> None:
        self.trader_id = trader_id
        self.rate = rate
        self._quotes: list[int] = []

    def act(self, ex: Exchange, now: int, rng: random.Random) -> int | None:
        for order_id in self._quotes:
            ex.cancel(order_id, now)  # a no-op if the quote was already filled
        mid = ex.mid()
        half = rng.choice((1, 1, 2, 2, 3))
        bid = max(1, math.floor(mid) - half)
        ask = math.ceil(mid) + half
        self._quotes = [
            ex.new_limit(self.trader_id, BID, bid, lognormal_qty(rng, 250, 0.5), now),
            ex.new_limit(self.trader_id, ASK, ask, lognormal_qty(rng, 250, 0.5), now),
        ]
        return _delay(rng, self.rate)


class NoiseTrader:
    """Retail-like flow: mostly passive limit orders, some cancels, edits and market orders."""

    def __init__(self, trader_id: int, rate: float) -> None:
        self.trader_id = trader_id
        self.rate = rate
        self._live: list[int] = []

    def act(self, ex: Exchange, now: int, rng: random.Random) -> int | None:
        live = self._live = [oid for oid in self._live if ex.is_live(oid)]
        r = rng.random()
        if live and (r < P_CANCEL or len(live) >= MAX_LIVE):
            ex.cancel(live.pop(rng.randrange(len(live))), now)
        elif live and r < P_CANCEL + P_MODIFY:
            order_id = rng.choice(live)
            qty = ex.book.orders[order_id].qty
            if qty > 1:
                ex.modify(order_id, max(1, qty // 2), now)
        else:
            self._place(ex, now, rng)
        return _delay(rng, self.rate)

    def _place(self, ex: Exchange, now: int, rng: random.Random) -> None:
        p_buy = min(0.95, max(0.05, 0.5 + IMBALANCE_SENSITIVITY * ex.imbalance()))
        side = BID if rng.random() < p_buy else ASK
        if rng.random() < P_MARKET:
            ex.new_market(self.trader_id, side, lognormal_qty(rng, 60, 0.8), now)
            return
        mid = ex.mid()
        offset = 1 + int(rng.expovariate(0.35))
        price = math.floor(mid) - offset if side == BID else math.ceil(mid) + offset
        order_id = ex.new_limit(self.trader_id, side, max(1, price), lognormal_qty(rng, 100, 0.8), now)
        self._live.append(order_id)


class MomentumTrader:
    """Trades in bursts: 3-8 market orders on one side, 50-300 ms apart, then goes quiet."""

    def __init__(self, trader_id: int, rate: float) -> None:
        self.trader_id = trader_id
        self.rate = rate
        self._left = 0
        self._side = BID

    def act(self, ex: Exchange, now: int, rng: random.Random) -> int | None:
        if self._left == 0:
            self._left = rng.randint(3, 8)
            self._side = rng.choice((BID, ASK))
        ex.new_market(self.trader_id, self._side, lognormal_qty(rng, 80, 0.6), now)
        self._left -= 1
        if self._left > 0:
            return int(rng.uniform(0.05, 0.3) * NS_PER_SEC)
        return _delay(rng, self.rate)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_agents.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/sim/agents.py tests/conftest.py tests/test_agents.py
git commit -m "$(cat <<'EOF'
feat(sim): market maker, noise and momentum agents

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Manipulation scenarios and the frozen difficulty table

**Concepts to explain:**
- **Spoofing:** place a large order you don't intend to fill on one side, to fake demand or supply, trade genuinely on the other side while the price moves, then cancel the large order.
- **Layering:** spoofing spread across several price levels.
- **Wash trading:** two accounts under one owner trade with each other to fake volume, with roughly no net position change.

Each scenario is a scripted timeline of actions `(offset, function)` executed through the same exchange as normal flow.

**Files:**
- Create: `src/sentinel/sim/scenarios.py`
- Test: `tests/test_scenarios.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_scenarios.py`:
```python
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
    depth_before = {s: market.depth5(s) for s in (BID, ASK)}
    agent = _spawn(market, "SPOOFING", "easy")
    episode = market.episodes[-1]
    side, trader = episode.side, episode.trader_ids[0]
    _run(market, agent)
    df, tags = market.to_frames()
    mine = df[df.trader_id == trader]
    news = mine[(mine.event_type == "NEW") & (mine.side == side)]
    spoof = news.iloc[0]
    assert spoof.qty >= 3 * max(depth_before[side], MIN_DEPTH)  # easy: >= 3x same-side depth
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scenarios.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.sim.scenarios'`

- [ ] **Step 3: Write `scenarios.py`**

```python
"""Manipulation scenarios, injected through the same exchange as normal flow.

Each scenario is built as a *script*: a list of ``(offset_ns, action)`` pairs
that a ``ScriptedEpisode`` agent plays back. Manipulators are ordinary noise
traders borrowed for the duration of the episode, so their windows contain
both normal and manipulative activity, and injected orders draw sizes from the
same distributions as normal orders.

The ``DIFFICULTY`` table is part of the experimental design. It must be
frozen (committed and tagged) before any detector is built, so the data cannot
be tuned to flatter a model.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from .agents import lognormal_qty
from .exchange import Episode, Exchange
from .types import ASK, BID, NS_PER_SEC

SPOOFING, LAYERING, WASH = "SPOOFING", "LAYERING", "WASH"
KINDS = (SPOOFING, LAYERING, WASH)
DIFFICULTIES = ("easy", "medium", "hard")
ALL_COMBOS = tuple((kind, level) for kind in KINDS for level in DIFFICULTIES)

MIN_DEPTH = 500  # floor on the depth a spoof size is measured against (thin-book guard)

Action = Callable[[Exchange, int], None]


@dataclass(frozen=True)
class Difficulty:
    """Knobs that make a scenario harder to tell apart from normal trading."""

    size_mult: tuple[float, float]  # spoof/layer volume as a multiple of same-side top-5 depth
    hold_s: tuple[float, float]  # seconds the fake orders stay before cancel
    price_offset: tuple[int, int]  # ticks behind the touch for the first fake order
    n_layers: int  # orders in a layering stack
    n_dilution: int  # small normal-looking orders mixed in during the episode
    wash_legs: tuple[int, int]  # number of wash trades
    wash_gap_s: tuple[float, float]  # seconds between wash trades
    wash_qty_jitter: float  # relative size variation between wash pairs
    wash_fixed_price: bool  # True: always trade at the same tick


DIFFICULTY = MappingProxyType({
    "easy": Difficulty((3.0, 5.0), (0.2, 1.0), (1, 1), 6, 0, (10, 16), (0.05, 0.15), 0.0, True),
    "medium": Difficulty((1.5, 2.5), (1.0, 4.0), (1, 2), 4, 3, (6, 10), (0.2, 1.5), 0.25, False),
    "hard": Difficulty((0.6, 1.2), (4.0, 10.0), (1, 3), 3, 8, (3, 6), (0.5, 5.0), 0.6, False),
})


class ScriptedEpisode:
    """Plays a script of timed actions; closes its episode after the last one."""

    def __init__(self, episode: Episode, steps: list[tuple[int, Action]]) -> None:
        self.episode = episode
        self._steps = steps
        self._i = 0

    @property
    def first_delay(self) -> int:
        """Nanoseconds from spawn to the first action."""
        return self._steps[0][0]

    def act(self, ex: Exchange, now: int, rng: random.Random) -> int | None:
        _, action = self._steps[self._i]
        action(ex, now)
        self._i += 1
        if self._i == len(self._steps):
            ex.end_episode(self.episode)
            return None
        return max(1, self._steps[self._i][0] - self._steps[self._i - 1][0])


class _Plan:
    """Builder for a script, plus the shared 'dilution' behaviour."""

    def __init__(self, episode: Episode, rng: random.Random) -> None:
        self.episode = episode
        self.rng = rng
        self.steps: list[tuple[int, Action]] = []
        self.dilution: list[int] = []

    def add(self, t_seconds: float, action: Action) -> None:
        self.steps.append((int(t_seconds * NS_PER_SEC), action))

    def dilute(self, trader: int, n: int, t_lo: float, t_hi: float) -> None:
        """Schedule ``n`` small passive orders that look like ordinary noise flow."""
        for _ in range(n):
            side = self.rng.choice((BID, ASK))
            offset = 1 + int(self.rng.expovariate(0.35))
            qty = lognormal_qty(self.rng, 60, 0.8)
            self.add(self.rng.uniform(t_lo, t_hi), self._dilution_action(trader, side, offset, qty))

    def _dilution_action(self, trader: int, side: int, offset: int, qty: int) -> Action:
        def run(ex: Exchange, now: int) -> None:
            mid = ex.mid()
            price = math.floor(mid) - offset if side == BID else math.ceil(mid) + offset
            self.dilution.append(ex.new_limit(trader, side, max(1, price), qty, now, self.episode.episode_id))
        return run

    def unwind(self, t_seconds: float) -> None:
        """Cancel about half of the dilution orders still resting."""
        def run(ex: Exchange, now: int) -> None:
            for order_id in self.dilution:
                if self.rng.random() < 0.5:
                    ex.cancel(order_id, now)
        self.add(t_seconds, run)

    def finish(self) -> ScriptedEpisode:
        self.steps.sort(key=lambda step: step[0])  # stable: ties keep insertion order
        return ScriptedEpisode(self.episode, self.steps)


def _spoofing(rng: random.Random, d: Difficulty, episode: Episode) -> ScriptedEpisode:
    trader, side = episode.trader_ids[0], episode.side
    plan = _Plan(episode, rng)
    hold = rng.uniform(*d.hold_s)
    mult = rng.uniform(*d.size_mult)
    offset = rng.randint(*d.price_offset)
    real_qty = lognormal_qty(rng, 100, 0.6)
    gap = rng.uniform(0.005, 0.05)
    state: dict[str, int] = {}

    def place(ex: Exchange, now: int) -> None:
        depth = max(ex.depth5(side), MIN_DEPTH)
        base = ex.best(side) or ex.last_price - side
        state["oid"] = ex.new_limit(trader, side, max(1, base - side * offset), int(mult * depth),
                                    now, episode.episode_id)

    plan.add(0.0, place)
    plan.dilute(trader, d.n_dilution, 0.1 * hold, 0.9 * hold)
    plan.add(hold, lambda ex, now: ex.new_market(trader, -side, real_qty, now, episode.episode_id))
    plan.add(hold + gap, lambda ex, now: ex.cancel(state["oid"], now))
    plan.unwind(hold + 2 * gap)
    return plan.finish()


def _layering(rng: random.Random, d: Difficulty, episode: Episode) -> ScriptedEpisode:
    trader, side, n = episode.trader_ids[0], episode.side, d.n_layers
    plan = _Plan(episode, rng)
    hold = rng.uniform(*d.hold_s)
    mult = rng.uniform(*d.size_mult)
    offset = rng.randint(*d.price_offset)
    real_qty = lognormal_qty(rng, 100, 0.6)
    place_gap = rng.uniform(0.002, 0.02)
    cancel_gap = rng.uniform(0.002, 0.02)
    state: dict[str, int] = {}
    layer_ids: list[int] = []

    def place(ex: Exchange, now: int, level: int) -> None:
        if not layer_ids:
            state["base"] = ex.best(side) or ex.last_price - side
            state["depth"] = max(ex.depth5(side), MIN_DEPTH)
        price = state["base"] - side * (offset + level)
        qty = max(1, int(mult * state["depth"] / n))
        layer_ids.append(ex.new_limit(trader, side, max(1, price), qty, now, episode.episode_id))

    for level in range(n):
        plan.add(level * place_gap, lambda ex, now, level=level: place(ex, now, level))
    t_real = (n - 1) * place_gap + hold
    plan.dilute(trader, d.n_dilution, 0.1 * hold, t_real)
    plan.add(t_real, lambda ex, now: ex.new_market(trader, -side, real_qty, now, episode.episode_id))
    for j in range(n):
        plan.add(t_real + cancel_gap * (j + 1),
                 lambda ex, now, j=j: ex.cancel(layer_ids[j], now) if j < len(layer_ids) else None)
    plan.unwind(t_real + cancel_gap * (n + 2))
    return plan.finish()


def _wash(rng: random.Random, d: Difficulty, episode: Episode) -> ScriptedEpisode:
    a, b = episode.trader_ids
    plan = _Plan(episode, rng)
    n_legs = rng.randint(*d.wash_legs)
    base_qty = lognormal_qty(rng, 100, 0.8)
    state: dict[str, int] = {"done": 0, "qty": 1, "px": 0}

    def leg(ex: Exchange, now: int) -> None:
        best_bid, best_ask = ex.snap[0], ex.snap[1]
        if not (best_bid and best_ask and best_ask - best_bid >= 2):
            return  # no free tick inside the spread: skip this leg
        done = state["done"]
        seller, buyer = (a, b) if done % 2 == 0 else (b, a)
        if done % 2 == 0:  # a new pair: choose the size once so the pair nets to zero
            jitter = 1 + rng.uniform(-d.wash_qty_jitter, d.wash_qty_jitter)
            state["qty"] = max(1, int(base_qty * jitter))
        if d.wash_fixed_price:
            inside = best_bid < state["px"] < best_ask
            state["px"] = state["px"] if inside else (best_bid + best_ask) // 2
            price = state["px"]
        else:
            price = rng.randint(best_bid + 1, best_ask - 1)
        # The ask rests alone at an empty interior tick; the bid then matches it exactly.
        ex.new_limit(seller, ASK, price, state["qty"], now, episode.episode_id)
        ex.new_limit(buyer, BID, price, state["qty"], now, episode.episode_id)
        state["done"] = done + 1

    t = 0.0
    for _ in range(n_legs):
        plan.add(t, leg)
        t += rng.uniform(*d.wash_gap_s)
    plan.dilute(a, math.ceil(d.n_dilution / 2), 0.0, t)
    plan.dilute(b, d.n_dilution // 2, 0.0, t)
    plan.unwind(t + 0.01)
    return plan.finish()


_BUILDERS = {SPOOFING: _spoofing, LAYERING: _layering, WASH: _wash}


def spawn_episode(ex: Exchange, rng: random.Random, kind: str, difficulty: str,
                  pool: list[int], now: int) -> ScriptedEpisode:
    """Pick free traders from ``pool``, register the episode and return its script.

    Raises RuntimeError if too few traders are free.
    """
    needed = 2 if kind == WASH else 1
    free = [t for t in pool if t not in ex.busy]
    if len(free) < needed:
        raise RuntimeError("not enough free traders to start an episode")
    traders = rng.sample(free, needed)
    side = 0 if kind == WASH else rng.choice((BID, ASK))
    episode = ex.start_episode(kind, difficulty, traders, side, now)
    return _BUILDERS[kind](rng, DIFFICULTY[difficulty], episode)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scenarios.py -v`
Expected: 6 passed. If `test_wash_trades_stay_between_the_pair_and_net_out` fails because legs were skipped (the market fixture spread is < 2 ticks), print `market.snap` and check that the fixture book has an interior tick. Fix the fixture seed, not the assertion.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/sim/scenarios.py tests/test_scenarios.py
git commit -m "$(cat <<'EOF'
feat(sim): spoofing, layering and wash scenarios with difficulty table

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Runner (simulate instruments, merge, invariants, price impact)

**Concept to explain:** the simulator is a discrete-event loop. A heap holds `(next_time, agent)`. Pop the earliest, let the agent act, and push it back with its next time. Episodes are spawned every `events_per_instrument / episodes_per_instrument` events (count-based, so the injection rate doesn't depend on the market's speed).

**Files:**
- Create: `src/sentinel/sim/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_runner.py`:
```python
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
    assert cancel_rate(mm) > 0.7 > cancel_rate(other) * 1.5


def _mid_at(events: pd.DataFrame, ts_array: np.ndarray, t: int) -> float:
    i = int(np.searchsorted(ts_array, t, side="right")) - 1
    best_bid, best_ask = events.best_bid.iat[i], events.best_ask.iat[i]
    return (best_bid + best_ask) / 2 if best_bid and best_ask else float("nan")


def test_spoofing_moves_the_mid_in_the_intended_direction_on_average() -> None:
    """The causal claim: fake size on one side pushes the price toward that side.

    If this fails, price impact is too weak: raise IMBALANCE_SENSITIVITY in
    agents.py, and record the change and reason in the commit message. Do this
    before the difficulty table is frozen.
    """
    cfg = SimConfig(instruments=("A",), events_per_instrument=100_000, episodes_per_instrument=80,
                    combos=(("SPOOFING", "easy"),), seed=21)
    res = simulate(cfg)
    ts = res.events.ts.to_numpy()
    impacts = []
    for row in res.episodes.itertuples():
        start, end = _mid_at(res.events, ts, row.t_start), _mid_at(res.events, ts, row.t_end)
        if not (np.isnan(start) or np.isnan(end)):
            impacts.append(row.side * (end - start))
    assert len(impacts) >= 60
    assert np.mean(impacts) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.sim.runner'`

- [ ] **Step 3: Write `runner.py`**

```python
"""Discrete-event simulation runner: agents, episode spawning and merging."""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd

from .agents import Agent, MarketMaker, MomentumTrader, NoiseTrader
from .exchange import Exchange
from .scenarios import ALL_COMBOS, spawn_episode
from .types import NS_PER_SEC, TRADER_ID_STRIDE

MM_RATE = 8.0  # market-maker refreshes per second, each
NOISE_RATE = 2.0  # noise-trader actions per second, each
MOMENTUM_RATE = 0.05  # momentum bursts per second, each
_SPAWN_CUTOFF = 0.97  # stop starting episodes after this fraction of the target


@dataclass(frozen=True)
class SimConfig:
    """Everything that determines a simulation run (with the seed, fully reproducible)."""

    instruments: tuple[str, ...] = ("SYM0", "SYM1", "SYM2", "SYM3")
    events_per_instrument: int = 400_000
    seed: int = 7
    n_market_makers: int = 6
    n_momentum: int = 6
    n_noise: int = 38
    episodes_per_instrument: int = 100
    combos: tuple[tuple[str, str], ...] = ALL_COMBOS
    window_s: int = 10


@dataclass
class SimResult:
    """Simulation output. ``event_episode`` is ground truth, aligned row-for-row with ``events``."""

    events: pd.DataFrame
    event_episode: np.ndarray
    episodes: pd.DataFrame


def _combo_cycle(combos: tuple[tuple[str, str], ...], rng: random.Random) -> Iterator[tuple[str, str]]:
    """Endless stream of (kind, difficulty), each round a fresh shuffle, so counts stay balanced."""
    while True:
        order = list(combos)
        rng.shuffle(order)
        yield from order


def simulate_instrument(cfg: SimConfig, index: int) -> tuple[pd.DataFrame, np.ndarray, list]:
    """Simulate one instrument. Returns (events, episode tags, Episode objects)."""
    rng = random.Random(cfg.seed * 1_000 + index)
    ex = Exchange(cfg.instruments[index], index)
    trader_id = index * TRADER_ID_STRIDE
    agents: list[Agent] = []
    for _ in range(cfg.n_market_makers):
        agents.append(MarketMaker(trader_id, MM_RATE))
        trader_id += 1
    for _ in range(cfg.n_momentum):
        agents.append(MomentumTrader(trader_id, MOMENTUM_RATE))
        trader_id += 1
    noise_pool: list[int] = []
    for _ in range(cfg.n_noise):
        agents.append(NoiseTrader(trader_id, NOISE_RATE))
        noise_pool.append(trader_id)
        trader_id += 1

    heap: list[tuple[int, int, Agent]] = []
    seq = 0
    for agent in agents:
        heapq.heappush(heap, (int(rng.random() * NS_PER_SEC), seq, agent))
        seq += 1

    target = cfg.events_per_instrument
    spawn_every = max(1, target // cfg.episodes_per_instrument)
    next_spawn = spawn_every
    spawn_stop = int(target * _SPAWN_CUTOFF)
    combos = _combo_cycle(cfg.combos, rng)

    while ex.n_events < target or ex.active_episodes:
        now, _, agent = heapq.heappop(heap)
        delay = agent.act(ex, now, rng)
        if delay is not None:
            heapq.heappush(heap, (now + delay, seq, agent))
            seq += 1
        if next_spawn <= ex.n_events < spawn_stop:
            kind, level = next(combos)
            try:
                episode_agent = spawn_episode(ex, rng, kind, level, noise_pool, now)
            except RuntimeError:
                continue  # no free traders right now; retry on the next iteration
            heapq.heappush(heap, (now + max(1, episode_agent.first_delay), seq, episode_agent))
            seq += 1
            next_spawn += spawn_every

    events, tags = ex.to_frames()
    return events, tags, ex.episodes


def _episodes_frame(episodes: list) -> pd.DataFrame:
    rows = [
        (e.episode_id, e.kind, e.difficulty, e.instrument, e.trader_ids[0],
         e.trader_ids[1] if len(e.trader_ids) > 1 else -1, e.side, e.t_start, e.t_end)
        for e in episodes
    ]
    columns = ["episode_id", "kind", "difficulty", "instrument", "trader_a", "trader_b",
               "side", "t_start", "t_end"]
    return pd.DataFrame(rows, columns=columns)


def simulate(cfg: SimConfig) -> SimResult:
    """Simulate every instrument and merge into one time-ordered event log."""
    parts = [simulate_instrument(cfg, i) for i in range(len(cfg.instruments))]
    events = pd.concat([p[0] for p in parts], ignore_index=True)
    events["instrument"] = events["instrument"].astype("category")
    tags = np.concatenate([p[1] for p in parts])
    instrument_index = np.concatenate([np.full(len(p[0]), i) for i, p in enumerate(parts)])
    order = np.lexsort((instrument_index, events["ts"].to_numpy()))
    events = events.iloc[order].reset_index(drop=True)
    episodes = _episodes_frame([e for p in parts for e in p[2]])
    return SimResult(events, tags[order], episodes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runner.py -v`
Expected: 6 passed.

**If `test_spoofing_moves_the_mid...` fails** (mean impact <= 0): print the mean and standard error first. If the effect is real but tiny, raise `IMBALANCE_SENSITIVITY` in `agents.py` (try 0.8) and re-run. If it is still negative, stop and debug (are spoof orders really adding to `bid_depth5`? does `imbalance()` see them?). Do not weaken the assertion.

**If `test_market_makers_cancel_far_more...` fails:** print both rates and adjust only the test's thresholds if the direction is right.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/sim/runner.py tests/test_runner.py
git commit -m "$(cat <<'EOF'
feat(sim): discrete-event runner with episode spawning and merge

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Labels and dataset writer

**Concept to explain:** the event log says what happened. The label table says, for each `(trader, instrument, 10 s window)` in which the trader had any activity, whether that window contains manipulation and of what type. A window's label comes from the hidden episode tags on that trader's events in the window.

**Files:**
- Create: `src/sentinel/sim/labels.py`, `scripts/generate_data.py`
- Test: `tests/test_labels.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_labels.py`:
```python
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
    assert out.loc[(7, 0), "n_events"] == 3 and out.loc[(7, 0), "window_start_ns"] == 0


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_labels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.sim.labels'`

- [ ] **Step 3: Write `labels.py`**

```python
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
```

Note: `groupby(...)["episode_id"].min()` is used instead of a Python-lambda mode. It's deterministic, and vectorized so it is fast at 1.6M rows.

- [ ] **Step 4: Write `scripts/generate_data.py`**

```python
"""Generate the Sentinel dataset: python scripts/generate_data.py --out data"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from sentinel.sim.labels import write_dataset
from sentinel.sim.runner import SimConfig, simulate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--events", type=int, default=400_000, help="events per instrument")
    args = parser.parse_args()

    cfg = SimConfig(seed=args.seed, events_per_instrument=args.events)
    start = time.perf_counter()
    result = simulate(cfg)
    sim_seconds = time.perf_counter() - start
    labels = write_dataset(result, args.out, cfg.window_s)

    positives = labels[labels.label != "NORMAL"]
    print(f"events: {len(result.events):,} in {sim_seconds:.1f}s "
          f"({len(result.events) / sim_seconds:,.0f} events/s generated)")
    print(f"trader-windows: {len(labels):,}, manipulation windows: {len(positives):,} "
          f"({100 * len(positives) / len(labels):.2f}%)")
    print(positives.groupby(["label", "difficulty"]).size().unstack(fill_value=0))
    print(f"episodes: {len(result.episodes)}; written to {args.out}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_labels.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/sim/labels.py scripts/generate_data.py tests/test_labels.py
git commit -m "$(cat <<'EOF'
feat(sim): trader-window labels and dataset writer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Scale test, generate the real dataset, freeze the design

**Files:**
- Create: `tests/test_scale.py`

- [ ] **Step 1: Write the scale test**

`tests/test_scale.py`:
```python
"""Scale test: the default configuration produces 1M+ events within a time budget."""
from __future__ import annotations

import time

import pytest

from sentinel.sim.runner import SimConfig, simulate


@pytest.mark.slow
def test_default_config_generates_over_a_million_events_in_budget() -> None:
    start = time.perf_counter()
    res = simulate(SimConfig())
    seconds = time.perf_counter() - start
    print(f"\n{len(res.events):,} events in {seconds:.1f}s")
    assert len(res.events) >= 1_000_000
    assert seconds < 600
```

- [ ] **Step 2: Run the scale test**

Run: `python -m pytest -m slow -s tests/test_scale.py`
Expected: PASS, printing the event count and seconds. If it exceeds the budget, profile before changing anything: `python -m cProfile -s cumtime -m pytest -m slow tests/test_scale.py`. Report the profile to the user and decide together.

- [ ] **Step 3: Generate the dataset and inspect it**

Run: `python scripts/generate_data.py --out data`
Expected: prints total events (>= 1.6M), the manipulation-window percentage, and a `label x difficulty` count table.

Check these and **report the actual numbers to the user**:
- Manipulation windows are roughly 1-4% of trader-windows.
- Every one of the 9 label/difficulty cells has at least ~30 windows. If some cell is under 30, raise `episodes_per_instrument` (or generate extra seeds later) and record the reason.
- The wash cells are not much smaller than the others (skipped legs on tight spreads would show up here).

- [ ] **Step 4: Sanity-check for trivially separable classes (spec risk 2)**

Run:
```bash
python - <<'EOF'
import pandas as pd
ev = pd.read_parquet("data/events.parquet")
lab = pd.read_parquet("data/labels.parquet")
ev["window"] = ev.ts // 10_000_000_000
new = ev[ev.event_type == "NEW"].merge(lab[["trader_id", "instrument", "window", "label"]],
                                       on=["trader_id", "instrument", "window"])
new["is_manip"] = new.label != "NORMAL"
print(new.groupby("is_manip").qty.describe()[["count", "mean", "50%", "max"]])
print(new.groupby("is_manip").apply(lambda d: (d.price == 0).mean()).rename("market_order_share"))
EOF
```
Expected: order-size medians and market-order shares are in the same ballpark for both groups (single-order features must not separate the classes; the manipulation signal is in the *pattern*). Report what you see; if a single feature separates them cleanly, flag it to the user before proceeding.

- [ ] **Step 5: Freeze the design and commit**

The `DIFFICULTY` table and agent parameters are now final for stage 1. Tag the commit so later stages can show the data was fixed before detectors existed.

```bash
git add tests/test_scale.py
git commit -m "$(cat <<'EOF'
test(sim): scale test for 1M+ events

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
git tag -a difficulty-frozen -m "Difficulty table and simulator parameters frozen before any detector work"
```

---

### Task 8: LOBSTER comparison

**Concept to explain:** we compare the *shape* of the simulator's statistics with a real market's, not the scale. Real and simulated prices, sizes and clocks are in different units, so ratios such as p90/p50 for sizes and inter-event times are the meaningful comparison. Mismatches (for example, real liquid stocks trade at 1-tick spreads while the simulator's are wider) go into the README's limitations. We do not tune the simulator to close them.

**Files:**
- Create: `src/sentinel/sim/validate.py`, `scripts/lobster_compare.py`
- Test: `tests/test_validate.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_validate.py`:
```python
"""Tests for the LOBSTER comparison statistics, using tiny synthetic inputs."""
from __future__ import annotations

import io

import pandas as pd
import pytest

from sentinel.sim.validate import comparison_markdown, load_lobster, lobster_stats, sim_stats

MESSAGE_CSV = """34200.0,1,1,100,1000000,1
34200.5,1,2,300,1000100,-1
34201.0,3,1,100,1000000,1
34202.0,2,2,100,1000100,-1
34203.0,4,3,50,1000100,-1
34210.0,1,4,200,1000000,1
"""
BOOK_CSV = """1000100,300,1000000,100,1000200,100,999900,50
1000100,300,1000000,100,1000200,100,999900,50
1000100,200,1000000,100,1000200,100,999900,50
1000100,200,1000000,150,1000200,100,999900,50
1000100,200,1000000,150,1000200,100,999900,50
1000100,200,1000000,350,1000200,100,999900,50
"""


def test_lobster_stats_on_a_tiny_sample() -> None:
    message, book = load_lobster(io.StringIO(MESSAGE_CSV), io.StringIO(BOOK_CSV))
    stats = lobster_stats(message, book, price_tick=100)
    assert stats["cancel_to_new"] == pytest.approx(2 / 3)  # types 2 and 3 over three type-1 rows
    assert stats["spread_p50_ticks"] == pytest.approx(1.0)  # (1000100-1000000)/100
    assert stats["size_p50"] == 200  # new-order sizes 100, 300, 200
    assert stats["iet_p50_ms"] > 0


def test_sim_stats_counts_cancel_ratio_and_spread() -> None:
    events = pd.DataFrame({
        "ts": [1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000],
        "instrument": "A",
        "event_type": pd.Categorical(["NEW", "NEW", "CANCEL", "EXEC", "NEW"],
                                     categories=["NEW", "CANCEL", "MODIFY", "EXEC"]),
        "qty": [100, 200, 100, 50, 300],
        "best_bid": [99, 99, 99, 99, 99], "best_ask": [101, 101, 101, 101, 101],
        "bid_qty1": [10] * 5, "ask_qty1": [10] * 5, "bid_depth5": [50] * 5, "ask_depth5": [50] * 5,
    })
    stats = sim_stats(events)
    assert stats["cancel_to_new"] == pytest.approx(1 / 3)
    assert stats["spread_p50_ticks"] == 2.0
    assert stats["size_p50"] == 200


def test_comparison_markdown_has_a_row_per_statistic() -> None:
    table = comparison_markdown({"a": 1.0, "b": 2.0}, {"a": 1.5, "b": 2.5})
    assert table.count("\n") >= 3 and "| a |" in table and "| b |" in table
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.sim.validate'`

- [ ] **Step 3: Write `validate.py`**

```python
"""Compare simulator statistics with a real LOBSTER sample.

Assumed LOBSTER format (verify against the docs on the download page; the
loader is the only code that depends on it):
* message file, no header: time (seconds after midnight), type
  (1 new, 2 partial cancel, 3 delete, 4 visible execution, 5 hidden execution),
  order id, size, price (dollars x 10000), direction (-1 sell, 1 buy).
* orderbook file, no header, per level: ask price, ask size, bid price, bid size.
"""
from __future__ import annotations

from typing import IO

import numpy as np
import pandas as pd

_MISSING_PRICE = 9_999_999_999  # LOBSTER's sentinel for an empty book side


def load_lobster(message_csv: str | IO[str], book_csv: str | IO[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load a LOBSTER message file and orderbook file."""
    message = pd.read_csv(message_csv, header=None, names=["time", "type", "order_id", "size", "price", "direction"])
    book = pd.read_csv(book_csv, header=None)
    return message, book


def _quantiles(values: np.ndarray, prefix: str) -> dict[str, float]:
    p50, p90, p99 = np.quantile(values, [0.5, 0.9, 0.99])
    return {f"{prefix}_p50": float(p50), f"{prefix}_p90_over_p50": float(p90 / p50),
            f"{prefix}_p99_over_p50": float(p99 / p50)}


def lobster_stats(message: pd.DataFrame, book: pd.DataFrame, price_tick: int = 100) -> dict[str, float]:
    """Scale-free summary statistics of a LOBSTER day (price_tick = one cent in LOBSTER units)."""
    new = message[message["type"] == 1]
    cancels = message[message["type"].isin([2, 3])]
    quotes = message[message["type"].isin([1, 2, 3])]
    inter_event_ms = np.diff(quotes["time"].to_numpy()) * 1_000
    inter_event_ms = inter_event_ms[inter_event_ms > 0]

    ask1, bid1 = book[0].to_numpy(), book[2].to_numpy()
    valid = (ask1 < _MISSING_PRICE) & (bid1 > -_MISSING_PRICE) & (bid1 > 0)
    spread_ticks = (ask1[valid] - bid1[valid]) / price_tick
    n_levels = min(book.shape[1] // 4, 5)
    ask_sizes = sum(book[4 * k + 1].to_numpy() for k in range(n_levels))
    bid_sizes = sum(book[4 * k + 3].to_numpy() for k in range(n_levels))
    size_median = float(new["size"].median())

    stats = {
        "cancel_to_new": len(cancels) / max(1, len(new)),
        "spread_p10_ticks": float(np.quantile(spread_ticks, 0.1)),
        "spread_p50_ticks": float(np.quantile(spread_ticks, 0.5)),
        "spread_p90_ticks": float(np.quantile(spread_ticks, 0.9)),
        "depth1_over_median_size": float(np.median((book[1].to_numpy() + book[3].to_numpy()) / 2) / size_median),
        "depth5_over_median_size": float(np.median((ask_sizes + bid_sizes) / 2) / size_median),
    }
    stats.update(_quantiles(new["size"].to_numpy(dtype=float), "size"))
    stats.update(_quantiles(inter_event_ms, "iet_ms") if len(inter_event_ms) else {})
    stats["iet_p50_ms"] = stats.pop("iet_ms_p50", float("nan"))
    return stats


def sim_stats(events: pd.DataFrame) -> dict[str, float]:
    """The same statistics from the simulator's event log (EXEC rows excluded from clocks)."""
    etype = events["event_type"].astype(str)
    new = events[etype == "NEW"]
    n_cancel = int((etype == "CANCEL").sum())
    quoting = events[etype.isin(["NEW", "CANCEL", "MODIFY"])]
    gaps = quoting.groupby("instrument", observed=True)["ts"].diff().dropna().to_numpy() / 1e6
    gaps = gaps[gaps > 0]

    quoted = (events["best_bid"] > 0) & (events["best_ask"] > 0)
    spread = (events["best_ask"] - events["best_bid"])[quoted].to_numpy()
    size_median = float(new["qty"].median())

    stats = {
        "cancel_to_new": n_cancel / max(1, len(new)),
        "spread_p10_ticks": float(np.quantile(spread, 0.1)),
        "spread_p50_ticks": float(np.quantile(spread, 0.5)),
        "spread_p90_ticks": float(np.quantile(spread, 0.9)),
        "depth1_over_median_size": float(((events["bid_qty1"] + events["ask_qty1"]) / 2).median() / size_median),
        "depth5_over_median_size": float(((events["bid_depth5"] + events["ask_depth5"]) / 2).median() / size_median),
    }
    stats.update(_quantiles(new["qty"].to_numpy(dtype=float), "size"))
    stats.update(_quantiles(gaps, "iet_ms"))
    stats["iet_p50_ms"] = stats.pop("iet_ms_p50")
    return stats


def comparison_markdown(sim: dict[str, float], real: dict[str, float]) -> str:
    """Side-by-side markdown table of the shared statistics."""
    lines = ["| statistic | simulator | LOBSTER sample |", "|---|---|---|"]
    for key in sim:
        if key in real:
            lines.append(f"| {key} | {sim[key]:.3f} | {real[key]:.3f} |")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_validate.py -v`
Expected: 3 passed. If `test_lobster_stats_on_a_tiny_sample` fails on a quantile tolerance, print the stats dict and fix the test's expectation, not the maths, after checking it by hand.

- [ ] **Step 5: Write `scripts/lobster_compare.py`**

```python
"""Compare simulator stats with a LOBSTER sample.

python scripts/lobster_compare.py --message data/lobster/<ticker>_message_10.csv \
    --book data/lobster/<ticker>_orderbook_10.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from sentinel.sim.validate import comparison_markdown, load_lobster, lobster_stats, sim_stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", type=Path, required=True)
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=Path("data/events.parquet"))
    parser.add_argument("--out", type=Path, default=Path("docs/lobster_comparison.md"))
    args = parser.parse_args()

    message, book = load_lobster(args.message, args.book)
    real = lobster_stats(message, book)
    sim = sim_stats(pd.read_parquet(args.events))
    table = comparison_markdown(sim, real)
    args.out.write_text("# Simulator vs. LOBSTER sample\n\n" + table, encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Manual step (needs the user): download one LOBSTER sample and run the comparison**

1. The user opens https://lobsterdata.com/info/DataSamples.php, **reads the terms of use**, and downloads one ticker's sample (for example AAPL, level 10) into `data/lobster/` (gitignored).
2. **Verify the format assumptions** in the `validate.py` docstring against the first rows of the downloaded files. In particular, confirm the message file has *no trader/account column* (this is what rules the sample out as labeled training data) and record the finding.
3. Run: `python scripts/lobster_compare.py --message data/lobster/<file>_message_10.csv --book data/lobster/<file>_orderbook_10.csv`
4. Commit only `docs/lobster_comparison.md` (never the samples):

```bash
git add src/sentinel/sim/validate.py scripts/lobster_compare.py tests/test_validate.py docs/lobster_comparison.md
git commit -m "$(cat <<'EOF'
feat(sim): LOBSTER comparison statistics and script

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

5. Read the table with the user and list the mismatches. Expect at least: wider simulator spreads than a liquid real stock, and different depth ratios. These belong in the README limitations, and the simulator is not tuned to close them.

---

## Self-review (against the spec)

**Spec coverage**
- Sec. 1 event log columns and integer ticks/ns: Tasks 1-2 (`Exchange.to_frames`), refinement of the snapshot columns noted at the top. Label file separate from events: Task 2 (tags returned separately) and Task 6.
- Sec. 2 book (dict of deques, id index, O(1) cancel, modify priority rule), agents (market makers, noise, momentum), lognormal sizes, seeded: Tasks 1, 3, 5.
- Sec. 3 scenarios and difficulty dial, frozen before detectors, injected traders use normal distributions, every active window labeled with difficulty: Tasks 4, 6, 7 (tag).
- Sec. 4 LOBSTER validation and the license/trader-ID check: Task 8 step 6. Tests list (priority, partial fills, cancel of filled, modify priority, no crossed book, conservation, property test, determinism, scenario signatures, label coverage, scale): Tasks 1, 2, 4, 5, 6, 7. Risks: speed (Task 7 step 2), leakage check (Task 7 step 4), class imbalance (Task 7 step 3).
- Gap check: "labels cover exactly the injected windows" is covered by `test_labels_from_a_real_simulation_cover_every_episode` (subset relation plus presence of all three kinds).

**Placeholder scan:** no TBD/TODO; every code step has full code.

**Type/name consistency:** `Exchange` methods (`new_limit`, `new_market`, `cancel`, `modify`, `best`, `depth5`, `imbalance`, `is_live`, `start_episode`, `end_episode`, `to_frames`, `snap`, `busy`, `active_episodes`, `n_events`), `spawn_episode(ex, rng, kind, difficulty, pool, now)`, `ScriptedEpisode.first_delay`, `SimConfig`/`SimResult` fields and `build_labels(events, event_episode, episodes, window_ns)` are used consistently across tasks and tests.

**Known risks the executor should expect:** (1) the price-impact test may need `IMBALANCE_SENSITIVITY` tuning (Task 5, guided); (2) wash legs are skipped when the spread has no interior tick (Task 7 step 3 checks the impact); (3) the scale-test budget may need profiling.
