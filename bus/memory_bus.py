"""Bus en mémoire pour le mode lockstep : file FIFO, livraison synchrone et déterministe."""

from __future__ import annotations

from collections import deque
from typing import Callable


class MemoryBus:
    def __init__(self) -> None:
        self.queue: deque[dict] = deque()
        self.taps: list[Callable[[dict], None]] = []
        self.channels: dict[str, deque] = {}

    def publish(self, msg: dict) -> None:
        self.queue.append(msg)
        for tap in self.taps:
            tap(msg)

    def tap(self, fn: Callable[[dict], None]) -> None:
        """Abonnement passif (journal, observateur) : voit chaque message à sa publication."""
        self.taps.append(fn)

    def pop(self) -> dict | None:
        return self.queue.popleft() if self.queue else None

    # Canaux annexes (requêtes d'approbation de la Vigie)
    def publish_raw(self, channel: str, data: dict) -> None:
        self.channels.setdefault(channel, deque()).append(data)
