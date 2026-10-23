import copy
import pytest
from bus.message import MessageFactory


def chain(
    c,
    keys,
    tick=10,
    amount=100000,
    agent="trader-1",
    justification="Rééquilibrage prudent",
    provenance=None,
):
    c.observe(MessageFactory("clock", keys).make(tick, "tick", "*", {"tick": tick}))
    market = [
        {
            "service": "market-feed",
            "version": c.cfg["services"]["market-feed"]["version"],
            "response_hash": f"clean-{tick}",
            "tick": tick,
        }
    ]
    quant = MessageFactory("quant", keys).make(
        tick,
        "price_update",
        "*",
        {"assets": {"BOREAL": {"price": 100, "confidence": 1.0}}},
        data_provenance=market,
    )
    c.observe(quant)
    proposal = MessageFactory(agent, keys).make(
        tick,
        "order_proposal",
        "compliance",
        {
            "proposal_id": f"p-{tick}",
            "trader": agent,
            "asset": "BOREAL",
            "side": "buy",
            "notional_eur": amount,
            "price_seen": 100,
            "quant_confidence": 1.0,
            "justification": justification,
        },
        depends_on=[quant["message_id"]],
        data_provenance=market + (provenance or []),
    )
    # Une fabrique par étape évite tout accès au code des agents surveillés.
    comp = MessageFactory("compliance", keys).make(
        tick,
        "compliance_decision",
        "risk",
        {
            "approved": True,
            "kind": "order",
            "proposal": {
                k: proposal[k] for k in ("message_id", "from", "type", "payload")
            },
        },
        depends_on=[proposal["message_id"]],
        data_provenance=provenance,
    )
    risk = MessageFactory("risk", keys).make(
        tick,
        "risk_decision",
        "executor",
        {
            "approved": True,
            "kind": "order",
            "notional_eur": amount,
            "requested_eur": amount,
            "proposal": comp["payload"]["proposal"],
            "risk_view": {"var_eur": 1},
        },
        depends_on=[comp["message_id"], proposal["message_id"]],
        data_provenance=provenance,
    )
    for msg in (proposal, comp, risk):
        c.observe(msg)
    return risk


def test_valid_portique_and_summary_to_human(setup):
    c, k, s = setup
    m = chain(c, k)
    assert c.approve(m, s).approved
    assert c.policy.layers["trader-1"]["2"] == "VERT"
    assert not any(m["to"] == "human" and m["type"] == "alert" for m in c.drain())


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("signature", "INVALID_SIGNATURE"),
        ("configuration", "CONFIG_CHANGED"),
        ("tool", "TOOL_DENIED"),
        ("sender", "UNKNOWN_AGENT"),
    ],
)
def test_identity_configuration_and_tools(setup, fault, reason):
    c, k, s = setup
    m = chain(c, k)
    if fault == "signature":
        m["signature"] = "0" * 64
    elif fault == "sender":
        m["from"] = "intrus"
    else:
        m["payload"][
            "configuration_fingerprint" if fault == "configuration" else "tool"
        ] = "invalide"
        k.sign(m)
    assert c.approve(m, s).reason == reason


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"cash_eur": 39000000}, "LIQUIDITY_LIMIT"),
        ({"pnl": {"day_eur": -2000001, "payment_fraud_eur": 0}}, "LOSS_LIMIT"),
        ({"var_99_1d_eur": 3000001}, "VAR_LIMIT"),
        (
            {"positions_eur": {"ACME": 30000000, "BOREAL": 30000000, "CITRA": 0}},
            "EXPOSURE_LIMIT",
        ),
        (
            {"books": {"trader-1": {"positions_eur": {"BOREAL": 20000000}}}},
            "POSITION_LIMIT",
        ),
        ({"params": {"restricted_list": ["BOREAL"]}}, "RESTRICTED_ASSET"),
    ],
)
def test_layer_two_recomputes_on_truth_not_risk_view(setup, change, reason):
    c, k, s = setup
    m = chain(c, k)
    s.update(change)
    assert c.approve(m, s).reason == reason


@pytest.mark.parametrize("mode", ["stale", "down"])
def test_layer_four_blocks_transitive_provenance(setup, mode):
    c, k, s = setup
    prov = [
        {
            "service": "market-feed",
            "version": "3.2.0",
            "response_hash": "abc",
            "tick": 10,
        }
    ]
    m = chain(c, k, provenance=prov)
    s["services"]["market-feed"]["mode"] = mode
    d = c.approve(m, s)
    assert not d.approved and d.reason == "UNTRUSTED_PROVENANCE"
    assert c.contagion_blocked == 1


def test_simulator_compromise_flag_is_not_read_but_a_cve_is(setup):
    c, k, s = setup
    prov = [
        {"service": "news-wire", "version": "1.8.4", "response_hash": "x", "tick": 10}
    ]
    m = chain(c, k, provenance=prov)
    s["services"]["news-wire"]["mode"] = "compromised"
    s["tainted_hashes"] = ["x"]
    assert c.approve(m, s).approved
    c.observe(
        MessageFactory("secu", k).make(
            10, "alert", "*", {"kind": "cve_published", "unpatchable": ["news-wire"]}
        )
    )
    m = chain(c, k, tick=11, provenance=[{**prov[0], "tick": 11}])
    assert c.approve(m, s).reason == "UNTRUSTED_PROVENANCE"


def test_suspect_service_stays_refused_until_a_human_clears_it(setup):
    c, k, s = setup
    c.observe(
        MessageFactory("secu", k).make(
            9, "alert", "*", {"kind": "cve_published", "unpatchable": ["news-wire"]}
        )
    )
    prov = [
        {"service": "news-wire", "version": "1.8.4", "response_hash": "x", "tick": 10}
    ]
    assert c.approve(chain(c, k, provenance=prov), s).reason == "UNTRUSTED_PROVENANCE"
    c.observe(
        MessageFactory("human", k).make(
            10, "human_decision", "*", {"action": "unblock", "target": "news-wire"}
        )
    )
    m = chain(c, k, tick=11, provenance=[{**prov[0], "tick": 11}])
    assert c.approve(m, s).approved


def test_missing_or_forged_ancestry_and_replay_are_rejected(setup):
    c, k, s = setup
    m = chain(c, k)
    forged = copy.deepcopy(m)
    forged["payload"]["proposal"]["payload"]["notional_eur"] = 1
    k.sign(forged)
    assert c.approve(forged, s).reason == "INVALID_ANCESTRY"
    assert c.approve(m, s).approved
    assert c.approve(m, s).reason == "REPLAY_ACTION"


def test_cumulative_orange_then_only_trader_quarantined(setup):
    c, k, s = setup
    c.profiles = {
        "trader-1": {
            "mean_order_eur": 100000,
            "frequency": 0.1,
            "assets": ["BOREAL"],
            "clean": True,
            "reference_ticks": 300,
        }
    }
    for tick in (10, 11):
        s["tick"] = tick
        m = chain(c, k, tick, 4900000)
        assert c.approve(m, s).approved
    s["tick"] = 12
    m = chain(
        c, k, 12, 4900000, justification="Vente motivée par la faillite de BOREAL"
    )
    assert not c.approve(m, s).approved
    assert not c.quarantined
    c.summary("trader-1", "slow", "BEHAVIOR_CUSUM", 3, [m["message_id"]])
    assert c.quarantined == {"trader-1"}
    assert not c.registry["trader-1"]["tools"]
    s["tick"] = 13
    m = chain(c, k, 13, 10000, agent="trader-2")
    assert c.approve(m, s).approved
    c.drain()
    before = c.suspicion["trader-1"]
    c.advance(20)
    assert c.suspicion["trader-1"] < before
    assert "trader-1" in c.quarantined


def test_clock_heartbeat_signed_and_human_resume_required(setup):
    c, k, s = setup
    tick = MessageFactory("clock", k).make(10, "tick", "*", {"tick": 10})
    c.observe(tick)
    out = c.drain()
    assert any(m["type"] == "heartbeat" and k.verify(m) for m in out)
    c.light = "ROUGE"
    c.observe(
        MessageFactory("trader-1", k).make(
            11, "human_decision", "*", {"action": "resume"}
        )
    )
    assert c.light == "ROUGE"
    c.observe(
        MessageFactory("human", k).make(11, "human_decision", "*", {"action": "resume"})
    )
    assert c.light == "VERT"


def test_auditor_disabled_and_cannot_lower_suspicion(setup):
    c, _, _ = setup
    assert not c.auditor_advice("trader-1", 10, "other-model")
    c.auditor_enabled = True
    assert not c.auditor_advice("trader-1", -5, "other-model")
    assert not c.auditor_advice("trader-1", 5, c.cfg["llm"]["model"])
    assert c.auditor_advice("trader-1", 5, "other-model")


def test_every_light_including_human_resume_has_signed_expiry(setup):
    c, k, _ = setup
    c.observe(MessageFactory("clock", k).make(10, "tick", "*", {"tick": 10}))
    c.quarantine("trader-1", "anomalie confirmée")
    c.light = "ROUGE"
    c.observe(
        MessageFactory("human", k).make(10, "human_decision", "*", {"action": "resume"})
    )
    lights = [m for m in c.drain() if m["type"] == "light_change"]
    assert len(lights) >= 3
    assert all(k.verify(m) and m["payload"]["expires_at_tick"] == 15 for m in lights)
