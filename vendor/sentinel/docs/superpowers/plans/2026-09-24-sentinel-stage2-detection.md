# Sentinel Stage 2: Features and Detectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit rule:** every commit message ends with the line `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` (use a heredoc).
>
> **Line endings:** the repo uses LF (`.gitattributes`). When writing files from Python use `newline="\n"`, or just let git normalize.

**Goal:** Build the streaming feature extractor, the rules baseline (textbook and tuned), a LightGBM multiclass model with SHAP explanations, and an Isolation Forest, all trained and validated on seeds 7/8/9. The test simulation (seed 100) is generated but not touched.

**Architecture:** One `WindowFeatureExtractor` consumes events one at a time and emits a feature row per (trader, instrument, 10 s window) when the window closes. Training data comes from replaying event logs through it. Rules and models score the same feature rows. Alerts (rule or model) share one `Alert` shape with evidence lines.

**Tech Stack:** Python 3.12, numpy, pandas, pyarrow, scikit-learn, lightgbm, shap, pytest.

Spec: `docs/superpowers/specs/2026-09-24-sentinel-detection-design.md`

**Deliberate refinements of the spec (decided while planning):**
- 22 features, not ~25: the spec's list plus `total_new_qty` and `total_fill_qty`.
- Tuned rules use a 20,000-draw random search (spec said at least 2,000) followed by a coordinate refinement pass, starting from the textbook thresholds, so the tuned rule is never worse than the textbook rule on validation. This makes the baseline stronger, not weaker.
- The trained LightGBM booster is truncated to its best iteration so SHAP (which reads every tree) and prediction agree exactly.

---

## File Structure

```
pyproject.toml                          (modify: add dependencies)
src/sentinel/sim/exchange.py            (modify: counterparty_id column)
src/sentinel/features/__init__.py
src/sentinel/features/extractor.py      WindowFeatureExtractor, FEATURE_NAMES, extract_features
src/sentinel/detect/__init__.py
src/sentinel/detect/dataset.py          load_sim, time_split, to_xy, test-set guard
src/sentinel/detect/rules.py            Condition, Rule, TEXTBOOK_RULES, GENERIC_RULE, tune_rule
src/sentinel/detect/model.py            LightGBM + thresholds + IsolationForest + save/load
src/sentinel/detect/explain.py          PHRASES, ModelExplainer
src/sentinel/detect/alerts.py           Alert, Evidence, builders, render_alert
src/sentinel/eval/__init__.py
src/sentinel/eval/metrics.py            binary_metrics, results_table, recall_by_difficulty
scripts/build_features.py
scripts/train_detectors.py              trains everything, writes the validation report
tests/conftest.py                       (modify: synthetic_windows fixture)
tests/test_exchange.py                  (modify: counterparty test)
tests/test_features.py
tests/test_dataset.py
tests/test_metrics.py
tests/test_rules.py
tests/test_model.py
tests/test_explain.py
tests/test_alerts.py
```

---

### Task 0: Dependencies, `counterparty_id`, and the datasets

**Concept to explain:** a trade report names both parties. Wash trading is defined by who you trade with, so the log needs the counterparty on execution rows. Adding a column doesn't change any simulator behavior or random draw, and we verify that.

**Files:**
- Modify: `pyproject.toml`, `src/sentinel/sim/exchange.py`, `tests/test_exchange.py`

- [ ] **Step 1: Add dependencies**

In `pyproject.toml` change the dependencies line to:
```toml
dependencies = ["numpy>=2.0", "pandas>=2.2", "pyarrow>=15", "scikit-learn>=1.4", "lightgbm>=4.3", "shap>=0.45"]
```
Run: `python -m pip install -e ".[dev]"` then `python -c "import lightgbm, shap, sklearn; print(lightgbm.__version__, shap.__version__, sklearn.__version__)"`.
Expected: three version numbers, no import error. If `shap` fails to install or import on this Python/numpy, STOP and report (do not substitute silently).

- [ ] **Step 2: Write the failing test** (append to `tests/test_exchange.py`)

```python
def test_exec_rows_carry_the_counterparty_and_other_rows_carry_zero() -> None:
    ex = Exchange("T", 0)
    ex.new_limit(1, ASK, 101, 10, ts=100)
    ex.new_limit(2, BID, 101, 4, ts=200)
    df, _ = ex.to_frames()
    assert list(df[df.event_type != "EXEC"].counterparty_id) == [0, 0]
    execs = df[df.event_type == "EXEC"].set_index("trader_id")
    assert execs.loc[1, "counterparty_id"] == 2 and execs.loc[2, "counterparty_id"] == 1
    assert list(df.columns[:6]) == ["ts", "instrument", "event_type", "order_id", "trader_id", "counterparty_id"]
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python -m pytest tests/test_exchange.py -k counterparty -v`
Expected: FAIL with `AttributeError: 'DataFrame' object has no attribute 'counterparty_id'`

- [ ] **Step 4: Implement in `exchange.py`**

1. Append `"counterparty_id"` to `_COLUMNS` (last item, after `"ask_depth5"`).
2. Change `_emit` to take `counterparty: int = 0` and append it after the snapshot:
```python
    def _emit(self, ts: int, etype: int, order_id: int, trader_id: int, side: int, price: int,
              qty: int, episode: int, snap: Snapshot, counterparty: int = 0) -> None:
        self._rows.append((self._stamp(ts), etype, order_id, trader_id, side, price, qty, episode)
                          + snap + (counterparty,))
        if len(self._rows) >= _FLUSH_ROWS:
            self._flush()
```
3. In `_record`, pass the counterparty on the two EXEC emits: the maker row gets `f.taker_trader_id`, the taker row gets `f.maker_trader_id` (add as the last argument, `counterparty=...`).
4. In `to_frames`, after `tags = df.pop("episode").to_numpy()` add:
```python
        counterparty = df.pop("counterparty_id")
        df.insert(df.columns.get_loc("trader_id") + 1, "counterparty_id", counterparty)
```
(the existing `df.insert(1, "instrument", ...)` stays after it).

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: all pass (52 existing + 1 new = 53).

- [ ] **Step 6: Prove the simulator output is unchanged apart from the new column**

The existing `data/events.parquet` is the Stage 1 output for seed 7 with default config. Run:
```bash
python scripts/generate_data.py --out data/sim_seed7 --seed 7
python - <<'EOF'
import pandas as pd, pandas.testing as pdt
old = pd.read_parquet("data/events.parquet")
new = pd.read_parquet("data/sim_seed7/events.parquet")
pdt.assert_frame_equal(new.drop(columns="counterparty_id"), old)
print("identical apart from counterparty_id:", len(new), "rows")
EOF
```
Expected: prints the identical message. If `assert_frame_equal` fails, STOP and report: the change altered simulator behavior.

- [ ] **Step 7: Generate the remaining datasets and remove the superseded Stage 1 files**

```bash
python scripts/generate_data.py --out data/sim_seed8 --seed 8
python scripts/generate_data.py --out data/sim_seed9 --seed 9
python scripts/generate_data.py --out data/test_seed100 --seed 100
rm -f data/events.parquet data/episodes.parquet data/labels.parquet
```
(`data/` is gitignored, and the removed files are the seed-7 Stage 1 output now regenerated as `sim_seed7`.) Do NOT open, load or summarize anything in `data/test_seed100` beyond the generator's printed summary.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/sentinel/sim/exchange.py tests/test_exchange.py
git commit -m "$(cat <<'EOF'
feat(sim): add counterparty_id to EXEC rows; add ML dependencies

Simulator output is identical apart from the new column (verified against
the Stage 1 seed-7 dataset).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 1: Streaming feature extractor

**Concept to explain:** the extractor keeps, for each trader, a table of their live orders and, for the current window, counters. It never looks ahead. A window is closed when the first event of a later window shows up, so the same code works on a replay and on a live feed. Every feature is one number computed from that state.

**Files:**
- Create: `src/sentinel/features/__init__.py`, `src/sentinel/features/extractor.py`
- Test: `tests/test_features.py`

- [ ] **Step 1: Write `src/sentinel/features/__init__.py`**

```python
"""Streaming feature extraction."""
```

- [ ] **Step 2: Write the failing tests**

`tests/test_features.py`:
```python
"""Tests for the streaming feature extractor (hand-computed expectations)."""
from __future__ import annotations

import numpy as np
import pytest

from sentinel.features.extractor import FEATURE_NAMES, WindowFeatureExtractor, extract_features
from sentinel.sim.labels import build_labels
from sentinel.sim.runner import SimConfig, simulate
from sentinel.sim.types import ASK, BID, CANCEL, EXEC, MODIFY, NEW, NS_PER_SEC

SNAP = (100, 102, 1000, 1000, 5000, 5000)  # best_bid, best_ask, bid_qty1, ask_qty1, bid_depth5, ask_depth5


def E(t_s, etype, oid, trader, side, price, qty, cp=0, snap=SNAP, inst="A"):
    """Build one event tuple in the order WindowFeatureExtractor.process expects."""
    return (int(t_s * NS_PER_SEC), inst, etype, oid, trader, cp, side, price, qty) + snap


def feed(events, window_s=10):
    ex = WindowFeatureExtractor(window_s)
    rows = []
    for e in events:
        rows += ex.process(*e)
    rows += ex.flush()
    return {(r.trader_id, r.window): dict(zip(FEATURE_NAMES, r.values)) for r in rows}


def test_feature_names_are_unique_and_contain_no_identity_or_time_tokens() -> None:
    assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES) == 22
    forbidden = {"id", "ts", "instrument", "window", "timestamp", "time"}
    for name in FEATURE_NAMES:
        assert not forbidden & set(name.split("_")), name


def test_rows_are_emitted_when_the_next_window_starts_and_on_flush() -> None:
    ex = WindowFeatureExtractor(10)
    assert ex.process(*E(1.0, NEW, 1, 7, BID, 100, 10)) == []
    assert ex.process(*E(2.0, NEW, 2, 8, BID, 100, 10)) == []
    closed = ex.process(*E(11.0, NEW, 3, 7, BID, 100, 10))  # first event of window 1
    assert [(r.trader_id, r.window) for r in closed] == [(7, 0), (8, 0)]
    assert [(r.trader_id, r.window) for r in ex.flush()] == [(7, 1)]
    assert ex.flush() == []


def test_out_of_order_events_raise() -> None:
    ex = WindowFeatureExtractor(10)
    ex.process(*E(5.0, NEW, 1, 7, BID, 100, 10))
    with pytest.raises(ValueError):
        ex.process(*E(4.0, NEW, 2, 7, BID, 100, 10))


def test_counts_cancel_rate_and_order_to_trade_ratio() -> None:
    out = feed([
        E(0.0, NEW, 1, 1, BID, 100, 10), E(0.1, NEW, 2, 1, BID, 100, 10),
        E(0.2, NEW, 3, 1, BID, 100, 10), E(0.3, NEW, 4, 1, BID, 100, 10),
        E(0.4, CANCEL, 1, 1, BID, 100, 10), E(0.5, CANCEL, 2, 1, BID, 100, 10),
        E(0.6, EXEC, 3, 1, BID, 100, 10, cp=9),
    ])
    f = out[(1, 0)]
    assert (f["n_new"], f["n_cancel"], f["n_fills"], f["n_modify"]) == (4, 2, 1, 0)
    assert f["cancel_rate"] == 0.5 and f["order_to_trade_ratio"] == 4.0
    assert f["total_new_qty"] == 40 and f["total_fill_qty"] == 10


def test_cancel_lifetimes_and_quick_cancel_shares() -> None:
    out = feed([
        E(0.0, NEW, 1, 1, BID, 100, 10), E(0.5, CANCEL, 1, 1, BID, 100, 10),  # lifetime 0.5
        E(1.0, NEW, 2, 1, BID, 100, 10), E(3.0, CANCEL, 2, 1, BID, 100, 10),  # lifetime 2.0
        E(4.0, NEW, 3, 1, BID, 100, 10), E(9.0, CANCEL, 3, 1, BID, 100, 10),  # lifetime 5.0
    ])
    f = out[(1, 0)]
    assert f["cancel_within_1s_share"] == pytest.approx(1 / 3)
    assert f["cancel_within_3s_share"] == pytest.approx(2 / 3)
    assert f["median_cancelled_lifetime_s"] == 2.0


def test_a_cancel_in_a_later_window_still_finds_its_order() -> None:
    out = feed([E(9.9, NEW, 1, 1, BID, 100, 10), E(10.2, CANCEL, 1, 1, BID, 100, 10)])
    f = out[(1, 1)]
    assert f["n_new"] == 0 and f["n_cancel"] == 1
    assert f["median_cancelled_lifetime_s"] == pytest.approx(0.3)
    assert f["cancel_within_1s_share"] == 1.0  # share is capped at 1 when the window has no new orders


def test_no_cancels_gives_sentinel_lifetime() -> None:
    assert feed([E(0.0, NEW, 1, 1, BID, 100, 10)])[(1, 0)]["median_cancelled_lifetime_s"] == -1.0


def test_size_relative_to_best_level_quantity() -> None:
    out = feed([E(0.0, NEW, 1, 1, BID, 100, 3000), E(0.1, NEW, 2, 1, BID, 100, 500)])
    f = out[(1, 0)]
    assert f["max_order_qty"] == 3000 and f["max_qty_over_best"] == 3.0  # bid_qty1 is 1000


def test_market_orders_do_not_count_as_resting_size() -> None:
    f = feed([E(0.0, NEW, 1, 1, BID, 0, 9999)])[(1, 0)]
    assert f["n_new"] == 1 and f["max_order_qty"] == 0 and f["total_new_qty"] == 0


def test_peak_resting_over_five_level_depth() -> None:
    snap = (100, 102, 1000, 1000, 2000, 5000)
    out = feed([E(0.0, NEW, 1, 1, BID, 100, 500, snap=snap), E(0.1, NEW, 2, 1, BID, 99, 500, snap=snap)])
    assert out[(1, 0)]["peak_resting_over_depth5"] == 0.5


def test_layering_levels_and_cancel_burst() -> None:
    events = [E(0.00, NEW, 1, 1, BID, 100, 10), E(0.01, NEW, 2, 1, BID, 99, 10), E(0.02, NEW, 3, 1, BID, 98, 10),
              E(1.00, CANCEL, 1, 1, BID, 100, 10), E(1.01, CANCEL, 2, 1, BID, 99, 10),
              E(1.02, CANCEL, 3, 1, BID, 98, 10)]
    f = feed(events)[(1, 0)]
    assert f["max_live_levels_same_side"] == 3 and f["max_cancel_burst_100ms"] == 3


def test_two_orders_at_one_price_are_one_level() -> None:
    f = feed([E(0.0, NEW, 1, 1, BID, 100, 10), E(0.1, NEW, 2, 1, BID, 100, 10)])[(1, 0)]
    assert f["max_live_levels_same_side"] == 1


def test_fills_and_modifies_reduce_live_size_and_free_the_level() -> None:
    ex = WindowFeatureExtractor(10)
    for e in [E(0.0, NEW, 1, 1, BID, 100, 10), E(0.1, MODIFY, 1, 1, BID, 100, 8),
              E(0.2, EXEC, 1, 1, BID, 100, 3, cp=5), E(0.3, EXEC, 1, 1, BID, 100, 5, cp=5)]:
        ex.process(*e)
    trader = ex._inst["A"].traders[1]
    assert trader.resting[BID] == 0 and trader.live == {} and trader.levels[BID] == {}


def test_wash_features_roundtrip_with_one_counterparty() -> None:
    out = feed([E(0.0, EXEC, 1, 1, ASK, 101, 100, cp=2), E(0.5, EXEC, 2, 1, BID, 101, 100, cp=2)])
    f = out[(1, 0)]
    assert f["net_over_gross"] == 0.0 and f["top_counterparty_share"] == 1.0
    assert f["n_counterparties"] == 1 and f["roundtrip_counterparty_share"] == 1.0


def test_diverse_counterparties_are_not_a_roundtrip() -> None:
    out = feed([E(0.0, EXEC, 1, 1, BID, 101, 100, cp=3), E(0.5, EXEC, 2, 1, ASK, 101, 100, cp=4)])
    f = out[(1, 0)]
    assert f["top_counterparty_share"] == 0.5 and f["n_counterparties"] == 2
    assert f["roundtrip_counterparty_share"] == 0.0


def test_no_fills_defaults() -> None:
    f = feed([E(0.0, NEW, 1, 1, BID, 100, 10)])[(1, 0)]
    assert f["net_over_gross"] == 1.0 and f["top_counterparty_share"] == 0.0
    assert f["opposite_fill_share"] == 0.0 and f["n_counterparties"] == 0


def test_opposite_fill_share_uses_the_peak_resting_side() -> None:
    both = feed([E(0.0, NEW, 1, 1, BID, 100, 1000), E(0.1, EXEC, 8, 1, ASK, 101, 100, cp=5),
                 E(0.2, EXEC, 9, 1, BID, 101, 100, cp=6)])[(1, 0)]
    only_sells = feed([E(0.0, NEW, 1, 1, BID, 100, 1000), E(0.1, EXEC, 8, 1, ASK, 101, 100, cp=5)])[(1, 0)]
    assert both["opposite_fill_share"] == 0.5 and only_sells["opposite_fill_share"] == 1.0


def test_signed_mid_drift_follows_the_peak_resting_side() -> None:
    up = (101, 103, 1000, 1000, 5000, 5000)  # mid 102 vs 101 at the start
    bid_peak = feed([E(0.0, NEW, 1, 1, BID, 100, 500), E(1.0, CANCEL, 1, 1, BID, 100, 500, snap=up)])[(1, 0)]
    ask_peak = feed([E(0.0, NEW, 1, 1, ASK, 102, 500), E(1.0, CANCEL, 1, 1, ASK, 102, 500, snap=up)])[(1, 0)]
    assert bid_peak["signed_mid_drift_ticks"] == 1.0 and ask_peak["signed_mid_drift_ticks"] == -1.0


def test_extractor_windows_match_label_windows_on_a_real_simulation() -> None:
    res = simulate(SimConfig(instruments=("A", "B"), events_per_instrument=20_000,
                             episodes_per_instrument=15, seed=5))
    feats = extract_features(res.events, 10)
    labels = build_labels(res.events, res.event_episode, res.episodes, 10 * NS_PER_SEC)
    key = ["trader_id", "instrument", "window"]
    assert len(feats) == len(labels)
    assert set(map(tuple, feats[key].to_numpy().tolist())) == set(map(tuple, labels[key].to_numpy().tolist()))
    assert np.isfinite(feats[list(FEATURE_NAMES)].to_numpy()).all()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.features.extractor'`

- [ ] **Step 4: Write `extractor.py`**

```python
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


def extract_features(events: pd.DataFrame, window_s: int = 10) -> pd.DataFrame:
    """Replay an event log (time-ordered) through the streaming extractor."""
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
    extractor = WindowFeatureExtractor(window_s)
    rows: list[FeatureRow] = []
    for event in zip(*columns):
        rows += extractor.process(*event)
    rows += extractor.flush()
    return rows_to_frame(rows)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_features.py -v`
Expected: all pass. If a hand-computed test fails, recompute the expectation by hand from the events before deciding whether the test or the code is wrong; report the reasoning. Never weaken an assertion to make it pass.

- [ ] **Step 6: Measure throughput on real data (information for Stage 3)**

Run:
```bash
python - <<'EOF'
import time, pandas as pd
from sentinel.features.extractor import extract_features
ev = pd.read_parquet("data/sim_seed7/events.parquet")
t = time.perf_counter(); f = extract_features(ev); dt = time.perf_counter() - t
print(f"{len(ev):,} events -> {len(f):,} feature rows in {dt:.1f}s ({len(ev)/dt:,.0f} events/s)")
print(f[["cancel_rate","max_qty_over_best","net_over_gross"]].describe().loc[["mean","50%","max"]])
EOF
```
Expected: it runs to completion and prints the throughput. Report it. Do not look at labels here.

- [ ] **Step 7: Commit**

```bash
git add src/sentinel/features tests/test_features.py
git commit -m "$(cat <<'EOF'
feat(features): streaming per-trader-window feature extractor

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Dataset loading and the leak-free split

**Concept to explain:** windows of one episode are correlated, so the split must never put them on both sides. Time-ordered split, drop episodes that straddle the boundary, drop the two windows at the boundary, and keep the fresh-simulation test set locked behind an explicit `final=True` and an access log.

**Files:**
- Create: `src/sentinel/detect/__init__.py`, `src/sentinel/detect/dataset.py`, `scripts/build_features.py`
- Test: `tests/test_dataset.py`

- [ ] **Step 1: Write `src/sentinel/detect/__init__.py`**

```python
"""Detectors: rules, models, explanations and alerts."""
```

- [ ] **Step 2: Write the failing tests**

`tests/test_dataset.py`:
```python
"""Tests for dataset loading, the leak-free split and the test-set guard."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.detect import dataset
from sentinel.detect.dataset import CLASSES, load_sim, time_split, to_xy
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.sim.labels import write_dataset
from sentinel.sim.runner import SimConfig, simulate


def _windows(sim: str, episodes: dict[int, tuple[int, int]], n_windows: int = 100) -> pd.DataFrame:
    """One NORMAL row per window plus one row per window of each episode (id -> first, last window)."""
    rows = [{"sim": sim, "window": w, "episode_id": -1, "label": "NORMAL"} for w in range(n_windows)]
    for eid, (first, last) in episodes.items():
        rows += [{"sim": sim, "window": w, "episode_id": eid, "label": "SPOOFING"} for w in range(first, last + 1)]
    return pd.DataFrame(rows)


def test_split_is_time_ordered_with_boundary_windows_dropped() -> None:
    train, val = time_split(_windows("s", {}), train_frac=0.7)
    assert train.window.max() == 68 and val.window.min() == 71  # boundary is 70: windows 69, 70 dropped
    assert train.window.max() < val.window.min()


def test_episodes_never_appear_on_both_sides_and_straddlers_are_dropped() -> None:
    eps = {1: (10, 12), 2: (68, 72), 3: (69, 69), 4: (80, 82), 5: (70, 70)}
    train, val = time_split(_windows("s", eps), train_frac=0.7)
    in_train, in_val = set(train.episode_id) - {-1}, set(val.episode_id) - {-1}
    assert in_train == {1} and in_val == {4}  # 2 straddles; 3 and 5 sit in dropped boundary windows


def test_split_is_applied_per_simulation() -> None:
    both = pd.concat([_windows("a", {}, 100), _windows("b", {}, 50)])
    train, val = time_split(both, train_frac=0.7)
    assert train[train.sim == "b"].window.max() == 33 and val[val.sim == "b"].window.min() == 36


def test_to_xy_orders_columns_and_maps_labels() -> None:
    df = pd.DataFrame({name: [1.0, 2.0] for name in FEATURE_NAMES})
    df["label"] = ["NORMAL", "WASH"]
    X, y = to_xy(df)
    assert X.shape == (2, len(FEATURE_NAMES)) and list(y) == [0, 3] and CLASSES[3] == "WASH"


def test_load_sim_joins_features_and_labels_one_to_one(tmp_path) -> None:
    cfg = SimConfig(instruments=("A",), events_per_instrument=8_000, episodes_per_instrument=8, seed=2)
    write_dataset(simulate(cfg), tmp_path / "sim_seed2", cfg.window_s)
    df = load_sim(tmp_path / "sim_seed2")
    assert set(FEATURE_NAMES) <= set(df.columns) and {"label", "difficulty", "episode_id", "sim"} <= set(df.columns)
    assert df.sim.eq("sim_seed2").all() and set(df.label) >= {"NORMAL"}
    assert (tmp_path / "sim_seed2" / "features.parquet").exists()
    again = load_sim(tmp_path / "sim_seed2")  # second call reads the cache
    pd.testing.assert_frame_equal(df, again)


def test_test_simulations_are_locked_without_final_and_access_is_logged(tmp_path, monkeypatch) -> None:
    cfg = SimConfig(instruments=("A",), events_per_instrument=5_000, episodes_per_instrument=5, seed=3)
    write_dataset(simulate(cfg), tmp_path / "test_seed3", cfg.window_s)
    monkeypatch.setattr(dataset, "ACCESS_LOG", tmp_path / "test_access.log")
    with pytest.raises(PermissionError):
        load_sim(tmp_path / "test_seed3")
    assert not (tmp_path / "test_access.log").exists()
    load_sim(tmp_path / "test_seed3", final=True)
    assert "test_seed3" in (tmp_path / "test_access.log").read_text()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_dataset.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.detect.dataset'`

- [ ] **Step 4: Write `dataset.py`**

```python
"""Load feature+label tables, split them without leakage, and guard the final test set."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from sentinel.features.extractor import FEATURE_NAMES, extract_features

CLASSES = ("NORMAL", "SPOOFING", "LAYERING", "WASH")
CLASS_INDEX = {name: i for i, name in enumerate(CLASSES)}
ACCESS_LOG = Path("data/test_access.log")
_KEY = ["trader_id", "instrument", "window"]
_LABEL_COLUMNS = ["window_start_ns", "n_events", "label", "difficulty", "episode_id"]


def _log_access(sim_dir: Path) -> None:
    ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with ACCESS_LOG.open("a", encoding="utf-8") as log:
        log.write(f"{stamp}\t{sim_dir}\n")


def load_sim(sim_dir: Path, window_s: int = 10, final: bool = False, rebuild: bool = False) -> pd.DataFrame:
    """Features joined with labels for one simulation directory.

    Directories named ``test_*`` are the fresh final test simulation: they can
    only be loaded with ``final=True`` and every load is appended to the access
    log, so single use is auditable. Features are cached in features.parquet
    and rebuilt if the cache is missing, stale (columns differ) or ``rebuild``.
    """
    sim_dir = Path(sim_dir)
    if sim_dir.name.startswith("test_"):
        if not final:
            raise PermissionError(f"{sim_dir.name} is the final test set; pass final=True (logged) to load it")
        _log_access(sim_dir)
    cache = sim_dir / "features.parquet"
    feats = None
    if cache.exists() and not rebuild:
        cached = pd.read_parquet(cache)
        if list(cached.columns) == ["trader_id", "instrument", "window", *FEATURE_NAMES]:
            feats = cached
    if feats is None:
        feats = extract_features(pd.read_parquet(sim_dir / "events.parquet"), window_s)
        feats.to_parquet(cache, index=False)
    labels = pd.read_parquet(sim_dir / "labels.parquet")
    df = feats.merge(labels[_KEY + _LABEL_COLUMNS], on=_KEY, how="inner", validate="one_to_one")
    if not (len(df) == len(feats) == len(labels)):
        raise ValueError("feature rows and label rows do not match one-to-one")
    df["sim"] = sim_dir.name
    return df


def time_split(df: pd.DataFrame, train_frac: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Leak-free train/validation split, applied per simulation.

    Boundary B = int(train_frac * n_windows). Train gets windows <= B-2 and
    validation windows >= B+1, so the two windows at the boundary are dropped.
    Any episode with rows in both parts, or in the dropped windows, is removed
    from both, so no episode contributes to more than one side.
    """
    train_parts, val_parts = [], []
    for _, g in df.groupby("sim", sort=False):
        boundary = int(train_frac * (g["window"].max() + 1))
        train, val = g[g["window"] <= boundary - 2], g[g["window"] >= boundary + 1]
        gap = g[g["window"].isin([boundary - 1, boundary])]
        drop = (set(train["episode_id"]) & set(val["episode_id"])) | set(gap["episode_id"])
        drop.discard(-1)
        train_parts.append(train[~train["episode_id"].isin(drop)])
        val_parts.append(val[~val["episode_id"].isin(drop)])
    return pd.concat(train_parts, ignore_index=True), pd.concat(val_parts, ignore_index=True)


def to_xy(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Feature matrix (columns in FEATURE_NAMES order) and integer class labels."""
    return df[list(FEATURE_NAMES)].to_numpy(dtype=np.float64), df["label"].map(CLASS_INDEX).to_numpy()
```

- [ ] **Step 5: Write `scripts/build_features.py`**

```python
"""Build (or rebuild) the cached feature table for a simulation directory.

python scripts/build_features.py data/sim_seed7 [--rebuild]
Test directories (test_*) are refused: they are loaded only via load_sim(final=True).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sentinel.detect.dataset import load_sim


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sim_dir", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    df = load_sim(args.sim_dir, rebuild=args.rebuild)
    print(f"{args.sim_dir}: {len(df):,} trader-windows, {(df.label != 'NORMAL').sum():,} manipulation windows")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests, then build the three training/validation feature caches**

Run: `python -m pytest tests/test_dataset.py -v` then the full suite `python -m pytest -q`
Expected: all pass.

Run: `python scripts/build_features.py data/sim_seed7 && python scripts/build_features.py data/sim_seed8 && python scripts/build_features.py data/sim_seed9`
Expected: three lines with counts. Do NOT run anything against `data/test_seed100`.

- [ ] **Step 7: Commit**

```bash
git add src/sentinel/detect scripts/build_features.py tests/test_dataset.py
git commit -m "$(cat <<'EOF'
feat(detect): dataset loading, leak-free time split, test-set guard

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Metrics and the rules baseline (with pre-registration)

**Concept to explain:** precision is "of the alerts, how many are real", recall is "of the real cases, how many did we catch", FPR is "of the innocent windows, how many did we flag". For each type, the negatives are all NORMAL windows and other types are excluded. A rule is a conjunction of threshold conditions and can show *why* it fired.

**The pre-registration constraint (very important):** the textbook thresholds below are fixed *now*, before any rule is evaluated on validation data. Commit them alone, tag the commit `rules-preregistered`, and only then write the tuning code and run any evaluation. If you change a textbook threshold after seeing a result, you have broken the pre-registration: do not.

**Files:**
- Create: `src/sentinel/eval/__init__.py`, `src/sentinel/eval/metrics.py`, `src/sentinel/detect/rules.py`
- Test: `tests/test_metrics.py`, `tests/test_rules.py`

- [ ] **Step 1: Metrics: write `src/sentinel/eval/__init__.py`, the failing tests, then `metrics.py`**

`src/sentinel/eval/__init__.py`:
```python
"""Evaluation metrics and reports."""
```

`tests/test_metrics.py`:
```python
"""Tests for the evaluation metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.eval.metrics import any_metrics, binary_metrics, recall_by_difficulty, results_table, type_metrics


def test_binary_metrics_confusion_counts() -> None:
    y = np.array([1, 1, 1, 0, 0, 0, 0, 0], dtype=bool)
    p = np.array([1, 1, 0, 1, 0, 0, 0, 0], dtype=bool)
    m = binary_metrics(y, p)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 4)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3) and m["fpr"] == pytest.approx(1 / 5)


def test_binary_metrics_handles_empty_denominators() -> None:
    m = binary_metrics(np.zeros(3, dtype=bool), np.zeros(3, dtype=bool))
    assert m["precision"] == 0.0 and m["recall"] == 0.0 and m["f1"] == 0.0 and m["fpr"] == 0.0


def test_type_metrics_excludes_other_types() -> None:
    labels = pd.Series(["NORMAL", "NORMAL", "SPOOFING", "WASH", "SPOOFING"])
    flagged = np.array([False, True, True, True, False])
    m = type_metrics(labels, flagged, "SPOOFING")
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)  # the WASH row is ignored


def test_any_metrics_treats_every_type_as_positive() -> None:
    labels = pd.Series(["NORMAL", "SPOOFING", "WASH", "NORMAL"])
    m = any_metrics(labels, np.array([False, True, False, True]))
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)


def test_results_table_and_recall_by_difficulty() -> None:
    df = pd.DataFrame({"label": ["NORMAL", "SPOOFING", "SPOOFING", "SPOOFING"],
                       "difficulty": ["none", "easy", "easy", "hard"]})
    preds = {"det": {"SPOOFING": np.array([False, True, True, False])}}
    table = results_table(df["label"], preds)
    assert list(table.columns[:3]) == ["detector", "type", "precision"] and table.iloc[0]["recall"] == pytest.approx(2 / 3)
    rec = recall_by_difficulty(df, preds)
    assert rec.set_index(["detector", "type", "difficulty"]).loc[("det", "SPOOFING", "easy"), "recall"] == 1.0
    assert rec.set_index(["detector", "type", "difficulty"]).loc[("det", "SPOOFING", "hard"), "recall"] == 0.0
```

`src/sentinel/eval/metrics.py`:
```python
"""Precision, recall, F1 and false-positive rate, per manipulation type."""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


def binary_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Confusion counts and precision / recall / F1 / FPR (0.0 where undefined)."""
    y_true, y_pred = np.asarray(y_true, dtype=bool), np.asarray(y_pred, dtype=bool)
    tp = int((y_true & y_pred).sum())
    fp = int((~y_true & y_pred).sum())
    fn = int((y_true & ~y_pred).sum())
    tn = int((~y_true & ~y_pred).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "fpr": fpr, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def type_metrics(labels: pd.Series, flagged: np.ndarray, kind: str) -> dict[str, float]:
    """One type versus all NORMAL windows; windows of other types are excluded."""
    keep = labels.isin([kind, "NORMAL"]).to_numpy()
    return binary_metrics((labels.to_numpy() == kind)[keep], np.asarray(flagged)[keep])


def any_metrics(labels: pd.Series, flagged: np.ndarray) -> dict[str, float]:
    """Any manipulation type versus NORMAL."""
    return binary_metrics((labels != "NORMAL").to_numpy(), np.asarray(flagged))


def results_table(labels: pd.Series, preds: Mapping[str, Mapping[str, np.ndarray]]) -> pd.DataFrame:
    """One row per (detector, type). ``preds[detector][type]`` is a boolean flag array; type 'ANY' is allowed."""
    rows = []
    for detector, by_type in preds.items():
        for kind, flagged in by_type.items():
            m = any_metrics(labels, flagged) if kind == "ANY" else type_metrics(labels, flagged, kind)
            rows.append({"detector": detector, "type": kind, **m})
    return pd.DataFrame(rows)


def recall_by_difficulty(df: pd.DataFrame, preds: Mapping[str, Mapping[str, np.ndarray]]) -> pd.DataFrame:
    """Recall of each detector on each type's windows, split by difficulty."""
    rows = []
    for detector, by_type in preds.items():
        for kind, flagged in by_type.items():
            if kind == "ANY":
                continue
            flagged = np.asarray(flagged)
            for level in ("easy", "medium", "hard"):
                mask = ((df["label"] == kind) & (df["difficulty"] == level)).to_numpy()
                if mask.any():
                    rows.append({"detector": detector, "type": kind, "difficulty": level,
                                 "n": int(mask.sum()), "recall": float(flagged[mask].mean())})
    return pd.DataFrame(rows)
```

Run: `python -m pytest tests/test_metrics.py -v`. Expected: fail first with `ModuleNotFoundError`, then all pass after `metrics.py` exists.

- [ ] **Step 2: Write the rule definitions with the textbook thresholds (NO tuning code yet)**

Create `src/sentinel/detect/rules.py` with exactly this content:

```python
"""Industry-style surveillance rules: textbook thresholds and a tuner.

PRE-REGISTRATION: the thresholds in TEXTBOOK_RULES and GENERIC_RULE were fixed
from the standard definitions of each behaviour BEFORE any rule was evaluated
on validation data (see git tag `rules-preregistered`). They must not be edited
after results are seen. Tuned variants are produced separately by ``tune_rule``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Condition:
    """One threshold test on one feature."""

    feature: str
    op: str  # ">=" or "<="
    threshold: float

    def holds(self, values: np.ndarray) -> np.ndarray:
        """Boolean array: does each value satisfy the condition?"""
        return values >= self.threshold if self.op == ">=" else values <= self.threshold

    def describe(self, value: float) -> str:
        """Human-readable evidence line for a triggering value."""
        return f"{self.feature} = {value:.3g} ({self.op} {self.threshold:.3g})"


@dataclass(frozen=True)
class Rule:
    """A conjunction ("all") or disjunction ("any") of conditions for one manipulation type."""

    kind: str
    conditions: tuple[Condition, ...]
    mode: str = "all"

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        """Boolean flag per row of a feature table."""
        masks = [c.holds(df[c.feature].to_numpy()) for c in self.conditions]
        return np.logical_and.reduce(masks) if self.mode == "all" else np.logical_or.reduce(masks)

    def trace(self, row: Mapping[str, float]) -> list[str]:
        """Evidence lines: the conditions that hold for this row, with their values."""
        return [c.describe(row[c.feature]) for c in self.conditions if bool(c.holds(np.asarray(row[c.feature])))]


TEXTBOOK_RULES: dict[str, Rule] = {
    # Spoofing: large orders relative to the book, mostly cancelled quickly, while trading the other side.
    "SPOOFING": Rule("SPOOFING", (
        Condition("cancel_rate", ">=", 0.5),
        Condition("max_qty_over_best", ">=", 3.0),
        Condition("cancel_within_3s_share", ">=", 0.3),
        Condition("opposite_fill_share", ">=", 0.5),
    )),
    # Layering: several price levels stacked on one side and pulled together.
    "LAYERING": Rule("LAYERING", (
        Condition("max_live_levels_same_side", ">=", 4.0),
        Condition("max_cancel_burst_100ms", ">=", 4.0),
        Condition("cancel_rate", ">=", 0.5),
    )),
    # Wash trading: repeated trades with one counterparty and no net position change.
    "WASH": Rule("WASH", (
        Condition("top_counterparty_share", ">=", 0.8),
        Condition("net_over_gross", "<=", 0.1),
        Condition("n_fills", ">=", 4.0),
    )),
}

# The classic untyped screen: extreme cancel rate or order-to-trade ratio.
GENERIC_RULE = Rule("ANY", (
    Condition("cancel_rate", ">=", 0.9),
    Condition("order_to_trade_ratio", ">=", 50.0),
), mode="any")
```

- [ ] **Step 3: Commit the pre-registration and tag it BEFORE anything else**

```bash
git add src/sentinel/eval src/sentinel/detect/rules.py tests/test_metrics.py
git commit -m "$(cat <<'EOF'
feat(detect): metrics and pre-registered textbook rule thresholds

Thresholds fixed from standard definitions before any rule evaluation.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
git tag -a rules-preregistered -m "Textbook rule thresholds fixed before any validation evaluation"
```

- [ ] **Step 4: Write the failing rule tests** (`tests/test_rules.py`)

```python
"""Tests for rule evaluation, traces and threshold tuning."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES, Condition, Rule, tune_rule
from sentinel.eval.metrics import type_metrics


def _table() -> pd.DataFrame:
    return pd.DataFrame({
        "cancel_rate": [0.9, 0.2, 0.95, 0.6],
        "order_to_trade_ratio": [1.0, 60.0, 2.0, 1.0],
        "label": ["NORMAL", "NORMAL", "SPOOFING", "SPOOFING"],
    })


def test_all_mode_requires_every_condition_and_any_mode_requires_one() -> None:
    df = _table()
    conj = Rule("X", (Condition("cancel_rate", ">=", 0.5), Condition("order_to_trade_ratio", "<=", 1.5)))
    assert list(conj.evaluate(df)) == [True, False, False, True]
    assert list(GENERIC_RULE.evaluate(df)) == [True, True, True, False]


def test_trace_lists_only_conditions_that_hold_with_their_values() -> None:
    rule = Rule("X", (Condition("cancel_rate", ">=", 0.5), Condition("order_to_trade_ratio", ">=", 50.0)), mode="any")
    lines = rule.trace({"cancel_rate": 0.9, "order_to_trade_ratio": 1.0})
    assert lines == ["cancel_rate = 0.9 (>= 0.5)"]


def test_textbook_rules_are_the_preregistered_ones() -> None:
    assert set(TEXTBOOK_RULES) == {"SPOOFING", "LAYERING", "WASH"}
    assert TEXTBOOK_RULES["WASH"].conditions[0] == Condition("top_counterparty_share", ">=", 0.8)
    assert TEXTBOOK_RULES["SPOOFING"].conditions[1] == Condition("max_qty_over_best", ">=", 3.0)


def _separable(n: int = 600, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    label = np.where(rng.random(n) < 0.15, "WASH", "NORMAL")
    pos = label == "WASH"
    return pd.DataFrame({
        "label": label,
        "top_counterparty_share": np.where(pos, rng.uniform(0.6, 1.0, n), rng.uniform(0.0, 0.7, n)),
        "net_over_gross": np.where(pos, rng.uniform(0.0, 0.3, n), rng.uniform(0.2, 1.0, n)),
        "n_fills": np.where(pos, rng.integers(3, 12, n), rng.integers(0, 8, n)).astype(float),
    })


def test_tuning_never_does_worse_than_the_textbook_rule_on_the_tuning_data() -> None:
    df = _separable()
    textbook = TEXTBOOK_RULES["WASH"]
    tuned = tune_rule(textbook, df, n_draws=2_000, seed=1)
    f1_text = type_metrics(df["label"], textbook.evaluate(df), "WASH")["f1"]
    f1_tuned = type_metrics(df["label"], tuned.evaluate(df), "WASH")["f1"]
    assert f1_tuned >= f1_text and f1_tuned > 0.8
    assert [c.feature for c in tuned.conditions] == [c.feature for c in textbook.conditions]
    assert [c.op for c in tuned.conditions] == [c.op for c in textbook.conditions]


def test_tuning_is_deterministic_for_a_seed() -> None:
    df = _separable()
    a = tune_rule(TEXTBOOK_RULES["WASH"], df, n_draws=500, seed=3)
    b = tune_rule(TEXTBOOK_RULES["WASH"], df, n_draws=500, seed=3)
    assert a == b


def test_tuning_ignores_windows_of_other_types() -> None:
    df = _separable()
    extra = df.iloc[:60].copy()
    extra["label"] = "SPOOFING"  # feature values that look like WASH but are labeled as another type
    a = tune_rule(TEXTBOOK_RULES["WASH"], df, n_draws=300, seed=4)
    b = tune_rule(TEXTBOOK_RULES["WASH"], pd.concat([df, extra], ignore_index=True), n_draws=300, seed=4)
    assert a == b
```

Run: `python -m pytest tests/test_rules.py -v`
Expected: the textbook/evaluate/trace tests pass; the tuning tests FAIL with `ImportError: cannot import name 'tune_rule'`.

- [ ] **Step 5: Append `tune_rule` to `rules.py`**

Add at the end of `src/sentinel/detect/rules.py` (do NOT modify anything above it):

```python
# Search ranges for each feature (wide on purpose: the data decides, not us).
_RANGES: dict[str, tuple[float, float]] = {
    "cancel_rate": (0.0, 1.0), "max_qty_over_best": (0.5, 12.0), "cancel_within_3s_share": (0.0, 1.0),
    "opposite_fill_share": (0.0, 1.0), "max_live_levels_same_side": (1.0, 8.0),
    "max_cancel_burst_100ms": (1.0, 10.0), "top_counterparty_share": (0.0, 1.0),
    "net_over_gross": (0.0, 1.0), "n_fills": (1.0, 12.0),
}


def tune_rule(template: Rule, df: pd.DataFrame, n_draws: int = 20_000, seed: int = 0,
              refine_rounds: int = 2) -> Rule:
    """Tune a conjunctive rule's thresholds to maximize F1 on ``df`` (the validation set).

    Only windows of the rule's own type and NORMAL windows are scored. The search
    starts from the template's (textbook) thresholds, tries ``n_draws`` random
    threshold vectors, then refines one threshold at a time over the quantiles of
    its feature, so the result is never worse than the template on ``df``.
    Ties on F1 go to higher precision.
    """
    sub = df[df["label"].isin([template.kind, "NORMAL"])]
    positives = (sub["label"] == template.kind).to_numpy()
    values = [sub[c.feature].to_numpy() for c in template.conditions]
    ops = [c.op for c in template.conditions]
    n_pos = int(positives.sum())

    def score(thresholds: list[float]) -> tuple[float, float]:
        masks = [(v >= t) if op == ">=" else (v <= t) for v, t, op in zip(values, thresholds, ops)]
        flagged = np.logical_and.reduce(masks)
        tp = int((flagged & positives).sum())
        fp = int((flagged & ~positives).sum())
        f1 = 2 * tp / (2 * tp + fp + (n_pos - tp)) if tp else 0.0
        precision = tp / (tp + fp) if tp + fp else 0.0
        return f1, precision

    rng = np.random.default_rng(seed)
    ranges = [_RANGES[c.feature] for c in template.conditions]
    best_t = [c.threshold for c in template.conditions]
    best = score(best_t)
    for _ in range(n_draws):
        candidate = [float(rng.uniform(lo, hi)) for lo, hi in ranges]
        s = score(candidate)
        if s > best:
            best, best_t = s, candidate
    for _ in range(refine_rounds):
        for i, v in enumerate(values):
            for cut in np.unique(np.quantile(v, np.linspace(0.0, 1.0, 101))):
                candidate = list(best_t)
                candidate[i] = float(cut)
                s = score(candidate)
                if s > best:
                    best, best_t = s, candidate
    conditions = tuple(Condition(c.feature, c.op, float(t)) for c, t in zip(template.conditions, best_t))
    return Rule(template.kind, conditions, "all")
```

Run: `python -m pytest tests/test_rules.py tests/test_metrics.py -v` then `python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/detect/rules.py tests/test_rules.py
git commit -m "$(cat <<'EOF'
feat(detect): rule threshold tuner (random search plus quantile refinement)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: LightGBM multiclass model, thresholds and Isolation Forest

**Concept to explain:** gradient boosting builds many small decision trees, each correcting the previous ones' mistakes, and the class scores are summed tree outputs. We weight rare classes up, stop early when validation log-loss stops improving, and keep trees small so the model can be ported to Java.

**Files:**
- Modify: `tests/conftest.py`
- Create: `src/sentinel/detect/model.py`
- Test: `tests/test_model.py`

- [ ] **Step 1: Add the synthetic fixture to `tests/conftest.py`** (append; keep existing content and imports, add the new imports at the top)

```python
import numpy as np
import pandas as pd

from sentinel.detect.dataset import CLASSES
from sentinel.features.extractor import FEATURE_NAMES


@pytest.fixture
def synthetic_windows() -> pd.DataFrame:
    """2,000 synthetic trader-windows where each manipulation class shifts a few features."""
    rng = np.random.default_rng(0)
    n = 2_000
    labels = rng.choice(len(CLASSES), size=n, p=[0.85, 0.05, 0.05, 0.05])
    X = rng.normal(size=(n, len(FEATURE_NAMES)))
    col = {name: i for i, name in enumerate(FEATURE_NAMES)}
    X[labels == 1, col["max_qty_over_best"]] += 4.0
    X[labels == 1, col["opposite_fill_share"]] += 3.0
    X[labels == 2, col["max_live_levels_same_side"]] += 4.0
    X[labels == 2, col["max_cancel_burst_100ms"]] += 3.0
    X[labels == 3, col["top_counterparty_share"]] += 4.0
    X[labels == 3, col["net_over_gross"]] -= 3.0
    df = pd.DataFrame(X, columns=list(FEATURE_NAMES))
    df["label"] = [CLASSES[i] for i in labels]
    df["difficulty"] = np.where(labels == 0, "none", "easy")
    return df
```

- [ ] **Step 2: Write the failing tests** (`tests/test_model.py`)

```python
"""Tests for the LightGBM classifier, alert thresholds and the Isolation Forest."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.detect.dataset import CLASSES, to_xy
from sentinel.detect.model import (IsolationForestDetector, choose_thresholds, class_weights, load_model,
                                   predict_proba, predict_types, save_model, train_lgbm)


def _split(df):
    return df.iloc[:1600], df.iloc[1600:]


def test_class_weights_upweight_rare_classes_and_respect_the_cap() -> None:
    y = np.array([0] * 90 + [1] * 10)
    w = class_weights(y)
    assert w[1] > w[0] and w[1] == pytest.approx(100 / (4 * 10))
    assert class_weights(np.array([0] * 999 + [1]))[1] == 20.0  # capped
    assert class_weights(y)[3] == 20.0  # absent class gets the cap


def test_lgbm_learns_separable_classes_and_returns_valid_probabilities(synthetic_windows) -> None:
    train, val = _split(synthetic_windows)
    booster = train_lgbm(train, val, seed=1)
    X, y = to_xy(val)
    proba = predict_proba(booster, X)
    assert proba.shape == (len(val), 4) and np.allclose(proba.sum(axis=1), 1.0)
    assert (proba.argmax(axis=1) == y).mean() > 0.9


def test_booster_is_truncated_to_the_best_iteration(synthetic_windows) -> None:
    booster = train_lgbm(*_split(synthetic_windows), seed=1)
    assert booster.num_trees() % 4 == 0 and booster.num_trees() <= 4 * 500
    assert booster.best_iteration in (0, -1) or booster.best_iteration == booster.num_trees() // 4


def test_training_is_deterministic(synthetic_windows) -> None:
    train, val = _split(synthetic_windows)
    X = to_xy(val)[0]
    a = predict_proba(train_lgbm(train, val, seed=5), X)
    b = predict_proba(train_lgbm(train, val, seed=5), X)
    assert np.allclose(a, b)


def test_thresholds_maximize_per_type_f1_and_predict_types_takes_the_highest_probability() -> None:
    labels = pd.Series(["NORMAL"] * 6 + ["SPOOFING"] * 2 + ["LAYERING"] * 2 + ["WASH"] * 2)
    proba = np.full((12, 4), 0.02)
    proba[:6, 0] = 0.9
    proba[6:8, 1] = 0.9
    proba[8:10, 2] = 0.8
    proba[10:12, 3] = 0.7
    thresholds = choose_thresholds(proba, labels)
    assert set(thresholds) == {"SPOOFING", "LAYERING", "WASH"}
    assert (predict_types(proba, thresholds) == np.array([0] * 6 + [1] * 2 + [2] * 2 + [3] * 2)).all()
    both = np.array([[0.1, 0.6, 0.7, 0.0]])
    assert predict_types(both, {"SPOOFING": 0.5, "LAYERING": 0.5, "WASH": 0.5})[0] == 2
    none = np.array([[0.9, 0.05, 0.05, 0.0]])
    assert predict_types(none, {"SPOOFING": 0.5, "LAYERING": 0.5, "WASH": 0.5})[0] == 0


def test_save_and_load_round_trip(synthetic_windows, tmp_path) -> None:
    train, val = _split(synthetic_windows)
    booster = train_lgbm(train, val, seed=2)
    thresholds = {"SPOOFING": 0.4, "LAYERING": 0.5, "WASH": 0.6}
    save_model(booster, thresholds, tmp_path)
    loaded, loaded_thresholds = load_model(tmp_path)
    X = to_xy(val)[0]
    assert np.allclose(predict_proba(booster, X), predict_proba(loaded, X)) and loaded_thresholds == thresholds


def test_isolation_forest_scores_outliers_higher_and_picks_an_f1_threshold(synthetic_windows) -> None:
    train, val = _split(synthetic_windows)
    detector = IsolationForestDetector(seed=1).fit(to_xy(train)[0])
    Xv = to_xy(val)[0]
    scores = detector.score(Xv)
    is_manip = (val["label"] != "NORMAL").to_numpy()
    assert scores[is_manip].mean() > scores[~is_manip].mean()
    thr = detector.choose_threshold(scores, val["label"])
    flagged = detector.predict(Xv, thr)
    assert flagged.shape == (len(val),) and flagged[is_manip].mean() > flagged[~is_manip].mean()
```

Run: `python -m pytest tests/test_model.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.detect.model'`.

- [ ] **Step 3: Write `src/sentinel/detect/model.py`**

```python
"""LightGBM multiclass classifier, per-type alert thresholds, and an Isolation Forest."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from sentinel.detect.dataset import CLASS_INDEX, CLASSES, to_xy
from sentinel.features.extractor import FEATURE_NAMES

_THRESHOLD_GRID = np.round(np.arange(0.05, 0.96, 0.01), 2)


def class_weights(y: np.ndarray, cap: float = 20.0) -> np.ndarray:
    """Inverse-frequency weight per class, N / (K * n_class), capped so tiny classes cannot dominate."""
    counts = np.bincount(y, minlength=len(CLASSES)).astype(float)
    weights = np.full(len(CLASSES), cap)
    present = counts > 0
    weights[present] = np.minimum(cap, len(y) / (len(CLASSES) * counts[present]))
    return weights


def train_lgbm(train: pd.DataFrame, val: pd.DataFrame, seed: int = 0, max_rounds: int = 500) -> lgb.Booster:
    """Train a small multiclass gradient-boosted model with early stopping on validation log-loss.

    The returned booster contains only the trees up to the best iteration, so
    prediction, SHAP and any exported copy all see exactly the same model.
    """
    X_train, y_train = to_xy(train)
    X_val, y_val = to_xy(val)
    weights = class_weights(y_train)
    names = list(FEATURE_NAMES)
    dtrain = lgb.Dataset(X_train, y_train, weight=weights[y_train], feature_name=names)
    dval = lgb.Dataset(X_val, y_val, weight=weights[y_val], feature_name=names, reference=dtrain)
    params = {
        "objective": "multiclass", "num_class": len(CLASSES), "metric": "multi_logloss",
        "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 10, "feature_fraction": 0.8,
        "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0, "seed": seed,
        "deterministic": True, "force_row_wise": True, "num_threads": 4, "verbosity": -1,
    }
    booster = lgb.train(params, dtrain, num_boost_round=max_rounds, valid_sets=[dval],
                        callbacks=[lgb.early_stopping(50, verbose=False)])
    return lgb.Booster(model_str=booster.model_to_string(num_iteration=booster.best_iteration))


def predict_proba(booster: lgb.Booster, X: np.ndarray) -> np.ndarray:
    """Class probabilities, shape (n, 4), columns in CLASSES order."""
    return booster.predict(X)


def choose_thresholds(proba: np.ndarray, labels: pd.Series) -> dict[str, float]:
    """Per-type alert threshold maximizing that type's F1 (type vs. NORMAL) on validation data.

    Ties go to higher precision, then to the higher threshold (fewer alerts).
    """
    thresholds: dict[str, float] = {}
    for kind in CLASSES[1:]:
        keep = labels.isin([kind, "NORMAL"]).to_numpy()
        y = (labels.to_numpy() == kind)[keep]
        p = proba[keep, CLASS_INDEX[kind]]
        best_key, best_t = (-1.0, -1.0, -1.0), float(_THRESHOLD_GRID[0])
        for t in _THRESHOLD_GRID:
            flagged = p >= t
            tp = int((flagged & y).sum())
            fp = int((flagged & ~y).sum())
            fn = int(y.sum()) - tp
            f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
            precision = tp / (tp + fp) if tp + fp else 0.0
            key = (f1, precision, float(t))
            if key > best_key:
                best_key, best_t = key, float(t)
        thresholds[kind] = best_t
    return thresholds


def predict_types(proba: np.ndarray, thresholds: dict[str, float]) -> np.ndarray:
    """Alert type per row (class index): among types whose probability meets its threshold, the most probable; else NORMAL (0)."""
    out = np.zeros(len(proba), dtype=int)
    best = np.full(len(proba), -1.0)
    for kind in CLASSES[1:]:
        k = CLASS_INDEX[kind]
        fires = (proba[:, k] >= thresholds[kind]) & (proba[:, k] > best)
        out[fires] = k
        best[fires] = proba[fires, k]
    return out


def save_model(booster: lgb.Booster, thresholds: dict[str, float], out_dir: Path) -> None:
    """Write lgbm.txt (LightGBM text model) and thresholds.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "lgbm.txt"))
    (out_dir / "thresholds.json").write_text(json.dumps(thresholds, indent=2), encoding="utf-8")


def load_model(model_dir: Path) -> tuple[lgb.Booster, dict[str, float]]:
    """Inverse of save_model."""
    model_dir = Path(model_dir)
    booster = lgb.Booster(model_file=str(model_dir / "lgbm.txt"))
    return booster, json.loads((model_dir / "thresholds.json").read_text(encoding="utf-8"))


class IsolationForestDetector:
    """Unsupervised anomaly detector: manipulation windows should be easier to isolate than normal ones."""

    def __init__(self, seed: int = 0, n_estimators: int = 200) -> None:
        self._forest = IsolationForest(n_estimators=n_estimators, random_state=seed, n_jobs=1)

    def fit(self, X: np.ndarray) -> "IsolationForestDetector":
        """Fit without labels."""
        self._forest.fit(X)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        """Anomaly score; higher means more anomalous."""
        return -self._forest.score_samples(X)

    def choose_threshold(self, scores: np.ndarray, labels: pd.Series) -> float:
        """Threshold maximizing F1 for 'any manipulation vs NORMAL' on validation data."""
        y = (labels != "NORMAL").to_numpy()
        best_key, best_t = (-1.0, -1.0), float(scores.min())
        for t in np.unique(np.quantile(scores, np.linspace(0.5, 0.999, 200))):
            flagged = scores >= t
            tp = int((flagged & y).sum())
            fp = int((flagged & ~y).sum())
            f1 = 2 * tp / (2 * tp + fp + (int(y.sum()) - tp)) if tp else 0.0
            key = (f1, float(t))
            if key > best_key:
                best_key, best_t = key, float(t)
        return best_t

    def predict(self, X: np.ndarray, threshold: float) -> np.ndarray:
        """Boolean flags at ``threshold``."""
        return self.score(X) >= threshold

    def save(self, path: Path) -> None:
        """Persist the fitted forest."""
        joblib.dump(self._forest, path)
```

Run: `python -m pytest tests/test_model.py -v` then `python -m pytest -q`
Expected: all pass. If `test_booster_is_truncated_to_the_best_iteration` fails, print `booster.best_iteration`, `booster.num_trees()` and `booster.current_iteration()` and reason about LightGBM's behavior before changing the test; the intent is that `num_trees() == 4 * (number of kept iterations)` and prediction uses all of them.

- [ ] **Step 4: Commit**

```bash
git add src/sentinel/detect/model.py tests/test_model.py tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(detect): LightGBM multiclass model, thresholds and Isolation Forest

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: SHAP explanations and alerts

**Concept to explain:** SHAP splits one prediction into a contribution per feature that adds up exactly to the model's score: base value + sum of contributions = raw score. For an alert of type k we show the features that pushed the class-k score *up* the most, with the actual value and a plain-language phrase, plus what a typical normal window looks like for comparison.

**Files:**
- Create: `src/sentinel/detect/explain.py`, `src/sentinel/detect/alerts.py`
- Test: `tests/test_explain.py`, `tests/test_alerts.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_explain.py`)

```python
"""Tests for SHAP explanations of model alerts."""
from __future__ import annotations

import numpy as np
import pytest

from sentinel.detect.dataset import CLASS_INDEX, to_xy
from sentinel.detect.explain import PHRASES, ModelExplainer
from sentinel.detect.model import train_lgbm
from sentinel.features.extractor import FEATURE_NAMES


@pytest.fixture
def trained(synthetic_windows):
    train, val = synthetic_windows.iloc[:1600], synthetic_windows.iloc[1600:]
    return train_lgbm(train, val, seed=1), val


def test_every_feature_has_a_phrase_that_formats() -> None:
    assert set(PHRASES) == set(FEATURE_NAMES)
    for name, template in PHRASES.items():
        assert isinstance(template.format(v=0.5), str), name


def test_shap_values_add_up_to_the_raw_model_score(trained) -> None:
    booster, val = trained
    X = to_xy(val)[0][:25]
    explainer = ModelExplainer(booster, reference={n: 0.0 for n in FEATURE_NAMES})
    raw = booster.predict(X, raw_score=True)
    for kind in ("SPOOFING", "LAYERING", "WASH"):
        k = CLASS_INDEX[kind]
        contrib, base = explainer.contributions(X, k)
        assert np.allclose(contrib.sum(axis=1) + base, raw[:, k], atol=1e-4), kind


def test_shap_agrees_with_lightgbms_own_tree_shap(trained) -> None:
    booster, val = trained
    X = to_xy(val)[0][:25]
    explainer = ModelExplainer(booster, reference={n: 0.0 for n in FEATURE_NAMES})
    native = booster.predict(X, pred_contrib=True)
    width = len(FEATURE_NAMES) + 1
    for kind in ("SPOOFING", "WASH"):
        k = CLASS_INDEX[kind]
        contrib, base = explainer.contributions(X, k)
        assert np.allclose(contrib, native[:, k * width:k * width + len(FEATURE_NAMES)], atol=1e-4)
        assert np.allclose(base, native[:, k * width + len(FEATURE_NAMES)], atol=1e-4)


def test_explain_returns_top_positive_contributors_with_values_and_phrases(trained) -> None:
    booster, val = trained
    X, y = to_xy(val)
    idx = int(np.nonzero(y == CLASS_INDEX["LAYERING"])[0][0])
    reference = {n: float(np.median(val[n])) for n in FEATURE_NAMES}
    evidence = ModelExplainer(booster, reference).explain(X[idx], CLASS_INDEX["LAYERING"], top_k=5)
    assert 1 <= len(evidence) <= 5
    assert all(e.contribution > 0 for e in evidence)
    assert [e.contribution for e in evidence] == sorted((e.contribution for e in evidence), reverse=True)
    assert evidence[0].feature in ("max_live_levels_same_side", "max_cancel_burst_100ms")
    assert evidence[0].value == pytest.approx(X[idx][list(FEATURE_NAMES).index(evidence[0].feature)])
    assert evidence[0].reference == pytest.approx(reference[evidence[0].feature])
```

`tests/test_alerts.py`:
```python
"""Tests for alert construction and rendering."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sentinel.detect.alerts import model_alerts, render_alert, rule_alerts
from sentinel.detect.dataset import to_xy
from sentinel.detect.explain import ModelExplainer
from sentinel.detect.model import choose_thresholds, predict_proba, train_lgbm
from sentinel.detect.rules import Condition, Rule
from sentinel.features.extractor import FEATURE_NAMES


def _with_keys(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["trader_id"] = np.arange(len(out))
    out["instrument"] = "SYM0"
    out["window"] = 7
    return out


def test_model_alerts_are_only_created_for_flagged_windows_and_carry_shap_evidence(synthetic_windows) -> None:
    train, val = synthetic_windows.iloc[:1600], synthetic_windows.iloc[1600:]
    booster = train_lgbm(train, val, seed=1)
    val = _with_keys(val)
    X = to_xy(val)[0]
    proba = predict_proba(booster, X)
    thresholds = choose_thresholds(proba, val["label"])
    reference = {n: float(np.median(train[n])) for n in FEATURE_NAMES}
    alerts = model_alerts(val, proba, thresholds, ModelExplainer(booster, reference))
    assert 0 < len(alerts) < len(val)
    a = alerts[0]
    assert a.source == "model" and a.kind in ("SPOOFING", "LAYERING", "WASH") and 0 < a.score <= 1
    assert a.instrument == "SYM0" and a.window == 7 and len(a.evidence) >= 1
    assert a.evidence[0].weight is not None and a.evidence[0].weight > 0
    text = render_alert(a)
    assert a.kind in text and a.evidence[0].feature in text and "SYM0" in text


def test_rule_alerts_carry_the_condition_trace() -> None:
    df = _with_keys(pd.DataFrame({"cancel_rate": [0.95, 0.1], "order_to_trade_ratio": [1.0, 1.0]}))
    rule = Rule("SPOOFING", (Condition("cancel_rate", ">=", 0.9),))
    alerts = rule_alerts(df, rule, name="textbook")
    assert len(alerts) == 1 and alerts[0].source == "rule:textbook" and alerts[0].kind == "SPOOFING"
    assert alerts[0].evidence[0].weight is None and "cancel_rate = 0.95" in alerts[0].evidence[0].text
    assert "cancel_rate = 0.95" in render_alert(alerts[0])
```

Run: `python -m pytest tests/test_explain.py tests/test_alerts.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.detect.explain'`.

- [ ] **Step 2: Write `src/sentinel/detect/explain.py`**

```python
"""SHAP explanations for model alerts, phrased for an investigator."""
from __future__ import annotations

from typing import Mapping, NamedTuple

import lightgbm as lgb
import numpy as np
import shap

from sentinel.features.extractor import FEATURE_NAMES

# One plain-language template per feature; {v} is the trigger value in this window.
PHRASES: dict[str, str] = {
    "n_new": "sent {v:.0f} new orders",
    "n_cancel": "cancelled {v:.0f} orders",
    "n_modify": "modified {v:.0f} orders",
    "n_fills": "had {v:.0f} executions",
    "order_to_trade_ratio": "sent {v:.1f} orders per execution",
    "cancel_rate": "cancelled {v:.0%} as many orders as it sent",
    "cancel_within_1s_share": "cancelled {v:.0%} of its orders within 1 second",
    "cancel_within_3s_share": "cancelled {v:.0%} of its orders within 3 seconds",
    "median_cancelled_lifetime_s": "typical cancelled order lived {v:.2f} s",
    "total_new_qty": "submitted {v:,.0f} shares of resting orders",
    "total_fill_qty": "traded {v:,.0f} shares",
    "max_order_qty": "largest order was {v:,.0f} shares",
    "max_qty_over_best": "largest order was {v:.1f}x the quantity at the best price",
    "peak_resting_over_depth5": "own resting orders reached {v:.0%} of five-level book depth",
    "max_live_levels_same_side": "held orders at {v:.0f} price levels on one side at once",
    "max_cancel_burst_100ms": "cancelled {v:.0f} orders within 100 ms",
    "opposite_fill_share": "{v:.0%} of executions were on the opposite side to its largest resting orders",
    "signed_mid_drift_ticks": "mid-price moved {v:+.2f} ticks in the direction of its resting orders",
    "net_over_gross": "net position change was {v:.0%} of gross traded volume",
    "top_counterparty_share": "{v:.0%} of volume was against a single counterparty",
    "n_counterparties": "traded with {v:.0f} distinct counterparties",
    "roundtrip_counterparty_share": "{v:.0%} of volume was with counterparties it both bought from and sold to",
}


class Contribution(NamedTuple):
    """One feature's push on the alerted class's score."""

    feature: str
    value: float
    reference: float  # median of this feature over normal training windows
    contribution: float  # SHAP value, in log-odds units
    phrase: str


class ModelExplainer:
    """TreeSHAP explanations for a LightGBM multiclass booster."""

    def __init__(self, booster: lgb.Booster, reference: Mapping[str, float]) -> None:
        self._explainer = shap.TreeExplainer(booster)
        self._reference = dict(reference)

    def contributions(self, X: np.ndarray, class_index: int) -> tuple[np.ndarray, np.ndarray]:
        """SHAP values for one class, shape (n, n_features), and the per-row base value (n,).

        contributions + base equals the raw (pre-softmax) score of that class.
        """
        values = self._explainer.shap_values(X)
        per_class = values[class_index] if isinstance(values, list) else values[:, :, class_index]
        base = np.atleast_1d(self._explainer.expected_value)[class_index]
        return np.asarray(per_class), np.full(len(X), float(base))

    def explain(self, x: np.ndarray, class_index: int, top_k: int = 5) -> list[Contribution]:
        """The top_k features pushing this window toward the alerted class, strongest first."""
        contrib, _ = self.contributions(x.reshape(1, -1), class_index)
        order = np.argsort(-contrib[0])[:top_k]
        out = []
        for i in order:
            if contrib[0][i] <= 0:
                break
            name = FEATURE_NAMES[i]
            out.append(Contribution(name, float(x[i]), self._reference[name], float(contrib[0][i]),
                                    PHRASES[name].format(v=float(x[i]))))
        return out
```

- [ ] **Step 3: Write `src/sentinel/detect/alerts.py`**

```python
"""One Alert shape for rules and models, with evidence an investigator can read."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sentinel.detect.dataset import CLASSES, to_xy
from sentinel.detect.explain import ModelExplainer
from sentinel.detect.model import predict_types
from sentinel.detect.rules import Rule


@dataclass(frozen=True)
class Evidence:
    """One line of evidence. Model alerts carry a SHAP weight; rule alerts carry only text."""

    feature: str
    value: float
    weight: float | None
    text: str


@dataclass(frozen=True)
class Alert:
    """A flagged trader-window."""

    trader_id: int
    instrument: str
    window: int
    kind: str
    score: float
    source: str  # "model" or "rule:<name>"
    evidence: tuple[Evidence, ...]


def model_alerts(df: pd.DataFrame, proba: np.ndarray, thresholds: dict[str, float],
                 explainer: ModelExplainer, top_k: int = 5) -> list[Alert]:
    """Alerts for the windows the model flags. SHAP is computed only for flagged windows."""
    types = predict_types(proba, thresholds)
    X = to_xy(df)[0]
    alerts = []
    for i in np.nonzero(types)[0]:
        k = int(types[i])
        evidence = tuple(
            Evidence(c.feature, c.value, c.contribution,
                     f"{c.phrase} (typical {c.reference:.3g}, SHAP {c.contribution:+.2f})")
            for c in explainer.explain(X[i], k, top_k))
        row = df.iloc[i]
        alerts.append(Alert(int(row["trader_id"]), str(row["instrument"]), int(row["window"]),
                            CLASSES[k], float(proba[i, k]), "model", evidence))
    return alerts


def rule_alerts(df: pd.DataFrame, rule: Rule, name: str) -> list[Alert]:
    """Alerts for the windows a rule flags, with the conditions that fired as evidence."""
    flagged = rule.evaluate(df)
    alerts = []
    for i in np.nonzero(flagged)[0]:
        row = df.iloc[i]
        evidence = tuple(Evidence(text.split(" = ")[0], float(row[text.split(" = ")[0]]), None, text)
                         for text in rule.trace(row))
        alerts.append(Alert(int(row["trader_id"]), str(row["instrument"]), int(row["window"]),
                            rule.kind, 1.0, f"rule:{name}", evidence))
    return alerts


def render_alert(alert: Alert) -> str:
    """Multi-line text an investigator can read."""
    lines = [f"ALERT {alert.kind}  trader {alert.trader_id}  {alert.instrument}  window {alert.window}"
             f"  score {alert.score:.2f}  ({alert.source})"]
    lines += [f"  - {e.text}" for e in alert.evidence]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_explain.py tests/test_alerts.py -v` then `python -m pytest -q`
Expected: all pass. If the SHAP additivity test fails, first print `booster.num_trees()`, `booster.current_iteration()` and compare `explainer.expected_value` with the native bias in `booster.predict(X, pred_contrib=True)`. Do not loosen the tolerance beyond 1e-3 without explaining why. If `shap_values` returns an unexpected shape for this shap version, adapt `contributions` (both list and ndarray forms are already handled) and report the shape you observed.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/detect/explain.py src/sentinel/detect/alerts.py tests/test_explain.py tests/test_alerts.py
git commit -m "$(cat <<'EOF'
feat(detect): SHAP explanations and unified alerts for rules and models

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Training pipeline and validation report

**Files:**
- Create: `scripts/train_detectors.py`
- Modify: `.gitignore` (add `models/` and `artifacts/`)

- [ ] **Step 1: Add to `.gitignore`**

```
models/
artifacts/
```

- [ ] **Step 2: Write `scripts/train_detectors.py`**

```python
"""Train every detector on seeds 7/8/9 and write the VALIDATION report.

python scripts/train_detectors.py [--rebuild]

Uses only data/sim_seed7, sim_seed8 and sim_seed9. The final test simulation
(data/test_seed100) is never loaded here. All numbers written are validation
numbers: thresholds, rule tuning and early stopping were selected on this same
data, so they are optimistic. The honest numbers come from the test set in Stage 4.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from sentinel.detect.alerts import model_alerts, render_alert, rule_alerts
from sentinel.detect.dataset import CLASS_INDEX, load_sim, time_split, to_xy
from sentinel.detect.explain import ModelExplainer
from sentinel.detect.model import (IsolationForestDetector, choose_thresholds, predict_proba, predict_types,
                                   save_model, train_lgbm)
from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES, tune_rule
from sentinel.eval.metrics import recall_by_difficulty, results_table
from sentinel.features.extractor import FEATURE_NAMES

SIMS = ("sim_seed7", "sim_seed8", "sim_seed9")
KINDS = ("SPOOFING", "LAYERING", "WASH")


def single_feature_auc(train: pd.DataFrame) -> pd.DataFrame:
    """Best single-feature AUC per type (type vs NORMAL): a check that no one feature gives the answer away."""
    rows = []
    for kind in KINDS:
        sub = train[train["label"].isin([kind, "NORMAL"])]
        y = (sub["label"] == kind).to_numpy()
        aucs = {f: roc_auc_score(y, sub[f]) for f in FEATURE_NAMES if sub[f].nunique() > 1}
        feature, auc = max(aucs.items(), key=lambda kv: abs(kv[1] - 0.5))
        rows.append({"type": kind, "best_feature": feature, "auc": auc if auc >= 0.5 else 1 - auc})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    full = pd.concat([load_sim(args.data / s, rebuild=args.rebuild) for s in SIMS], ignore_index=True)
    train, val = time_split(full)
    print(f"train {len(train):,} windows ({(train.label != 'NORMAL').sum()} manipulation), "
          f"validation {len(val):,} ({(val.label != 'NORMAL').sum()} manipulation)")

    # -- rules ---------------------------------------------------------------
    tuned = {kind: tune_rule(rule, val, seed=0) for kind, rule in TEXTBOOK_RULES.items()}
    generic = GENERIC_RULE.evaluate(val)
    preds: dict[str, dict[str, np.ndarray]] = {
        "generic rule (untyped)": {"ANY": generic, **{k: generic for k in KINDS}},
        "textbook rules": {k: TEXTBOOK_RULES[k].evaluate(val) for k in KINDS},
        "tuned rules": {k: tuned[k].evaluate(val) for k in KINDS},
    }

    # -- LightGBM ------------------------------------------------------------
    booster = train_lgbm(train, val, seed=0)
    X_val = to_xy(val)[0]
    proba = predict_proba(booster, X_val)
    thresholds = choose_thresholds(proba, val["label"])
    types = predict_types(proba, thresholds)
    lgbm = {k: types == CLASS_INDEX[k] for k in KINDS}
    preds["LightGBM"] = {**lgbm, "ANY": types != 0}

    # -- Isolation Forest ----------------------------------------------------
    forest = IsolationForestDetector(seed=0).fit(to_xy(train)[0])
    scores = forest.score(X_val)
    iso_threshold = forest.choose_threshold(scores, val["label"])
    flagged = scores >= iso_threshold
    preds["Isolation Forest"] = {"ANY": flagged, **{k: flagged for k in KINDS}}

    table = results_table(val["label"], preds)
    recall = recall_by_difficulty(val, preds)
    auc = single_feature_auc(train)

    # -- persist + report ----------------------------------------------------
    save_model(booster, thresholds, Path("models"))
    forest.save(Path("models") / "iforest.joblib")
    reference = {n: float(np.median(train.loc[train.label == "NORMAL", n])) for n in FEATURE_NAMES}
    explainer = ModelExplainer(booster, reference)
    alerts = model_alerts(val, proba, thresholds, explainer)
    examples = [a for a in alerts if a.kind == "SPOOFING"][:1] + [a for a in alerts if a.kind == "LAYERING"][:1] \
        + [a for a in alerts if a.kind == "WASH"][:1]
    rule_examples = rule_alerts(val, tuned["WASH"], "tuned")[:1]

    out = Path("artifacts")
    out.mkdir(exist_ok=True)
    fmt = {"precision": "{:.3f}", "recall": "{:.3f}", "f1": "{:.3f}", "fpr": "{:.4f}"}
    report = ["# Validation report (Stage 2)", "",
              "Validation numbers only: rule tuning, thresholds and early stopping used this same data, so they are "
              "optimistic. Final numbers come from the untouched test simulation in Stage 4.", "",
              f"Model: {booster.num_trees()} trees. Thresholds: {thresholds}. Isolation Forest threshold: {iso_threshold:.4f}.",
              "", "## Precision / recall / F1 / FPR", "",
              table.round({"precision": 3, "recall": 3, "f1": 3, "fpr": 4}).to_markdown(index=False), "",
              "## Recall by difficulty", "", recall.round(3).to_markdown(index=False), "",
              "## Best single-feature AUC on training data (leakage check)", "", auc.round(3).to_markdown(index=False), "",
              "## Tuned rules", ""]
    report += [f"- {k}: " + " AND ".join(f"{c.feature} {c.op} {c.threshold:.3g}" for c in r.conditions)
               for k, r in tuned.items()]
    report += ["", "## Example alerts", ""]
    report += ["```", *[render_alert(a) for a in examples + rule_examples], "```"]
    (out / "validation_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
```
`to_markdown` needs `tabulate`: add `"tabulate>=0.9"` to the dependencies in `pyproject.toml` and run `python -m pip install -e ".[dev]"` if it isn't installed.

- [ ] **Step 3: Run the pipeline**

Run: `python scripts/train_detectors.py`
Expected: prints the train/validation sizes and the full report, and writes `artifacts/validation_report.md` and `models/`. It must NOT touch `data/test_seed100` (check `ls data/test_access.log` does not exist).

- [ ] **Step 4: Sanity-check the report and report it to the user**

Read `artifacts/validation_report.md` and report in your summary:
- The results table (per detector and type) and recall by difficulty.
- The single-feature AUC table. If any AUC is above 0.97, flag it prominently: a single feature nearly separates the class, which is a simulator artifact to investigate before Stage 3.
- Whether the generic rule's false-positive rate shows the market-maker problem (FPR should be visibly high).
- Whether tuned rules beat textbook rules and whether LightGBM beats tuned rules, honestly, including if it does not.
- The three example alerts, verbatim.
Do not tune anything to make the model look better.

- [ ] **Step 5: Commit**

```bash
git add scripts/train_detectors.py .gitignore pyproject.toml
git commit -m "$(cat <<'EOF'
feat(detect): training pipeline and validation report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review (against the spec)

**Spec coverage**
- Sec. 1 extractor (one class, window close semantics, out-of-order raises, `counterparty_id`): Tasks 0-1. Replay helper `extract_features` covers the training path; Stage 3 covers the live loop.
- Sec. 2 features: all 20 listed plus 2 (`total_new_qty`, `total_fill_qty`), Task 1. Token-based denylist test covers "no IDs, raw timestamps, instrument names".
- Sec. 3 rules: typed rules and the generic rule with pre-registration (commit plus tag) and tuning: Task 3. Rule traces: `Rule.trace`, `rule_alerts`.
- Sec. 4 models: LightGBM multiclass (weights, 15 leaves, early stopping, per-type thresholds, highest-probability wins): Task 4. Isolation Forest: Task 4. SHAP for alerts only: Task 5. Unified alert shape: Task 5.
- Sec. 5 data protocol: seeds 7/8/9, 70/30 time split, episode grouping, boundary purge, locked test set with `--final`/access log (implemented as `final=True` plus the access log): Tasks 0 and 2. Metrics contract: Task 3 (`type_metrics`, `recall_by_difficulty`). Tests listed in the spec: hand-computed features, window close, out-of-order, denylist, SHAP additivity, rule traces, threshold search, training, determinism: all present.
- Not covered by design: streaming engine loop, Java port, final test evaluation, charts, RESULTS.md, README (Stages 3-5).

**Placeholder scan:** none; every code step has full code.

**Consistency:** `process` argument order (`ts, instrument, etype, order_id, trader_id, counterparty_id, side, price, qty, best_bid, ...`) matches `E(...)` in tests and `_INPUT_COLUMNS`. `FeatureRow.values` matches `FEATURE_NAMES` order in `_features`. `to_xy`, `CLASSES`, `CLASS_INDEX` are defined in `dataset.py` and imported elsewhere. `tests/conftest.py` imports `CLASSES` from `dataset` (Task 2 comes before Task 4).

**Known risks the executor should expect:** (1) shap/lightgbm version quirks in the output shapes of multiclass SHAP (handled, but verify); (2) if the LightGBM early-stopped model has `best_iteration` semantics that differ from the truncation test, reason before editing the test; (3) tuned rules may already be strong: report honestly; (4) feature extraction speed (Task 1 step 6 reports it, and Stage 3 uses it).
