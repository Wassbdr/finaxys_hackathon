"""Live Sentinel adapter. Hidden scenario scheduling never enters surveillance events."""
from __future__ import annotations
import heapq
import random
from collections import deque
from typing import Callable
from sentinel.sim.agents import MarketMaker, MomentumTrader, NoiseTrader
from sentinel.sim.exchange import Exchange
from sentinel.sim.types import BID, EVENT_TYPES, NS_PER_SEC


class ControlledExchange(Exchange):
    def __init__(self, sink: Callable[[dict], None]):
        super().__init__("SYN/ USD", 0)
        self.sink = sink
        self.sequence = 0
        self.restricted: dict[int, int] = {}
        self.rejections = 0

    def _emit(self, ts, etype, order_id, trader_id, side, price, qty, episode, snap, counterparty=0):
        self.sequence += 1
        self.sink(dict(id=self.sequence, ts=self._stamp(ts), type=EVENT_TYPES[etype],
                       order_id=order_id, agent=trader_id, side=side, price=price, qty=qty,
                       counterparty=counterparty, bid=snap[0], ask=snap[1],
                       bid_qty=snap[2], ask_qty=snap[3], bid_depth=snap[4], ask_depth=snap[5]))

    def _blocked(self, trader, ts, action):
        if self.restricted.get(trader, 0) <= ts:
            return False
        self.rejections += 1
        self.sequence += 1
        self.sink(dict(id=self.sequence, ts=self._stamp(ts), type="REJECT", agent=trader,
                       order_id=0, side=0, price=0, qty=0, counterparty=0,
                       reason=f"Active suspension: {action}", bid=self.snap[0], ask=self.snap[1]))
        return True

    def new_limit(self, trader_id, side, price, qty, ts, episode=-1):
        if self._blocked(trader_id, ts, "limit order"):
            return -1
        return super().new_limit(trader_id, side, price, qty, ts, episode)

    def new_market(self, trader_id, side, qty, ts, episode=-1):
        if self._blocked(trader_id, ts, "market order"):
            return -1
        return super().new_market(trader_id, side, qty, ts, episode)

    def modify(self, order_id, new_qty, ts, new_price=None):
        order = self.book.orders.get(order_id)
        if order and self._blocked(order.trader_id, ts, "modification"):
            return False
        return super().modify(order_id, new_qty, ts, new_price)

    def suspend(self, agents, until, now):
        for agent in agents:
            self.restricted[agent] = max(until, self.restricted.get(agent, 0))
        for oid, order in list(self.book.orders.items()):
            if order.trader_id in agents:
                self.cancel(oid, now)


class LiveMarket:
    def __init__(self, seed, scenario, sink):
        self.rng = random.Random(seed)
        self.exchange = ControlledExchange(sink)
        self.now = 0
        self.heap = []
        self.seq = 0
        self.history = deque(maxlen=240)
        self.roles = {i: "Market maker" if i <= 2 else "Momentum" if i <= 4 else "Noise trader" for i in range(1, 11)}
        for i in self.roles:
            agent = MarketMaker(i, 3.0) if i <= 2 else MomentumTrader(i, .15) if i <= 4 else NoiseTrader(i, 1.4)
            self.schedule(int(self.rng.random() * NS_PER_SEC), lambda t, a=agent: self._act(a, t))
        # Identical participants in both scenarios. Only the timing relationship changes.
        for wave in range(34):
            base = 12 + wave * 5
            for j, agent in enumerate((7, 8, 9)):
                delay = j * .025 if scenario == "coordinated" else j * 1.1
                state = {}
                self.schedule(int((base + delay) * NS_PER_SEC),
                              lambda t, a=agent, s=state: self._place(a, s, t))
                self.schedule(int((base + delay + .65) * NS_PER_SEC),
                              lambda t, s=state: self.exchange.cancel(s.get("oid", -1), t))

    def schedule(self, ts, fn):
        self.seq += 1
        heapq.heappush(self.heap, (ts, self.seq, fn))

    def _act(self, agent, ts):
        delay = agent.act(self.exchange, ts, self.rng)
        if delay:
            self.schedule(ts + delay, lambda t: self._act(agent, t))

    def _place(self, agent, state, ts):
        ex = self.exchange
        state["oid"] = ex.new_limit(agent, BID, max(1, int(ex.mid()) - 4), 900, ts)

    def advance(self, seconds):
        target = self.now + int(seconds * NS_PER_SEC)
        while self.heap and self.heap[0][0] <= target:
            ts, _, fn = heapq.heappop(self.heap)
            self.now = ts
            fn(ts)
        self.now = target
        self.history.append({"time": round(self.now / NS_PER_SEC, 2), "price": self.exchange.mid()})

    def book(self):
        levels = {"bids": {}, "asks": {}}
        for o in self.exchange.book.orders.values():
            side = levels["bids" if o.side == BID else "asks"]
            side[o.price] = side.get(o.price, 0) + o.qty
        return {k: [{"price": p, "qty": q} for p, q in sorted(v.items(), reverse=k == "bids")[:8]] for k, v in levels.items()}
