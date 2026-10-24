"""Scale test: the default configuration produces 1M+ events within a time budget."""
from __future__ import annotations

import time

import pytest

from sentinel.sim.runner import SimConfig, simulate


@pytest.mark.slow
def test_default_config_generates_over_a_million_events_in_budget() -> None:
    start = time.perf_counter()
    res = simulate(SimConfig())
    seconds = time.perf_counter() - start
    print(f"\n{len(res.events):,} events in {seconds:.1f}s")
    assert len(res.events) >= 1_000_000
    assert seconds < 600
