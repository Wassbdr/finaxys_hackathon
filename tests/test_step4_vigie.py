"""Étape 4 : gate stub, feux, heartbeat de la Vigie, --no-vigie."""

import threading
import time

import fakeredis
import pytest

from bus.gate import BusGate, Decision, PassThroughGate, StubGate, make_gate
from sim.lockstep import Lockstep
from tests.helpers import of_type, price_update, send


def light(ls, value, sender="vigie"):
    ls.publish(send(ls, sender, "light_change", "*", {"light": value, "reason": "test"}))


def executions(ls, status):
    return [m for m in ls.world.observer.decisions.items() if m[0] == f"execution:{status}"]


def test_gate_factory(cfg):
    assert isinstance(make_gate(cfg), StubGate)
    cfg["vigie"]["enabled"] = False
    assert isinstance(make_gate(cfg), PassThroughGate)


def test_stub_gate_approves_everything_and_keeps_requests(cfg):
    ls = Lockstep(cfg)
    ls.run(150)
    assert ls.gate.requests, "toute exécution passe par le gate"
    assert all(m["type"] == "risk_decision" for m in ls.gate.requests)
    assert ls.world.observer.decisions.get("execution:executed", 0) == len(ls.gate.requests)


def test_gate_refusal_blocks_execution(cfg):
    class DenyGate(StubGate):
        def request_approval(self, msg):
            return Decision(False, "refus Vigie (test)", "vigie")

    ls = Lockstep(cfg)
    ls.world.executor.gate = DenyGate()
    state = ls.run(150)
    assert state["decisions"].get("execution:executed", 0) == 0
    assert state["decisions"].get("execution:refused", 0) > 0
    assert state["payments"]["legit_total_eur"] == 0


def test_orange_halves_amounts_and_blocks_irreversible(cfg):
    ls = Lockstep(cfg)
    ls.run(5)
    light(ls, "ORANGE")
    ls.drain()
    trader = ls.agents["trader-3"]
    assert trader.light == "ORANGE" and trader.amount_factor == 0.5
    trader.tick, trader.last_update = 6, price_update(ls, {"ACME": 100.0, "BOREAL": 100.0, "CITRA": 100.0})
    trader.sentiment["BOREAL"] = (-1.0, 6)
    trader.last_order_tick.clear()
    trader.peers.clear()
    trader.limits_scale = 1.0
    trader.out = []
    trader.decide()
    assert of_type(trader.out, "order_proposal")[0]["payload"]["notional_eur"] == 2_500_000
    assert ls.agents["payment"].check_limits(600_000) is not None           # 1 M€ / 2
    executed_before = ls.world.observer.decisions.get("execution:executed", 0)
    state = ls.run(120)
    assert state["decisions"].get("execution:executed", 0) == executed_before
    assert state["decisions"].get("execution:refused", 0) > 0
    assert any(a["kind"] == "light_effective" for a in state["alerts"])


def test_red_pauses_agents_until_a_human_restarts(cfg):
    ls = Lockstep(cfg)
    ls.run(3)
    light(ls, "ROUGE")
    ls.drain()
    published = ls.agents["quant"].published
    ls.run(10)
    assert ls.agents["quant"].published == published                        # en pause
    assert ls.world.observer.heartbeats["quant"]["paused"] is True           # mais vivant
    light(ls, "VERT", sender="trader-1")                                     # pas d'autorité
    ls.drain()
    assert ls.agents["quant"].paused
    ls.publish(send(ls, "human", "human_decision", "*", {"action": "resume"}))
    ls.run(3)
    assert not ls.agents["quant"].paused and ls.agents["quant"].published > published


def test_lost_vigie_heartbeat_turns_agents_orange(cfg):
    ls = Lockstep(cfg)
    ls.run(10)
    assert ls.agents["risk"].light == "VERT"
    ls.killed.add("vigie")
    ls.run(5)
    assert ls.agents["risk"].light == "VERT"                                 # 5 ticks : toléré
    ls.run(2)
    assert all(a.light == "ORANGE" for a in ls.agents.values())
    assert ls.world.executor.light == "ORANGE"
    assert any(a["kind"] == "vigie_heartbeat_lost" for a in ls.world.observer.alerts)
    ls.killed.discard("vigie")
    ls.run(2)
    assert ls.agents["risk"].light == "VERT"


def test_no_vigie_disables_gate_and_heartbeat_rule(cfg):
    ls = Lockstep(cfg, no_vigie=True)
    assert isinstance(ls.world.executor.gate, PassThroughGate) and ls.vigie is None
    state = ls.run(60)
    assert all(a.light == "VERT" for a in ls.agents.values())
    assert state["vigie"]["enabled"] is False


def test_bus_gate_round_trip_and_timeout():
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    gate = BusGate(client, "", timeout_s=1.0, default_on_timeout="reject")

    def vigie():
        import json

        sub = fakeredis.FakeRedis(server=server, decode_responses=True).pubsub(ignore_subscribe_messages=True)
        sub.subscribe("vigie.approval_requests")
        deadline = time.time() + 2
        while time.time() < deadline:
            raw = sub.get_message(timeout=0.1)
            if raw:
                req = json.loads(raw["data"])
                client.publish("vigie.approval_responses", json.dumps({"request_id": req["request_id"], "approved": False,
                                                                        "reason": "IBAN suspect", "decided_by": "vigie"}))
                return

    thread = threading.Thread(target=vigie)
    thread.start()
    time.sleep(0.1)
    decision = gate.request_approval({"message_id": "m1", "type": "risk_decision"})
    thread.join()
    assert decision == Decision(False, "IBAN suspect", "vigie")
    gate.timeout_s = 0.2
    late = gate.request_approval({"message_id": "m2"})
    assert not late.approved and late.decided_by == "timeout"
