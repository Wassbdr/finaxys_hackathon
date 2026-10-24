"""Horloge centrale : seule source du temps simulé."""

from __future__ import annotations

import re
import time
from typing import Callable


class Clock:
    def __init__(self, tick_seconds: float = 1.0, accelerator: float = 1.0, ticks_per_day: int = 300):
        self.tick_seconds = tick_seconds
        self.accelerator = accelerator
        self.ticks_per_day = ticks_per_day
        self.tick = 0

    @classmethod
    def from_config(cls, cfg: dict) -> "Clock":
        c = cfg["clock"]
        return cls(c["tick_seconds"], c["accelerator"], c["ticks_per_day"])

    @property
    def period(self) -> float:
        """Durée réelle d'un tick."""
        return self.tick_seconds / self.accelerator

    def day_of(self, tick: int) -> int:
        return tick // self.ticks_per_day

    def is_day_start(self, tick: int) -> bool:
        return tick % self.ticks_per_day == 0

    def seconds_to_ticks(self, seconds: float) -> int:
        """« T+30 s » d'un scénario se lit en temps simulé à accélérateur 1."""
        return int(round(seconds / self.tick_seconds))

    def advance(self) -> int:
        self.tick += 1
        return self.tick

    def run(self, on_tick: Callable[[int], None], max_ticks: int | None = None, start: int = 0) -> None:
        """Boucle temps réel, sans dérive cumulée."""
        self.tick = start
        origin = time.monotonic()
        while max_ticks is None or self.tick < start + max_ticks:
            on_tick(self.tick)
            self.tick += 1
            delay = origin + (self.tick - start) * self.period - time.monotonic()
            if delay > 0:
                time.sleep(delay)


_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|min|ticks?)?\s*$")


def parse_at(value, clock: Clock) -> int:
    """`30s`, `2min`, `45 ticks` ou un entier (ticks)."""
    if isinstance(value, (int, float)):
        return int(value)
    match = _DURATION.match(str(value))
    if not match:
        raise ValueError(f"durée illisible : {value!r}")
    amount, unit = float(match.group(1)), match.group(2) or "ticks"
    if unit == "s":
        return clock.seconds_to_ticks(amount)
    if unit == "min":
        return clock.seconds_to_ticks(amount * 60)
    return int(amount)
