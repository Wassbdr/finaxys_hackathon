"""Risk : exposition, VaR, perte du jour, coussin de liquidité. Réduit le risque, ne l'augmente jamais.

Faiblesse volontaire : il fait confiance au Quant (prix, volatilité) et à core-ledger (soldes,
positions, P&L). Si l'un ou l'autre est piégé, sa VaR et ses marges sont fausses.
"""

from __future__ import annotations

import math

from agents.base import Agent
from bus.message import merge_provenance
from services.client import ServiceUnavailable


class Risk(Agent):
    def __init__(self, agent_id, cfg, ctx):
        super().__init__(agent_id, cfg, ctx)
        self.r = cfg["agents"]["risk"]
        self.horizon = cfg["risk_metrics"]["var_horizon_ticks"]
        self.quant: dict | None = None
        self.balances: dict | None = None
        self.balances_prov: dict | None = None
        self.balances_tick = -1
        self.halted = False
        self.halt_reason = ""
        self.trader_scale = 1.0
        self.last_view: dict = {}
        self.decisions = {"approved": 0, "reduced": 0, "rejected": 0}

    # --- vue du risque (Quant + core-ledger) -----------------------------------------------
    def refresh_balances(self) -> bool:
        if self.balances_tick == self.tick:
            return self.balances is not None
        try:
            result = self.ctx.services.get("core-ledger", "/balances")
        except ServiceUnavailable as err:
            self.note(f"core-ledger {err.kind}")
            self.balances = None
            return False
        self.balances, self.balances_prov, self.balances_tick = result.data, result.provenance, self.tick
        # Le portique exige un témoin public antérieur : une décision ne doit
        # pas certifier elle-même les nouvelles données qu'elle veut utiliser.
        self.emit('heartbeat', '*', {'agent':self.id, 'role':self.role,
            'view':{'cash_eur':self.balances['cash_eur'], 'as_of_tick':self.balances['as_of_tick']}},
            provenance=[self.balances_prov])
        return True

    def positions_eur(self, extra: dict[str, float] | None = None) -> dict[str, float]:
        assets = self.quant["payload"]["assets"]
        out = {}
        for asset, qty in self.balances["positions_qty"].items():
            if asset in assets:
                out[asset] = qty * assets[asset]["price"]
        for asset, delta in (extra or {}).items():
            out[asset] = out.get(asset, 0.0) + delta
        return out

    def var(self, positions_eur: dict[str, float]) -> float:
        """VaR paramétrique avec la volatilité du Quant (indépendance entre actifs)."""
        assets = self.quant["payload"]["assets"]
        variance = sum((v * assets[a]["vol"]) ** 2 for a, v in positions_eur.items() if a in assets)
        return self.r["var_z"] * math.sqrt(variance) * math.sqrt(self.horizon)

    def on_price_update(self, msg: dict) -> None:
        self.quant = msg

    def on_tick(self) -> None:
        if self.quant is None or not self.refresh_balances():
            return
        positions = self.positions_eur()
        var = self.var(positions)
        gross = sum(abs(v) for v in positions.values())
        pnl_day = self.balances["pnl_day_eur"]
        self.last_view = {"var_eur": round(var, 2), "gross_eur": round(gross, 2), "pnl_day_eur": round(pnl_day, 2),
                          "cash_eur": self.balances["cash_eur"],
                          "as_of_tick": self.balances["as_of_tick"]}
        provenance = merge_provenance(self.quant["data_provenance"], [self.balances_prov])
        if pnl_day < -self.r["max_daily_loss_eur"] and not self.halted:
            self.halted, self.halt_reason = True, f"perte du jour {pnl_day:,.0f} €"
            self.emit("alert", "human", {"kind": "risk_halt", "severity": "critical",
                                         "text": f"Risk : {self.halt_reason} > {self.r['max_daily_loss_eur']:,.0f} €. Trading arrêté, relance humaine requise."},
                      provenance=provenance)
        warning = var >= self.r["var_warning_ratio"] * self.r["max_var_eur"]
        scale = self.r["var_warning_scale"] if warning else 1.0
        if scale != self.trader_scale:
            self.trader_scale = scale
            self.emit("risk_decision", "traders", {"kind": "limits_scale", "scale": scale, "var_eur": round(var, 2),
                                                   "reason": f"VaR à {var / self.r['max_var_eur']:.0%} de la limite"},
                      provenance=provenance)

    # --- décisions ------------------------------------------------------------------------
    def on_compliance_decision(self, msg: dict) -> None:
        p = msg["payload"]
        if not p["approved"]:
            return
        proposal = p["proposal"]
        if p["kind"] == "payment":
            self.decide_payment(msg, proposal)
        else:
            self.decide_order(msg, proposal)

    def decide_order(self, msg: dict, proposal: dict) -> None:
        order = proposal["payload"]
        asset, side = order["asset"], 1 if order["side"] == "buy" else -1
        requested = float(order["notional_eur"])
        if self.halted:
            return self.reject(msg, proposal, [f"trading arrêté ({self.halt_reason})"])
        if self.quant is None or asset not in self.quant["payload"]["assets"] or not self.refresh_balances():
            return self.reject(msg, proposal, ["données de risque indisponibles : refus par défaut"])
        before = self.positions_eur()
        gross_before = sum(abs(v) for v in before.values())
        pos_before = before.get(asset, 0.0)
        notional, reasons = requested, []
        reduces_risk = abs(pos_before + side * notional) < abs(pos_before) and abs(side * notional) <= 2 * abs(pos_before)
        if not reduces_risk:
            # Exposition brute
            room = self.r["max_exposure_eur"] - (gross_before - abs(pos_before))
            allowed = max(0.0, room - abs(pos_before)) if (pos_before >= 0) == (side > 0) else max(0.0, room + abs(pos_before))
            if notional > allowed:
                notional = allowed
                reasons.append(f"exposition plafonnée à {self.r['max_exposure_eur']:,.0f} €")
            # VaR : on réduit par paliers, jamais on n'augmente
            for fraction in (1.0, 0.75, 0.5, 0.25, 0.0):
                candidate = notional * fraction
                if self.var(self.positions_eur({asset: side * candidate})) <= self.r["max_var_eur"]:
                    if fraction < 1.0:
                        reasons.append(f"VaR plafonnée à {self.r['max_var_eur']:,.0f} €")
                    notional = candidate
                    break
            # Coussin de liquidité, sur les soldes de core-ledger
            if side > 0 and self.balances["cash_eur"] - notional < self.r["min_cash_eur"]:
                notional = max(0.0, self.balances["cash_eur"] - self.r["min_cash_eur"])
                reasons.append(f"coussin de liquidité de {self.r['min_cash_eur']:,.0f} €")
        notional = min(notional, requested)
        if notional < 1_000:
            return self.reject(msg, proposal, reasons or ["taille nulle après limites"])
        self.approve(msg, proposal, notional, requested, reasons)

    def decide_payment(self, msg: dict, proposal: dict) -> None:
        amount = float(proposal["payload"]["amount_eur"])
        if not self.refresh_balances():
            return self.reject(msg, proposal, ["soldes core-ledger indisponibles : refus par défaut"])
        if self.balances["cash_eur"] - amount < self.r["min_cash_eur"]:
            return self.reject(msg, proposal, [f"coussin de liquidité de {self.r['min_cash_eur']:,.0f} € entamé"])
        self.approve(msg, proposal, amount, amount, [])

    def provenance_for(self, msg: dict) -> list[dict]:
        """Ce que Risk a réellement utilisé : soldes core-ledger, et prix du Quant pour un ordre."""
        extra = [self.balances_prov] if self.balances_prov else []
        is_order = msg["payload"].get("kind") == "order"
        quant = self.quant["data_provenance"] if self.quant and is_order else []
        return merge_provenance(msg["data_provenance"], quant, extra)

    def approve(self, msg, proposal, notional, requested, reasons) -> None:
        self.decisions["reduced" if notional < requested else "approved"] += 1
        self.emit("risk_decision", "executor", {
            "approved": True,
            "kind": "payment" if proposal["type"] == "payment_proposal" else "order",
            "notional_eur": round(notional, 2),
            "requested_eur": requested,
            "reasons": reasons,
            "proposal": proposal,
            "risk_view": self.last_view,
        }, depends_on=[msg["message_id"], proposal["message_id"]], provenance=self.provenance_for(msg))

    def reject(self, msg, proposal, reasons) -> None:
        self.decisions["rejected"] += 1
        self.emit("risk_decision", proposal["from"], {
            "approved": False,
            "kind": "payment" if proposal["type"] == "payment_proposal" else "order",
            "reasons": reasons,
            "proposal": proposal,
        }, depends_on=[msg["message_id"], proposal["message_id"]], provenance=self.provenance_for(msg))

    def on_human_decision(self, msg: dict) -> None:
        if msg["payload"].get("action") in ("resume", "resume_trading") and self.halted:
            self.halted, self.halt_reason = False, ""
            self.note("trading relancé par un humain")

    def status(self) -> dict:
        return {"halted": self.halted, "halt_reason": self.halt_reason, "trader_scale": self.trader_scale,
                "view": self.last_view, "decisions": self.decisions}


if __name__ == "__main__":
    from agents.runtime import main

    main(Risk, "risk")
