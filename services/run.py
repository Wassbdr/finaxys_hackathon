"""Lance un service : python -m services.run market-feed"""

from __future__ import annotations

import argparse

import uvicorn

from bus.message import Keyring, MessageFactory
from bus.redis_bus import RedisBus, connect
from services.app import make_app
from services.registry import SERVICE_CLASSES
from sim.config import known_senders, load_config


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="services.run")
    parser.add_argument("service", choices=sorted(SERVICE_CLASSES))
    args = parser.parse_args(argv)
    cfg = load_config()
    prefix = cfg["redis"]["prefix"]
    client = connect(cfg["redis"]["url"])
    bus = RedisBus(client, prefix)
    sender = f"svc:{args.service}"
    factory = MessageFactory(sender, Keyring(known_senders(cfg)), deterministic=False)
    core_holder = {}

    def publish(type_, payload):
        bus.publish(factory.make(core_holder["core"].tick(), type_, "*", payload))

    core = SERVICE_CLASSES[args.service](cfg, client, prefix, publish)
    core_holder["core"] = core
    port = cfg["services"][args.service]["port"] + int(cfg.get("port_offset", 0))
    uvicorn.run(make_app(core), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
