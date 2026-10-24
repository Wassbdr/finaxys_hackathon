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
MAX_LIVE = 6  # noise trader: cap on simultaneously resting orders (keeps book depth realistic)
P_MARKETABLE = 0.5  # share of aggressive orders sent as marketable limits instead of market orders
P_IMPROVE = 0.10  # share of noise limit orders priced at floor(mid)/ceil(mid), i.e. inside the spread
IMBALANCE_SENSITIVITY = 0.5  # how strongly noise flow follows book imbalance
MM_SKEW = 3.0  # market makers shift both quotes by round(MM_SKEW * imbalance) ticks, clamped to +/-1


class Agent(Protocol):
    """Anything the runner can schedule."""

    def act(self, ex: Exchange, now: int, rng: random.Random) -> int | None:
        """Act at time ``now``; return ns until the next action, or None to retire."""
        ...


def lognormal_qty(rng: random.Random, median: float, sigma: float) -> int:
    """Heavy-tailed order size: lognormal with the given median, at least 1 share."""
    return max(1, int(rng.lognormvariate(math.log(median), sigma)))


def send_aggressive(ex: Exchange, trader_id: int, side: int, qty: int, now: int, rng: random.Random) -> None:
    """Send an order that trades immediately: a market order, or a limit priced through the far touch.

    Real participants use both, so an order that fills within nanoseconds of
    submission must not, on its own, identify anything unusual.
    """
    far = ex.best(-side)
    if far and rng.random() < P_MARKETABLE:
        price = far + side * int(rng.expovariate(1.0))  # bid: far ask + k ticks; ask: far bid - k ticks
        ex.new_limit(trader_id, side, max(1, price), qty, now)
    else:
        ex.new_market(trader_id, side, qty, now)


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
        """Cancel the previous quotes, post a fresh bid/ask pair, return next delay."""
        for order_id in self._quotes:
            ex.cancel(order_id, now)  # a no-op if the quote was already filled
        mid = ex.mid()
        half = rng.choice((1, 1, 2, 2, 3))
        # Quote skew: a heavier bid side means buyers are pressing, so quotes move up (and vice
        # versa). A clamp of +/-1 tick with half >= 1 keeps the quotes from ever crossing the book.
        skew = max(-1, min(1, round(MM_SKEW * ex.imbalance())))
        bid = max(1, math.floor(mid) - half + skew)
        ask = math.ceil(mid) + half + skew
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
        """Cancel, shrink or place one order; return ns until the next action."""
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
            send_aggressive(ex, self.trader_id, side, lognormal_qty(rng, 60, 0.8), now, rng)
            return
        mid = ex.mid()
        # Most passive orders sit behind the touch; some improve it, as real price-improving orders do.
        offset = int(rng.expovariate(0.35)) + (0 if rng.random() < P_IMPROVE else 1)
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
        """Fire the next market order of the current burst; return ns until the next."""
        if self._left == 0:
            self._left = rng.randint(3, 8)
            self._side = rng.choice((BID, ASK))
        send_aggressive(ex, self.trader_id, self._side, lognormal_qty(rng, 80, 0.6), now, rng)
        self._left -= 1
        if self._left > 0:
            return int(rng.uniform(0.05, 0.3) * NS_PER_SEC)
        return _delay(rng, self.rate)
