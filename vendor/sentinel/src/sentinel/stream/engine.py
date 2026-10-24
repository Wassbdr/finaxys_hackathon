"""Python reference streaming engine: extractor + LightGBM + rules, one event at a time."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, NamedTuple, Sequence

import lightgbm as lgb
import numpy as np

from sentinel.detect.dataset import CLASSES
from sentinel.detect.model import load_model, predict_proba, predict_types
from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES, Rule, load_rules
from sentinel.features.extractor import FEATURE_NAMES, FeatureRow, WindowFeatureExtractor

_COL = {name: i for i, name in enumerate(FEATURE_NAMES)}


class StreamAlert(NamedTuple):
    """A flagged trader-window from one detector. Explanations are added later, off the hot path."""

    detector: str  # "model", or the rule-set name: "textbook", "tuned", "generic"
    kind: str
    trader_id: int
    instrument: str
    window: int
    score: float


class EngineOutput(NamedTuple):
    """What one ``process`` call produced (all empty unless the event closed a window)."""

    rows: list[FeatureRow]
    proba: np.ndarray | None  # (n_rows, 4), rows in the same order as ``rows``
    alerts: list[StreamAlert]


_NOTHING = EngineOutput([], None, [])


def rule_flags(rule: Rule, X: np.ndarray) -> np.ndarray:
    """Evaluate a rule on a feature matrix (columns in FEATURE_NAMES order)."""
    masks = [c.holds(X[:, _COL[c.feature]]) for c in rule.conditions]
    return np.logical_and.reduce(masks) if rule.mode == "all" else np.logical_or.reduce(masks)


class PythonEngine:
    """Single-threaded engine. Feed events with ``process``; call ``flush`` at end of stream."""

    def __init__(self, booster: lgb.Booster, thresholds: Mapping[str, float],
                 rule_sets: Mapping[str, Sequence[Rule]], window_s: int = 10) -> None:
        self._extractor = WindowFeatureExtractor(window_s)
        self._booster = booster
        self._thresholds = dict(thresholds)
        self._rule_sets = {name: list(rules) for name, rules in rule_sets.items()}

    def process(self, *event) -> EngineOutput:
        """Process one event (arguments as WindowFeatureExtractor.process)."""
        rows = self._extractor.process(*event)
        return self._score(rows) if rows else _NOTHING

    def flush(self) -> EngineOutput:
        """Close and score every open window."""
        rows = self._extractor.flush()
        return self._score(rows) if rows else _NOTHING

    def _score(self, rows: list[FeatureRow]) -> EngineOutput:
        X = np.array([r.values for r in rows], dtype=np.float64)
        proba = predict_proba(self._booster, X)
        types = predict_types(proba, self._thresholds)
        alerts = [StreamAlert("model", CLASSES[k], r.trader_id, r.instrument, r.window, float(proba[i, k]))
                  for i, (r, k) in enumerate(zip(rows, types)) if k]
        for name, rules in self._rule_sets.items():
            for rule in rules:
                flags = rule_flags(rule, X)
                alerts += [StreamAlert(name, rule.kind, r.trader_id, r.instrument, r.window, 1.0)
                           for r, flagged in zip(rows, flags) if flagged]
        return EngineOutput(rows, proba, alerts)


def load_engine_parts(model_dir: Path = Path("models")) -> tuple[lgb.Booster, dict[str, float], dict[str, list[Rule]]]:
    """Booster, per-type thresholds and the three rule sets (textbook, tuned, generic) from a model directory."""
    model_dir = Path(model_dir)
    booster, thresholds = load_model(model_dir)
    tuned = load_rules(model_dir / "tuned_rules.json")
    rule_sets = {"textbook": list(TEXTBOOK_RULES.values()), "tuned": list(tuned.values()), "generic": [GENERIC_RULE]}
    return booster, thresholds, rule_sets
