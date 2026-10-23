"""Quant : juste prix, volatilité, confiance, âge de la donnée. Ne trade jamais.

S'il se trompe, tout le monde se trompe : Traders et Risk consomment ses price_update.
"""

from __future__ import annotations

import statistics
from collections import deque

from agents.base import Agent
from services.client import ServiceUnavailable


class Quant(Agent):
    def __init__(self, agent_id, cfg, ctx):
        super().__init__(agent_id, cfg, ctx)
        q = cfg["agents"]["quant"]
        self.q = q
        self.history: dict[str, deque] = {a: deque(maxlen=q["vol_window_ticks"] + 1) for a in cfg["market"]["assets"]}
        self.last_as_of: int | None = None
        self.published = 0
        self.withheld_reason = ""

    def on_tick(self) -> None:
        try:
            result = self.ctx.services.get("market-feed", "/prices")
        except ServiceUnavailable as err:
            self._withhold(f"market-feed {err.kind}")
            return
        data = result.data
        age = self.tick - data["as_of_tick"]
        if age > self.q["max_data_age_ticks"]:
            self._withhold(f"donnée de {age} ticks (> {self.q['max_data_age_ticks']})")
            return
        if data["as_of_tick"] != self.last_as_of:
            for asset, price in data["prices"].items():
                if asset in self.history:
                    self.history[asset].append(price)
            self.last_as_of = data["as_of_tick"]
        assets = {}
        for asset, prices in self.history.items():
            if not prices:
                continue
            assets[asset] = self.estimate(list(prices))
        self.withheld_reason = ""
        self.published += 1
        self.emit("price_update", "*", {"as_of_tick": data["as_of_tick"], "age_ticks": age, "assets": assets}, provenance=[result.provenance])

    def estimate(self, prices: list[float]) -> dict:
        q = self.q
        price = prices[-1]
        fair_window = prices[-q["fair_window_ticks"]:]
        fair = sum(fair_window) / len(fair_window)
        returns = [b / a - 1 for a, b in zip(prices, prices[1:]) if a > 0]
        vol = statistics.pstdev(returns[-q["vol_window_ticks"]:]) if len(returns) >= 2 else 0.0
        confidence, reason = 1.0, ""
        if price <= 0:
            confidence, reason = q["low_confidence"], "prix nul ou négatif"
        elif len(prices) >= 2 and prices[-2] > 0 and abs(price / prices[-2] - 1) * 100 > q["jump_threshold_pct"]:
            confidence, reason = q["low_confidence"], f"saut de {abs(price / prices[-2] - 1) * 100:.0f} %"
        out = {"price": round(price, 4), "fair": round(fair, 4), "vol": round(vol, 6), "confidence": confidence}
        if reason:
            out["low_confidence_reason"] = reason
        return out

    def _withhold(self, reason: str) -> None:
        if reason != self.withheld_reason:
            self.emit("alert", "*", {"kind": "quant_withheld", "severity": "warning", "text": f"Quant ne publie pas : {reason}."})
            self.note(f"publication suspendue : {reason}")
        self.withheld_reason = reason

    def status(self) -> dict:
        return {"published": self.published, "withheld": self.withheld_reason}


if __name__ == "__main__":
    from agents.runtime import main

    main(Quant, "quant")
