"""One Alert shape for rules and models, with evidence an investigator can read."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sentinel.detect.dataset import CLASSES, to_xy
from sentinel.detect.explain import ModelExplainer
from sentinel.detect.model import predict_types
from sentinel.detect.rules import Rule


@dataclass(frozen=True)
class Evidence:
    """One line of evidence. Model alerts carry a SHAP weight; rule alerts carry only text."""

    feature: str
    value: float
    weight: float | None
    text: str


@dataclass(frozen=True)
class Alert:
    """A flagged trader-window."""

    trader_id: int
    instrument: str
    window: int
    kind: str
    score: float
    source: str  # "model" or "rule:<name>"
    evidence: tuple[Evidence, ...]


def model_alerts(df: pd.DataFrame, proba: np.ndarray, thresholds: dict[str, float],
                 explainer: ModelExplainer, top_k: int = 5) -> list[Alert]:
    """Alerts for the windows the model flags. SHAP is computed only for flagged windows."""
    types = predict_types(proba, thresholds)
    X = to_xy(df)[0]
    alerts = []
    for i in np.nonzero(types)[0]:
        k = int(types[i])
        evidence = tuple(
            Evidence(c.feature, c.value, c.contribution,
                     f"{c.feature}: {c.phrase} (typical {c.reference:.3g}, SHAP {c.contribution:+.2f})")
            for c in explainer.explain(X[i], k, top_k))
        row = df.iloc[i]
        alerts.append(Alert(int(row["trader_id"]), str(row["instrument"]), int(row["window"]),
                            CLASSES[k], float(proba[i, k]), "model", evidence))
    return alerts


def rule_alerts(df: pd.DataFrame, rule: Rule, name: str) -> list[Alert]:
    """Alerts for the windows a rule flags, with the conditions that fired as evidence."""
    flagged = rule.evaluate(df)
    alerts = []
    for i in np.nonzero(flagged)[0]:
        row = df.iloc[i]
        evidence = tuple(Evidence(text.split(" = ")[0], float(row[text.split(" = ")[0]]), None, text)
                         for text in rule.trace(row))
        alerts.append(Alert(int(row["trader_id"]), str(row["instrument"]), int(row["window"]),
                            rule.kind, 1.0, f"rule:{name}", evidence))
    return alerts


def render_alert(alert: Alert) -> str:
    """Multi-line text an investigator can read."""
    lines = [f"ALERT {alert.kind}  trader {alert.trader_id}  {alert.instrument}  window {alert.window}"
             f"  score {alert.score:.2f}  ({alert.source})"]
    lines += [f"  - {e.text}" for e in alert.evidence]
    return "\n".join(lines)
