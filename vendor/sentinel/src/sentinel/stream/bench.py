"""Latency and throughput measurement for a streaming engine."""
from __future__ import annotations

import math
import time
from typing import Callable, Sequence

import numpy as np


def nearest_rank(sorted_values: np.ndarray, p: float) -> int:
    """Nearest-rank percentile: the value at 1-based position ceil(p * n) of the sorted data."""
    index = max(0, math.ceil(p * len(sorted_values)) - 1)
    return int(sorted_values[index])


def summarize(values: np.ndarray) -> dict[str, int]:
    """n, median, p99, p99.9 and max (nanoseconds) of a set of latencies."""
    if len(values) == 0:
        return {"n": 0, "median": 0, "p99": 0, "p999": 0, "max": 0}
    s = np.sort(values)
    return {"n": len(s), "median": nearest_rank(s, 0.5), "p99": nearest_rank(s, 0.99),
            "p999": nearest_rank(s, 0.999), "max": int(s[-1])}


def latency_stats(lat: np.ndarray, closes: np.ndarray, overhead: int) -> dict[str, dict[str, int]]:
    """Timer-overhead-corrected latency summary: overall, events that closed a window, and the rest."""
    adjusted = np.maximum(lat - overhead, 0)
    return {"overall": summarize(adjusted), "window_close": summarize(adjusted[closes]),
            "other": summarize(adjusted[~closes])}


def timer_overhead(timer: Callable[[], int] = time.perf_counter_ns, samples: int = 100_000) -> int:
    """Median cost of an empty timed region (two timer calls), in ns."""
    diffs = np.empty(samples, dtype=np.int64)
    for i in range(samples):
        a = timer()
        b = timer()
        diffs[i] = b - a
    return int(np.median(diffs))


def bench_python(make_engine: Callable[[], object], columns: Sequence[Sequence], n: int,
                 timer: Callable[[], int] = time.perf_counter_ns) -> dict:
    """Benchmark an engine in this process.

    Pass 1 measures throughput with no per-event timers. Pass 2 times each
    ``process`` call and records whether that event closed a window. Event
    tuples are built lazily outside the timed region.
    """
    cols = [c[:n] for c in columns]
    engine = make_engine()
    start = timer()
    for event in zip(*cols):
        engine.process(*event)
    engine.flush()
    seconds = (timer() - start) / 1e9

    engine = make_engine()
    lat = np.empty(n, dtype=np.int64)
    closes = np.zeros(n, dtype=bool)
    for i, event in enumerate(zip(*cols)):
        a = timer()
        out = engine.process(*event)
        b = timer()
        lat[i] = b - a
        closes[i] = bool(out.rows)
    overhead = timer_overhead(timer)
    return {"engine": "python", "n_events": n, "throughput_eps": n / seconds,
            "timer_overhead_ns": overhead, "latency_ns": latency_stats(lat, closes, overhead)}
