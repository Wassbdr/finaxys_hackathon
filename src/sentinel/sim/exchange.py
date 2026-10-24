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
    "counterparty_id",
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

    def __init__(self, instrument: str, index: int, initial_price: int = INITIAL_PRICE,
                 retain_history: bool = True) -> None:
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
        self._event_count = 0
        self._retain_history = retain_history
        self._next_order = index * ORDER_ID_STRIDE + 1
        self._order_episode: dict[int, int] = {}
        self.restricted_traders: set[int] = set()
        self.rejected_actions: list[dict[str, int | str]] = []
        self._live_events: list[tuple[int, ...]] = []

    # -- market state used by agents ---------------------------------------

    @property
    def n_events(self) -> int:
        """Number of rows logged so far."""
        return self._event_count

    def mid(self) -> float:
        """Mid-price; falls back to the last traded price if a side is empty."""
        best_bid, best_ask = self.snap[0], self.snap[1]
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return float(self.last_price)

    def best(self, side: int) -> int:
        """Best price on ``side`` (0 if that side is empty)."""
        return self.snap[0] if side == BID else self.snap[1]

    def best_qty(self, side: int) -> int:
        """Quantity resting at the best price on ``side`` (0 if that side is empty)."""
        return self.snap[2] if side == BID else self.snap[3]

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

    def drain_live_events(self) -> list[dict[str, int | str]]:
        """Return newly emitted market events for live consumers, without hidden labels."""
        rows, self._live_events = self._live_events, []
        columns = _COLUMNS[:7] + _COLUMNS[8:]
        names = columns[:1] + ("instrument",) + columns[1:]
        result = []
        for row in rows:
            visible = row[:7] + row[8:]
            item = dict(zip(names, (visible[0], self.instrument, *visible[1:])))
            item["event_type"] = EVENT_TYPES[int(item["event_type"])]
            result.append(item)
        return result

    def drain_rejected_actions(self) -> list[dict[str, int | str]]:
        """Return attempted new orders rejected by an active restriction."""
        actions, self.rejected_actions = self.rejected_actions, []
        return actions

    def restrict_trader(self, trader_id: int, ts: int) -> list[int]:
        """Block new orders and cancel a trader's resting orders; cancellations remain allowed."""
        self.restricted_traders.add(trader_id)
        cancelled = []
        for order_id, order in list(self.book.orders.items()):
            if order.trader_id == trader_id and self.cancel(order_id, ts):
                cancelled.append(order_id)
        return cancelled

    def restore_trader(self, trader_id: int) -> None:
        """Remove an active restriction from a trader."""
        self.restricted_traders.discard(trader_id)

    # -- actions ------------------------------------------------------------

    def new_limit(self, trader_id: int, side: int, price: int, qty: int, ts: int,
                  episode: int = NO_EPISODE) -> int:
        """Submit a limit order; returns its order id."""
        if trader_id in self.restricted_traders:
            self.rejected_actions.append({"ts": ts, "trader_id": trader_id, "side": side,
                                          "price": price, "qty": qty, "order_type": "LIMIT"})
            return 0
        order_id = self._alloc(episode)
        fills = self.book.add_limit(order_id, trader_id, side, price, qty)
        self._record(ts, NEW, order_id, trader_id, side, price, qty, episode, fills)
        return order_id

    def new_market(self, trader_id: int, side: int, qty: int, ts: int,
                   episode: int = NO_EPISODE) -> int:
        """Submit a market order (logged with price 0); returns its order id."""
        if trader_id in self.restricted_traders:
            self.rejected_actions.append({"ts": ts, "trader_id": trader_id, "side": side,
                                          "price": 0, "qty": qty, "order_type": "MARKET"})
            return 0
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
        if trader_id in self.restricted_traders:
            self.rejected_actions.append({"ts": ts, "trader_id": trader_id, "side": side,
                                          "price": order.price, "qty": new_qty, "order_type": "MODIFY"})
            return False
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
        if not self._retain_history:
            raise RuntimeError("event history retention is disabled for this live exchange")
        self._flush()
        arr = np.concatenate(self._chunks) if self._chunks else np.empty((0, len(_COLUMNS)), dtype=np.int64)
        df = pd.DataFrame(arr, columns=list(_COLUMNS))
        tags = df.pop("episode").to_numpy()
        counterparty = df.pop("counterparty_id")
        df.insert(df.columns.get_loc("trader_id") + 1, "counterparty_id", counterparty)
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
              qty: int, episode: int, snap: Snapshot, counterparty: int = 0) -> None:
        row = (self._stamp(ts), etype, order_id, trader_id, side, price, qty, episode) + snap + (counterparty,)
        self._event_count += 1
        self._live_events.append(row)
        if self._retain_history:
            self._rows.append(row)
        if self._retain_history and len(self._rows) >= _FLUSH_ROWS:
            self._flush()

    def _record(self, ts: int, etype: int, order_id: int, trader_id: int, side: int, price: int,
                qty: int, episode: int, fills: list[Fill] | tuple[()]) -> None:
        if fills:
            self.last_price = fills[-1].price
        snap = self.book.snapshot()
        self.snap = snap
        self._emit(ts, etype, order_id, trader_id, side, price, qty, episode, snap)
        for own in self.book.stp_cancels:
            self._emit(ts, CANCEL, own.order_id, own.trader_id, own.side, own.price, own.qty,
                       self._order_episode.get(own.order_id, NO_EPISODE), snap)
        for f in fills:
            self._emit(ts, EXEC, f.maker_order_id, f.maker_trader_id, -f.taker_side, f.price, f.qty,
                       self._order_episode.get(f.maker_order_id, NO_EPISODE), snap,
                       counterparty=f.taker_trader_id)
            self._emit(ts, EXEC, f.taker_order_id, f.taker_trader_id, f.taker_side, f.price, f.qty,
                       episode, snap, counterparty=f.maker_trader_id)

    def _flush(self) -> None:
        if self._rows:
            self._chunks.append(np.array(self._rows, dtype=np.int64))
            self._flushed += len(self._rows)
            self._rows = []
