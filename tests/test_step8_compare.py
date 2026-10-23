"""Étape 8 : mode comparaison (A sans Vigie, B avec), même graine, injections au même tick."""

import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.sources import LiveSource
from bus.gate import PassThroughGate, StubGate
from sim.compare import QUEUE, CompareRunner
from sim.inject import QueueTarget, run_command


@pytest.fixture
def runner(cfg):
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    return CompareRunner(cfg, client), client


def test_worlds_are_isolated_and_configured(runner):
    r, client = runner
    a, b = r.worlds["A"], r.worlds["B"]
    assert isinstance(a.world.executor.gate, PassThroughGate) and isinstance(b.world.executor.gate, StubGate)
    assert a.vigie is None and b.vigie is not None
    assert a.llm.cache is b.llm.cache                                         # cache LLM partagé
    for _ in range(30):
        r.step()
    assert client.get("A:state") and client.get("B:state") and client.get("state") is None
    sa, sb = json.loads(client.get("A:state")), json.loads(client.get("B:state"))
    assert sa["tick"] == sb["tick"] == 29
    assert sa["prices"] == sb["prices"] and sa["pnl"] == sb["pnl"]           # même graine, même horloge


def test_inject_applies_to_both_on_the_same_tick(runner, cfg):
    r, client = runner
    for _ in range(10):
        r.step()
    target = QueueTarget({**cfg, "redis": {**cfg["redis"], "prefix": "A:"}}, client)
    assert "en file" in run_command(target, ["compromise", "payment-gateway"])
    run_command(target, ["kill-vigie"])
    r.step()
    assert client.llen(QUEUE) == 0
    gateways = [r.worlds[name].cores["payment-gateway"] for name in ("A", "B")]
    assert {g.mode() for g in gateways} == {"compromised"}
    assert gateways[0].mode_since() == gateways[1].mode_since()
    for _ in range(60):
        r.step()
    sa, sb = json.loads(client.get("A:state")), json.loads(client.get("B:state"))
    assert sa["pnl"]["payment_fraud_eur"] < 0                                 # sans Vigie : la fraude passe
    assert sb["pnl"]["payment_fraud_eur"] > sa["pnl"]["payment_fraud_eur"]    # Vigie absente -> ORANGE dans B
    assert r.worlds["B"].world.executor.light == "ORANGE" and r.worlds["A"].world.executor.light == "VERT"


def test_api_serves_both_worlds(runner, cfg):
    r, client = runner
    for _ in range(5):
        r.step()
    sources = {n: LiveSource({**cfg, "redis": {**cfg["redis"], "prefix": f"{n}:"}}, client) for n in ("A", "B")}
    target = QueueTarget({**cfg, "redis": {**cfg["redis"], "prefix": "A:"}}, client)
    api = TestClient(create_app(sources, {"all": target}, default_world="A"))
    assert api.get("/state", params={"world": "B"}).json()["vigie"]["enabled"] is True
    assert api.get("/state", params={"world": "A"}).json()["vigie"]["enabled"] is False
    assert set(api.get("/compare").json()) == {"A", "B"}
    assert api.get("/graph", params={"world": "B"}).status_code == 200
    resp = api.post("/inject", json={"command": "set herding 0.8"}).json()
    assert resp["ok"] and client.llen(QUEUE) == 1
    r.step()
    assert r.worlds["A"].params.get("herding") == r.worlds["B"].params.get("herding") == 0.8


def test_external_vigie_messages_reach_world_b(runner):
    r, _ = runner
    for _ in range(3):
        r.step()
    from bus.message import MessageFactory

    light = MessageFactory("vigie", r.worlds["B"].keyring).make(3, "light_change", "*", {"light": "ROUGE"})
    r.inbox["B"].append(light)
    r.step()
    assert r.worlds["B"].agents["quant"].paused and not r.worlds["A"].agents["quant"].paused
