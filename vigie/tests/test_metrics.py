from bus.message import MessageFactory
from vigie.metrics import Baseline, Layer1


def test_refusals_limits_and_repeated_orders(cfg, setup):
    _, k, _ = setup
    trader, compliance = MessageFactory("trader-1", k), MessageFactory("compliance", k)
    layer = Layer1(cfg)
    layer.observe(trader.make(1, "heartbeat", "*", {"agent": "trader-1", "depends_on": [], "limits_scale": 0.5, "positions_eur_seen": {"ACME": 15_000_000}, "pnl_day_eur_seen": -500_000}))
    for tick in range(2, 6):
        order = trader.make(tick, "order_proposal", "compliance", {"asset": "ACME", "side": "sell", "notional_eur": 4_900_000})
        layer.observe(order)
    layer.observe(compliance.make(6, "compliance_decision", "risk", {"approved": False, "kind": "order"}, [order["message_id"]]))
    m = layer.metrics()["agents"]["trader-1"]
    assert (m["proposals"], m["refused"], m["refusal_rate"]) == (4, 1, 0.25)
    assert m["limits"]["position"] == {"pct": 150.0, "light": "ROUGE"}
    assert m["limits"]["daily_loss"] == {"pct": 25.0, "light": "VERT"}
    (rep,) = [f for f in layer.drain() if f.kind == "repetition"]
    assert rep.target == "trader-1" and len(rep.evidence) == 4


def test_baseline_learns_calm_ticks_then_flags_a_burst_once():
    b = Baseline()
    for tick in range(30):
        assert not b.update(tick, {"trader-1.order_eur": (1_000_000 if tick % 10 == 0 else 0, [])})
    assert not b.learning
    assert not b.update(30, {"trader-1.order_eur": (0, [])})
    (found,) = b.update(31, {"trader-1.order_eur": (15_000_000, ["trader-1:31:1"])})
    assert (found.kind, found.target, found.detail["metric"], found.evidence) == ("anomaly", "trader-1", "order_eur", ["trader-1:31:1"])
    assert not b.update(32, {"trader-1.order_eur": (15_000_000, [])})
