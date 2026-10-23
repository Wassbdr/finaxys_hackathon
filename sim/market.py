"""Marché fictif : bruit, chocs de news et impact des ordres de la banque.

Chaque variation de prix est notifiée avec sa cause ("market", "news:<id>", "exec:<id>"),
ce qui permet au ledger de décomposer exactement le P&L.
"""

from __future__ import annotations

import math
import random
from typing import Callable

Listener = Callable[[str, float, float, str], None]


class Market:
    def __init__(self, cfg: dict, rng: random.Random, listener: Listener | None = None):
        m = cfg["market"]
        self.assets: list[str] = list(m["assets"])
        self.prices: dict[str, float] = {a: float(m["initial_price"]) for a in self.assets}
        self.noise = m["noise_pct"] / 100.0
        self.impact_per_meur = m["impact_bps_per_meur"] / 1e4
        self.decay = 1.0 - 0.5 ** (1.0 / m["impact_half_life_ticks"])
        self.rng = rng
        self.listener = listener
        # Impacts encore actifs : exec_id -> (actif, composante log restante)
        self.impacts: dict[str, list] = {}

    def _move(self, asset: str, new_price: float, cause: str) -> None:
        old = self.prices[asset]
        self.prices[asset] = new_price
        if self.listener:
            self.listener(asset, old, new_price, cause)

    def noise_step(self) -> None:
        for asset in self.assets:
            r = self.rng.uniform(-self.noise, self.noise)
            self._move(asset, self.prices[asset] * (1 + r), "market")

    def shock(self, asset: str, pct: float, cause: str) -> None:
        self._move(asset, self.prices[asset] * (1 + pct / 100.0), cause)

    def decay_impacts(self) -> None:
        for exec_id in sorted(self.impacts):
            asset, component = self.impacts[exec_id]
            step = component * self.decay
            remaining = component - step
            self._move(asset, self.prices[asset] * math.exp(-step), f"exec:{exec_id}")
            if abs(remaining) < 1e-7:
                del self.impacts[exec_id]
            else:
                self.impacts[exec_id][1] = remaining

    def impact_fraction(self, notional_eur: float) -> float:
        return min(0.5, self.impact_per_meur * abs(notional_eur) / 1e6)

    def trade(self, asset: str, side: int, notional_eur: float, exec_id: str) -> dict:
        """Exécute au prix moyen (la moitié de l'impact), puis l'impact complet reste sur le prix."""
        mid = self.prices[asset]
        imp = self.impact_fraction(notional_eur)
        fill = mid * (1 + side * imp / 2)
        qty = side * notional_eur / fill
        new_price = mid * (1 + side * imp)
        self.impacts[exec_id] = [asset, math.log(new_price / mid)]
        return {"mid": mid, "fill": fill, "qty": qty, "new_price": new_price, "impact": imp}

    def apply_trade_impact(self, asset: str, new_price: float, exec_id: str) -> None:
        self._move(asset, new_price, f"exec:{exec_id}")

    def liquidation_cost(self, positions_eur: dict[str, float]) -> float:
        """Coût estimé pour tout solder en un ordre par actif (glissement de demi-impact)."""
        return sum(abs(v) * self.impact_fraction(v) / 2 for v in positions_eur.values())
