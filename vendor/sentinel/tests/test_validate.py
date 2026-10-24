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
        "qty": [100, 200, 100, 50, 300], "price": [100, 100, 100, 100, 100],
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


def test_sim_inter_event_times_ignore_one_nanosecond_burst_stamps() -> None:
    ts = [0, 1, 2, 3, 5_000_000, 5_000_001, 20_000_000, 20_000_001]  # bursts of ~1 ns spacing
    events = pd.DataFrame({
        "ts": ts, "instrument": "A",
        "event_type": pd.Categorical(["NEW"] * len(ts), categories=["NEW", "CANCEL", "MODIFY", "EXEC"]),
        "qty": [100] * len(ts), "price": [100] * len(ts), "best_bid": [99] * len(ts), "best_ask": [101] * len(ts),
        "bid_qty1": [1] * len(ts), "ask_qty1": [1] * len(ts), "bid_depth5": [1] * len(ts), "ask_depth5": [1] * len(ts),
    })
    stats = sim_stats(events)
    assert stats["iet_p50_ms"] == pytest.approx(10.0, rel=0.6)  # gaps 5 ms and 15 ms, not 1 ns


def test_sim_stats_exclude_market_orders_and_count_modifies_as_cancels() -> None:
    events = pd.DataFrame({
        "ts": [1_000_000 * k for k in range(1, 6)], "instrument": "A",
        "event_type": pd.Categorical(["NEW", "NEW", "MODIFY", "CANCEL", "EXEC"],
                                     categories=["NEW", "CANCEL", "MODIFY", "EXEC"]),
        "qty": [100, 999, 50, 100, 10], "price": [100, 0, 100, 100, 100],  # second NEW is a market order
        "best_bid": [99] * 5, "best_ask": [101] * 5,
        "bid_qty1": [1] * 5, "ask_qty1": [1] * 5, "bid_depth5": [1] * 5, "ask_depth5": [1] * 5,
    })
    stats = sim_stats(events)
    assert stats["cancel_to_new"] == pytest.approx(2.0)  # MODIFY + CANCEL over one limit NEW
    assert stats["size_p50"] == 100  # the 999 market order is not a limit submission
