"""Runtime « un processus par agent » : abonnement au bus Redis, services en HTTP.

    python -m agents.quant [--no-llm] [--no-vigie]
    python -m agents.trader --id trader-2

L'agent traite les messages un par un. Pendant un appel LLM il ne lit plus le bus : s'il prend
du retard, il saute les ticks dépassés (ses données vieillissent, comme en vrai).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys

from agents.base import Context
from agents.llm import LLMCache, LLMJudge
from bus.message import Keyring, matches
from bus.redis_bus import RedisBus, connect
from services.client import HttpServiceClient
from sim.config import known_senders, load_config
from sim.params import Params


def build(cls, agent_id: str, cfg: dict, client):
    keyring = Keyring(known_senders(cfg))
    params = Params(client, cfg["redis"]["prefix"], cfg)
    cache = LLMCache(cfg["llm"]["cache_path"]) if cfg["llm"]["enabled"] and cfg["llm"]["cache"] else None
    llm = LLMJudge(cfg, params, cache=cache)
    ctx = Context(HttpServiceClient(cfg, agent_id), llm, params, keyring, vigie_enabled=cfg["vigie"]["enabled"], deterministic=False)
    return cls(agent_id, cfg, ctx), keyring


def main(cls, default_id: str | None) -> None:
    parser = argparse.ArgumentParser(prog=cls.__name__.lower())
    parser.add_argument("--id", default=default_id, required=default_id is None)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-vigie", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if args.no_llm:
        cfg["llm"]["enabled"] = False
    if args.no_vigie:
        cfg["vigie"]["enabled"] = False
    prefix = cfg["redis"]["prefix"]
    client = connect(cfg["redis"]["url"])
    bus = RedisBus(client, prefix)
    agent, keyring = build(cls, args.id, cfg, client)
    client.set(f"{prefix}agent:{agent.id}:pid", os.getpid())
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"[{agent.id}] prêt (LLM : {agent.ctx.llm.model}, Vigie : {'oui' if cfg['vigie']['enabled'] else 'non'})", flush=True)
    for msg in bus.listen():
        if msg is None or msg.get("from") == agent.id:
            continue
        if not keyring.verify(msg) or not matches(msg, agent.id, agent.role, agent.observes):
            continue
        if msg["type"] == "tick":
            current = int(client.get(f"{prefix}world:tick") or 0)
            if msg["payload"]["tick"] < current:
                continue
        for out in agent.handle(msg):
            bus.publish(out)
