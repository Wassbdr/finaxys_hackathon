"""Discrete-event simulation runner: agents, episode spawning and merging."""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd

from .agents import Agent, MarketMaker, MomentumTrader, NoiseTrader
from .exchange import Exchange
from .scenarios import ALL_COMBOS, spawn_episode
from .types import NS_PER_SEC, TRADER_ID_STRIDE

MM_RATE = 8.0  # market-maker refreshes per second, each
NOISE_RATE = 2.0  # noise-trader actions per second, each
MOMENTUM_RATE = 0.05  # momentum bursts per second, each
_SPAWN_CUTOFF = 0.97  # stop starting episodes after this fraction of the target


@dataclass(frozen=True)
class SimConfig:
    """Everything that determines a simulation run (with the seed, fully reproducible)."""

    instruments: tuple[str, ...] = ("SYM0", "SYM1", "SYM2", "SYM3")
    events_per_instrument: int = 400_000
    seed: int = 7
    n_market_makers: int = 6
    n_momentum: int = 6
    n_noise: int = 38
    episodes_per_instrument: int = 100
    combos: tuple[tuple[str, str], ...] = ALL_COMBOS
    window_s: int = 10


@dataclass
class SimResult:
    """Simulation output. ``event_episode`` is ground truth, aligned row-for-row with ``events``."""

    events: pd.DataFrame
    event_episode: np.ndarray
    episodes: pd.DataFrame


def _combo_cycle(combos: tuple[tuple[str, str], ...], rng: random.Random) -> Iterator[tuple[str, str]]:
    """Endless stream of (kind, difficulty), each round a fresh shuffle, so counts stay balanced."""
    while True:
        order = list(combos)
        rng.shuffle(order)
        yield from order


def simulate_instrument(cfg: SimConfig, index: int) -> tuple[pd.DataFrame, np.ndarray, list]:
    """Simulate one instrument. Returns (events, episode tags, Episode objects)."""
    rng = random.Random(cfg.seed * 1_000 + index)
    ex = Exchange(cfg.instruments[index], index)
    trader_id = index * TRADER_ID_STRIDE
    agents: list[Agent] = []
    for _ in range(cfg.n_market_makers):
        agents.append(MarketMaker(trader_id, MM_RATE))
        trader_id += 1
    for _ in range(cfg.n_momentum):
        agents.append(MomentumTrader(trader_id, MOMENTUM_RATE))
        trader_id += 1
    noise_pool: list[int] = []
    for _ in range(cfg.n_noise):
        agents.append(NoiseTrader(trader_id, NOISE_RATE))
        noise_pool.append(trader_id)
        trader_id += 1

    heap: list[tuple[int, int, Agent]] = []
    seq = 0
    for agent in agents:
        heapq.heappush(heap, (int(rng.random() * NS_PER_SEC), seq, agent))
        seq += 1

    target = cfg.events_per_instrument
    spawn_every = max(1, target // cfg.episodes_per_instrument)
    next_spawn = spawn_every
    spawn_stop = int(target * _SPAWN_CUTOFF)
    combos = _combo_cycle(cfg.combos, rng)
    pending: tuple[str, str] | None = None

    while ex.n_events < target or ex.active_episodes:
        now, _, agent = heapq.heappop(heap)
        delay = agent.act(ex, now, rng)
        if delay is not None:
            heapq.heappush(heap, (now + delay, seq, agent))
            seq += 1
        if next_spawn <= ex.n_events < spawn_stop:
            if pending is None:
                pending = next(combos)
            try:
                episode_agent = spawn_episode(ex, rng, pending[0], pending[1], noise_pool, now)
            except RuntimeError:
                continue  # no free traders right now; retry the same combo next iteration
            pending = None
            heapq.heappush(heap, (now + max(1, episode_agent.first_delay), seq, episode_agent))
            seq += 1
            # Random (exponential) spacing so episode start times are not a periodic signal.
            next_spawn += 1 + int(rng.expovariate(1 / spawn_every))

    events, tags = ex.to_frames()
    return events, tags, ex.episodes


def _episodes_frame(episodes: list) -> pd.DataFrame:
    rows = [
        (e.episode_id, e.kind, e.difficulty, e.instrument, e.trader_ids[0],
         e.trader_ids[1] if len(e.trader_ids) > 1 else -1, e.side, e.t_start, e.t_end)
        for e in episodes
    ]
    columns = ["episode_id", "kind", "difficulty", "instrument", "trader_a", "trader_b",
               "side", "t_start", "t_end"]
    return pd.DataFrame(rows, columns=columns)


def simulate(cfg: SimConfig) -> SimResult:
    """Simulate every instrument and merge into one time-ordered event log."""
    parts = [simulate_instrument(cfg, i) for i in range(len(cfg.instruments))]
    events = pd.concat([p[0] for p in parts], ignore_index=True)
    events["instrument"] = events["instrument"].astype("category")
    tags = np.concatenate([p[1] for p in parts])
    instrument_index = np.concatenate([np.full(len(p[0]), i) for i, p in enumerate(parts)])
    order = np.lexsort((instrument_index, events["ts"].to_numpy()))
    events = events.iloc[order].reset_index(drop=True)
    episodes = _episodes_frame([e for p in parts for e in p[2]])
    return SimResult(events, tags[order], episodes)
