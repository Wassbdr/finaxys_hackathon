"""SHAP explanations for model alerts, phrased for an investigator."""
from __future__ import annotations

from typing import Mapping, NamedTuple

import lightgbm as lgb
import numpy as np
import shap

from sentinel.features.extractor import FEATURE_NAMES

# One plain-language template per feature; {v} is the trigger value in this window.
PHRASES: dict[str, str] = {
    "n_new": "sent {v:.0f} new order(s)",
    "n_cancel": "cancelled {v:.0f} order(s)",
    "n_modify": "modified {v:.0f} order(s)",
    "n_fills": "had {v:.0f} executions",
    "order_to_trade_ratio": "sent {v:.1f} orders per execution",
    "cancel_rate": "cancellations equalled {v:.0%} of the orders it sent",
    "cancel_within_1s_share": "cancelled {v:.0%} of its orders within 1 second",
    "cancel_within_3s_share": "cancelled {v:.0%} of its orders within 3 seconds",
    "median_cancelled_lifetime_s": "typical cancelled order lived {v:.2f} s",
    "total_new_qty": "submitted {v:,.0f} shares of resting orders",
    "total_fill_qty": "traded {v:,.0f} shares",
    "max_order_qty": "largest order was {v:,.0f} shares",
    "max_qty_over_best": "largest order was {v:.1f}x the quantity at the best price",
    "peak_resting_over_depth5": "own resting orders reached {v:.0%} of five-level book depth",
    "max_live_levels_same_side": "held orders at {v:.0f} price levels on one side at once",
    "max_cancel_burst_100ms": "cancelled {v:.0f} order(s) within 100 ms",
    "opposite_fill_share": "{v:.0%} of executions were on the opposite side to its largest resting orders",
    "signed_mid_drift_ticks": "mid-price moved {v:+.2f} ticks in the direction of its resting orders",
    "net_over_gross": "net position change was {v:.0%} of gross traded volume",
    "top_counterparty_share": "{v:.0%} of volume was against a single counterparty",
    "n_counterparties": "traded with {v:.0f} distinct counterparties",
    "roundtrip_counterparty_share": "{v:.0%} of volume was with counterparties it both bought from and sold to",
}


class Contribution(NamedTuple):
    """One feature's push on the alerted class's score."""

    feature: str
    value: float
    reference: float  # median of this feature over normal training windows
    contribution: float  # SHAP value, in log-odds units
    phrase: str


class ModelExplainer:
    """TreeSHAP explanations for a LightGBM multiclass booster."""

    def __init__(self, booster: lgb.Booster, reference: Mapping[str, float]) -> None:
        self._explainer = shap.TreeExplainer(booster)
        self._reference = dict(reference)

    def contributions(self, X: np.ndarray, class_index: int) -> tuple[np.ndarray, np.ndarray]:
        """SHAP values for one class, shape (n, n_features), and the per-row base value (n,).

        contributions + base equals the raw (pre-softmax) score of that class.
        """
        values = self._explainer.shap_values(X)
        per_class = values[class_index] if isinstance(values, list) else values[:, :, class_index]
        base = np.atleast_1d(self._explainer.expected_value)[class_index]
        return np.asarray(per_class), np.full(len(X), float(base))

    def explain(self, x: np.ndarray, class_index: int, top_k: int = 5) -> list[Contribution]:
        """The top_k features pushing this window toward the alerted class, strongest first."""
        contrib, _ = self.contributions(x.reshape(1, -1), class_index)
        order = np.argsort(-contrib[0])[:top_k]
        out = []
        for i in order:
            if contrib[0][i] <= 0:
                break
            name = FEATURE_NAMES[i]
            out.append(Contribution(name, float(x[i]), self._reference[name], float(contrib[0][i]),
                                    PHRASES[name].format(v=float(x[i]))))
        return out
