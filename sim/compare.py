"""Mode comparaison : deux mondes isolés, même graine, même horloge, cache LLM partagé.

    A : --no-vigie (gate pass-through, pas de règle du heartbeat)
    B : avec la Vigie (gate + feux + heartbeat ; la vraie Vigie se branche sur le bus `B:bus`)

Les deux mondes tournent en lockstep dans ce processus, sur une seule horloge : une injection est
appliquée aux deux au même tick. Leur état est publié dans Redis sous les préfixes A: et B:, si bien
que l'API sert /state?world=A|B, /graph?world=..., /stream?world=... et /compare.

    python -m sim.compare        (broker + comparaison + API, via le superviseur)
    python -m sim.compare run    (la comparaison seule)
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from collections import deque
from pathlib import Path

from agents.llm import LLMCache
from bus.redis_bus import RedisBus, connect
from sim.config import load_config
from sim.inject import LockstepTarget, load_scenario, run_command, schedule_scenario
from sim.lockstep import Lockstep

EXTERNAL_SENDERS = {"vigie", "human", "inject", "secu"}
QUEUE = "compare:inject_queue"


class CompareRunner:
    def __init__(self, cfg: dict, client, no_llm: bool = True):
        self.cfg = cfg
        self.client = client
        cache = LLMCache(cfg["llm"]["cache_path"]) if cfg["llm"]["cache"] and not no_llm else LLMCache(None)
        self.worlds: dict[str, Lockstep] = {}
        self.buses: dict[str, RedisBus] = {}
        self.ours: dict[str, set] = {}
        self.inbox: dict[str, deque] = {}
        for name, no_vigie in (("A", True), ("B", False)):
            wcfg = copy.deepcopy(cfg)
            wcfg["redis"]["prefix"] = f"{name}:"
            run_dir = Path(cfg["runs_dir"]) / name
            ls = Lockstep(wcfg, no_llm=no_llm, no_vigie=no_vigie, store=client, run_dir=run_dir, llm_cache=cache)
            bus = RedisBus(client, wcfg["redis"]["prefix"])
            self.ours[name] = set()
            self.inbox[name] = deque()
            ls.bus.tap(self._forward(name, bus))
            self.worlds[name], self.buses[name] = ls, bus

    def _forward(self, name: str, bus: RedisBus):
        def tap(msg: dict) -> None:
            self.ours[name].add(msg["message_id"])
            bus.publish(msg)
        return tap

    def listen_external(self, name: str) -> None:
        """Messages venus d'ailleurs (vraie Vigie, humain) : livrés au monde au tick suivant."""
        for msg in self.buses[name].listen(timeout=0.5):
            if msg is None or msg.get("message_id") in self.ours[name]:
                continue
            if msg.get("from") in EXTERNAL_SENDERS:
                self.inbox[name].append(msg)

    def apply_commands(self) -> None:
        while (raw := self.client.lpop(QUEUE)) is not None:
            args = json.loads(raw)
            for name, ls in self.worlds.items():
                try:
                    if args[0] == "scenario":
                        path = Path(args[1])
                        if not path.exists():
                            path = Path("scenarios") / f"{args[1].removesuffix('.yaml')}.yaml"
                        schedule_scenario(ls, load_scenario(path), start=ls.tick)
                        print(f"[compare] {name} t{ls.tick} scénario {path.name} planifié", flush=True)
                    else:
                        print(f"[compare] {name} t{ls.tick} {' '.join(args)} -> {run_command(LockstepTarget(ls), args)}", flush=True)
                except (ValueError, KeyError, IndexError) as err:
                    print(f"[compare] {name} commande refusée : {err}", flush=True)

    def step(self) -> None:
        self.apply_commands()
        for name, ls in self.worlds.items():
            while self.inbox[name]:
                ls.publish(self.inbox[name].popleft())
            ls.step()


def run(no_llm: bool, ticks: int | None) -> None:
    cfg = load_config()
    client = connect(cfg["redis"]["url"])
    runner = CompareRunner(cfg, client, no_llm=no_llm)
    for name in runner.worlds:
        threading.Thread(target=runner.listen_external, args=(name,), daemon=True).start()
    clock = runner.worlds["A"].world.clock
    print(f"[compare] mondes A (sans Vigie) et B (avec Vigie), graine {cfg['seed']}, tick {clock.period:.2f} s", flush=True)
    try:
        clock.run(lambda _t: runner.step(), max_ticks=ticks)
    except KeyboardInterrupt:
        pass
    finally:
        for ls in runner.worlds.values():
            ls.close()


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    no_llm = "--llm" not in argv
    if argv and argv[0] == "run":
        ticks = int(argv[argv.index("--ticks") + 1]) if "--ticks" in argv else None
        run(no_llm, ticks)
        return
    from sim.launch import python_bin, supervise

    os.environ["HORIZON_COMPARE"] = "1"
    py = python_bin()
    sys.exit(supervise([
        ("broker", f"{py} -m bus.broker"),
        ("compare", f"{py} -m sim.compare run" + ("" if no_llm else " --llm")),
        ("api", f"{py} -m api.app"),
    ], essential={"broker", "compare"}))


if __name__ == "__main__":
    main()
