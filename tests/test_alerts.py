"""Tests for alert construction and rendering."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.detect.alerts import model_alerts, render_alert, rule_alerts
from sentinel.detect.dataset import to_xy
from sentinel.detect.explain import ModelExplainer
from sentinel.detect.model import choose_thresholds, predict_proba, train_lgbm
from sentinel.detect.rules import Condition, Rule
from sentinel.features.extractor import FEATURE_NAMES


def _with_keys(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["trader_id"] = np.arange(len(out))
    out["instrument"] = "SYM0"
    out["window"] = 7
    return out


def test_model_alerts_are_only_created_for_flagged_windows_and_carry_shap_evidence(synthetic_windows) -> None:
    train, val = synthetic_windows.iloc[:1600], synthetic_windows.iloc[1600:]
    booster = train_lgbm(train, val, seed=1)
    val = _with_keys(val)
    X = to_xy(val)[0]
    proba = predict_proba(booster, X)
    thresholds = choose_thresholds(proba, val["label"])
    reference = {n: float(np.median(train[n])) for n in FEATURE_NAMES}
    alerts = model_alerts(val, proba, thresholds, ModelExplainer(booster, reference))
    assert 0 < len(alerts) < len(val)
    a = alerts[0]
    assert a.source == "model" and a.kind in ("SPOOFING", "LAYERING", "WASH") and 0 < a.score <= 1
    assert a.instrument == "SYM0" and a.window == 7 and len(a.evidence) >= 1
    assert a.evidence[0].weight is not None and a.evidence[0].weight > 0
    text = render_alert(a)
    assert a.kind in text and a.evidence[0].feature in text and "SYM0" in text


def test_rule_alerts_carry_the_condition_trace() -> None:
    df = _with_keys(pd.DataFrame({"cancel_rate": [0.95, 0.1], "order_to_trade_ratio": [1.0, 1.0]}))
    rule = Rule("SPOOFING", (Condition("cancel_rate", ">=", 0.9),))
    alerts = rule_alerts(df, rule, name="textbook")
    assert len(alerts) == 1 and alerts[0].source == "rule:textbook" and alerts[0].kind == "SPOOFING"
    assert alerts[0].evidence[0].weight is None and "cancel_rate = 0.95" in alerts[0].evidence[0].text
    assert "cancel_rate = 0.95" in render_alert(alerts[0])
