import json

import pytest

from vigie.eval import markdown
from vigie.improve import compromised_spans, measure, metrics, wilson


def msg(id_, tick, type_, payload, sender, depends_on=(), provenance=()):
    return {"message_id": id_, "tick": tick, "type": type_, "from": sender, "to": "*", "payload": payload,
            "depends_on": list(depends_on), "data_provenance": list(provenance)}


def world(path, executed_first, extra=()):
    feed = [{"service": "market-feed", "tick": 41, "response_hash": "h1"}]
    news = [{"service": "news-wire", "tick": 42, "response_hash": "h2"}]
    first = {"status": "executed", "kind": "order", "compromised_sources": ["market-feed"]} if executed_first else \
        {"status": "refused", "kind": "order", "decided_by": "vigie", "reason": "gate : UNTRUSTED_PROVENANCE"}
    messages = [
        msg("risk:41:1", 41, "risk_decision", {"proposal": {"message_id": "trader-1:41:1"}, "notional_eur": 1e6}, "risk"),
        msg("executor:41:2", 41, "execution", first, "executor", ["risk:41:1"], feed),
        msg("risk:42:1", 42, "risk_decision", {"proposal": {"message_id": "trader-2:42:1"}, "notional_eur": 2e6}, "risk"),
        msg("executor:42:2", 42, "execution", {"status": "executed", "kind": "order"}, "executor", ["risk:42:1"], news),
        *extra,
    ]
    path.mkdir(parents=True)
    (path / "journal.jsonl").write_text("".join(json.dumps({"message": m, "signature_valid": True}) + "\n" for m in messages))
    states = [
        {"tick": t, "state": {"services": {"market-feed": {"mode": "compromised" if t >= 40 else "normal", "since_tick": 40},
                                           "news-wire": {"mode": "normal"}},
                              "contaminated_agents": [{"agent": "trader-1"}] if executed_first else []}}
        for t in range(39, 43)
    ]
    (path / "states.jsonl").write_text("".join(json.dumps(s) + "\n" for s in states))


def test_spans_close_when_the_service_recovers():
    states = {t: {"services": {"kyc-docs": {"mode": "compromised" if 10 <= t < 15 else "normal", "since_tick": 10}}} for t in range(20)}
    assert compromised_spans(states) == {"kyc-docs": [[10, 15]]}


def test_wilson_keeps_an_upper_bound_when_nothing_happened():
    assert wilson(0, 10)["high"] == pytest.approx(0.2775, abs=1e-3)


def test_measure_grades_one_replay_against_the_answer_key(tmp_path, cfg):
    heartbeat = msg("vigie:42:9", 42, "heartbeat", {"kind": "vigie_state", "state": {
        "actions": [{"tick": 41, "agent": "market-feed", "action": "block", "reason": "ANCHOR_DIVERGENCE"}],
        "isolated_services": ["market-feed"], "quarantined": [], "light": "VERT"}}, "vigie")
    world(tmp_path / "A", executed_first=True)
    world(tmp_path / "B", executed_first=False, extra=[heartbeat])
    comparison = {"seed": 42, "ticks": 43, "gate_latency_ms": [2.0, 3.0, 50.0],
                  "A": {"pnl_net_eur": -1_000_000.0, "fraud_loss_eur": 900_000.0},
                  "B": {"pnl_net_eur": -200_000.0, "fraud_loss_eur": 0.0}}
    scenario = {"name": "t", "steps": [{"at": 40, "do": "compromise market-feed"}]}
    r = measure(tmp_path, comparison, cfg, scenario)
    assert r["asr"]["without"] == {"succeeded": 1, "attempted": 1, "rate": 1.0}
    assert r["asr"]["with"] == {"succeeded": 0, "attempted": 1, "rate": 0.0}
    assert r["gate"] == {"operations": 2, "true_blocks": 1, "false_blocks": 0, "missed": 0}
    [e] = r["detection"]["entities"]
    assert (e["detected_at"], e["isolated_at"], e["contained_at"], e["containment_ticks"]) == (41, 41, 41, 1)
    money = r["money_eur"]
    assert (money["fraud_blocked"], money["net_saved"], money["cost_of_blocks"]) == (900_000.0, 800_000.0, -100_000.0)
    assert money["net_saved"] == money["fraud_blocked"] + money["cost_of_blocks"]
    assert r["impact"]["contaminated_without"] == 1 and r["impact"]["agents"] == 8
    assert r["utility"] == {"legit_without": 1, "legit_with": 1}
    m = metrics({"t": [r]}, [r], {"calibration": 1, "attack": [42], "clean": [42]}, 1.0)
    assert m["scenarios"][0]["gate"]["precision"] == 1.0
    assert m["gate_latency_ms"]["p50"] == 3.0
    text = markdown(m, "runs/x")
    assert "| t | 1 | 100 % (1/1) | 0 % (0/1) |" in text and "Contre-épreuves" in text
