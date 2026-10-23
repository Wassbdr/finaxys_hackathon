from bus.message import MessageFactory
from vigie.provenance import Provenance
from vigie.tests.test_core import chain
from vigie.tests.test_rules import payment_chain


def test_taint_retroactive_and_invalid_parent_contaminates():
    graph = Provenance(["news-wire"])
    source = {
        "message_id": "q",
        "from": "quant",
        "tick": 1,
        "type": "price_update",
        "depends_on": [],
        "data_provenance": [{"service": "news-wire"}],
    }
    child = {
        **source,
        "message_id": "t",
        "from": "trader-1",
        "data_provenance": [],
        "depends_on": ["q"],
    }
    graph.add(source)
    graph.add(child)
    assert graph.contaminated("t") == ([], [])
    graph.isolate("news-wire", 10)
    assert graph.contaminated("t")[0] == ["news-wire"]
    graph.reject("q")
    assert graph.contaminated("t")[1] == ["q"]
    assert any(n["id"] == "t" and n["contaminated"] for n in graph.graph()["nodes"])


def test_contaminated_victim_keeps_zero_suspicion(setup):
    c, k, s = setup
    m = chain(
        c,
        k,
        provenance=[
            {
                "service": "news-wire",
                "version": "1.8.4",
                "response_hash": "bad",
                "tick": 10,
            }
        ],
    )
    c.observe(
        MessageFactory("secu", k).make(
            9, "alert", "*", {"kind": "cve_published", "unpatchable": ["news-wire"]}
        )
    )
    assert not c.approve(m, s).approved
    assert c.suspicion["trader-1"] == 0 and not c.quarantined


def test_payment_assertion_without_signed_same_client_proof_is_rejected(setup):
    c, k, s = setup
    m = payment_chain(c, k)
    compid = m["depends_on"][1]
    m["depends_on"].remove(compid)
    k.sign(m)
    assert c.approve(m, s).reason == "MISSING_COMPLIANCE_PROOF"


def test_wrong_client_or_unsigned_compliance_is_not_a_proof(setup):
    c, k, s = setup
    m = payment_chain(c, k)
    comp = c.seen[m["depends_on"][1]]
    comp["payload"]["proposal"]["payload"] = {
        **comp["payload"]["proposal"]["payload"],
        "beneficiary_id": "BEN-02",
    }
    k.sign(comp)
    assert not c.approve(m, s).approved


def test_independent_anchor_overrides_a_service_claiming_normal(setup):
    from bus.message import MessageFactory

    c, k, s = setup
    s["anchors"] = {"BOREAL": [100.0, 9]}  # the bank traded BOREAL at 100 one tick ago
    c.observe(MessageFactory("clock", k).make(10, "tick", "*", {"tick": 10}), s)
    c.observe(
        MessageFactory("quant", k).make(
            10,
            "price_update",
            "*",
            {"assets": {"BOREAL": {"price": 97, "confidence": 1.0}}},
            data_provenance=[
                {
                    "service": "market-feed",
                    "version": "3.2.0",
                    "response_hash": "a",
                    "tick": 10,
                }
            ],
        ),
        s,
    )
    assert c.isolated_services == {"market-feed"}
    assert c.suspicion["quant"] == 0 and not c.quarantined


def test_payment_can_use_transitive_signed_compliance_proof(setup):
    from bus.message import MessageFactory

    c, k, s = setup
    m = payment_chain(c, k)
    parent = MessageFactory("human", k).make(
        10,
        "alert",
        "*",
        {"text": "enveloppe de transport"},
        depends_on=list(m["depends_on"]),
    )
    c.observe(parent)
    m["depends_on"] = [parent["message_id"]]
    k.sign(m)
    assert c.approve(m, s).approved


def test_human_release_does_not_launder_previously_poisoned_messages():
    graph = Provenance(["news-wire"])
    old = {
        "message_id": "old",
        "from": "quant",
        "tick": 5,
        "type": "price_update",
        "depends_on": [],
        "data_provenance": [{"service": "news-wire"}],
    }
    graph.add(old)
    graph.isolate("news-wire", 10)
    graph.release("news-wire", 20)
    graph.add({**old, "message_id": "new", "tick": 21})
    graph.add(
        {
            **old,
            "message_id": "child",
            "tick": 21,
            "data_provenance": [],
            "depends_on": ["old"],
        }
    )
    assert graph.contaminated("new") == ([], [])
    assert graph.contaminated("child")[0] == ["news-wire"]


def test_confidence_uses_the_causal_quant_and_numeric_gap(setup):
    c, k, s = setup
    m = chain(c, k)
    source = c.seen[m["payload"]["proposal"]["message_id"]]
    q = c.seen[source["depends_on"][0]]
    q["payload"]["assets"]["BOREAL"]["confidence"] = 0.6
    k.sign(q)
    assert c.approve(m, s).reason == "INCOHERENT_CONFIDENCE"
    assert not c.quarantined
