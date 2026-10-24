"""Étape 6 : API (/state, /graph, /llm_trace, /stream, /inject, /record), replay et rapport d'incident."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.sources import LiveSource, ReplaySource
from journal.report import build_report
from journal.writer import read_journal, verify_journal
from sim.inject import LockstepTarget, load_scenario, schedule_scenario
from sim.lockstep import Lockstep

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"


@pytest.fixture
def played(cfg, tmp_path):
    """Une partie php2shell_day jouée en lockstep et enregistrée."""
    run = tmp_path / "run"
    ls = Lockstep(cfg, run_dir=run)
    ls.run(schedule_scenario(ls, load_scenario(SCENARIOS / "php2shell_day.yaml")))
    yield ls, run
    ls.close()


@pytest.fixture
def api(played):
    ls, _ = played
    return TestClient(create_app({"main": LiveSource(ls.cfg, ls.store)}, {"main": LockstepTarget(ls)})), ls


def test_state_contract(api):
    client, _ = api
    state = client.get("/state").json()
    for key in ("tick", "prices", "positions_eur", "pnl", "var_99_1d_eur", "exposure", "liquidation_cost_eur", "limits",
                "losses", "contaminated_agents", "trapped_operations", "services", "agents", "light", "risk_view_vs_truth"):
        assert key in state, key
    assert set(state["limits"]["bank"]) >= {"exposure_pct", "var_pct", "daily_loss_pct"}
    assert state["losses"]["losses_by_service_eur"]["payment-gateway"] > 0
    assert state["services"]["market-feed"]["mode"] == "compromised"
    assert {"quant", "risk"} <= {c["agent"] for c in state["contaminated_agents"]}


def test_graph_shows_hidden_dependency(api):
    client, _ = api
    graph = client.get("/graph").json()
    services = {n["id"]: n for n in graph["nodes"] if n["kind"] == "service"}
    assert services["core-ledger"]["php_version"] == "5.6" and services["core-ledger"]["patchable"] is False
    assert services["market-feed"]["cve"][0]["status"] == "vulnerable_non_patchable"
    data = [e for e in graph["edges"] if e["kind"] == "data" and not e["transitive"]]
    hidden = {(e["source"], e["target"]) for e in data if e["hidden"]}
    assert hidden == {("trader-1", "payment-gateway"), ("trader-2", "payment-gateway"), ("trader-3", "payment-gateway")}
    declared = {(e["source"], e["target"]) for e in data if e["declared"]}
    assert ("trader-1", "news-wire") in declared and ("quant", "market-feed") in declared
    assert all(e["observed"] for e in data if e["hidden"])
    transitive = {(e["source"], e["target"]) for e in graph["edges"] if e.get("transitive")}
    assert ("trader-1", "market-feed") in transitive                         # via le Quant


def test_llm_trace_endpoint(api):
    client, _ = api
    traces = client.get("/llm_trace", params={"limit": 1000}).json()
    assert traces and {"agent", "model", "temperature", "prompt_hash", "prompt_excerpt", "response", "declared_confidence",
                       "input_provenance", "latency_ms", "tokens"} <= set(traces[0])
    only = client.get("/llm_trace", params={"agent": "compliance"}).json()
    assert only and all(t["agent"] == "compliance" for t in only)


def test_inject_endpoint(api):
    client, ls = api
    assert client.post("/inject", json={"command": "restore market-feed"}).json()["ok"]
    assert ls.cores["market-feed"].mode() == "normal"
    assert client.post("/inject", json={"command": "set herding 0.6"}).status_code == 200
    assert ls.params.get("herding") == 0.6
    assert client.post("/inject", json={"command": "pirater market-feed"}).status_code == 400
    assert client.get("/state", params={"world": "B"}).status_code == 404


def test_record_and_recordings(api, tmp_path, monkeypatch):
    import api.app as app_module

    monkeypatch.setattr(app_module, "RECORDINGS", tmp_path / "recordings")
    client, ls = api
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    resp = client.post("/record", json={"name": "demo 1"}).json()
    assert resp["ok"] and (tmp_path / "recordings" / "demo1" / "journal.jsonl").exists()
    assert client.get("/recordings").json() == ["demo1"]


def test_sim_layout_saved_and_served(tmp_path, monkeypatch):
    import api.app as app_module

    monkeypatch.setattr(app_module, "SIM_LAYOUT", tmp_path / "sim-layout.json")
    client = TestClient(create_app({}))
    assert client.get("/dashboard/sim-layout.json").json() == {}
    assert client.post("/dashboard/sim-layout", json={"quant": [1.5, -2]}).json()["ok"]
    assert client.get("/dashboard/sim-layout.json").json() == {"quant": [1.5, -2.0]}
    for bad in ({"quant": [1]}, {"quant": [1, 99]}, {"../x": [1, 2]}):
        assert client.post("/dashboard/sim-layout", json=bad).status_code == 400


def test_stream_websocket_replays_messages(played):
    _, run = played
    source = ReplaySource(run, tick_seconds=0.001, speed=1.0)
    client = TestClient(create_app({"main": source}))
    with client.websocket_connect("/stream?types=execution") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "execution"


def test_replay_serves_recorded_states(played):
    ls, run = played
    source = ReplaySource(run, tick_seconds=1000.0)
    client = TestClient(create_app({"main": source}))
    first = client.get("/state").json()
    assert first["tick"] == 0 and first["replay"] is True
    source.started -= 1000.0 * 10_000
    last = client.get("/state").json()
    live_final = json.loads(ls.store.get("state"))
    assert {**last, "replay": None} == {**live_final, "replay": None}
    assert client.post("/inject", json={"command": "compromise market-feed"}).status_code == 409


def test_replay_is_deterministic(cfg, tmp_path):
    """Même graine, même scénario : journal et états identiques à l'octet près."""
    finals = []
    for i in range(2):
        run = tmp_path / f"run{i}"
        ls = Lockstep(cfg, run_dir=run)
        ls.run(schedule_scenario(ls, load_scenario(SCENARIOS / "php2shell_day.yaml")))
        ls.close()
        entries = list(read_journal(run / "journal.jsonl"))
        finals.append((entries[-1]["hash"], len(entries), (run / "states.jsonl").read_text()))
    assert finals[0] == finals[1]
    assert verify_journal(tmp_path / "run0" / "journal.jsonl")[0]


def test_other_seed_differs(cfg, tmp_path):
    hashes = []
    for seed in (1, 2):
        cfg["seed"] = seed
        ls = Lockstep(cfg, run_dir=tmp_path / f"s{seed}")
        ls.run(60)
        ls.close()
        hashes.append(list(read_journal(tmp_path / f"s{seed}" / "journal.jsonl"))[-1]["hash"])
    assert hashes[0] != hashes[1]


def test_incident_report(played):
    _, run = played
    report = build_report(run, 0, 180)
    for section in ("## 1. Résumé", "## 2. Chronologie", "## 3. Services TIC tiers concernés", "## 4. Agents impliqués",
                    "## 5. Décisions humaines", "## 6. Pertes attribuées", "## 7. Causes"):
        assert section in report
    assert "Incident majeur" in report and "chaîne intacte" in report
    assert "| core-ledger | php | 5.6 | non | critique |" in report                # registre depuis /meta
    assert "trader-1 → payment-gateway" in report                           # dépendance cachée
    assert "IBAN substitué" in report and "PHP2Shell" in report
    assert "Lecture des dépêches" in report                                 # justification
    window = build_report(run, 0, 20)
    assert "Incident non majeur" in window and "mode **compromised**" not in window


def test_report_lists_human_decisions(cfg, tmp_path):
    run = tmp_path / "run"
    ls = Lockstep(cfg, run_dir=run)
    scenario = load_scenario(SCENARIOS / "vigie_debranchee.yaml")
    scenario["steps"].append({"at": 50, "do": "human resume"})
    ls.run(schedule_scenario(ls, scenario))
    ls.close()
    report = build_report(run, 0, 120)
    assert "t50 (human) : resume" in report
    assert "vigie_heartbeat_lost" not in report or "plus de heartbeat Vigie" in report


def test_report_cli(played, capsys):
    from journal.report import main

    _, run = played
    main(["incident", "30", "120", "--run", str(run)])
    assert "Rapport d'incident" in capsys.readouterr().out
