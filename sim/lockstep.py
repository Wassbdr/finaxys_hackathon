"""Lockstep : tout le monde dans un seul processus, ordre fixe, résultat déterministe.

Sert aux tests (même graine -> même journal), au mode comparaison et aux scénarios hors ligne :
    python -m sim.lockstep --ticks 300 [--scenario scenarios/php2shell_day.yaml] [--no-vigie]
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import fakeredis

from agents.base import Context
from agents.compliance import Compliance
from agents.llm import LLMCache, LLMJudge
from agents.payment import Payment
from agents.quant import Quant
from agents.risk import Risk
from agents.trader import Trader
from bus.gate import make_gate
from bus.memory_bus import MemoryBus
from bus.message import Keyring, MessageFactory, matches
from bus.vigie_stub import VigieStub
from journal.writer import JournalWriter
from services.client import LocalServiceClient
from services.registry import build_cores
from sim.config import agent_ids, known_senders, load_config
from sim.params import Params
from sim.world import World

AGENT_CLASSES = {"quant": Quant, "compliance": Compliance, "risk": Risk, "payment": Payment}
MAX_MESSAGES_PER_TICK = 20_000


def agent_class(agent_id: str):
    return Trader if agent_id.startswith("trader-") else AGENT_CLASSES[agent_id]


class Lockstep:
    def __init__(self, cfg: dict | None = None, *, no_llm: bool = True, no_vigie: bool = False, llm_backend=None,
                 run_dir: str | Path | None = None, store=None, llm_cache: LLMCache | None = None):
        cfg = copy.deepcopy(cfg or load_config())
        if no_llm:
            cfg["llm"]["enabled"] = False
        if no_vigie:
            cfg["vigie"]["enabled"] = False
            cfg["gate"]["impl"] = "passthrough"
        self.cfg = cfg
        self.prefix = cfg["redis"]["prefix"]
        self.store = store or fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        self.keyring = Keyring(known_senders(cfg))
        self.bus = MemoryBus()
        self.run_dir = Path(run_dir) if run_dir else None
        self.journal = JournalWriter(self.run_dir / "journal.jsonl") if self.run_dir else None
        if self.journal:
            self.bus.tap(lambda m: self.journal.append(m, self.keyring.verify(m)))
        self.tick = 0
        self._svc_factories = {sid: MessageFactory(f"svc:{sid}", self.keyring) for sid in cfg["services"]}
        self.cores = build_cores(cfg, self.store, self.prefix, self._publisher_for)
        self.params = Params(self.store, self.prefix, cfg)
        self.gate = make_gate(cfg)
        timeout = cfg["chaos"]["client_timeout_ms"]
        self.world = World(cfg, self.store, self.keyring, self.gate, LocalServiceClient(self.cores, "executor", timeout),
                           deterministic=True, run_dir=self.run_dir, public_publish=self.bus.publish)
        cache = llm_cache
        if cache is None and cfg["llm"]["enabled"] and cfg["llm"]["cache"]:
            cache = LLMCache(cfg["llm"]["cache_path"])
        self.llm = LLMJudge(cfg, self.params, backend=llm_backend, cache=cache)
        self.agents = {}
        for agent_id in agent_ids(cfg):
            ctx = Context(LocalServiceClient(self.cores, agent_id, timeout), self.llm, self.params, self.keyring,
                          vigie_enabled=cfg["vigie"]["enabled"], deterministic=True)
            self.agents[agent_id] = agent_class(agent_id)(agent_id, cfg, ctx)
        self.vigie = VigieStub(self.keyring) if cfg["vigie"]["enabled"] and cfg["vigie"]["stub"] else None
        self.killed: set[str] = set()
        self.schedule: dict[int, list] = {}
        self.inject_factory = MessageFactory("inject", self.keyring)
        self.messages_seen = 0

    def _publisher_for(self, sid: str):
        def publish(type_, payload):
            self.bus.publish(self._svc_factories[sid].make(self.tick, type_, "*", payload))
        return publish

    # --- livraison ------------------------------------------------------------------------
    def deliver(self, msg: dict) -> None:
        valid = self.keyring.verify(msg)
        out = self.world.on_message(msg, valid)
        if valid:
            for agent_id, agent in self.agents.items():
                if agent_id not in self.killed and matches(msg, agent.id, agent.role, agent.observes):
                    out += agent.handle(msg)
            if self.vigie and "vigie" not in self.killed:
                out += self.vigie.on_message(msg)
        for m in out:
            self.bus.publish(m)

    def drain(self) -> None:
        count = 0
        while (msg := self.bus.pop()) is not None:
            self.deliver(msg)
            count += 1
            if count > MAX_MESSAGES_PER_TICK:
                raise RuntimeError(f"tick {self.tick} : plus de {MAX_MESSAGES_PER_TICK} messages, boucle suspecte")
        self.messages_seen += count

    def step(self) -> dict:
        tick_msgs = self.world.begin_tick(self.tick)
        for action in self.schedule.pop(self.tick, []):
            action(self)
        for m in tick_msgs:
            self.bus.publish(m)
        self.drain()
        state = self.world.end_tick(self.tick)
        self.tick += 1
        return state

    def run(self, ticks: int) -> dict:
        state = {}
        for _ in range(ticks):
            state = self.step()
        return state

    def at(self, tick: int, action) -> None:
        """Planifie une action (callable(lockstep)) au début du tick donné."""
        self.schedule.setdefault(tick, []).append(action)

    def publish(self, msg: dict) -> None:
        """Message venu de l'extérieur (inject, humain) : livré au prochain drain."""
        self.bus.publish(msg)

    def close(self) -> None:
        self.world.close()
        if self.journal:
            self.journal.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="sim.lockstep")
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--scenario")
    parser.add_argument("--no-vigie", action="store_true")
    parser.add_argument("--llm", action="store_true", help="utilise le LLM réel (sinon --no-llm)")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    overrides = {"seed": args.seed} if args.seed is not None else None
    cfg = load_config(overrides=overrides)
    run_dir = args.run_dir or str(Path(cfg["runs_dir"]) / "lockstep")
    ls = Lockstep(cfg, no_llm=not args.llm, no_vigie=args.no_vigie, run_dir=run_dir)
    ticks = args.ticks
    if args.scenario:
        from sim.inject import load_scenario, schedule_scenario

        scenario = load_scenario(args.scenario)
        ticks = schedule_scenario(ls, scenario) if args.ticks == 300 else args.ticks
    state = ls.run(ticks)
    ls.close()
    summary = {k: state[k] for k in ("tick", "equity_eur", "pnl", "exposure", "var_99_1d_eur")}
    summary["losses_by_service_eur"] = state["losses"]["losses_by_service_eur"]
    summary["contaminated_agents"] = [c["agent"] for c in state["contaminated_agents"]]
    summary["trapped_operations"] = len(state["trapped_operations"])
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"journal : {run_dir}/journal.jsonl")


if __name__ == "__main__":
    main()
