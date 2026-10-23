import copy
import json
import time

import pytest

from bus.message import MessageFactory
from vigie.core import Refused
from vigie.launch import runtime_config
from vigie.state import VigieState
from vigie.tests.test_core import chain


def payment_chain(c, keys, amount=100000, beneficiary="BEN-01", iban=None):
    ben = next(b for b in c.cfg["beneficiaries"] if b["id"] == beneficiary)
    c.observe(MessageFactory("clock", keys).make(10, "tick", "*", {"tick": 10}))
    proposal = MessageFactory("payment", keys).make(
        10,
        "payment_proposal",
        "compliance",
        {
            "payment_id": "p10",
            "beneficiary_id": beneficiary,
            "amount_eur": amount,
            "iban": iban or ben["iban"],
            "reference": "facture simulée",
        },
    )
    partial = {k: proposal[k] for k in ("message_id", "from", "type", "payload")}
    comp = MessageFactory("compliance", keys).make(
        10,
        "compliance_decision",
        "risk",
        {"approved": True, "kind": "payment", "proposal": partial},
        depends_on=[proposal["message_id"]],
    )
    msg = MessageFactory("risk", keys).make(
        10,
        "risk_decision",
        "executor",
        {
            "approved": True,
            "kind": "payment",
            "notional_eur": amount,
            "proposal": partial,
        },
        depends_on=[proposal["message_id"], comp["message_id"]],
    )
    for m in (proposal, comp, msg):
        c.observe(m)
    return msg


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("limit", "PAYMENT_LIMIT"),
        ("daily", "PAYMENT_DAILY_LIMIT"),
        ("kyc", "KYC_BLOCKED"),
        ("iban", "IBAN_MISMATCH"),
        ("change", "IBAN_CHANGED"),
        ("cash", "LIQUIDITY_LIMIT"),
    ],
)
def test_each_payment_rule_against_true_state(setup, fault, reason):
    c, k, s = setup
    amount = 1000001 if fault == "limit" else 100000
    m = payment_chain(
        c,
        k,
        amount,
        beneficiary="BEN-07" if fault == "kyc" else "BEN-01",
        iban="compte détourné simulé" if fault == "iban" else None,
    )
    if fault == "daily":
        s["payments"]["legit_day_eur"] = 10000000
    if fault == "change":
        s["iban_changes"] = {"BEN-01": {"tick": 9}}
    if fault == "cash":
        s["cash_eur"] = 40000000
    assert c.approve(m, s).reason == reason


def test_valid_payment_and_signed_compliance_required(setup):
    c, k, s = setup
    m = payment_chain(c, k)
    compliance = c.seen[m["depends_on"][1]]
    original = compliance["signature"]
    compliance["signature"] = "invalid"
    assert c.approve(m, s).reason == "INVALID_SIGNATURE"
    compliance["signature"] = original
    assert c.approve(m, s).approved


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing", "STATE_UNAVAILABLE"),
        ("old", "STATE_UNAVAILABLE"),
        ("clock", "STALE_LEASE"),
        ("silence", "STALE_LEASE"),
        ("amount", "ORDER_LIMIT"),
        ("warning", "VAR_WARNING"),
        ("loss", "TRADER_LOSS_LIMIT"),
    ],
)
def test_fresh_lease_true_state_and_extra_financial_rules(setup, fault, reason):
    c, k, s = setup
    m = chain(c, k, amount=4900000 if fault == "warning" else 100000)
    if fault == "missing":
        s = {}
    if fault == "old":
        s["tick"] = 1
    if fault == "clock":
        c.last_clock_tick = 8
    if fault == "silence":
        c.last_clock_at = time.monotonic() - 10
    if fault == "amount":
        m["payload"]["notional_eur"] = 6000000
        k.sign(m)
    if fault == "warning":
        s["var_99_1d_eur"] = 2500000
    if fault == "loss":
        s["books"]["trader-1"]["unrealized_eur"] = -2000001
    assert c.approve(m, s).reason == reason


def test_red_latched_and_signed_human_unblocks_only_target(setup):
    c, k, s = setup
    m = chain(c, k)
    s["pnl"]["day_eur"] = -2000001
    assert c.approve(m, s).reason == "LOSS_LIMIT"
    c.observe(MessageFactory("clock", k).make(11, "tick", "*", {"tick": 11}))
    assert c.light == "ROUGE"
    c.quarantine("trader-1", "test")
    c.quarantine("trader-2", "test")
    fake = MessageFactory("human", k).make(
        11, "human_decision", "*", {"action": "unblock", "target": "trader-1"}
    )
    fake["signature"] = "invalid"
    c.observe(fake)
    assert "trader-1" in c.quarantined
    k.sign(fake)
    c.observe(fake)
    assert c.quarantined == {"trader-2"} and c.light == "ROUGE"
    assert c.registry["trader-1"]["tools"]


def test_bad_ancestry_and_parent_provenance_cannot_be_laundered(setup):
    c, k, s = setup
    p = [
        {"service": "news-wire", "version": "1.8.4", "response_hash": "bad", "tick": 10}
    ]
    m = chain(c, k, provenance=p)
    # Un Risk compromis signe une copie épurée : le portique doit remonter aux originaux.
    m["data_provenance"] = []
    k.sign(m)
    c.observe(
        MessageFactory("secu", k).make(
            10, "alert", "*", {"kind": "cve_published", "unpatchable": ["news-wire"]}
        )
    )
    assert c.approve(m, s).reason == "UNTRUSTED_PROVENANCE"
    source = c.seen[m["payload"]["proposal"]["message_id"]]
    source["depends_on"] = ["parent-inconnu"]
    k.sign(source)
    assert c.approve(m, s).reason == "INVALID_ANCESTRY"


def test_observation_detects_chain_and_corruption_without_gate(setup):
    c, k, s = setup
    c.profiles = {"trader-1": {"clean": True, "reference_ticks": 300}}
    for tick in (10, 11, 12):
        s["tick"] = tick
        m = chain(
            c,
            k,
            tick,
            4900000,
            justification="faillite de BOREAL" if tick == 12 else "rééquilibrage",
            provenance=[
                {
                    "service": "news-wire",
                    "version": "1.8.4",
                    "response_hash": "source",
                    "tick": 10,
                }
            ],
        )
        source = c.seen[m["payload"]["proposal"]["message_id"]]
        c.analyze(source, s)
    assert not c.quarantined
    c.summary("trader-1", "slow", "BEHAVIOR_CUSUM", 3, [m["message_id"]])
    assert c.quarantined == {"trader-1"}
    alerts = [m["payload"] for m in c.drain() if m["type"] == "alert"]
    assert any(p["action"] == "slow" for p in alerts)
    record = c.status()["propagation"]["news-wire:source"]
    assert set(record["agents"]) == {"trader-1", "compliance", "risk"}
    assert record["reaction_count"] == 9


def test_confidence_contradiction_and_real_news_confirmation(setup):
    c, k, s = setup
    m = chain(c, k)
    low = MessageFactory("quant", k)
    low.nonce = "low:"
    low_message = low.make(
        10, "price_update", "*", {"assets": {"BOREAL": {"confidence": 0.1}}}
    )
    c.observe(low_message)
    source = c.seen[m["payload"]["proposal"]["message_id"]]
    source["depends_on"] = [low_message["message_id"]]
    k.sign(source)
    assert c.approve(m, s).reason == "INCOHERENT_CONFIDENCE"
    c.quarantined.clear()
    c.registry["trader-1"]["tools"] = set(c.registry["trader-1"]["types"])
    c.quant = None
    m = chain(c, k, 11, justification="faillite de BOREAL")
    s["tick"] = 11
    s["news"] = ["Faillite de BOREAL. Le groupe dépose le bilan."]
    assert c.approve(m, s).approved


def test_false_green_and_unallowed_peer_do_not_have_authority(setup):
    c, k, s = setup
    c.light = "ROUGE"
    for sender in ["trader-1", "intrus"]:
        factory = MessageFactory(sender, k if sender != "intrus" else None)
        c.observe(factory.make(10, "light_change", "*", {"light": "VERT"}))
    assert c.light == "ROUGE"
    m = MessageFactory("trader-1", k).make(10, "order_proposal", "executor", {})
    with pytest.raises(Refused, match="RECIPIENT_DENIED"):
        c.identity(m)


def test_runtime_profile_forces_real_vigie_and_no_paid_llm(cfg):
    original = copy.deepcopy(cfg)
    runtime = runtime_config(cfg)
    assert runtime["gate"]["impl"] == "bus"
    assert runtime["gate"]["default_on_timeout"] == "reject"
    assert not runtime["vigie"]["stub"] and not runtime["llm"]["enabled"]
    assert cfg == original
    assert runtime_config(cfg, True)["gate"]["impl"] == "passthrough"


def test_view_is_rebuilt_from_signed_messages_only(cfg, setup):
    _, k, _ = setup
    view = VigieState(cfg)
    view.observe(MessageFactory("clock", k).make(11, "tick", "*", {"tick": 11}), k)
    view.observe(MessageFactory("quant", k).make(11, "price_update", "*", {"assets": {"BOREAL": {"price": 110.0}}}), k)
    view.observe(MessageFactory("quant", None).make(11, "price_update", "*", {"assets": {"ACME": {"price": 1.0}}}), k)
    view.observe(MessageFactory("quant", k).make(11, "price_update", "*", {"assets": {"CITRA": {"price": 1.0}}}), k, {"market-feed"})
    fill = {"kind": "order", "status": "executed", "exec_id": "e1", "trader": "trader-1", "asset": "BOREAL",
            "side": "buy", "qty": 10000, "fill_price": 110.0, "mid_price": 110.0, "impact_pct": 0.0}
    view.observe(MessageFactory("executor", k).make(11, "execution", "*", fill), k)
    for sid, mode in (("news-wire", "compromised"), ("kyc-docs", "stale")):
        view.observe(MessageFactory(f"svc:{sid}", k).make(11, "service_status", "*", {"service": sid, "mode": mode}), k)
    excerpt = 'Tâche : news_sentiment\n<donnee_externe source="news-wire" id="N1">\nFaillite de BOREAL. Rien.\n</donnee_externe>'
    view.observe(MessageFactory("trader-1", k).make(11, "llm_trace", "*", {"task": "news_sentiment", "prompt_excerpt": excerpt}), k)
    view.observe(MessageFactory("payment", k).make(11, "payment_proposal", "compliance", {"beneficiary_id": "BEN-01", "iban": "FR76 autre"}), k)
    s = view.read()
    assert s["prices"] == {"ACME": 100.0, "BOREAL": 110.0, "CITRA": 100.0}
    assert s["books"]["trader-1"]["positions_eur"]["BOREAL"] == 3300000
    assert s["positions_eur"]["BOREAL"] == 7700000
    assert s["anchors"] == {"BOREAL": [110.0, 11]}
    assert s["services"]["news-wire"]["mode"] == "normal" and s["services"]["kyc-docs"]["mode"] == "stale"
    assert s["news"] == ["Faillite de BOREAL. Rien."]
    assert s["iban_changes"] == {"BEN-01": {"iban": "FR76 autre", "tick": 11}}
    restored = VigieState(cfg, checkpoint=json.loads(json.dumps(view.checkpoint())))
    assert restored.read() == s
    restored.observe(MessageFactory("clock", k).make(1, "tick", "*", {"tick": 1}), k)
    assert restored.read()["positions_eur"]["BOREAL"] == 6000000


@pytest.mark.parametrize(
    "entry,reason",
    [
        (
            {
                "service": "news-wire",
                "version": "1.8.4",
                "response_hash": "x",
                "tick": 100,
            },
            "INVALID_PROVENANCE",
        ),
        (
            {
                "service": "market-feed",
                "version": "3.2.0",
                "response_hash": "x",
                "tick": 0,
            },
            "UNTRUSTED_PROVENANCE",
        ),
        (
            {
                "service": "news-wire",
                "version": "0.0",
                "response_hash": "x",
                "tick": 10,
            },
            "UNTRUSTED_PROVENANCE",
        ),
        (
            {"service": "intrus", "version": "0.0", "response_hash": "x", "tick": 10},
            "UNTRUSTED_PROVENANCE",
        ),
        (
            {"service": "news-wire", "version": "1.8.4", "tick": 10},
            "INVALID_PROVENANCE",
        ),
    ],
)
def test_each_invalid_provenance_is_refused(setup, entry, reason):
    c, k, s = setup
    m = chain(c, k, provenance=[entry])
    assert c.approve(m, s).reason == reason


def test_configuration_file_change_quarantines_only_its_agent(setup, tmp_path):
    c, k, s = setup
    path = tmp_path / "agent.py"
    path.write_text("configuration modifiée")
    c.registry["trader-1"]["file"] = path
    c.registry["trader-1"]["file_hash"] = "empreinte antérieure"
    c.observe(MessageFactory("trader-1", k).make(10, "heartbeat", "*", {}))
    assert c.quarantined == {"trader-1"}
    assert c.registry["trader-2"]["tools"]


def test_profiles_are_learned_from_signed_reference_journal(cfg, tmp_path):
    import json
    from vigie.compare import learn_reference

    profiles = learn_reference(cfg, 120, tmp_path / "reference")
    assert set(profiles) == {"trader-1", "trader-2", "trader-3"}
    orders = {a: [] for a in profiles}
    for line in (tmp_path / "reference/journal.jsonl").read_text().splitlines():
        entry = json.loads(line)
        msg = entry.get("message", entry)
        if msg["type"] == "order_proposal":
            orders[msg["from"]].append(msg["payload"]["notional_eur"])
    for agent, profile in profiles.items():
        assert profile["reference_ticks"] == 120
        assert profile["frequency"] == len(orders[agent]) / 120
        assert profile["mean_order_eur"] == sum(orders[agent]) / len(orders[agent])


def test_targeted_quarantine_does_not_stop_team_executor(cfg, setup):
    from sim.lockstep import Lockstep

    c, _, _ = setup
    ls = Lockstep(cfg, no_llm=True)
    try:
        ls.run(3)
        c.quarantine("trader-1", "incohérence confirmée")
        for msg in c.drain():
            ls.publish(msg)
        ls.drain()
        assert ls.agents["trader-1"].light == "ROUGE"
        assert ls.agents["trader-2"].light == "VERT"
        assert ls.agents["payment"].light == "VERT"
        assert ls.world.executor.light == "VERT"
        # Un vrai rouge global ne doit jamais être levé par cette correction de routage.
        c.light = "ROUGE"
        c.quarantine("trader-2", "autre incohérence")
        for msg in c.drain():
            ls.publish(msg)
        ls.drain()
        assert ls.world.executor.light == "ROUGE"
    finally:
        ls.close()
