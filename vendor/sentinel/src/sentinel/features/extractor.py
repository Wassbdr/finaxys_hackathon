"""Streaming per-trader-window feature extraction.

One class, one definition of every feature. It is used (1) to build training
data by replaying an event log, (2) inside the live engine, and (3) as the
reference implementation the Java port must match.

Events must arrive in non-decreasing timestamp order per instrument. A window
closes when the first event of a later window arrives, so a window's features
are available only after it ends (alert delay is up to one window length).
Nothing here looks ahead.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import NamedTuple

import pandas as pd

from sentinel.sim.types import ASK, BID, CANCEL, EXEC, MODIFY, NEW, NS_PER_SEC

_BURST_NS = 100_000_000  # cancel-burst horizon: 100 ms

FEATURE_NAMES: tuple[str, ...] = (
    "n_new", "n_cancel", "n_modify", "n_fills", "order_to_trade_ratio",
    "cancel_rate", "cancel_within_1s_share", "cancel_within_3s_share", "median_cancelled_lifetime_s",
    "total_new_qty", "total_fill_qty", "max_order_qty", "max_qty_over_best", "peak_resting_over_depth5",
    "max_live_levels_same_side", "max_cancel_burst_100ms",
    "opposite_fill_share", "signed_mid_drift_ticks",
    "net_over_gross", "top_counterparty_share", "n_counterparties", "roundtrip_counterparty_share",
)


class FeatureRow(NamedTuple):
    """Features of one trader in one closed window, ordered as FEATURE_NAMES."""

    trader_id: int
    instrument: str
    window: int
    values: tuple[float, ...]


@dataclass(slots=True)
class _Live:
    """One of the trader's resting orders."""

    ts: int
    side: int
    price: int
    qty: int  # remaining quantity


@dataclass(slots=True)
class _Window:
    """Counters for one trader in the current window."""

    n_new: int = 0
    n_new_limit: int = 0
    n_cancel: int = 0
    n_modify: int = 0
    n_fills: int = 0
    new_qty: int = 0
    fill_buy: int = 0
    fill_sell: int = 0
    max_qty: int = 0
    max_qty_over_best: float = 0.0
    quick1: int = 0
    quick3: int = 0
    max_levels: int = 0
    max_burst: int = 0
    peak_ratio: float = 0.0
    peak_bid: int = 0
    peak_ask: int = 0
    first_mid: float | None = None
    last_mid: float | None = None
    lifetimes: list[float] = field(default_factory=list)
    cp_buy: dict[int, int] = field(default_factory=dict)
    cp_sell: dict[int, int] = field(default_factory=dict)


@dataclass(slots=True)
class _Trader:
    """Per-trader state that persists across windows (live orders) plus the current window."""

    live: dict[int, _Live] = field(default_factory=dict)
    resting: dict[int, int] = field(default_factory=lambda: {BID: 0, ASK: 0})
    levels: dict[int, dict[int, int]] = field(default_factory=lambda: {BID: {}, ASK: {}})
    cancels: deque[int] = field(default_factory=deque)
    win: _Window | None = None


@dataclass(slots=True)
class _Instrument:
    name: str
    window: int | None = None
    last_ts: int = -1
    traders: dict[int, _Trader] = field(default_factory=dict)
    active: list[int] = field(default_factory=list)


class WindowFeatureExtractor:
    """Consume events one at a time; emit FeatureRows as windows close."""

    def __init__(self, window_s: int = 10) -> None:
        self.window_ns = window_s * NS_PER_SEC
        self._inst: dict[str, _Instrument] = {}

    def process(self, ts: int, instrument: str, etype: int, order_id: int, trader_id: int,
                counterparty_id: int, side: int, price: int, qty: int, best_bid: int, best_ask: int,
                bid_qty1: int, ask_qty1: int, bid_depth5: int, ask_depth5: int) -> list[FeatureRow]:
        """Process one event; return the rows of any window that this event closed."""
        inst = self._inst.get(instrument)
        if inst is None:
            inst = self._inst[instrument] = _Instrument(instrument)
        if ts < inst.last_ts:
            raise ValueError(f"out-of-order event for {instrument}: {ts} < {inst.last_ts}")
        inst.last_ts = ts
        window = ts // self.window_ns
        closed: list[FeatureRow] = []
        if inst.window is None:
            inst.window = window
        elif window > inst.window:
            closed = self._close(inst)
            inst.window = window

        trader = inst.traders.get(trader_id)
        if trader is None:
            trader = inst.traders[trader_id] = _Trader()
        win = trader.win
        if win is None:
            win = trader.win = _Window()
            inst.active.append(trader_id)
        if best_bid and best_ask:
            mid = (best_bid + best_ask) / 2
            if win.first_mid is None:
                win.first_mid = mid
            win.last_mid = mid

        own_best = bid_qty1 if side == BID else ask_qty1
        own_depth = bid_depth5 if side == BID else ask_depth5
        if etype == NEW:
            self._on_new(trader, win, ts, order_id, side, price, qty, own_best, own_depth)
        elif etype == CANCEL:
            self._on_cancel(trader, win, ts, order_id)
        elif etype == MODIFY:
            self._on_modify(trader, win, order_id, price, qty, own_depth)
        elif etype == EXEC:
            self._on_exec(trader, win, order_id, side, qty, counterparty_id)
        return closed

    def flush(self) -> list[FeatureRow]:
        """Close every open window (call at end of stream)."""
        rows: list[FeatureRow] = []
        for inst in self._inst.values():
            if inst.window is not None and inst.active:
                rows += self._close(inst)
        return rows

    # -- event handlers -----------------------------------------------------

    def _on_new(self, t: _Trader, w: _Window, ts: int, order_id: int, side: int, price: int,
                qty: int, own_best: int, own_depth: int) -> None:
        w.n_new += 1
        if price <= 0:  # market order: never rests
            return
        w.n_new_limit += 1
        w.new_qty += qty
        w.max_qty = max(w.max_qty, qty)
        w.max_qty_over_best = max(w.max_qty_over_best, qty / max(1, own_best))
        t.live[order_id] = _Live(ts, side, price, qty)
        t.resting[side] += qty
        self._level_up(t, side, price)
        self._note_peaks(t, w, side, own_depth)

    def _on_cancel(self, t: _Trader, w: _Window, ts: int, order_id: int) -> None:
        w.n_cancel += 1
        live = self._remove_live(t, order_id)
        if live is not None:
            lifetime = (ts - live.ts) / NS_PER_SEC
            w.lifetimes.append(lifetime)
            if lifetime <= 1.0:
                w.quick1 += 1
            if lifetime <= 3.0:
                w.quick3 += 1
        recent = t.cancels
        recent.append(ts)
        while ts - recent[0] > _BURST_NS:
            recent.popleft()
        w.max_burst = max(w.max_burst, len(recent))

    def _on_modify(self, t: _Trader, w: _Window, order_id: int, price: int, qty: int, own_depth: int) -> None:
        w.n_modify += 1
        live = t.live.get(order_id)
        if live is None:
            return
        t.resting[live.side] += qty - live.qty
        if price != live.price:
            self._level_down(t, live.side, live.price)
            self._level_up(t, live.side, price)
            live.price = price
        live.qty = qty
        self._note_peaks(t, w, live.side, own_depth)

    def _on_exec(self, t: _Trader, w: _Window, order_id: int, side: int, qty: int, counterparty: int) -> None:
        w.n_fills += 1
        if side == BID:
            w.fill_buy += qty
            w.cp_buy[counterparty] = w.cp_buy.get(counterparty, 0) + qty
        else:
            w.fill_sell += qty
            w.cp_sell[counterparty] = w.cp_sell.get(counterparty, 0) + qty
        live = t.live.get(order_id)
        if live is not None:
            live.qty -= qty
            t.resting[live.side] -= qty
            if live.qty <= 0:
                self._remove_live(t, order_id)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _level_up(t: _Trader, side: int, price: int) -> None:
        levels = t.levels[side]
        levels[price] = levels.get(price, 0) + 1

    @staticmethod
    def _level_down(t: _Trader, side: int, price: int) -> None:
        levels = t.levels[side]
        if levels[price] > 1:
            levels[price] -= 1
        else:
            del levels[price]

    def _remove_live(self, t: _Trader, order_id: int) -> _Live | None:
        live = t.live.pop(order_id, None)
        if live is not None:
            t.resting[live.side] -= live.qty
            self._level_down(t, live.side, live.price)
        return live

    @staticmethod
    def _note_peaks(t: _Trader, w: _Window, side: int, own_depth: int) -> None:
        resting = t.resting[side]
        if side == BID:
            w.peak_bid = max(w.peak_bid, resting)
        else:
            w.peak_ask = max(w.peak_ask, resting)
        w.peak_ratio = max(w.peak_ratio, resting / max(1, own_depth))
        w.max_levels = max(w.max_levels, len(t.levels[side]))

    def _close(self, inst: _Instrument) -> list[FeatureRow]:
        rows = []
        for trader_id in sorted(inst.active):
            trader = inst.traders[trader_id]
            rows.append(FeatureRow(trader_id, inst.name, inst.window, _features(trader.win)))
            trader.win = None
        inst.active = []
        return rows


def _features(w: _Window) -> tuple[float, ...]:
    """Turn a window's counters into the FEATURE_NAMES vector."""
    limit_den = max(1, w.n_new_limit)
    total_fill = w.fill_buy + w.fill_sell
    peak_side = BID if w.peak_bid >= w.peak_ask else ASK
    has_peak = (w.peak_bid + w.peak_ask) > 0
    if has_peak and total_fill:
        opposite_qty = w.fill_sell if peak_side == BID else w.fill_buy
        opposite_share = opposite_qty / total_fill
    else:
        opposite_share = 0.0
    if has_peak and w.first_mid is not None and w.last_mid is not None:
        drift = (1 if peak_side == BID else -1) * (w.last_mid - w.first_mid)
    else:
        drift = 0.0
    totals = dict(w.cp_buy)
    for cp, qty in w.cp_sell.items():
        totals[cp] = totals.get(cp, 0) + qty
    roundtrip = sum(totals[cp] for cp in w.cp_buy if cp in w.cp_sell)
    return (
        float(w.n_new), float(w.n_cancel), float(w.n_modify), float(w.n_fills),
        w.n_new / max(1, w.n_fills),
        w.n_cancel / max(1, w.n_new), min(1.0, w.quick1 / limit_den), min(1.0, w.quick3 / limit_den),
        median(w.lifetimes) if w.lifetimes else -1.0,
        float(w.new_qty), float(total_fill), float(w.max_qty), w.max_qty_over_best, w.peak_ratio,
        float(w.max_levels), float(w.max_burst),
        opposite_share, drift,
        abs(w.fill_buy - w.fill_sell) / total_fill if total_fill else 1.0,
        max(totals.values()) / total_fill if totals else 0.0,
        float(len(totals)),
        roundtrip / total_fill if total_fill else 0.0,
    )


_EVENT_CODES = {"NEW": NEW, "CANCEL": CANCEL, "MODIFY": MODIFY, "EXEC": EXEC}
_INPUT_COLUMNS = ("ts", "instrument", "event_type", "order_id", "trader_id", "counterparty_id", "side",
                  "price", "qty", "best_bid", "best_ask", "bid_qty1", "ask_qty1", "bid_depth5", "ask_depth5")


def rows_to_frame(rows: list[FeatureRow]) -> pd.DataFrame:
    """FeatureRows as a DataFrame: trader_id, instrument, window, then FEATURE_NAMES."""
    data = [(r.trader_id, r.instrument, r.window) + r.values for r in rows]
    return pd.DataFrame(data, columns=["trader_id", "instrument", "window", *FEATURE_NAMES])


def event_columns(events: pd.DataFrame) -> list[list]:
    """The 15 input columns as Python lists in ``process`` argument order.

    ``instrument`` is a string and ``event_type`` an int code (NEW=0, CANCEL=1, MODIFY=2, EXEC=3).
    """
    missing = [c for c in _INPUT_COLUMNS if c not in events.columns]
    if missing:
        raise ValueError(f"event log is missing columns: {missing}")
    columns = []
    for name in _INPUT_COLUMNS:
        if name == "event_type":
            columns.append(events[name].astype(str).map(_EVENT_CODES).to_numpy().tolist())
        elif name == "instrument":
            columns.append(events[name].astype(str).to_numpy().tolist())
        else:
            columns.append(events[name].to_numpy().tolist())
    return columns


def extract_features(events: pd.DataFrame, window_s: int = 10) -> pd.DataFrame:
    """Replay an event log (time-ordered) through the streaming extractor."""
    extractor = WindowFeatureExtractor(window_s)
    rows: list[FeatureRow] = []
    for event in zip(*event_columns(events)):
        rows += extractor.process(*event)
    rows += extractor.flush()
    return rows_to_frame(rows)
