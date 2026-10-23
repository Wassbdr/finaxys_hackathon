import pytest
from bus.message import MessageFactory
from vigie.policy import Policy
from vigie.tests.test_core import chain


def test_half_life_and_hysteresis():
    p = Policy()
    p.scores["trader-1"] = 80
    p.levels["trader-1"] = "ORANGE"
    p.advance(30)
    assert p.scores["trader-1"] == pytest.approx(40)
    p.advance(90)
    assert p.scores["trader-1"] == pytest.approx(10)
    assert p.levels["trader-1"] == "ORANGE"
    p.advance(91)
    assert p.levels["trader-1"] == "VERT"


def test_two_independent_families_not_repeated_one_detector():
    p = Policy()
    for tick in (0, 31, 62):
        p.record("trader-1", "D3A", 3, tick, 25)
    assert not p.two_witnesses("trader-1", 62)
    p.record("trader-1", "D4B", 4, 62, 20)
    assert p.two_witnesses("trader-1", 62)
    assert not p.two_witnesses("trader-1", 94)


def test_alerts_grouped_by_causal_source_and_never_punish_victims():
    p = Policy()
    for tick in range(20):
        for agent in ("quant", "trader-1", "risk"):
            p.record(
                agent,
                "TAINT",
                4,
                tick,
                100,
                [str(tick)],
                source="news-wire",
                victim=True,
            )
    assert p.status()["counters"] == {
        "raw_alerts": 60,
        "incidents": 1,
        "notifications": 1,
    }
    assert not any(p.scores.values())
    assert not any(p.two_witnesses(a, 20) for a in ("quant", "trader-1", "risk"))


def test_cold_start_cannot_quarantine_from_behavior(setup):
    c, k, _ = setup
    c.suspicion["trader-1"] = 70
    c.policy.witnesses["trader-1"] = {3: 10, 4: 10}
    c.tick = 10
    c.summary("trader-1", "slow", "BEHAVIOR_CUMULATIVE", 3)
    assert not c.quarantined
    c.tick = 60
    c.policy.witnesses["trader-1"] = {3: 60, 4: 60}
    c.summary("trader-1", "block", "UNRELIABLE_NEWS", 4)
    assert c.quarantined == {"trader-1"}
    c.advance(600)
    assert "trader-1" in c.quarantined
    c.observe(
        MessageFactory("human", k).make(
            600, "human_decision", "*", {"action": "unblock", "target": "trader-1"}
        )
    )
    assert not c.quarantined


def test_contaminated_oversized_decision_cannot_sanction_its_victim(setup):
    c, k, s = setup
    s["services"]["market-feed"]["mode"] = "compromised"
    msg = chain(c, k, amount=6000000)
    c.isolate_service("market-feed", "UNTRUSTED_PROVENANCE", [msg["message_id"]])
    assert not c.approve(msg, s).approved
    assert c.suspicion["trader-1"] == 0 and not c.quarantined


def test_policy_checkpoint_preserves_counters_and_independent_witnesses():
    import json

    p = Policy()
    p.advance(10)
    p.record("trader-1", "D3A", 3, 10, 25)
    p.record("trader-1", "D4A", 4, 10, 30)
    saved = json.loads(json.dumps(p.checkpoint()))
    new = Policy()
    new.restore(saved)
    assert new.status() == p.status()
    assert new.two_witnesses("trader-1", 10)
    new.advance(40)
    assert new.scores["trader-1"] == pytest.approx(27.5)


def test_bad_signature_notifies_once_without_revoking_impersonated_risk(setup):
    c, k, _ = setup
    factory = MessageFactory("risk", k)
    for _ in range(2):
        msg = factory.make(10, "heartbeat", "*", {})
        msg["signature"] = "inconnue"
        c.observe(msg)
    assert not c.quarantined and c.suspicion["risk"] == 0
    assert c.policy.raw == 2 and c.policy.sequence == 1 and c.policy.notifications == 1
    messages = [m for m in c.drain() if m["to"] == "human"]
    assert (
        len(messages) == 1 and messages[0]["payload"]["attribution_verified"] is False
    )


def test_lingering_cusum_keeps_tainted_evidence_instead_of_punishing_later(setup):
    c, k, s = setup
    c.profiles = {
        "trader-1": {
            "clean": True,
            "reference_ticks": 300,
            "robust": {
                "count": {"effective_median": 0.0, "mad": 0.0, "floor": 0.5, "h": 6.0}
            },
        }
    }
    m = chain(c, k, tick=1)
    source = m["payload"]["proposal"]["message_id"]
    c.isolate_service("market-feed", "UNTRUSTED_PROVENANCE", [source])
    c.detector.scores["trader-1"] = {"count": 10.0}
    c.detector.evidence = {"trader-1": {source}}
    s["tick"] = 20
    c.observe(MessageFactory("clock", k).make(20, "tick", "*", {"tick": 20}), s)
    assert c.suspicion["trader-1"] == 0 and not c.quarantined
