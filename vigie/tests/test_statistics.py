import math
import pytest
from vigie.statistics import robust_z, cusum, structuring, Behavior
from vigie.compare import learn_reference


def test_mad_floor_and_capped_cusum():
    assert math.isfinite(robust_z(1, 0, 0, 0.5))
    assert robust_z(1, 0, 0, 0.5) == pytest.approx(1.349)
    assert cusum(0, 1000000) == 7.5
    assert cusum(0, -100) == 0


def test_structuring_twenty_ticks_and_reference_excess():
    orders = [(t, 4900000, "ACME", str(t)) for t in (0, 10, 20)]
    s = structuring(orders, 20, 5000000, {"max_cumulative_20": 9000000})
    assert s["alert"] and s["near_count"] == 3
    assert not structuring(orders, 41, 5000000, {})["alert"]
    assert not structuring(orders, 20, 5000000, {"max_cumulative_20": 14700000})[
        "alert"
    ]
    assert structuring(
        [(1, 4000000, "A", "1"), (2, 4000000, "A", "2"), (3, 4000000, "A", "3")],
        3,
        5000000,
        {"max_cumulative_20": 9000000},
    )["alert"]


def test_clean_reference_does_not_trigger_its_own_detector(cfg, tmp_path):
    import json

    profiles = learn_reference(cfg, 300, tmp_path / "reference")
    detector = Behavior(profiles)
    orders = {a: [] for a in profiles}
    for line in (tmp_path / "reference/journal.jsonl").read_text().splitlines():
        m = json.loads(line)
        m = m.get("message", m)
        if m["from"] == "clock":
            assert all(
                not detector.tick(a, records, m["tick"])
                for a, records in orders.items()
            )
        elif m["type"] == "order_proposal":
            orders[m["from"]].append(
                (
                    m["tick"],
                    m["payload"]["notional_eur"],
                    m["payload"]["asset"],
                    m["message_id"],
                )
            )
            assert not structuring(
                orders[m["from"]], m["tick"], 5000000, profiles[m["from"]]
            )["alert"]


def test_poisoned_reference_is_rejected_instead_of_becoming_normal(cfg, setup):
    from bus.message import MessageFactory
    from vigie.statistics import calibrate

    _, keys, _ = setup
    poisoned = MessageFactory("svc:market-feed", keys).make(
        1, "service_status", "*", {"service": "market-feed", "mode": "compromised"}
    )
    with pytest.raises(ValueError, match="REFERENCE_CONTAMINATED"):
        calibrate(cfg, [poisoned], 300, keys)
