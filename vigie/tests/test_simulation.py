import json

from vigie.simulation import world


def write(directory, states, messages):
    directory.mkdir()
    (directory / "states.jsonl").write_text("".join(json.dumps(s) + "\n" for s in states))
    (directory / "journal.jsonl").write_text("".join(json.dumps({"message": m}) + "\n" for m in messages))


def state(tick, gateway, fraud=0):
    return {"tick": tick, "state": {
        "services": {"market-feed": {"mode": "normal"}, "payment-gateway": {"mode": gateway}},
        "agents": {"trader-1": {"contaminated": gateway == "compromised", "paused": False, "alive": True}},
        "light": {"light": "VERT"}, "pnl": {"trading_eur": 0, "payment_fraud_eur": fraud}}}


def execution(tick, **payload):
    return {"tick": tick, "type": "execution", "from": "executor", "to": "payment", "payload": {"kind": "payment", "amount_eur": 622000, **payload}}


def test_the_film_marks_compromised_data_frauds_and_refusals(tmp_path):
    states = [state(0, "normal"), state(1, "compromised", fraud=-622000)]
    price = {"tick": 1, "type": "price_update", "from": "quant", "to": "traders", "payload": {}, "data_provenance": [{"service": "payment-gateway", "tick": 1}]}
    write(tmp_path / "A", states, [price, execution(1, status="executed", iban_requested="FR1", iban_credited="XX9", gate={"decided_by": "none"})])
    write(tmp_path / "B", states, [execution(1, status="refused", compromised_sources=["payment-gateway"], gate={"decided_by": "vigie", "approved": False})])

    a, b = world(tmp_path / "A", 2), world(tmp_path / "B", 2)
    assert [1, "quant", "trader-1", "price", 1] in a["msgs"]
    assert [1, "executor", "payment-gateway", "fraud", 1] in a["msgs"]
    assert [1, "bad", "€622k wired to a rerouted IBAN"] in a["events"] and a["fraud"] == [0, 622000]
    assert [1, "s:payment-gateway", "compromised"] in a["changes"] and [1, "c:trader-1", 1] in a["changes"]
    assert [1, "executor", "vigie", "refuse", 1] in b["msgs"]
    assert not [m for m in b["msgs"] if m[3] in ("exec", "wire", "fraud")]
    assert [1, "good", "the gate refuses a payment: payment-gateway"] in b["events"]
