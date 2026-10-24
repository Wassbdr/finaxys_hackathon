"""Tests for rule evaluation, traces and threshold tuning."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES, Condition, Rule, tune_rule
from sentinel.eval.metrics import type_metrics


def _table() -> pd.DataFrame:
    return pd.DataFrame({
        "cancel_rate": [0.9, 0.2, 0.95, 0.6],
        "order_to_trade_ratio": [1.0, 60.0, 2.0, 1.0],
        "label": ["NORMAL", "NORMAL", "SPOOFING", "SPOOFING"],
    })


def test_all_mode_requires_every_condition_and_any_mode_requires_one() -> None:
    df = _table()
    conj = Rule("X", (Condition("cancel_rate", ">=", 0.5), Condition("order_to_trade_ratio", "<=", 1.5)))
    assert list(conj.evaluate(df)) == [True, False, False, True]
    assert list(GENERIC_RULE.evaluate(df)) == [True, True, True, False]


def test_trace_lists_only_conditions_that_hold_with_their_values() -> None:
    rule = Rule("X", (Condition("cancel_rate", ">=", 0.5), Condition("order_to_trade_ratio", ">=", 50.0)), mode="any")
    lines = rule.trace({"cancel_rate": 0.9, "order_to_trade_ratio": 1.0})
    assert lines == ["cancel_rate = 0.9 (>= 0.5)"]


def test_textbook_rules_are_the_preregistered_ones() -> None:
    assert set(TEXTBOOK_RULES) == {"SPOOFING", "LAYERING", "WASH"}
    assert TEXTBOOK_RULES["WASH"].conditions[0] == Condition("top_counterparty_share", ">=", 0.8)
    assert TEXTBOOK_RULES["SPOOFING"].conditions[1] == Condition("max_qty_over_best", ">=", 3.0)


def _separable(n: int = 600, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    label = np.where(rng.random(n) < 0.15, "WASH", "NORMAL")
    pos = label == "WASH"
    return pd.DataFrame({
        "label": label,
        "top_counterparty_share": np.where(pos, rng.uniform(0.6, 1.0, n), rng.uniform(0.0, 0.7, n)),
        "net_over_gross": np.where(pos, rng.uniform(0.0, 0.3, n), rng.uniform(0.2, 1.0, n)),
        "n_fills": np.where(pos, rng.integers(3, 12, n), rng.integers(0, 8, n)).astype(float),
    })


def test_tuning_never_does_worse_than_the_textbook_rule_on_the_tuning_data() -> None:
    df = _separable()
    textbook = TEXTBOOK_RULES["WASH"]
    tuned = tune_rule(textbook, df, n_draws=2_000, seed=1)
    f1_text = type_metrics(df["label"], textbook.evaluate(df), "WASH")["f1"]
    f1_tuned = type_metrics(df["label"], tuned.evaluate(df), "WASH")["f1"]
    assert f1_tuned >= f1_text and f1_tuned > 0.8
    assert [c.feature for c in tuned.conditions] == [c.feature for c in textbook.conditions]
    assert [c.op for c in tuned.conditions] == [c.op for c in textbook.conditions]


def test_tuning_is_deterministic_for_a_seed() -> None:
    df = _separable()
    a = tune_rule(TEXTBOOK_RULES["WASH"], df, n_draws=500, seed=3)
    b = tune_rule(TEXTBOOK_RULES["WASH"], df, n_draws=500, seed=3)
    assert a == b


def test_tuning_ignores_windows_of_other_types() -> None:
    df = _separable()
    extra = df.iloc[:60].copy()
    extra["label"] = "SPOOFING"  # feature values that look like WASH but are labeled as another type
    a = tune_rule(TEXTBOOK_RULES["WASH"], df, n_draws=300, seed=4)
    b = tune_rule(TEXTBOOK_RULES["WASH"], pd.concat([df, extra], ignore_index=True), n_draws=300, seed=4)
    assert a == b


def test_rules_round_trip_through_json(tmp_path) -> None:
    from sentinel.detect.rules import load_rules, save_rules

    rules = {"WASH": Rule("WASH", (Condition("n_fills", ">=", 6.7500000001234),
                                   Condition("net_over_gross", "<=", 0.345))),
             "ANY": Rule("ANY", (Condition("cancel_rate", ">=", 0.9),), mode="any")}
    save_rules(tmp_path / "rules.json", rules)
    assert load_rules(tmp_path / "rules.json") == rules
