"""Bus Redis pub/sub : un canal `{prefix}bus` pour tous les messages."""

from __future__ import annotations

import json
import time
from typing import Iterator

import redis

from bus.message import canonical, warn


def connect(url: str, wait_s: float = 30.0) -> redis.Redis:
    """Connexion avec attente : le broker peut démarrer après nous."""
    deadline = time.time() + wait_s
    while True:
        try:
            client = redis.Redis.from_url(url, decode_responses=True)
            client.ping()
            return client
        except redis.exceptions.ConnectionError:
            if time.time() > deadline:
                raise
            time.sleep(0.5)


class RedisBus:
    def __init__(self, client: redis.Redis, prefix: str = ""):
        self.client = client
        self.prefix = prefix
        self.channel = f"{prefix}bus"

    def publish(self, msg: dict) -> None:
        self.client.publish(self.channel, canonical(msg))

    def publish_raw(self, channel: str, data: dict) -> None:
        self.client.publish(f"{self.prefix}{channel}", canonical(data))

    def listen(self, channels: list[str] | None = None, timeout: float = 1.0) -> Iterator[dict | None]:
        """Itère sur les messages ; renvoie None à chaque délai sans message."""
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        names = [self.channel] if channels is None else [f"{self.prefix}{c}" for c in channels]
        pubsub.subscribe(*names)
        try:
            while True:
                raw = pubsub.get_message(timeout=timeout)
                if raw is None:
                    yield None
                    continue
                try:
                    yield json.loads(raw["data"])
                except (TypeError, ValueError):
                    warn(f"message illisible ignoré sur {raw.get('channel')}")
        finally:
            pubsub.close()
