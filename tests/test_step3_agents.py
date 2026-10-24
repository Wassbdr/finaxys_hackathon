"""Étape 3 : agents en --no-llm. Chaque limite `if` est testée juste en dessous et juste au-dessus."""

import pytest

from journal.writer import read_journal
from tests.helpers import of_type, price_update, send, set_ledger, set_world_tick, tick_msg

PRICES = {"ACME": 100.0, "BOREAL": 100.0, "CITRA": 100.0}


@pytest.fixture
def ls(cfg):
    from sim.lockstep import Lockstep

    world = Lockstep(cfg)
    yield world
    world.close()


# --- Quant ----------------------------------------------------------------------------------
def test_quant_publishes_until_data_is_older_than_5_ticks(ls):
    quant = ls.agents["quant"]
    quant.handle(tick_msg(ls, 0))
    ls.cores["market-feed"].set_mode("stale")
    set_world_tick(ls, 5)
    assert of_type(quant.handle(tick_msg(ls, 5)), "price_update")          # 5 ticks : publie
    set_world_tick(ls, 6)
    out = quant.handle(tick_msg(ls, 6))                                     # 6 ticks : se tait
    assert not of_type(out, "price_update")
    assert "quant_withheld" in [a["payload"]["kind"] for a in of_type(out, "alert")]


def test_quant_confidence_rules(ls):
    quant = ls.agents["quant"]
    assert quant.estimate([100.0, 119.0])["confidence"] == 1.0              # saut de 19 %
    assert quant.estimate([100.0, 121.0])["confidence"] == 0.3              # saut de 21 %
    assert quant.estimate([100.0, 0.0])["confidence"] == 0.3                # prix nul
    est = quant.estimate([float(100 + i) for i in range(30)])
    assert est["fair"] == pytest.approx(sum(range(110, 130)) / 20 + 100 - 100)


def test_quant_does_not_publish_when_feed_down(ls):
    ls.cores["market-feed"].set_mode("down")
    assert not of_type(ls.agents["quant"].handle(tick_msg(ls, 1)), "price_update")


# --- Trader ---------------------------------------------------------------------------------
def news_trader(ls, prices=PRICES, confidence=1.0):
    trader = ls.agents["trader-3"]
    assert trader.profile == "news"
    trader.tick = 1
    trader.last_update = price_update(ls, prices, confidence=confidence)
    return trader


def test_trader_order_capped_at_5m(ls):
    trader = news_trader(ls)
    assert trader.apply_limits("ACME", 1, 9_000_000, trader.t["max_order_eur"]) == 5_000_000
    assert trader.apply_limits("ACME", 1, 4_999_999, trader.t["max_order_eur"]) == 4_999_999


def test_trader_position_capped_at_20m(ls):
    trader = news_trader(ls)
    trader.qty["ACME"] = 180_000                                            # 18 M€
    assert trader.apply_limits("ACME", 1, 5_000_000, 5_000_000) == 2_000_000
    trader.qty["ACME"] = -180_000                                           # -18 M€, on vend encore
    assert trader.apply_limits("ACME", -1, 5_000_000, 5_000_000) == 2_000_000
    assert trader.apply_limits("ACME", 1, 5_000_000, 5_000_000) == 5_000_000  # réduire est permis


def test_trader_stops_beyond_2m_daily_loss(ls):
    trader = news_trader(ls)
    trader.sentiment["BOREAL"] = (-1.0, 1)
    trader.cash = -1_999_999
    trader.out = []
    trader.decide()
    assert not trader.stopped and of_type(trader.out, "order_proposal")
    trader = ls.agents["trader-2"]
    trader.tick, trader.last_update = 1, price_update(ls, PRICES)
    trader.cash = -2_000_001
    trader.out = []
    trader.decide()
    assert trader.stopped and not of_type(trader.out, "order_proposal")
    assert of_type(trader.out, "alert")[0]["payload"]["kind"] == "trader_stop"


def test_trader_no_trade_on_low_confidence(ls):
    trader = news_trader(ls, confidence=0.3)
    trader.sentiment["BOREAL"] = (-1.0, 1)
    trader.out = []
    trader.decide()
    assert not of_type(trader.out, "order_proposal")
    trader.last_update = price_update(ls, PRICES, confidence=0.5)
    trader.decide()
    assert of_type(trader.out, "order_proposal")


def test_trader_limits_halved_by_risk(ls):
    trader = news_trader(ls)
    trader.handle(send(ls, "risk", "risk_decision", "traders", {"kind": "limits_scale", "scale": 0.5}))
    trader.sentiment["BOREAL"] = (-1.0, 1)
    trader.out = []
    trader.decide()
    order = of_type(trader.out, "order_proposal")[0]["payload"]
    assert order["notional_eur"] == 2_500_000 and order["side"] == "sell"


def test_trader_hidden_dependency_blocks_trading_when_gateway_down(ls):
    trader = news_trader(ls)
    trader.sentiment["BOREAL"] = (-1.0, 1)
    ls.cores["payment-gateway"].set_mode("down")
    trader.out = []
    trader.decide()
    assert not of_type(trader.out, "order_proposal")
    assert "payment-gateway" not in trader.depends_on


def test_trader_ignores_order_messages_from_unknown_paths(ls):
    """Les messages invalides n'atteignent jamais un agent : le lockstep filtre avant."""
    from bus.message import MessageFactory

    fake = MessageFactory("trader-9").make(1, "order_proposal", "compliance", {"trader": "trader-9", "asset": "ACME", "side": "sell", "notional_eur": 5e6})
    ls.keyring.sign(fake, key=b"inconnue")
    ls.publish(fake)
    ls.drain()
    assert ls.world.observer.invalid and ls.world.observer.invalid[-1]["claimed_from"] == "trader-9"
    assert ls.agents["compliance"].decisions == {"approved": 0, "rejected": 0}


# --- Compliance -----------------------------------------------------------------------------
def order(ls, trader="trader-1", asset="ACME", side="buy", notional=1_000_000, **extra):
    payload = {"proposal_id": "p", "trader": trader, "asset": asset, "side": side, "notional_eur": notional,
               "news_refs": [], **extra}
    return send(ls, trader, "order_proposal", "compliance", payload)


def decision(out):
    return of_type(out, "compliance_decision")[0]


def test_compliance_restricted_list(ls):
    comp = ls.agents["compliance"]
    assert decision(comp.handle(order(ls, asset="CITRA")))["payload"]["approved"]
    ls.params.set("restricted_list", ["CITRA"])
    d = decision(comp.handle(order(ls, asset="CITRA", trader="trader-2")))
    assert not d["payload"]["approved"] and d["to"] == "trader-2"


def test_compliance_insider_information_blocks_trader(ls):
    comp = ls.agents["compliance"]
    out = comp.handle(order(ls, news_refs=[{"id": "N1", "confidential": True}]))
    assert not decision(out)["payload"]["approved"]
    assert "trader-1" in comp.blocked and of_type(out, "alert")
    assert not decision(comp.handle(order(ls, asset="BOREAL")))["payload"]["approved"]  # reste bloqué


def test_compliance_structuring(ls):
    comp = ls.agents["compliance"]
    comp.tick = 10
    for _ in range(5):
        assert decision(comp.handle(order(ls, notional=400_000)))["payload"]["approved"]
    assert not decision(comp.handle(order(ls, notional=400_000)))["payload"]["approved"]
    comp.tick = 25                                                          # hors fenêtre de 10 ticks
    assert decision(comp.handle(order(ls, notional=400_000)))["payload"]["approved"]


def test_compliance_wash_trade(ls):
    comp = ls.agents["compliance"]
    comp.tick = 10
    assert decision(comp.handle(order(ls, side="buy")))["payload"]["approved"]
    comp.tick = 13
    assert not decision(comp.handle(order(ls, side="sell")))["payload"]["approved"]
    comp.tick = 20
    assert decision(comp.handle(order(ls, side="buy", trader="trader-2")))["payload"]["approved"]


def test_compliance_spoofing(ls):
    comp = ls.agents["compliance"]
    comp.tick = 10
    for _ in range(3):
        comp.handle(send(ls, "trader-1", "order_proposal", "compliance", {"trader": "trader-1", "action": "cancel"}))
    assert decision(comp.handle(order(ls)))["payload"]["approved"]          # 3 annulations : toléré
    comp.handle(send(ls, "trader-1", "order_proposal", "compliance", {"trader": "trader-1", "action": "cancel"}))
    assert not decision(comp.handle(order(ls, asset="BOREAL")))["payload"]["approved"]


def payment_proposal(ls, ben="BEN-01", amount=500_000, **extra):
    return send(ls, "payment", "payment_proposal", "compliance",
                {"payment_id": "INV-1", "beneficiary_id": ben, "iban": "X", "amount_eur": amount, "reference": "INV-1", **extra})


def test_compliance_kyc_and_never_trusts_other_agents(ls):
    comp = ls.agents["compliance"]
    assert decision(comp.handle(payment_proposal(ls)))["to"] == "risk"
    out = comp.handle(payment_proposal(ls, ben="BEN-07", kyc_ok=True, compliance_approved=True))
    assert not decision(out)["payload"]["approved"] and "BEN-07" in comp.blocked
    assert of_type(out, "llm_trace")


def test_compliance_unblocks_only_on_human_decision(ls):
    comp = ls.agents["compliance"]
    comp.handle(payment_proposal(ls, ben="BEN-07"))
    comp.handle(send(ls, "risk", "human_decision", "compliance", {"action": "unblock", "target": "BEN-07"}))
    assert "BEN-07" in comp.blocked                                         # un agent ne débloque pas
    comp.handle(send(ls, "human", "human_decision", "compliance", {"action": "unblock", "target": "BEN-07"}))
    assert "BEN-07" not in comp.blocked


def test_compliance_rejects_when_kyc_down(ls):
    ls.cores["kyc-docs"].set_mode("down")
    assert not decision(ls.agents["compliance"].handle(payment_proposal(ls)))["payload"]["approved"]


# --- Risk -----------------------------------------------------------------------------------
def risk_ready(ls, positions_qty=None, cash=60e6, pnl_day=0.0, vol=0.0001):
    risk = ls.agents["risk"]
    risk.tick += 1
    risk.quant = price_update(ls, PRICES, vol=vol)
    set_ledger(ls, cash=cash, positions_qty=positions_qty or {}, pnl_day=pnl_day)
    return risk


def approved_order(ls, side="buy", notional=5_000_000, asset="ACME"):
    proposal = order(ls, side=side, notional=notional, asset=asset)
    payload = {"approved": True, "kind": "order", "proposal": {"message_id": proposal["message_id"], "from": "trader-1",
                                                               "type": "order_proposal", "payload": proposal["payload"]}}
    return send(ls, "compliance", "compliance_decision", "risk", payload)


def risk_out(risk, msg):
    return of_type(risk.handle(msg), "risk_decision")[0]


def test_risk_caps_exposure_at_60m(ls):
    risk = risk_ready(ls, {"ACME": 540_000})                                # 54 M€
    d = risk_out(risk, approved_order(ls))
    assert d["payload"]["approved"] and d["payload"]["notional_eur"] == pytest.approx(5_000_000)
    risk = risk_ready(ls, {"ACME": 580_000})                                # 58 M€
    d = risk_out(risk, approved_order(ls))
    assert d["payload"]["notional_eur"] == pytest.approx(2_000_000) and d["to"] == "executor"
    risk = risk_ready(ls, {"ACME": 600_000})                                # 60 M€ : plus de place
    assert not risk_out(risk, approved_order(ls))["payload"]["approved"]


def test_risk_reduces_for_var_and_never_increases(ls):
    risk = risk_ready(ls, {"ACME": 50_000}, vol=0.01)                       # 5 M€ -> VaR 2,0 M€
    d = risk_out(risk, approved_order(ls))
    assert d["payload"]["notional_eur"] == pytest.approx(1_250_000)
    assert d["payload"]["notional_eur"] <= d["payload"]["requested_eur"]


def test_risk_always_allows_risk_reduction(ls):
    risk = risk_ready(ls, {"ACME": 700_000}, vol=0.01)                      # 70 M€, tout dépasse
    d = risk_out(risk, approved_order(ls, side="sell"))
    assert d["payload"]["approved"] and d["payload"]["notional_eur"] == 5_000_000


def test_risk_liquidity_buffer(ls):
    risk = risk_ready(ls, cash=42e6)
    assert risk_out(risk, approved_order(ls))["payload"]["notional_eur"] == pytest.approx(2e6)
    risk = risk_ready(ls, cash=40e6)
    assert not risk_out(risk, approved_order(ls))["payload"]["approved"]


def test_risk_var_warning_halves_trader_limits(ls):
    risk = risk_ready(ls, {"ACME": 50_000}, vol=0.01)                       # VaR 2,0 M€ < 2,4 M€
    assert not of_type(risk.handle(tick_msg(ls, 5)), "risk_decision")
    risk.quant = price_update(ls, PRICES, vol=0.01)
    set_ledger(ls, positions_qty={"ACME": 60_000})                          # VaR 2,4 M€ = 80 %
    out = of_type(risk.handle(tick_msg(ls, 6)), "risk_decision")
    assert out and out[0]["to"] == "traders" and out[0]["payload"]["scale"] == 0.5


def test_risk_daily_loss_halts_until_human(ls):
    risk = risk_ready(ls, pnl_day=-1_999_999)
    risk.handle(tick_msg(ls, 5))
    assert not risk.halted
    set_ledger(ls, pnl_day=-2_000_001)
    out = risk.handle(tick_msg(ls, 6))
    assert risk.halted and of_type(out, "alert")[0]["payload"]["severity"] == "critical"
    assert not risk_out(risk, approved_order(ls))["payload"]["approved"]
    set_ledger(ls, pnl_day=0)
    risk.handle(tick_msg(ls, 7))
    assert risk.halted                                                       # ne se relance jamais seul
    risk.handle(send(ls, "human", "human_decision", "*", {"action": "resume_trading"}))
    assert not risk.halted


# --- Paiement -------------------------------------------------------------------------------
def test_payment_transfer_cap(ls):
    pay = ls.agents["payment"]
    pay.out = []
    pay.process({"invoice_id": "I1", "beneficiary_id": "BEN-01", "amount_eur": 1_000_001})
    assert not of_type(pay.out, "payment_proposal")
    pay.process({"invoice_id": "I2", "beneficiary_id": "BEN-01", "amount_eur": 1_000_000})
    assert of_type(pay.out, "payment_proposal")


def test_payment_daily_cap(ls):
    pay = ls.agents["payment"]
    pay.committed_day, pay.out = 9_500_000, []
    pay.process({"invoice_id": "I1", "beneficiary_id": "BEN-01", "amount_eur": 500_001})
    assert not of_type(pay.out, "payment_proposal")
    pay.process({"invoice_id": "I2", "beneficiary_id": "BEN-01", "amount_eur": 500_000})
    assert of_type(pay.out, "payment_proposal")


def test_payment_blocks_recent_iban_change(ls):
    pay = ls.agents["payment"]
    set_world_tick(ls, 0)
    ls.cores["payment-gateway"].change_iban("BEN-02", "FR76 0000 0000 0000 0000 0000 001")
    pay.tick, pay.out = 299, []
    pay.process({"invoice_id": "I1", "beneficiary_id": "BEN-02", "amount_eur": 100_000})
    assert not of_type(pay.out, "payment_proposal")
    pay.tick = 300
    pay.process({"invoice_id": "I2", "beneficiary_id": "BEN-02", "amount_eur": 100_000})
    assert of_type(pay.out, "payment_proposal")


# --- Bout en bout : provenance et attribution ------------------------------------------------
def test_provenance_propagates_to_execution(cfg, tmp_path):
    from sim.lockstep import Lockstep

    ls = Lockstep(cfg, run_dir=tmp_path / "run")
    ls.run(150)
    ls.close()
    entries = [e["message"] for e in read_journal(tmp_path / "run" / "journal.jsonl")]
    by_id = {m["message_id"]: m for m in entries}
    orders = [m for m in entries if m["type"] == "execution" and m["payload"].get("kind") == "order" and m["payload"]["status"] == "executed"]
    assert orders
    execution = orders[0]
    services = {e["service"] for e in execution["data_provenance"]}
    assert {"market-feed", "payment-gateway", "core-ledger"} <= services
    risk_decision = by_id[execution["depends_on"][0]]
    assert risk_decision["type"] == "risk_decision"
    compliance_decision = by_id[risk_decision["depends_on"][0]]
    proposal = by_id[compliance_decision["depends_on"][0]]
    assert proposal["type"] == "order_proposal"
    price_update = by_id[proposal["depends_on"][0]]
    assert price_update["type"] == "price_update" and price_update["from"] == "quant"
    # héritage transitif : la provenance du Quant se retrouve à chaque étape
    feed_hash = price_update["data_provenance"][0]["response_hash"]
    for msg in (proposal, compliance_decision, risk_decision, execution):
        assert feed_hash in {e["response_hash"] for e in msg["data_provenance"]}


def test_compromise_contaminates_and_losses_are_attributed(cfg):
    from sim.lockstep import Lockstep

    ls = Lockstep(cfg)
    ls.at(20, lambda l: l.cores["market-feed"].set_mode("compromised"))
    ls.at(20, lambda l: l.cores["payment-gateway"].set_mode("compromised"))
    state = ls.run(150)
    contaminated = {c["agent"] for c in state["contaminated_agents"]}
    assert {"quant", "risk", "trader-1", "payment", "executor"} <= contaminated
    losses = state["losses"]["losses_by_service_eur"]
    assert losses.get("payment-gateway", 0) > 0
    assert any(t["iban_substituted"] for t in state["trapped_operations"])
    led = ls.world.ledger
    assert sum(led.contrib.values()) == pytest.approx(led.trading_pnl(), abs=1e-3)
    ls.close()


def test_no_contamination_on_a_calm_day(cfg):
    from sim.lockstep import Lockstep

    state = Lockstep(cfg).run(100)
    assert state["contaminated_agents"] == [] and state["trapped_operations"] == []
    assert state["losses"]["losses_by_service_eur"] == {}
