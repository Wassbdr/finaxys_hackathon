"""Exécuteur : seul composant qui fait de l'irréversible (ordre au marché, virement).

Chaque action passe d'abord par le feu courant, puis par gate.request_approval(msg).
"""

from __future__ import annotations

from agents.base import Agent
from bus.lights import VERT
from bus.message import merge_provenance
from services.client import ServiceUnavailable


class Executor(Agent):
    def __init__(self, agent_id, cfg, ctx, world, gate):
        super().__init__(agent_id, cfg, ctx)
        self.world = world
        self.gate = gate
        self.done: set[str] = set()
        self.counts = {"executed": 0, "refused": 0}

    def handle(self, msg: dict) -> list[dict]:
        if self.paused and msg["type"] == "risk_decision" and msg.get("to") == self.id:
            self.out = []
            self.refuse(msg, "feu ROUGE : agents en pause", "light")
            return self.out
        before = self.light
        out = super().handle(msg)
        if self.light != before:
            # L'humain est prévenu à chaque changement de feu effectif côté exécution
            self.emit("alert", "human", {"kind": "light_effective", "severity": "warning" if self.light != VERT else "info",
                                         "light": self.light, "previous": before, "vigie_lost": self.vigie_lost,
                                         "text": f"Exécution en feu {self.light} (avant : {before})."})
        return self.out

    def on_risk_decision(self, msg: dict) -> None:
        p = msg["payload"]
        if msg["to"] != self.id or not p.get("approved") or msg["from"] != "risk":
            return
        proposal_id = p["proposal"]["message_id"]
        if proposal_id in self.done:
            return
        self.done.add(proposal_id)
        if self.light != VERT:
            return self.refuse(msg, f"feu {self.light} : aucune action irréversible", "light")
        decision = self.gate.request_approval(msg)
        gate_info = {"approved": decision.approved, "reason": decision.reason, "decided_by": decision.decided_by}
        if not decision.approved:
            return self.refuse(msg, f"gate : {decision.reason}", decision.decided_by, gate_info)
        if p["kind"] == "order":
            self.execute_order(msg, gate_info)
        else:
            self.execute_payment(msg, gate_info)

    def provenance_report(self, provenance: list[dict]) -> tuple[list[str], list[str], bool]:
        services = sorted({e["service"] for e in provenance})
        tainted = [e for e in provenance if self.world.observer.is_tainted(e["response_hash"])]
        altered = any(self.world.observer.is_altered(e["response_hash"]) for e in tainted)
        return services, sorted({e["service"] for e in tainted}), altered

    def execute_order(self, msg: dict, gate_info: dict) -> None:
        p = msg["payload"]
        order = p["proposal"]["payload"]
        world = self.world
        exec_id = f"X{self.tick:05d}-{len(self.done)}"
        side = 1 if order["side"] == "buy" else -1
        fill = world.market.trade(order["asset"], side, p["notional_eur"], exec_id)
        services, compromised, altered = self.provenance_report(msg["data_provenance"])
        world.ledger.register_execution(exec_id, {
            "agent": order["trader"], "asset": order["asset"], "side": order["side"], "notional": p["notional_eur"],
            "tick": self.tick, "provenance": services, "compromised": compromised,
        })
        world.ledger.apply_trade(order["trader"], order["asset"], fill["qty"], fill["fill"], fill["mid"], exec_id)
        world.market.apply_trade_impact(order["asset"], fill["new_price"], exec_id)
        world.publish_ledger()
        self.counts["executed"] += 1
        payload = {
            "kind": "order", "status": "executed", "exec_id": exec_id,
            "proposal_id": order["proposal_id"], "trader": order["trader"], "asset": order["asset"], "side": order["side"],
            "notional_eur": p["notional_eur"], "requested_eur": p["requested_eur"], "qty": fill["qty"],
            "fill_price": fill["fill"], "mid_price": fill["mid"], "impact_pct": round(fill["impact"] * 100, 3),
            "justification": order.get("justification", ""), "gate": gate_info,
            "compromised_sources": compromised,
        }
        sent = self.emit("execution", "*", payload, depends_on=[msg["message_id"]], provenance=msg["data_provenance"])
        if compromised:
            world.observer.record_trapped(sent, compromised, altered, amount=p["notional_eur"], substituted=False)

    def execute_payment(self, msg: dict, gate_info: dict) -> None:
        p = msg["payload"]
        pay = p["proposal"]["payload"]
        exec_id = f"P{self.tick:05d}-{len(self.done)}"
        body = {"beneficiary_id": pay["beneficiary_id"], "amount_eur": p["notional_eur"], "iban": pay["iban"], "reference": pay["reference"]}
        try:
            result = self.ctx.services.post("payment-gateway", "/transfer", body)
        except ServiceUnavailable as err:
            return self.refuse(msg, f"payment-gateway {err.kind}", "executor", gate_info)
        provenance = merge_provenance(msg["data_provenance"], [result.provenance])
        services, compromised, altered = self.provenance_report(provenance)
        substituted = result.data["iban_credited"] != pay["iban"]
        if substituted and "payment-gateway" not in compromised:
            compromised = sorted({*compromised, "payment-gateway"})
        self.world.ledger.apply_payment(p["notional_eur"], exec_id, trapped=substituted, info={
            "agent": "payment", "beneficiary_id": pay["beneficiary_id"], "tick": self.tick,
            "iban_requested": pay["iban"], "iban_credited": result.data["iban_credited"],
            "provenance": services, "compromised": ["payment-gateway"] if substituted else compromised,
        })
        self.world.publish_ledger()
        self.counts["executed"] += 1
        payload = {
            "kind": "payment", "status": "executed", "exec_id": exec_id, "payment_id": pay["payment_id"],
            "beneficiary_id": pay["beneficiary_id"], "amount_eur": p["notional_eur"],
            "iban_requested": pay["iban"], "iban_credited": result.data["iban_credited"],
            "transfer_id": result.data["transfer_id"], "gate": gate_info, "compromised_sources": compromised,
            "justification": pay.get("justification", ""), "kyc_justification": pay.get("kyc_justification", ""),
        }
        sent = self.emit("execution", "*", payload, depends_on=[msg["message_id"]], provenance=provenance)
        if compromised:
            self.world.observer.record_trapped(sent, compromised, altered or substituted, amount=p["notional_eur"], substituted=substituted)

    def refuse(self, msg: dict, reason: str, decided_by: str, gate_info: dict | None = None) -> None:
        p = msg["payload"]
        proposal = p["proposal"]
        self.counts["refused"] += 1
        payload = {"kind": p["kind"], "status": "refused", "reason": reason, "decided_by": decided_by,
                   "proposal_id": proposal["payload"].get("proposal_id"), "payment_id": proposal["payload"].get("payment_id"),
                   "trader": proposal["payload"].get("trader"), "gate": gate_info}
        self.emit("execution", proposal["from"], payload, depends_on=[msg["message_id"]], provenance=msg["data_provenance"])

    def status(self) -> dict:
        return {"counts": self.counts}
