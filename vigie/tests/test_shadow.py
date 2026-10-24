from bus.message import MessageFactory
from vigie.shadow import ShadowLedger


def test_shadow_conserves_cash_positions_and_deduplicates(cfg, setup):
    _, keys, _ = setup
    ledger = ShadowLedger(cfg)
    msg = MessageFactory("executor", keys).make(
        1,
        "execution",
        "*",
        {
            "status": "executed",
            "kind": "order",
            "exec_id": "X1",
            "trader": "trader-1",
            "asset": "ACME",
            "qty": 1000,
            "fill_price": 101,
        },
    )
    ledger.apply(msg)
    ledger.apply(msg)
    state = ledger.mark({"ACME": 100, "BOREAL": 100, "CITRA": 100}, 1)
    assert state["cash_eur"] == 81899000
    assert state["positions_qty"]["ACME"] == 61000
    assert state["pnl"]["trading_eur"] == -1000
    assert state["books"]["trader-1"]["positions_eur"]["ACME"] == 2100000
    assert (
        ledger.check({"cash_eur": 81899000, "positions_qty": state["positions_qty"]})
        == []
    )
    assert (
        ledger.check({"cash_eur": 82000000, "positions_qty": state["positions_qty"]})[
            0
        ]["field"]
        == "cash_eur"
    )


def test_payments_are_external_flows_not_trading_losses(cfg):
    ledger = ShadowLedger(cfg)
    for i, fraud in enumerate([False, True]):
        ledger.apply(
            {
                "message_id": str(i),
                "tick": 1,
                "payload": {
                    "status": "executed",
                    "kind": "payment",
                    "exec_id": str(i),
                    "amount_eur": 100000,
                    "iban_requested": "simule",
                    "iban_credited": "autre" if fraud else "simule",
                },
            }
        )
    s = ledger.mark({"ACME": 100, "BOREAL": 100, "CITRA": 100}, 1)
    assert s["cash_eur"] == 81800000
    assert s["pnl"]["trading_eur"] == 0
    assert s["pnl"]["payment_fraud_eur"] == -100000
    assert s["payments"]["legit_day_eur"] == 100000


def test_shadow_checkpoint_roundtrip(cfg):
    original = ShadowLedger(cfg)
    restored = ShadowLedger(cfg, original.checkpoint())
    assert restored.mark(
        {"ACME": 100, "BOREAL": 100, "CITRA": 100}, 0
    ) == original.mark({"ACME": 100, "BOREAL": 100, "CITRA": 100}, 0)


def test_lying_ledger_is_isolated_without_sanctioning_risk(setup):
    c, keys, state = setup
    state["shadow_cash_history"] = [82000000.0]
    c.observe(MessageFactory('clock', keys).make(10, 'tick', '*', {'tick': 10}))
    msg = MessageFactory("risk", keys).make(
        10, "heartbeat", "*", {"view": {"cash_eur": 94000000.0}}
    )
    c.observe(msg, state)
    assert c.light == "ROUGE"
    assert c.isolated_services == {"core-ledger"}
    assert not c.quarantined
    assert c.suspicion["risk"] == 0
    assert c.status()["ledger_evidence"][-1]["proof"] == [msg["message_id"]]


def test_non_finite_claim_is_not_a_conserved_balance(cfg):
    ledger = ShadowLedger(cfg)
    assert ledger.check({"cash_eur": float("nan")})
    assert ledger.check({"positions_qty": {"ACME": float("inf")}})
