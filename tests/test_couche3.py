"""Contrats de la couche 3, sans oracle de simulation."""
import copy
from dataclasses import asdict

import pytest

from bus.message import Keyring, MessageFactory
from sim.config import known_senders, load_config
from vigie.core import VigieState
from vigie.contagion import Contagion
from vigie.main import Runtime, serve


@pytest.fixture
def setup():
    cfg = load_config(overrides={"llm": {"enabled": False, "cache": False}})
    keys = Keyring(known_senders(cfg))
    state = VigieState(meta={sid: {"language": "php", **spec} for sid, spec in cfg["services"].items()})
    detector = Contagion(cfg, state)
    factories = {s: MessageFactory(s, keys) for s in known_senders(cfg)}
    return cfg, keys, state, detector, factories


def accept(setup, sender, kind, payload=None, *, tick=0, parents=(), services=()):
    _, keys, state, detector, factories = setup
    msg = factories[sender].make(tick, kind, "*", payload or {}, list(parents), [
        {"service": s, "response_hash": f"{s}-{tick}", "tick": tick, "version": "1"} for s in services])
    state.observe(msg, keys)
    return msg, detector.on_message(msg)


def test_retroactive_marking_and_targeted_proof(setup):
    root, _ = accept(setup, "quant", "price_update", services=["market-feed"])
    child, _ = accept(setup, "trader-1", "order_proposal", parents=[root["message_id"]])
    safe, _ = accept(setup, "payment", "payment_proposal", services=["news-wire"])
    detector = setup[3]
    assert not detector.marked(child)[0]
    alert, findings = accept(setup, "secu", "alert", {"kind": "cve_published", "cve": {
        "id": "test-cve", "affects": {"language": "php", "version_lt": "8.1"}}}, tick=10)
    assert any(f.target == "market-feed" and f.layer == 3 for f in findings)
    marked, evidence = detector.marked(child)
    assert marked
    assert set([root["message_id"], child["message_id"], alert["message_id"]]) <= set(evidence)
    assert not detector.marked(safe)[0]


def test_missing_parent_and_cycle_are_not_invented_contamination(setup):
    msg, _ = accept(setup, "trader-1", "order_proposal", parents=["missing"])
    assert setup[3].marked(msg) == (False, [])
    setup[2].messages[msg["message_id"]]["depends_on"].append(msg["message_id"])
    assert not setup[3].marked(msg)[0]


def test_ledger_without_cve_and_forged_execution_ignored(setup):
    cfg, keys, state, detector, factories = setup
    initial = detector.cash
    trade = factories["executor"].make(0, "execution", "*", {
        "status": "executed", "kind": "order", "exec_id": "e1", "qty": 10, "fill_price": 123.45})
    forged = copy.deepcopy(trade)
    forged["payload"]["qty"] = 1000
    assert not state.observe(forged, keys)
    detector.on_message(forged)
    assert detector.cash == initial
    assert state.observe(trade, keys)
    detector.on_message(trade)
    detector.on_message(trade)
    assert detector.cash == pytest.approx(initial - 1234.5)
    _, findings = accept(setup, "risk", "heartbeat", {"view": {"cash_eur": initial * 1.15}}, tick=1,
                         services=["core-ledger"])
    assert any(f.kind == "ledger_mismatch" and f.target == "core-ledger" for f in findings)
    assert not detector.cve_ticks


@pytest.mark.parametrize("series,kind", [([100, 125], "price_jump"),
    ([100] * 7, "price_frozen"), ([100, 98, 96, 94, 92, 90, 88], "monotone_drift")])
def test_data_anomalies(setup, series, kind):
    findings = []
    for tick, price in enumerate(series):
        _, out = accept(setup, "quant", "price_update", {"assets": {"ACME": {"price": price}}},
                        tick=tick, services=["market-feed"])
        findings.extend(out)
    assert kind in [f.kind for f in findings]


def test_no_false_positive_on_small_fluctuations(setup):
    for tick, price in enumerate([100, 100.1, 99.9, 100.05, 100.2, 100.1, 99.95]):
        _, out = accept(setup, "quant", "price_update", {"assets": {"ACME": {"price": price}}},
                        tick=tick, services=["market-feed"])
        assert out == []
    assert setup[3].suspects == {}


def test_runtime_gate_only_blocks_marked_and_herding_hook(setup):
    cfg, keys, _, _, factories = setup
    class Herding:
        def on_message(self, msg):
            from vigie.core import Finding
            return [Finding(msg["tick"], 3, "herding", "traders", "warning", [msg["message_id"]], 1.0)]
    runtime = Runtime(cfg, keys, meta=setup[2].meta, layers=[Herding()])
    clean = factories["risk"].make(1, "risk_decision", "executor", {"kind": "payment"}, data_provenance=[{"service": "news-wire"}])
    assert runtime.request_approval(clean).approved
    runtime.on_message(factories["secu"].make(1, "alert", "*", {"kind": "cve_published", "cve": {
        "affects": {"language": "php", "version_lt": "8.1"}}}))
    dirty = factories["risk"].make(1, "risk_decision", "executor", {"kind": "payment"}, data_provenance=[{"service": "market-feed"}])
    assert not runtime.request_approval(dirty).approved
    assert runtime.decisions[-1]["evidence"]
    assert runtime.request_approval(clean).approved
    assert any(f.kind == "herding" for f in runtime.findings)


def test_production_client_whitelist_and_signed_heartbeat(setup):
    import json
    cfg, keys, _, _, factories = setup
    clock = factories["clock"].make(2, "tick", "*", {"tick": 2})
    cve = factories["secu"].make(2, "alert", "*", {"kind": "cve_published", "cve": {
        "affects": {"language": "php", "version_lt": "8.1"}}})
    operation = factories["risk"].make(2, "risk_decision", "executor", {},
        data_provenance=[{"service": "market-feed"}])
    events = [("bus", clock), ("bus", cve),
              ("vigie.approval_requests", {"request_id": "request1", "message": operation})]
    class Sub:
        def subscribe(self, *channels):
            self.channels = list(channels)
        def get_message(self, **kwargs):
            if self.channels:
                return {"type": "subscribe", "channel": self.channels.pop(), "data": 1}
            if events:
                channel, payload = events.pop(0)
                return {"type": "message", "channel": cfg["redis"]["prefix"] + channel, "data": json.dumps(payload)}
            return None
        def close(self):
            pass
    class Stop:
        def __init__(self): self.n = 0
        def is_set(self):
            self.n += 1
            return self.n > 4
    class Client:
        def __init__(self): self.published = []
        def pubsub(self, **kwargs): return Sub()
        def set(self, key, value, **kwargs):
            assert key in {cfg["redis"]["prefix"] + "agent:vigie:pid", cfg["redis"]["prefix"] + "vigie:ready"}
        def delete(self, key): assert key == cfg["redis"]["prefix"] + "vigie:ready"
        def publish(self, channel, data):
            assert channel in {cfg["redis"]["prefix"] + "bus", cfg["redis"]["prefix"] + "vigie.approval_responses"}
            self.published.append(json.loads(data))
        def __getattr__(self, name):
            raise AssertionError(f"Accès Redis hors liste blanche : {name}")
    client = Client()
    serve(cfg, client, stop=Stop(), meta=setup[2].meta)
    heartbeats = [m for m in client.published if m.get("type") == "heartbeat"]
    assert len(heartbeats) == 1 and keys.verify(heartbeats[0])
    responses = [m for m in client.published if m.get("request_id") == "request1"]
    assert len(responses) == 1 and responses[0]["approved"] is False
    assert cve["message_id"] in json.loads(responses[0]["reason"])["evidence"]


def test_compromise_announcement_is_optional(setup):
    import fakeredis
    from services.registry import build_cores
    cfg = copy.deepcopy(setup[0])
    cfg["chaos"]["announce_compromised"] = False
    seen = []
    cores = build_cores(cfg, fakeredis.FakeRedis(decode_responses=True), "", lambda sid: lambda k, p: seen.append(p))
    core = cores["core-ledger"]
    core.set_mode("compromised")
    core.set_mode("down")
    assert seen[0]["mode"] == "normal"
    assert seen[1]["previous"] == "normal" and seen[1]["mode"] == "down"


def test_evaluation_clean_ledger_and_determinism():
    from vigie.eval import evaluate
    clean = evaluate("clean", ticks=90)
    assert clean["avec_couche3"]["marking"]["false_positives"] == 0
    first = evaluate("ledger_legacy", ticks=70)
    second = evaluate("ledger_legacy", ticks=70)
    first.pop("duration_s")
    second.pop("duration_s")
    assert first == second
    assert first["avec_couche3"]["detections"]["core-ledger"]["kind"] == "ledger_mismatch"
    assert first["avec_couche3"]["cve_seen"] is False


def test_metrics_known_graph_and_mastery_clock(setup):
    root, _ = accept(setup, "trader-1", "order_proposal", {"notional_eur": 100}, services=["market-feed"])
    child, _ = accept(setup, "trader-2", "order_proposal", {"notional_eur": 300}, parents=[root["message_id"]])
    alert, _ = accept(setup, "secu", "alert", {"kind": "cve_published", "cve": {
        "affects": {"language": "php", "version_lt": "8.1"}}}, tick=10)
    detector = setup[3]
    metrics = detector.metrics()
    assert metrics["r0"] == 2
    assert metrics["impact_multiplier"] == 4
    assert metrics["impact_eur"] == 400
    assert metrics["mastery"]["market-feed"]["radius_tick"] == 10
    assert metrics["mastery"]["market-feed"]["cve_to_radius_ticks"] == 0
    assert metrics["mastery"]["market-feed"]["quarantine_tick"] is None
    op, _ = accept(setup, "risk", "risk_decision", parents=[child["message_id"]], tick=12)
    assert detector.quarantine(op)[0]
    assert detector.metrics()["mastery"]["market-feed"]["cve_to_quarantine_ticks"] == 2


def test_cve_cannot_be_forged_by_an_agent_or_boundary_version(setup):
    payload = {"kind": "cve_published", "cve": {"affects": {"language": "php", "version_lt": "8.1.0"}}}
    assert accept(setup, "trader-1", "alert", payload)[1] == []
    findings = accept(setup, "secu", "alert", payload)[1]
    assert "news-wire" not in [f.target for f in findings]


def test_late_parent_retroactively_propagates_and_replay_is_idempotent(setup):
    _, keys, state, detector, factories = setup
    root = factories["quant"].make(0, "price_update", "*", {}, data_provenance=[{"service": "market-feed"}])
    child, _ = accept(setup, "trader-1", "order_proposal", parents=[root["message_id"]])
    accept(setup, "secu", "alert", {"kind": "cve_published", "cve": {"affects": {"language": "php", "version_lt": "8.1"}}})
    assert not detector.marked(child)[0]
    assert state.observe(root, keys)
    detector.on_message(root)
    assert detector.marked(child)[0]
    assert not state.observe(root, keys)
    assert detector.on_message(root) == []


def test_shadow_payment_uses_signed_amount_and_ignores_truth_annotations(setup):
    _, keys, state, detector, factories = setup
    initial = detector.cash
    msg = factories["executor"].make(1, "execution", "*", {
        "status": "executed", "exec_id": "pay1", "kind": "payment", "amount_eur": 500,
        "compromised_sources": ["made-up-oracle"], "data_altered": True})
    assert state.observe(msg, keys)
    detector.on_message(msg)
    assert detector.cash == initial - 500
    assert "compromised_sources" not in state.messages[msg["message_id"]]["payload"]
    assert "data_altered" not in state.messages[msg["message_id"]]["payload"]


def test_launch_profile_is_optional_and_bus_is_wired(setup):
    from vigie.main import couche3_config
    from sim.launch import procfile
    cfg = setup[0]
    before = copy.deepcopy(cfg)
    active = couche3_config(cfg)
    assert cfg == before
    assert active["chaos"]["announce_compromised"] is False
    assert active["gate"]["impl"] == "bus"
    text = procfile(active, True, False)
    assert "-m vigie.main" in text
    assert "-m bus.vigie_stub" not in text
    assert "-m vigie.main" not in procfile(active, True, True)


def test_delivered_herding_is_enabled_with_common_findings(setup):
    from vigie.core import Finding
    from vigie.herding import Finding as HerdingFinding
    assert HerdingFinding is Finding
    cfg, keys, _, _, factories = setup
    runtime = Runtime(cfg, keys, meta=setup[2].meta)
    for sender in ["trader-1", "trader-2"]:
        order = factories[sender].make(0, "order_proposal", "compliance", {
            "asset": "ACME", "side": "sell", "notional_eur": 100})
        runtime.on_message(order)
        trace = factories[sender].make(0, "llm_trace", "*", {"response": {"justification": "risque de baisse"}})
        runtime.on_message(trace)
    out = runtime.on_message(factories["clock"].make(1, "tick", "*", {"tick": 1}))
    findings = [f for f in runtime.findings if f.kind in {"herding_index", "llm_similarity"}]
    assert {f.kind for f in findings} == {"herding_index", "llm_similarity"}
    assert all(type(f) is Finding and f.layer == 3 and f.evidence for f in findings)
    assert all(f.score == pytest.approx(1) for f in findings)
    assert sum(m["payload"].get("kind") == "layer3_finding" for m in out) >= 2
    # Ces mesures n'inventent aucune décision de couche 2 sur un ordre sain.
    safe = factories["risk"].make(1, "risk_decision", "executor", {"kind": "payment"})
    assert runtime.request_approval(safe).approved


def test_evaluation_has_five_cases_and_two_columns():
    from vigie.eval import evaluate, SCENARIOS
    assert SCENARIOS == ("php2shell_day", "ledger_legacy", "rumeur_mimetisme", "trader_corrompu_49m", "clean")
    with pytest.raises(ValueError, match="Scénario hors périmètre"):
        evaluate("dossier_piege")
    row = evaluate("clean", ticks=5)
    assert "sans_vigie" in row and "avec_couche3" in row
    assert "herding" in row["avec_couche3"]


def test_corrupted_trader_scenario_injects_three_signed_orders(setup):
    from sim.lockstep import Lockstep
    from vigie.eval import schedule_evaluation
    from sim.inject import load_scenario
    from pathlib import Path
    cfg = setup[0]
    ls = Lockstep(cfg, no_llm=True, no_vigie=True)
    messages = []
    ls.bus.tap(messages.append)
    scenario = load_scenario(Path("vigie/scenarios/trader_corrompu_49m.yaml"))
    try:
        ids = schedule_evaluation(ls, scenario)[1]
        ls.run(7)
        orders = [m for m in messages if m["message_id"] in ids]
        assert len(orders) == 3
        assert [m["tick"] for m in orders] == [3, 4, 5]
        assert all(m["payload"]["notional_eur"] == 4_900_000 for m in orders)
        assert all(m["from"] == "trader-3" and ls.keyring.verify(m) and m["depends_on"] for m in orders)
        prices = {m["message_id"]: m for m in messages if m["type"] == "price_update"}
        assert all(m["depends_on"][0] in prices for m in orders)
    finally:
        ls.close()
