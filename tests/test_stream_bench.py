"""Tests for latency statistics and the benchmark loop."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from sentinel.stream.bench import bench_python, latency_stats, nearest_rank, summarize


def test_nearest_rank_percentiles() -> None:
    values = np.arange(1, 101)  # 1..100
    assert nearest_rank(values, 0.5) == 50 and nearest_rank(values, 0.99) == 99
    assert nearest_rank(values, 0.999) == 100 and nearest_rank(values, 1.0) == 100
    assert nearest_rank(np.array([7]), 0.99) == 7


def test_summarize_and_latency_stats_split_and_subtract_overhead() -> None:
    lat = np.array([100, 110, 120, 130, 5_000], dtype=np.int64)
    closes = np.array([False, False, False, False, True])
    stats = latency_stats(lat, closes, overhead=10)
    assert stats["overall"]["max"] == 4_990 and stats["overall"]["median"] == 110
    assert stats["window_close"] == {"n": 1, "median": 4_990, "p99": 4_990, "p999": 4_990, "max": 4_990}
    assert stats["other"]["n"] == 4 and stats["other"]["max"] == 120
    assert summarize(np.array([], dtype=np.int64))["n"] == 0


class _CountingEngine:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, *event):
        self.calls += 1
        return SimpleNamespace(rows=[1] if self.calls % 50 == 0 else [])

    def flush(self):
        return SimpleNamespace(rows=[])


def test_bench_python_times_every_event_and_flags_window_closes() -> None:
    n = 500
    columns = [list(range(n)) for _ in range(15)]
    result = bench_python(_CountingEngine, columns, n)
    assert result["engine"] == "python" and result["n_events"] == n and result["throughput_eps"] > 0
    assert result["latency_ns"]["overall"]["n"] == n
    assert result["latency_ns"]["window_close"]["n"] == n // 50
    assert result["timer_overhead_ns"] >= 0
