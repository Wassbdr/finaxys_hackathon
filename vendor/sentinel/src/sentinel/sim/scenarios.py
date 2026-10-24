"""Manipulation scenarios, injected through the same exchange as normal flow.

Each scenario is built as a *script*: a list of ``(offset_ns, action)`` pairs
that a ``ScriptedEpisode`` agent plays back. Manipulators are ordinary noise
traders borrowed for the duration of the episode, so their windows contain
both normal and manipulative activity. The genuine trades and the dilution
orders draw sizes from the normal distributions. The fake (spoof/layer) orders
are deliberately large relative to the best-level quantity, since that is the
behaviour being modelled, but they are not orders of magnitude beyond the
book: hard spoofs sit in the upper tail of normal order sizes, so no
single-order size threshold separates them.

The ``DIFFICULTY`` table is part of the experimental design. It must be
frozen (committed and tagged) before any detector is built, so the data cannot
be tuned to flatter a model.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from .agents import lognormal_qty
from .exchange import Episode, Exchange
from .types import ASK, BID, NS_PER_SEC

SPOOFING, LAYERING, WASH = "SPOOFING", "LAYERING", "WASH"
KINDS = (SPOOFING, LAYERING, WASH)
DIFFICULTIES = ("easy", "medium", "hard")
ALL_COMBOS = tuple((kind, level) for kind in KINDS for level in DIFFICULTIES)

MIN_DEPTH = 500  # floor on the best-level quantity a spoof size is measured against (thin-book guard)

Action = Callable[[Exchange, int], None]


@dataclass(frozen=True)
class Difficulty:
    """Knobs that make a scenario harder to tell apart from normal trading."""

    size_mult: tuple[float, float]  # spoof/layer volume as a multiple of the same-side best-level quantity
    hold_s: tuple[float, float]  # seconds the fake orders stay before cancel
    price_offset: tuple[int, int]  # ticks behind the touch for the first fake order
    n_layers: int  # orders in a layering stack
    n_dilution: int  # small normal-looking orders mixed in during the episode
    wash_legs: tuple[int, int]  # number of wash trades
    wash_gap_s: tuple[float, float]  # seconds between wash trades
    wash_qty_jitter: float  # relative size variation between wash pairs
    wash_fixed_price: bool  # True: always trade at the same tick


DIFFICULTY = MappingProxyType({
    "easy": Difficulty((3.0, 5.0), (0.2, 1.0), (1, 1), 6, 0, (10, 16), (0.05, 0.15), 0.0, True),
    "medium": Difficulty((1.5, 2.5), (1.0, 4.0), (1, 2), 4, 3, (6, 10), (0.2, 1.5), 0.25, False),
    "hard": Difficulty((0.6, 1.2), (4.0, 10.0), (1, 3), 3, 8, (3, 6), (0.5, 5.0), 0.6, False),
})


class ScriptedEpisode:
    """Plays a script of timed actions; closes its episode after the last one."""

    def __init__(self, episode: Episode, steps: list[tuple[int, Action]]) -> None:
        self.episode = episode
        self._steps = steps
        self._i = 0

    @property
    def first_delay(self) -> int:
        """Nanoseconds from spawn to the first action."""
        return self._steps[0][0]

    def act(self, ex: Exchange, now: int, rng: random.Random) -> int | None:
        _, action = self._steps[self._i]
        action(ex, now)
        self._i += 1
        if self._i == len(self._steps):
            ex.end_episode(self.episode)
            return None
        return max(1, self._steps[self._i][0] - self._steps[self._i - 1][0])


class _Plan:
    """Builder for a script, plus the shared 'dilution' behaviour."""

    def __init__(self, episode: Episode, rng: random.Random) -> None:
        self.episode = episode
        self.rng = rng
        self.steps: list[tuple[int, Action]] = []
        self.dilution: list[int] = []

    def add(self, t_seconds: float, action: Action) -> None:
        self.steps.append((int(t_seconds * NS_PER_SEC), action))

    def dilute(self, trader: int, n: int, t_lo: float, t_hi: float) -> None:
        """Schedule ``n`` small passive orders that look like ordinary noise flow."""
        for _ in range(n):
            side = self.rng.choice((BID, ASK))
            offset = 1 + int(self.rng.expovariate(0.35))
            qty = lognormal_qty(self.rng, 60, 0.8)
            self.add(self.rng.uniform(t_lo, t_hi), self._dilution_action(trader, side, offset, qty))

    def _dilution_action(self, trader: int, side: int, offset: int, qty: int) -> Action:
        def run(ex: Exchange, now: int) -> None:
            mid = ex.mid()
            price = math.floor(mid) - offset if side == BID else math.ceil(mid) + offset
            self.dilution.append(ex.new_limit(trader, side, max(1, price), qty, now, self.episode.episode_id))
        return run

    def unwind(self, t_seconds: float) -> None:
        """Cancel about half of the dilution orders still resting."""
        def run(ex: Exchange, now: int) -> None:
            for order_id in self.dilution:
                if self.rng.random() < 0.5:
                    ex.cancel(order_id, now)
        self.add(t_seconds, run)

    def finish(self) -> ScriptedEpisode:
        self.steps.sort(key=lambda step: step[0])  # stable: ties keep insertion order
        return ScriptedEpisode(self.episode, self.steps)


def _spoofing(rng: random.Random, d: Difficulty, episode: Episode) -> ScriptedEpisode:
    trader, side = episode.trader_ids[0], episode.side
    plan = _Plan(episode, rng)
    hold = rng.uniform(*d.hold_s)
    mult = rng.uniform(*d.size_mult)
    offset = rng.randint(*d.price_offset)
    real_qty = lognormal_qty(rng, 100, 0.6)
    gap = rng.uniform(0.005, 0.05)
    state: dict[str, int] = {}

    def place(ex: Exchange, now: int) -> None:
        depth = max(ex.best_qty(side), MIN_DEPTH)
        base = ex.best(side) or ex.last_price - side
        state["oid"] = ex.new_limit(trader, side, max(1, base - side * offset), int(mult * depth),
                                    now, episode.episode_id)

    plan.add(0.0, place)
    plan.dilute(trader, d.n_dilution, 0.1 * hold, 0.9 * hold)
    plan.add(hold, lambda ex, now: ex.new_market(trader, -side, real_qty, now, episode.episode_id))
    plan.add(hold + gap, lambda ex, now: ex.cancel(state["oid"], now))
    plan.unwind(hold + 2 * gap)
    return plan.finish()


def _layering(rng: random.Random, d: Difficulty, episode: Episode) -> ScriptedEpisode:
    trader, side, n = episode.trader_ids[0], episode.side, d.n_layers
    plan = _Plan(episode, rng)
    hold = rng.uniform(*d.hold_s)
    mult = rng.uniform(*d.size_mult)
    offset = rng.randint(*d.price_offset)
    real_qty = lognormal_qty(rng, 100, 0.6)
    place_gap = rng.uniform(0.002, 0.02)
    cancel_gap = rng.uniform(0.002, 0.02)
    state: dict[str, int] = {}
    layer_ids: list[int] = []
    jitter = [rng.uniform(0.7, 1.3) for _ in range(n)]  # layers are never exactly equal in size

    def place(ex: Exchange, now: int, level: int) -> None:
        if not layer_ids:
            state["base"] = ex.best(side) or ex.last_price - side
            state["depth"] = max(ex.best_qty(side), MIN_DEPTH)
        price = state["base"] - side * (offset + level)
        qty = max(1, int(mult * state["depth"] / n * jitter[level]))
        layer_ids.append(ex.new_limit(trader, side, max(1, price), qty, now, episode.episode_id))

    for level in range(n):
        plan.add(level * place_gap, lambda ex, now, level=level: place(ex, now, level))
    t_real = (n - 1) * place_gap + hold
    plan.dilute(trader, d.n_dilution, 0.1 * hold, t_real)
    plan.add(t_real, lambda ex, now: ex.new_market(trader, -side, real_qty, now, episode.episode_id))
    for j in range(n):
        plan.add(t_real + cancel_gap * (j + 1),
                 lambda ex, now, j=j: ex.cancel(layer_ids[j], now) if j < len(layer_ids) else None)
    plan.unwind(t_real + cancel_gap * (n + 2))
    return plan.finish()


def _wash(rng: random.Random, d: Difficulty, episode: Episode) -> ScriptedEpisode:
    a, b = episode.trader_ids
    plan = _Plan(episode, rng)
    n_legs = rng.randint(*d.wash_legs)
    base_qty = lognormal_qty(rng, 100, 0.8)
    state: dict[str, int] = {"done": 0, "qty": 1, "px": 0}

    def leg(ex: Exchange, now: int) -> None:
        best_bid, best_ask = ex.snap[0], ex.snap[1]
        if not (best_bid and best_ask and best_ask - best_bid >= 2):
            return  # no free tick inside the spread: skip this leg
        done = state["done"]
        seller, buyer = (a, b) if done % 2 == 0 else (b, a)
        if done % 2 == 0:  # a new pair: choose the size once so the pair nets to zero
            jitter = 1 + rng.uniform(-d.wash_qty_jitter, d.wash_qty_jitter)
            state["qty"] = max(1, int(base_qty * jitter))
        if d.wash_fixed_price:
            inside = best_bid < state["px"] < best_ask
            state["px"] = state["px"] if inside else (best_bid + best_ask) // 2
            price = state["px"]
        else:
            price = rng.randint(best_bid + 1, best_ask - 1)
        # The ask rests alone at an empty interior tick; the bid then matches it exactly.
        ex.new_limit(seller, ASK, price, state["qty"], now, episode.episode_id)
        ex.new_limit(buyer, BID, price, state["qty"], now, episode.episode_id)
        state["done"] = done + 1

    t = 0.0
    for _ in range(n_legs):
        plan.add(t, leg)
        t += rng.uniform(*d.wash_gap_s)
    plan.dilute(a, math.ceil(d.n_dilution / 2), 0.0, t)
    plan.dilute(b, d.n_dilution // 2, 0.0, t)
    plan.unwind(t + 0.01)
    return plan.finish()


_BUILDERS = {SPOOFING: _spoofing, LAYERING: _layering, WASH: _wash}


def spawn_episode(ex: Exchange, rng: random.Random, kind: str, difficulty: str,
                  pool: list[int], now: int) -> ScriptedEpisode:
    """Pick free traders from ``pool``, register the episode and return its script.

    Raises RuntimeError if too few traders are free.
    """
    needed = 2 if kind == WASH else 1
    free = [t for t in pool if t not in ex.busy]
    if len(free) < needed:
        raise RuntimeError("not enough free traders to start an episode")
    traders = rng.sample(free, needed)
    side = 0 if kind == WASH else rng.choice((BID, ASK))
    episode = ex.start_episode(kind, difficulty, traders, side, now)
    return _BUILDERS[kind](rng, DIFFICULTY[difficulty], episode)
