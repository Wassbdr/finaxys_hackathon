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
