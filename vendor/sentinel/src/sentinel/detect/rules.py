"""Industry-style surveillance rules: textbook thresholds and a tuner.

PRE-REGISTRATION: the thresholds in TEXTBOOK_RULES and GENERIC_RULE were fixed
from the standard definitions of each behaviour BEFORE any rule was evaluated
on validation data (see git tag `rules-preregistered`). They must not be edited
after results are seen. Tuned variants are produced separately by ``tune_rule``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Condition:
    """One threshold test on one feature."""

    feature: str
    op: str  # ">=" or "<="
    threshold: float

    def holds(self, values: np.ndarray) -> np.ndarray:
        """Boolean array: does each value satisfy the condition?"""
        return values >= self.threshold if self.op == ">=" else values <= self.threshold

    def describe(self, value: float) -> str:
        """Human-readable evidence line for a triggering value."""
        return f"{self.feature} = {value:.3g} ({self.op} {self.threshold:.3g})"


@dataclass(frozen=True)
class Rule:
    """A conjunction ("all") or disjunction ("any") of conditions for one manipulation type."""

    kind: str
    conditions: tuple[Condition, ...]
    mode: str = "all"

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        """Boolean flag per row of a feature table."""
        masks = [c.holds(df[c.feature].to_numpy()) for c in self.conditions]
        return np.logical_and.reduce(masks) if self.mode == "all" else np.logical_or.reduce(masks)

    def trace(self, row: Mapping[str, float]) -> list[str]:
        """Evidence lines: the conditions that hold for this row, with their values."""
        return [c.describe(row[c.feature]) for c in self.conditions if bool(c.holds(np.asarray(row[c.feature])))]


TEXTBOOK_RULES: dict[str, Rule] = {
    # Spoofing: large orders relative to the book, mostly cancelled quickly, while trading the other side.
    "SPOOFING": Rule("SPOOFING", (
        Condition("cancel_rate", ">=", 0.5),
        Condition("max_qty_over_best", ">=", 3.0),
        Condition("cancel_within_3s_share", ">=", 0.3),
        Condition("opposite_fill_share", ">=", 0.5),
    )),
    # Layering: several price levels stacked on one side and pulled together.
    "LAYERING": Rule("LAYERING", (
        Condition("max_live_levels_same_side", ">=", 4.0),
        Condition("max_cancel_burst_100ms", ">=", 4.0),
        Condition("cancel_rate", ">=", 0.5),
    )),
    # Wash trading: repeated trades with one counterparty and no net position change.
    "WASH": Rule("WASH", (
        Condition("top_counterparty_share", ">=", 0.8),
        Condition("net_over_gross", "<=", 0.1),
        Condition("n_fills", ">=", 4.0),
    )),
}

# The classic untyped screen: extreme cancel rate or order-to-trade ratio.
GENERIC_RULE = Rule("ANY", (
    Condition("cancel_rate", ">=", 0.9),
    Condition("order_to_trade_ratio", ">=", 50.0),
), mode="any")


# Search ranges for each feature (wide on purpose: the data decides, not us).
_RANGES: dict[str, tuple[float, float]] = {
    "cancel_rate": (0.0, 1.0), "max_qty_over_best": (0.5, 12.0), "cancel_within_3s_share": (0.0, 1.0),
    "opposite_fill_share": (0.0, 1.0), "max_live_levels_same_side": (1.0, 8.0),
    "max_cancel_burst_100ms": (1.0, 10.0), "top_counterparty_share": (0.0, 1.0),
    "net_over_gross": (0.0, 1.0), "n_fills": (1.0, 12.0),
}


def tune_rule(template: Rule, df: pd.DataFrame, n_draws: int = 20_000, seed: int = 0,
              refine_rounds: int = 2) -> Rule:
    """Tune a conjunctive rule's thresholds to maximize F1 on ``df`` (the validation set).

    Only windows of the rule's own type and NORMAL windows are scored. The search
    starts from the template's (textbook) thresholds, tries ``n_draws`` random
    threshold vectors, then refines one threshold at a time over the quantiles of
    its feature, so the result is never worse than the template on ``df``.
    Ties on F1 go to higher precision.
    """
    sub = df[df["label"].isin([template.kind, "NORMAL"])]
    positives = (sub["label"] == template.kind).to_numpy()
    values = [sub[c.feature].to_numpy() for c in template.conditions]
    ops = [c.op for c in template.conditions]
    n_pos = int(positives.sum())

    def score(thresholds: list[float]) -> tuple[float, float]:
        masks = [(v >= t) if op == ">=" else (v <= t) for v, t, op in zip(values, thresholds, ops)]
        flagged = np.logical_and.reduce(masks)
        tp = int((flagged & positives).sum())
        fp = int((flagged & ~positives).sum())
        f1 = 2 * tp / (2 * tp + fp + (n_pos - tp)) if tp else 0.0
        precision = tp / (tp + fp) if tp + fp else 0.0
        return f1, precision

    rng = np.random.default_rng(seed)
    ranges = [_RANGES[c.feature] for c in template.conditions]
    best_t = [c.threshold for c in template.conditions]
    best = score(best_t)
    for _ in range(n_draws):
        candidate = [float(rng.uniform(lo, hi)) for lo, hi in ranges]
        s = score(candidate)
        if s > best:
            best, best_t = s, candidate
    for _ in range(refine_rounds):
        for i, v in enumerate(values):
            for cut in np.unique(np.quantile(v, np.linspace(0.0, 1.0, 101))):
                candidate = list(best_t)
                candidate[i] = float(cut)
                s = score(candidate)
                if s > best:
                    best, best_t = s, candidate
    conditions = tuple(Condition(c.feature, c.op, float(t)) for c, t in zip(template.conditions, best_t))
    return Rule(template.kind, conditions, "all")


def save_rules(path: Path, rules: Mapping[str, Rule]) -> None:
    """Write rules to JSON, keyed by name. Thresholds keep full float precision."""
    data = {name: {"kind": r.kind, "mode": r.mode,
                   "conditions": [[c.feature, c.op, c.threshold] for c in r.conditions]}
            for name, r in rules.items()}
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_rules(path: Path) -> dict[str, Rule]:
    """Inverse of save_rules."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: Rule(d["kind"], tuple(Condition(f, op, float(t)) for f, op, t in d["conditions"]), d["mode"])
            for name, d in data.items()}
