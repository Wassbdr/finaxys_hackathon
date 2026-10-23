"""core-ledger (PHP 5.6, non patchable) : soldes et positions de référence, lus par Risk et Paiement."""

from services.base import ServiceCore


class CoreLedger(ServiceCore):
    service_id = "core-ledger"
    routes = {("GET", "/balances"): "balances"}

    def balances(self, params, body, compromised):
        ledger = self.world("ledger", {})
        data = {
            "as_of_tick": self.tick(),
            "cash_eur": ledger.get("cash_eur", 0.0),
            "equity_eur": ledger.get("equity_eur", 0.0),
            "positions_qty": ledger.get("positions_qty", {}),
            "pnl_day_eur": ledger.get("pnl_day_eur", 0.0),
        }
        if compromised:
            k = self.chaos["core_ledger_inflation"]
            inflation = data["equity_eur"] * (k - 1)
            data["cash_eur"] = round(data["cash_eur"] * k, 2)
            data["equity_eur"] = round(data["equity_eur"] * k, 2)
            # Le P&L du jour se déduit des soldes : il gonfle d'autant et masque les pertes
            data["pnl_day_eur"] = round(data["pnl_day_eur"] + inflation, 2)
        return data
