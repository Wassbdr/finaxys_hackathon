"""Paiement : propose les virements fournisseurs. Max 1 M€ par virement, 10 M€ par jour,
bloqué si l'IBAN du bénéficiaire a changé dans les dernières 24 h simulées.
"""

from __future__ import annotations

import random

from agents.base import Agent
from bus.message import merge_provenance
from services.client import ServiceUnavailable


class Payment(Agent):
    def __init__(self, agent_id, cfg, ctx):
        super().__init__(agent_id, cfg, ctx)
        self.p = cfg["agents"]["payment"]
        self.ticks_per_day = cfg["clock"]["ticks_per_day"]
        self.day = 0
        self.committed_day = 0.0
        self.pending: dict[str, float] = {}
        self.held: list[dict] = []
        self.sent = 0

    def invoice_for(self, tick: int) -> dict | None:
        """Facture fournisseur déterministe : tirée de la graine, une tous les `invoice_every_ticks`."""
        every = self.p["invoice_every_ticks"]
        if tick % every != every // 2:
            return None
        rng = random.Random(f"{self.cfg['seed']}-invoice-{tick}")
        ben = rng.choice(self.cfg["beneficiaries"])
        lo, hi = self.p["invoice_amount_eur"]
        amount = round(rng.uniform(lo, hi), -3)
        return {"invoice_id": f"INV-{tick:05d}", "beneficiary_id": ben["id"], "amount_eur": amount}

    def on_tick(self) -> None:
        day = self.tick // self.ticks_per_day
        if day != self.day:
            self.day, self.committed_day = day, 0.0
        invoice = self.invoice_for(self.tick)
        if invoice:
            self.process(invoice)

    def on_alert(self, msg: dict) -> None:
        p = msg["payload"]
        if p.get("kind") == "invoice" and msg["from"] in ("inject", "human"):
            self.process({"invoice_id": f"INV-X{self.tick:05d}", "beneficiary_id": p["beneficiary_id"], "amount_eur": p["amount_eur"]})

    def check_limits(self, amount: float) -> str | None:
        """Limites du paiement : des `if`, jamais des consignes de prompt."""
        max_transfer = self.p["max_transfer_eur"] * self.amount_factor
        if amount > max_transfer:
            return f"montant {amount:,.0f} € > plafond par virement {max_transfer:,.0f} €"
        if self.committed_day + amount > self.p["max_daily_eur"] * self.amount_factor:
            return f"plafond journalier {self.p['max_daily_eur'] * self.amount_factor:,.0f} € atteint"
        return None

    def process(self, invoice: dict) -> None:
        amount = invoice["amount_eur"]
        problem = self.check_limits(amount)
        if problem:
            return self.hold(invoice, problem)
        try:
            ben = self.ctx.services.get("payment-gateway", f"/beneficiary/{invoice['beneficiary_id']}")
            balances = self.ctx.services.get("core-ledger", "/balances")
        except ServiceUnavailable as err:
            return self.hold(invoice, f"{err.service} {err.kind}")
        if self.tick - ben.data["last_changed_tick"] < self.p["iban_change_lock_ticks"]:
            return self.hold(invoice, f"IBAN modifié au tick {ben.data['last_changed_tick']} (< 24 h simulées)")
        if balances.data["cash_eur"] < amount:
            return self.hold(invoice, "solde insuffisant selon core-ledger")
        self.committed_day += amount
        self.pending[invoice["invoice_id"]] = amount
        self.sent += 1
        self.emit("payment_proposal", "compliance", {
            "payment_id": invoice["invoice_id"],
            "beneficiary_id": invoice["beneficiary_id"],
            "beneficiary_name": ben.data["name"],
            "iban": ben.data["iban"],
            "amount_eur": amount,
            "reference": invoice["invoice_id"],
            "justification": f"Règlement de la facture {invoice['invoice_id']} à {ben.data['name']}.",
        }, provenance=merge_provenance([ben.provenance], [balances.provenance]))

    def hold(self, invoice: dict, reason: str) -> None:
        self.held.append({**invoice, "reason": reason, "tick": self.tick})
        self.held = self.held[-20:]
        self.emit("alert", "human", {"kind": "payment_held", "severity": "info", "invoice": invoice,
                                     "text": f"Paiement : facture {invoice['invoice_id']} en attente, {reason}."})

    def release(self, msg: dict) -> None:
        payment_id = msg["payload"].get("proposal", {}).get("payload", {}).get("payment_id") or msg["payload"].get("payment_id")
        amount = self.pending.pop(payment_id, 0.0)
        self.committed_day -= amount

    def on_compliance_decision(self, msg: dict) -> None:
        if not msg["payload"]["approved"]:
            self.release(msg)

    def on_risk_decision(self, msg: dict) -> None:
        if not msg["payload"].get("approved", True):
            self.release(msg)

    def on_execution(self, msg: dict) -> None:
        p = msg["payload"]
        if p.get("kind") != "payment":
            return
        if p["status"] == "executed":
            self.pending.pop(p["payment_id"], None)
        else:
            self.release(msg)

    def status(self) -> dict:
        return {"committed_day_eur": round(self.committed_day, 2), "sent": self.sent, "held": len(self.held)}


if __name__ == "__main__":
    from agents.runtime import main

    main(Payment, "payment")
