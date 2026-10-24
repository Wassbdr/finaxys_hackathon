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


def next_message(pubsub, timeout: float, **kwargs):
    """get_message that waits at most `timeout` for a message to begin, then reads all of it.

    redis-py 8 reads the rest of a message split across TCP packets with the same timeout and
    drops the connection when it runs out: on a loaded Mac, 90 KB Vigie heartbeats were lost and
    the Vigie process died with TimeoutError.
    """
    if not pubsub.connection.can_read(timeout=timeout):
        return None
    return pubsub.get_message(timeout=None, **kwargs)


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
                raw = next_message(pubsub, timeout)
                if raw is None:
                    yield None
                    continue
                try:
                    yield json.loads(raw["data"])
                except (TypeError, ValueError):
                    warn(f"message illisible ignoré sur {raw.get('channel')}")
        finally:
            pubsub.close()
