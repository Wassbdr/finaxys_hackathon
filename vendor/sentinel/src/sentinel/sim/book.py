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
        # Resting orders cancelled by self-trade prevention during the last operation;
        # the Exchange drains this list to log them.
        self.stp_cancels: list[Order] = []
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
        self.stp_cancels = []
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
                if maker.trader_id == trader_id:
                    # Self-trade prevention (cancel-resting mode): never trade with yourself.
                    maker.alive = False
                    queue.popleft()
                    del self.orders[maker.order_id]
                    level_qty[best] -= maker.qty
                    level_n[best] -= 1
                    self.stp_cancels.append(maker)
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
