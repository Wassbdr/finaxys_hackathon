"""Règles du gel provisoire, avec preuves publiques construites séparément."""
import copy
import pytest

from bus.message import Keyring, MessageFactory
from sim.config import load_config, known_senders
from vigie.main import Runtime
from vigie.freeze import Freeze, validate_profile


def profile(**updates):
    return {"version": 1, "training_seeds": [7, 11], "reference": {"ACME": .6, "BOREAL": .6, "CITRA": .6},
            "margin": .05, "min_aligned": 2, "persistence_ticks": 1, "window_ticks": 30,
            "freeze_ticks": 5, "similarity_threshold": .98, **updates}


@pytest.fixture
def setup():
    cfg = load_config(overrides={"vigie": {"freeze_profile": profile()}})
    keys = Keyring(known_senders(cfg))
    rt = Runtime(cfg, keys)
    factories = {sender: MessageFactory(sender, keys) for sender in known_senders(cfg)}
    return rt, factories, keys


def send(setup, sender, kind, payload, tick=0, parents=()):
    rt, factories, _ = setup
    msg = factories[sender].make(tick, kind, "*", payload, list(parents))
    rt.on_message(msg)
    return msg


def align(setup, asset="ACME", side="sell", tick=0, news=True):
    return [send(setup, s, "order_proposal", {"trader": s, "asset": asset, "side": side,
        "notional_eur": 100, "news_refs": [{"id": "new-story", "source": "single-wire"}] if news else []}, tick)
        for s in ("trader-1", "trader-2")]


def operation(setup, proposal, *, tick=1):
    return setup[1]["risk"].make(tick, "risk_decision", "executor", {
        "kind": "order", "approved": True, "proposal": proposal, "notional_eur": 100}, [proposal["message_id"]])


def test_alignment_alone_never_freezes(setup):
    orders = align(setup, news=False)
    assert not setup[0].freeze.active
    assert setup[0].request_approval(operation(setup, orders[0])).approved


def test_proof_alone_below_reference_never_freezes(setup):
    setup[0].freeze.profile["reference"]["ACME"] = 1.0
    align(setup)
    assert not setup[0].freeze.active


def test_unique_new_source_and_targeted_scope(setup):
    orders = align(setup)
    rt = setup[0]
    assert "ACME" in rt.freeze.active
    decision = rt.request_approval(operation(setup, orders[0]))
    assert not decision.approved and 'herding_freeze' in decision.reason
    assert orders[0]["message_id"] in decision.reason
    for asset, side, trader in [("BOREAL", "sell", "trader-1"), ("ACME", "buy", "trader-1"), ("ACME", "sell", "trader-3")]:
        safe = send(setup, trader, "order_proposal", {"trader": trader, "asset": asset, "side": side, "notional_eur": 100}, 1)
        assert rt.request_approval(operation(setup, safe)).approved
    payment = setup[1]["risk"].make(1, "risk_decision", "executor", {"kind": "payment", "approved": True})
    assert rt.request_approval(payment).approved


def test_independent_source_corroboration_prevents_unique_source_trigger(setup):
    send(setup, "inject", "alert", {"kind": "market_event", "asset": "ACME", "pct": -1}, 0)
    align(setup)
    assert not setup[0].freeze.active


def test_very_similar_llm_justifications_are_an_independent_witness(setup):
    for sender in ("trader-1", "trader-2"):
        send(setup, sender, "llm_trace", {"response": {"sentiment": {"ACME": -1}, "justification": "vente commune sur ACME"}})
    align(setup, news=False)
    assert setup[0].freeze.active["ACME"]["witness"] == "llm_similarity"


def test_reflexive_loop_requires_sale_drop_and_dependent_sale(setup):
    send(setup, "quant", "price_update", {"assets": {"ACME": {"price": 100}}}, 0)
    send(setup, "executor", "execution", {"kind": "order", "status": "executed", "exec_id": "x", "asset": "ACME", "qty": -1, "fill_price": 100}, 1)
    update = send(setup, "quant", "price_update", {"assets": {"ACME": {"price": 99}}}, 2)
    send(setup, "trader-1", "order_proposal", {"asset": "ACME", "side": "sell", "notional_eur": 100}, 2, [update["message_id"]])
    send(setup, "trader-2", "order_proposal", {"asset": "ACME", "side": "sell", "notional_eur": 100}, 2)
    assert setup[0].freeze.active["ACME"]["witness"] == "reflexive_loop"


def test_expiration_and_start_end_findings(setup):
    align(setup)
    send(setup, "clock", "tick", {"tick": 40}, 40)
    rt=setup[0]
    assert not rt.freeze.active
    findings=[f for f in rt.findings if f.kind == "herding_freeze"]
    assert len(findings) == 2
    assert findings[0].severity == "warning" and findings[1].severity == "info"
    assert all(f.layer == 3 and f.evidence for f in findings)


def test_signed_human_release_and_no_immediate_retrigger(setup):
    align(setup)
    forged = setup[1]["human"].make(1, "human_decision", "*", {"action": "unfreeze", "target": "ACME"})
    forged["signature"] = "invalid"
    setup[0].on_message(forged)
    assert setup[0].freeze.active
    send(setup, "trader-1", "human_decision", {"action": "unfreeze", "target": "ACME"}, 1)
    assert setup[0].freeze.active
    release = send(setup, "human", "human_decision", {"action": "unfreeze", "target": "ACME"}, 1)
    send(setup, "clock", "tick", {"tick": 2}, 2)
    assert not setup[0].freeze.active
    assert release["message_id"] in [f for f in setup[0].findings if f.kind == "herding_freeze"][-1].evidence


def test_training_rejects_held_out_seed_and_invalid_profile():
    with pytest.raises(ValueError): validate_profile(profile(training_seeds=[42]))
    with pytest.raises(ValueError): validate_profile(profile(freeze_ticks=0))


def test_independent_source_from_non_aligned_trader_is_corroboration(setup):
    send(setup, "trader-3", "order_proposal", {"asset": "ACME", "side": "buy", "news_refs": [
        {"id": "new-story", "source": "independent-wire"}]})
    align(setup)
    assert not setup[0].freeze.active


def test_active_signal_renews_tenure_then_falling_signal_expires_exactly(setup):
    align(setup)
    send(setup, "clock", "tick", {"tick": 4}, 4)
    assert setup[0].freeze.active["ACME"]["until_tick"] == 9
    for trader in ("trader-1", "trader-2"):
        send(setup, trader, "order_proposal", {"asset": "ACME", "side": "buy"}, 5)
    send(setup, "clock", "tick", {"tick": 8}, 8)
    assert setup[0].freeze.active
    send(setup, "clock", "tick", {"tick": 9}, 9)
    assert not setup[0].freeze.active
    assert setup[0].freeze.transitions[-1]["reason"] == "expired"


def test_unblock_command_is_a_signed_human_release(setup):
    align(setup)
    send(setup, "human", "human_decision", {"action": "unblock", "target": "ACME"}, 1)
    assert not setup[0].freeze.active


def test_persistence_is_reset_when_alignment_or_proof_falls(setup):
    setup[0].freeze.profile["persistence_ticks"] = 3
    align(setup)
    assert not setup[0].freeze.active
    send(setup, "clock", "tick", {"tick": 1}, 1)
    assert not setup[0].freeze.active
    for trader in ("trader-1", "trader-2"):
        send(setup, trader, "order_proposal", {"asset": "ACME", "side": "buy"}, 2)
    assert not setup[0].freeze.since
    align(setup, tick=3)
    send(setup, "clock", "tick", {"tick": 4}, 4)
    assert not setup[0].freeze.active
    send(setup, "clock", "tick", {"tick": 5}, 5)
    assert setup[0].freeze.active


def test_no_training_run_can_use_42():
    from vigie.calibrate_freeze import train
    with pytest.raises(ValueError, match="42"):
        train([7, 42])


def test_dissimilar_texts_do_not_supply_a_witness(setup):
    for trader, text in [("trader-1", "ACME baisse rapide"), ("trader-2", "ACME croissance future")]:
        send(setup, trader, "llm_trace", {"response": {"sentiment": {"ACME": -1}, "justification": text}})
    align(setup, news=False)
    assert not setup[0].freeze.active


def test_reflexive_sequence_without_dependency_is_not_a_witness(setup):
    send(setup, "quant", "price_update", {"assets": {"ACME": {"price": 100}}}, 0)
    send(setup, "executor", "execution", {"kind": "order", "status": "executed", "exec_id": "x", "asset": "ACME", "qty": -1, "fill_price": 100}, 1)
    send(setup, "quant", "price_update", {"assets": {"ACME": {"price": 99}}}, 2)
    align(setup, news=False, tick=2)
    assert not setup[0].freeze.active


def test_an_old_single_source_is_not_new_again(setup):
    send(setup, "trader-1", "order_proposal", {"asset": "ACME", "side": "sell", "news_refs": [
        {"id": "new-story", "source": "single-wire"}]}, 0)
    align(setup, tick=40)
    assert not setup[0].freeze.active


def test_a_buying_herd_only_freezes_buys_and_preserves_evidence(setup):
    import json
    orders=align(setup, side="buy")
    decision=setup[0].request_approval(operation(setup, orders[0]))
    assert not decision.approved
    proof=json.loads(decision.reason)["evidence"]
    assert proof and all(mid in setup[0].state.messages for mid in proof)
    sell=send(setup, "trader-1", "order_proposal", {"asset": "ACME", "side": "sell"}, 1)
    assert setup[0].request_approval(operation(setup, sell)).approved
