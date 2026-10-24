"""Vigie de remplacement : émet `heartbeat` à chaque tick tant que la vraie Vigie n'est pas branchée.

    python -m bus.vigie_stub     (kill-vigie arrête ce processus)
"""

from __future__ import annotations

import os

from bus.message import MessageFactory


class VigieStub:
    id = "vigie"

    def __init__(self, keyring, deterministic: bool = True):
        self.factory = MessageFactory("vigie", keyring, deterministic)

    def on_message(self, msg: dict) -> list[dict]:
        if msg["type"] != "tick":
            return []
        return [self.factory.make(msg["payload"]["tick"], "heartbeat", "*", {"agent": "vigie", "role": "vigie", "stub": True})]


def main() -> None:
    from bus.message import Keyring
    from bus.redis_bus import RedisBus, connect
    from sim.config import known_senders, load_config

    cfg = load_config()
    client = connect(cfg["redis"]["url"])
    bus = RedisBus(client, cfg["redis"]["prefix"])
    client.set(f"{cfg['redis']['prefix']}agent:vigie:pid", os.getpid())
    stub = VigieStub(Keyring(known_senders(cfg)), deterministic=False)
    print("[vigie-stub] heartbeat à chaque tick", flush=True)
    for msg in bus.listen():
        if msg is not None and msg.get("from") == "clock":
            for out in stub.on_message(msg):
                bus.publish(out)


if __name__ == "__main__":
    main()
