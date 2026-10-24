"""Tests for the binary event format and the engine config file."""
from __future__ import annotations

import struct

import numpy as np

from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES, Condition, Rule
from sentinel.features.extractor import FEATURE_NAMES
from sentinel.sim.runner import SimConfig, simulate
from sentinel.stream.export import EVENT_MAGIC, read_events, write_engine_config, write_events


def _props(path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_events_round_trip_through_the_binary_format(tmp_path) -> None:
    res = simulate(SimConfig(instruments=("B", "A"), events_per_instrument=3_000, episodes_per_instrument=3, seed=1))
    names = write_events(res.events, tmp_path / "events.bin")
    assert names == ["A", "B"]  # instrument index follows sorted order, not first appearance
    arr = read_events(tmp_path / "events.bin")
    ev = res.events
    assert arr.shape == (len(ev), 15)
    codes = ev.event_type.astype(str).map({"NEW": 0, "CANCEL": 1, "MODIFY": 2, "EXEC": 3}).to_numpy()
    expected = [ev.ts, ev.instrument.astype(str).map({"A": 0, "B": 1}), codes, ev.order_id, ev.trader_id,
                ev.counterparty_id, ev.side, ev.price, ev.qty, ev.best_bid, ev.best_ask, ev.bid_qty1,
                ev.ask_qty1, ev.bid_depth5, ev.ask_depth5]
    for column, values in enumerate(expected):
        assert np.array_equal(arr[:, column], np.asarray(values)), column


def test_file_header_is_magic_then_little_endian_count(tmp_path) -> None:
    res = simulate(SimConfig(instruments=("A",), events_per_instrument=1_000, episodes_per_instrument=2, seed=2))
    write_events(res.events, tmp_path / "events.bin")
    raw = (tmp_path / "events.bin").read_bytes()
    assert raw[:8] == EVENT_MAGIC and struct.unpack("<q", raw[8:16])[0] == len(res.events)
    assert len(raw) == 16 + len(res.events) * 15 * 8


def test_config_lists_features_thresholds_and_rules_with_exact_floats(tmp_path) -> None:
    tuned = {"WASH": Rule("WASH", (Condition("n_fills", ">=", 6.7500000001234),))}
    write_engine_config(
        tmp_path / "engine.properties", window_s=10, instruments=["A", "B"],
        thresholds={"SPOOFING": 0.85, "LAYERING": 0.65, "WASH": 0.83},
        rule_sets={"textbook": list(TEXTBOOK_RULES.values()), "tuned": list(tuned.values()), "generic": [GENERIC_RULE]})
    props = _props(tmp_path / "engine.properties")
    assert props["window_s"] == "10" and props["instruments"] == "A,B"
    assert props["features"].split(",") == list(FEATURE_NAMES)
    assert props["threshold.WASH"] == "0.83" and props["threshold.SPOOFING"] == "0.85"
    assert props["rule.count"] == "5"
    assert (props["rule.0.detector"], props["rule.0.kind"], props["rule.0.mode"]) == ("textbook", "SPOOFING", "all")
    assert "cancel_rate>=0.5" in props["rule.0.conditions"].split(";")
    assert (props["rule.3.detector"], props["rule.3.kind"]) == ("tuned", "WASH")
    assert float(props["rule.3.conditions"].split(">=")[1]) == 6.7500000001234
    assert (props["rule.4.detector"], props["rule.4.mode"]) == ("generic", "any")
