from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.sources import LiveSource
from bus.message import MessageFactory, canonical
from journal.writer import read_journal
from sim.inject import load_scenario, schedule_scenario
from sim.lockstep import Lockstep
from vigie.graph import Graph
from vigie.metrics import Layer1

ROOT = Path(__file__).resolve().parents[2]


def prov(service, tick, h):
    return {"service": service, "tick": tick, "response_hash": h, "version": "x"}


def chain(k, tick=5, eur=1_000_000):
    """quant -> trader-1 (calls news-wire and payment-gateway) -> compliance -> risk -> executor."""
    f = {a: MessageFactory(a, k) for a in ("quant", "trader-1", "compliance", "risk", "executor")}
    beat = f["trader-1"].make(tick, "heartbeat", "*", {"agent": "trader-1", "depends_on": ["news-wire"]})
    q = f["quant"].make(tick, "price_update", "*", {"assets": {}}, [], [prov("market-feed", tick, "m1")])
    mine = [prov("market-feed", tick, "m1"), prov("news-wire", tick, "n1"), prov("payment-gateway", tick, "p1")]
    order = f["trader-1"].make(tick, "order_proposal", "compliance", {"asset": "ACME", "side": "buy", "notional_eur": eur}, [q["message_id"]], mine)
    comp = f["compliance"].make(tick, "compliance_decision", "risk", {"approved": True, "kind": "order"}, [order["message_id"]], mine)
    # Risk reads quant's prices without citing quant's message: reuse, not a call.
    scale = f["risk"].make(tick, "risk_decision", "traders", {"kind": "limits_scale", "scale": 0.5}, [], [prov("market-feed", tick, "m1")])
    risk =f["risk"].make(tick, "risk_decision", "executor", {"approved": True, "kind": "order"}, [comp["message_id"], order["message_id"]], [*mine, prov("core-ledger", tick, "c1")])
    ex = f["executor"].make(tick, "execution", "*", {"status": "executed", "kind": "order", "notional_eur": eur}, [risk["message_id"]], risk["data_provenance"])
    return [beat, q, order, comp, scale, risk, ex]


def test_hidden_dependency_comes_from_the_bus_not_the_declaration(cfg, setup):
    _, k, _ = setup
    g = Graph(cfg)
    msgs = chain(k)
    for m in msgs:
        g.observe(m)
    (found,) = g.drain()
    assert (found.kind, found.target, found.severity) == ("hidden_dependency", "trader-1->payment-gateway", "critical")
    assert found.evidence == [msgs[0]["message_id"], msgs[2]["message_id"]]
    assert set(g.uses) == {("quant", "market-feed"), ("trader-1", "news-wire"), ("trader-1", "payment-gateway"), ("risk", "core-ledger")}
    assert ("quant", "risk") in g.unlinked


def test_graph_telemetry_counts_calls_but_its_flags_are_not_trusted(cfg, setup):
    _, k, _ = setup
    g = Graph(cfg)
    g.observe(chain(k)[0])
    g.topology({"edges": [
        {"source": "trader-1", "target": "kyc-docs", "kind": "data", "declared": True, "hidden": False, "transitive": False, "calls": 4},
        {"source": "trader-1", "target": "core-ledger", "kind": "data", "transitive": True, "messages": 9},
    ]})
    (found,) = g.drain()
    assert found.target == "trader-1->kyc-docs" and found.detail["seen_by"] == ["telemetry"]


def test_cve_reports_the_impact_radius_with_the_money_behind_it(cfg, setup):
    _, k, _ = setup
    g = Graph(cfg)
    for m in chain(k):
        g.observe(m)
    g.drain()
    cve = MessageFactory("secu", k).make(6, "alert", "*", {"kind": "cve_published", "affected_services": ["payment-gateway"]})
    g.observe(cve)
    (found,) = g.drain()
    assert (found.kind, found.target, found.severity) == ("impact_radius", "payment-gateway", "critical")
    assert found.detail["direct"] == ["trader-1"]
    assert found.detail["transitive"] == ["compliance", "executor", "risk"]
    assert found.detail["eur"] == 1_000_000 and found.evidence[0] == cve["message_id"]
    g.suspect("payment-gateway", "cve_unpatchable", [cve["message_id"]])
    assert not g.drain()


def test_ceo_figures_and_an_untraced_execution(cfg, setup):
    _, k, _ = setup
    layer = Layer1(cfg)
    for m in chain(k):
        layer.observe(m)
    orphan = MessageFactory("executor", k).make(7, "execution", "*", {"status": "executed", "kind": "order", "notional_eur": 500_000}, ["risk:forged"], [])
    layer.observe(orphan)
    found = [f for f in layer.drain() if f.kind == "untraced_decision"]
    ceo = layer.metrics()["ceo"]
    assert ceo["executed_eur"] == 1_500_000 and ceo["unpatchable_eur"] == 1_000_000
    assert ceo["reconstitution_rate"] == 0.5
    assert found[0].evidence == [orphan["message_id"], "risk:forged"]


def test_status_carries_layer1_and_stays_json(setup):
    c, k, state = setup
    c.observe(MessageFactory('clock', k).make(10, 'tick', '*', {'tick': 10}))
    for m in chain(k, tick=10):
        c.observe(m, state)
    status = c.status()
    assert {"graph", "layer1", "findings"} <= set(status)
    assert any(f["kind"] == "hidden_dependency" for f in status["findings"])
    canonical(status)


def test_php2shell_day_journal_shows_hidden_dependency_and_radius(cfg, tmp_path):
    ls = Lockstep(cfg, run_dir=tmp_path / "run")
    ls.run(schedule_scenario(ls, load_scenario(ROOT / "scenarios" / "php2shell_day.yaml")))
    layer, found = Layer1(cfg), []
    for entry in read_journal(tmp_path / "run" / "journal.jsonl"):
        if entry["signature_valid"]:
            layer.observe(entry["message"])
            found += layer.drain()
    hidden = {f.target for f in found if f.kind == "hidden_dependency"}
    assert hidden == {f"trader-{i}->payment-gateway" for i in (1, 2, 3)}
    radius = {f.target: f for f in found if f.kind == "impact_radius"}
    assert {"market-feed", "payment-gateway"} <= set(radius)
    assert radius["market-feed"].detail["direct"] == ["quant"] and radius["market-feed"].detail["eur"] > 0
    assert layer.metrics()["ceo"]["reconstitution_rate"] == 1.0

    ls.store.set(ls.cfg["redis"]["prefix"] + "vigie:status", canonical({"graph": layer.graph.snapshot(), "layer1": layer.metrics(), "findings": [f.as_dict() for f in found]}))
    client = TestClient(create_app({"main": LiveSource(ls.cfg, ls.store)}))
    assert any(e["hidden"] for e in client.get("/vigie/graph").json()["edges"])
    assert client.get("/vigie/metrics").json()["ceo"]["executions"] > 0
    assert len(client.get("/vigie/findings").json()) == len(found)
    ls.close()
