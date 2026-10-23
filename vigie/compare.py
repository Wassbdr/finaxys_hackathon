"""Comparatif borné A/B : mêmes agents, même graine, Vigie dans un processus Redis séparé."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import fakeredis
import redis

from bus.gate import BusGate
from bus.message import canonical
from sim.config import load_config
from sim.inject import load_scenario
from sim.lockstep import Lockstep
from vigie.scenario import install


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise TimeoutError("Vigie : barrière Redis non acquittée")


class TimedGate(BusGate):
    """BusGate that keeps each decision's wall-clock latency for the bench's p50/p99."""

    def __init__(self, *args):
        super().__init__(*args)
        self.latency_ms = []

    def request_approval(self, msg):
        start = time.perf_counter()
        try:
            return super().request_approval(msg)
        finally:
            self.latency_ms.append(round((time.perf_counter() - start) * 1000, 3))


def learn_reference(cfg, ticks, directory):
    from vigie.statistics import calibrate

    messages = []
    ls = Lockstep(cfg, no_vigie=True, no_llm=True, run_dir=directory)
    ls.bus.tap(lambda msg: messages.append(msg))
    try:
        ls.run(ticks)
        if ls.store.smembers(ls.prefix + "tainted") or any(
            info["mode"] != "normal" for info in ls.world.state()["services"].values()
        ):
            raise ValueError("REFERENCE_CONTAMINATED")
        return calibrate(cfg, messages, ticks, ls.keyring)
    finally:
        ls.close()


class ProtectedWorld:
    def __init__(self, cfg, client, url, directory, profiles):
        cfg = copy.deepcopy(cfg)
        cfg["redis"].update(url=url, prefix="B:")
        # Le constructeur lockstep historique ne transmet pas son client à BusGate.
        cfg["gate"]["impl"] = "stub"
        cfg["vigie"].update(enabled=True, stub=False, public_network_calls=True)
        self.ls = Lockstep(cfg, store=client, no_llm=True, run_dir=directory)
        self.client = client
        self.gate = TimedGate(client, "B:", 0.2, "reject")
        self.ls.cfg["gate"]["impl"] = "bus"
        self.ls.gate = self.ls.world.gate = self.ls.world.executor.gate = self.gate
        self.sub = client.pubsub(ignore_subscribe_messages=True)
        self.sub.subscribe("B:bus")
        self.log = open(directory / "vigie-process.log", "w")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "vigie",
                "--redis-url",
                url,
                "--prefix",
                "B:",
                "--profiles",
                str(profiles),
                "--freeze-state", str(directory / "freeze-state.json"),
                "--fresh-freeze-state",
            ],
            env={**os.environ, "HORIZON_NO_LLM": "1"},
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.last = None
        until(lambda: client.get("B:vigie:ready"))
        # Le banc local publie le même /meta public que les services HTTP ;
        # le contrôleur ne reçoit ni objet World ni état de service interne.
        for sid, core in self.ls.cores.items():
            factory = self.ls._svc_factories[sid]
            client.publish('B:bus', canonical(factory.make(0, 'service_status', '*',
                           {'service': sid, 'mode': 'normal', 'meta': core.meta()})))
        self.ls.bus.tap(self.forward)

    def forward(self, msg):
        if msg["from"] == "vigie":
            return
        self.last = msg["message_id"]
        # Exactement l'état du moteur utilisé par /state, avant l'action irréversible.
        self.client.set("B:state", canonical(self.ls.world.state()))
        if self.process.poll() is not None:
            return
        if "vigie" in self.ls.killed:
            self.disconnect()
            return
        self.client.publish("B:bus", canonical(msg))
        if msg["from"] == "clock":
            self.barrier()

    def pump(self):
        # redis-py 8 reads a message split across packets with the poll timeout:
        # with 0, a large vigie_state heartbeat raised TimeoutError under load.
        while self.sub.connection.can_read(timeout=0):
            raw = self.sub.get_message(timeout=None)
            if raw is None:
                continue
            msg = json.loads(raw["data"])
            if msg.get("from") == "vigie":
                self.ls.publish(msg)

    def barrier(self):
        if self.process.poll() is None and self.last:
            until(lambda: self.client.exists("B:vigie:ack:" + self.last))
            self.pump()

    def step(self):
        self.ls.step()
        self.barrier()
        self.pump()
        self.ls.drain()
        return self.ls.world.state()

    def disconnect(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=3)

    def close(self):
        self.disconnect()
        self.sub.close()
        self.ls.close()
        self.log.close()


def metrics(state):
    losses = state["losses"]
    return {
        "tick": state["tick"],
        "pnl_net_eur": round(
            state["pnl"]["trading_eur"] + state["pnl"]["payment_fraud_eur"], 2
        ),
        "fraud_loss_eur": -state["pnl"]["payment_fraud_eur"],
        "losses_by_service_eur": losses["losses_by_service_eur"],
        "trapped_operations": len(state["trapped_operations"]),
        "contaminated_agents": [c["agent"] for c in state["contaminated_agents"]],
        "decisions": state["decisions"],
        "agents": {
            a: {"light": s["light"], "alive": s["alive"]}
            for a, s in state["agents"].items()
        },
    }


def compare(cfg, scenario, directory, ticks=None, profiles=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    if profiles is None:
        profiles = learn_reference(cfg, 300, directory / "reference")
    profile_path = directory / "profiles.json"
    profile_path.write_text(canonical(profiles))
    server = fakeredis.TcpFakeServer(("127.0.0.1", 0), server_type="redis")
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"redis://127.0.0.1:{server.server_address[1]}/0"
    client = redis.Redis.from_url(url, decode_responses=True)
    a_cfg = copy.deepcopy(cfg)
    a_cfg["redis"]["prefix"] = "A:"
    a = Lockstep(
        a_cfg, no_llm=True, no_vigie=True, store=client, run_dir=directory / "A"
    )
    b = None
    try:
        b = ProtectedWorld(cfg, client, url, directory / "B", profile_path)
        duration = install(a, scenario)
        install(b.ls, scenario)
        for _ in range(ticks or duration):
            sa, sb = a.step(), b.step()
        status = json.loads(client.get("B:vigie:status") or "{}")
        decisions = [
            json.loads(raw) for raw in client.lrange("B:vigie:decisions", 0, -1)
        ]
        result = {
            "seed": cfg["seed"],
            "scenario": scenario["name"],
            "ticks": ticks or duration,
            "A": metrics(sa),
            "B": metrics(sb),
            "vigie": status,
            "gate_decisions": decisions,
            "gate_latency_ms": b.gate.latency_ms,
            "vigie_pid": b.process.pid,
            "vigie_disconnected": b.process.poll() is not None,
        }
        result["avoided_loss_eur"] = round(
            result["B"]["pnl_net_eur"] - result["A"]["pnl_net_eur"], 2
        )
        result["avoided_fraud_eur"] = round(
            result["A"]["fraud_loss_eur"] - result["B"]["fraud_loss_eur"], 2
        )
        (directory / "comparison.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False)
        )
        return result
    finally:
        a.close()
        if b:
            b.close()
        client.close()
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="scenarios/php2shell_day.yaml")
    parser.add_argument("--ticks", type=int)
    parser.add_argument("--out")
    parser.add_argument("--profiles", help="clean reference calibrated elsewhere (another seed)")
    args = parser.parse_args()
    cfg = load_config(overrides={"llm": {"enabled": False, "cache": False}})
    directory = args.out or str(
        Path(cfg["runs_dir"]) / f"vigie-compare-{time.time_ns()}"
    )
    profiles = json.loads(Path(args.profiles).read_text()) if args.profiles else None
    result = compare(cfg, load_scenario(args.scenario), directory, args.ticks, profiles)
    for key in ("A", "B"):
        m = result[key]
        print(
            f"{key} : P&L net {m['pnl_net_eur']:,.2f} € ; fraude {m['fraud_loss_eur']:,.2f} € ; opérations contaminées {m['trapped_operations']}"
        )
    print(
        f"Pertes évitées A/B : {result['avoided_loss_eur']:,.2f} € ; fraude évitée : {result['avoided_fraud_eur']:,.2f} €"
    )
    print(
        f"Provenances bloquées : {result['vigie'].get('contagion_blocked', 0)} ; quarantaine : {result['vigie'].get('quarantined', [])} ; processus Vigie : {result['vigie_pid']}, débranché : {result['vigie_disconnected']}"
    )
    print(f"Preuves : {directory}/comparison.json et journaux A/B")


if __name__ == "__main__":
    main()
