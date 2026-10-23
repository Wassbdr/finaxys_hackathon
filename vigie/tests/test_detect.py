from bus.message import MessageFactory
from vigie.detect import Indicators


def msg(tick, sender, kind, payload):
    return {"tick": tick, "from": sender, "type": kind, "payload": payload}


def test_price_jump_marks_the_feed_suspect_from_that_tick(cfg):
    ind = Indicators(cfg)
    price = 100.0
    for tick in range(30):
        price *= 1.001 if tick % 2 else 0.999
        ind.observe(msg(tick, "quant", "price_update", {"assets": {"BOREAL": {"price": price}}}))
    assert not ind.drain()
    ind.observe(msg(30, "quant", "price_update", {"assets": {"BOREAL": {"price": price * 0.95}}}))
    (alert,) = ind.drain()
    assert alert["rule"] == "price_drift_z" and alert["service"] == "market-feed"
    assert ind.suspect_at("market-feed", 30) and not ind.suspect_at("market-feed", 29)


def test_the_banks_own_fill_is_not_a_feed_jump(cfg):
    ind = Indicators(cfg)
    price = 100.0
    for tick in range(30):
        price *= 1.001 if tick % 2 else 0.999
        ind.observe(msg(tick, "quant", "price_update", {"assets": {"ACME": {"price": price}}}))
    fill = {"kind": "order", "status": "executed", "asset": "ACME", "side": "buy", "mid_price": price, "impact_pct": 1.5}
    ind.observe(msg(30, "executor", "execution", fill))
    ind.observe(msg(31, "quant", "price_update", {"assets": {"ACME": {"price": price * 1.015}}}))
    assert not ind.drain()


def test_risk_cash_far_from_the_book_marks_core_ledger(cfg):
    ind = Indicators(cfg)
    ind.observe(msg(5, "clock", "tick", {}), {"cash_eur": 100_000_000})
    ind.observe(msg(5, "risk", "heartbeat", {"view": {"cash_eur": 100_000_000}}))
    assert not ind.drain()
    ind.observe(msg(6, "risk", "heartbeat", {"view": {"cash_eur": 115_000_000}}))
    assert ind.drain()[0]["rule"] == "reconciliation_gap_pct"
    assert ind.suspect_at("core-ledger", 6)


def test_limit_use_comes_from_what_agents_publish(cfg):
    ind = Indicators(cfg)
    for tick, gross in ((1, 30e6), (2, 40e6), (3, 50e6)):
        ind.observe(msg(tick, "risk", "heartbeat", {"view": {"cash_eur": 80e6, "gross_eur": gross, "var_eur": 0, "pnl_day_eur": 0}}))
    assert ind.limits["risk"]["exposure_pct"] == {"pct": 83.3, "ticks_to_breach": 1.0}
    ind.observe(msg(4, "trader-1", "heartbeat", {"positions_eur_seen": {"ACME": -5e6}, "pnl_day_eur_seen": -1e6}))
    assert ind.limits["trader-1"]["max_position_pct"]["pct"] == 25.0 and ind.limits["trader-1"]["daily_loss_pct"]["pct"] == 50.0
    ind.observe(msg(4, "payment", "heartbeat", {"committed_day_eur": 2.5e6}))
    assert ind.limits["payment"]["daily_pct"]["pct"] == 25.0


def test_human_tunes_a_threshold_and_a_bad_one_is_refused(setup):
    c, k, _ = setup
    human = MessageFactory("human", k)
    c.observe(human.make(1, "human_decision", "*", {"action": "set_threshold", "rule": "price_drift_z", "value": 6}))
    assert c.detect.thresholds["price_drift_z"] == 6
    c.observe(human.make(2, "human_decision", "*", {"action": "set_threshold", "rule": "nope", "value": 1}))
    assert c.detect.thresholds["price_drift_z"] == 6
    assert c.actions[-2]["reason"].startswith("THRESHOLD_REFUSED")
