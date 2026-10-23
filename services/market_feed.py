"""market-feed (PHP 7.4, non patchable) : cours des actions, consommé par le Quant."""

from services.base import ServiceCore


class MarketFeed(ServiceCore):
    service_id = "market-feed"
    routes = {("GET", "/prices"): "prices"}

    def prices(self, params, body, compromised):
        prices = self.world("prices", {})
        data = {"as_of_tick": self.tick(), "prices": {a: round(p, 4) for a, p in prices.items()}}
        if compromised:
            # Dérive progressive : -3 %/tick cumulés sur la cible, sous le seuil de saut du Quant
            target = self.chaos["market_feed_target"]
            factor = (1 + self.chaos["market_feed_drift_pct"] / 100.0) ** self.since_compromise()
            if target in data["prices"]:
                data["prices"][target] = round(data["prices"][target] * factor, 4)
        return data
