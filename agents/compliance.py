"""Compliance : liste restreinte, information privilégiée, découpage, spoofing, achat-vente à soi-même, KYC.

Le LLM lit les dossiers KYC. Le code n'accepte jamais la validation d'un autre agent (aucun champ
d'approbation reçu n'est lu) et ne lève jamais seul un blocage : il faut une human_decision.
"""

from __future__ import annotations

from collections import deque

from agents.base import Agent
from bus.message import merge_provenance
from services.client import ServiceUnavailable


class Compliance(Agent):
    def __init__(self, agent_id, cfg, ctx):
        super().__init__(agent_id, cfg, ctx)
        self.c = cfg["agents"]["compliance"]
        self.history: deque = deque(maxlen=500)   # (tick, trader, asset, side, notional)
        self.cancels: deque = deque(maxlen=500)   # (tick, trader)
        self.blocked: dict[str, str] = {}         # cible -> raison
        self.decisions = {"approved": 0, "rejected": 0}

    # --- ordres ---------------------------------------------------------------------------
    def check_order(self, p: dict) -> list[str]:
        reasons = []
        trader, asset, side, notional = p["trader"], p["asset"], p["side"], p["notional_eur"]
        if trader in self.blocked:
            reasons.append(f"trader bloqué ({self.blocked[trader]})")
        if asset in self.ctx.params.get("restricted_list"):
            reasons.append(f"{asset} est sur la liste restreinte")
        if any(ref.get("confidential") for ref in p.get("news_refs", [])):
            reasons.append("information privilégiée : décision fondée sur une news confidentielle")
        window = self.tick - self.c["structuring_window_ticks"]
        small = [h for h in self.history if h[0] > window and h[1] == trader and h[2] == asset and h[4] < self.c["small_order_eur"]]
        if notional < self.c["small_order_eur"] and len(small) + 1 > self.c["structuring_max_orders"]:
            reasons.append(f"ordre découpé : {len(small) + 1} petits ordres en {self.c['structuring_window_ticks']} ticks")
        wash = [h for h in self.history if h[0] >= self.tick - self.c["wash_window_ticks"] and h[1] == trader and h[2] == asset and h[3] != side]
        if wash:
            reasons.append("achat-vente à soi-même : ordre opposé récent sur le même actif")
        recent_cancels = [c for c in self.cancels if c[0] > self.tick - self.c["spoofing_window_ticks"] and c[1] == trader]
        if len(recent_cancels) > self.c["spoofing_max_cancels"]:
            reasons.append(f"faux ordres annulés : {len(recent_cancels)} annulations récentes")
        return reasons

    def on_order_proposal(self, msg: dict) -> None:
        p = msg["payload"]
        if p.get("action") == "cancel":
            self.cancels.append((self.tick, p["trader"]))
            return
        reasons = self.check_order(p)
        self.history.append((self.tick, p["trader"], p["asset"], p["side"], p["notional_eur"]))
        if any("information privilégiée" in r for r in reasons):
            self.block(p["trader"], "information privilégiée")
        self.decide(msg, reasons, checks=["liste restreinte", "information privilégiée", "découpage", "achat-vente à soi-même", "spoofing"])

    # --- paiements ------------------------------------------------------------------------
    def on_payment_proposal(self, msg: dict) -> None:
        p = msg["payload"]
        ben = p["beneficiary_id"]
        reasons, provenance = [], list(msg["data_provenance"])
        if ben in self.blocked:
            reasons.append(f"bénéficiaire bloqué ({self.blocked[ben]})")
        else:
            try:
                dossier = self.ctx.services.get("kyc-docs", f"/dossier/{ben}")
            except ServiceUnavailable as err:
                reasons.append(f"dossier KYC indisponible ({err.kind}) : refus par défaut")
            else:
                provenance = merge_provenance(provenance, [dossier.provenance])
                d = dossier.data
                out = self.ask_llm(
                    "kyc_review",
                    f"Vérifie le dossier KYC du bénéficiaire {ben} avant un virement de {p['amount_eur']:,.0f} €.",
                    {"beneficiary_id": ben, "amount_eur": p["amount_eur"], "country": d["country"],
                     "high_risk_countries": self.c["high_risk_countries"]},
                    [{"source": "kyc-docs", "id": ben,
                      "text": f"Dossier {d['name']}, pays {d['country']}, pièces complètes : {d['docs_complete']}, "
                              f"bénéficiaire effectif identifié : {d['ubo_identified']}. Notes : {d['notes']}"}],
                    provenance,
                )
                if out.get("verdict") != "approve":
                    reasons.append(f"KYC refusé : {out.get('justification', '')}")
                    self.block(ben, "KYC refusé")
                p = {**p, "kyc_justification": out.get("justification", "")}
        self.decide({**msg, "payload": p}, reasons, checks=["KYC", "blocages"], provenance=provenance)

    # --- décision -------------------------------------------------------------------------
    def decide(self, msg: dict, reasons: list[str], checks: list[str], provenance=None) -> None:
        approved = not reasons
        self.decisions["approved" if approved else "rejected"] += 1
        payload = {
            "approved": approved,
            "kind": "order" if msg["type"] == "order_proposal" else "payment",
            "proposal": {"message_id": msg["message_id"], "from": msg["from"], "type": msg["type"], "payload": msg["payload"]},
            "checks": checks,
            "reasons": reasons,
        }
        to = "risk" if approved else msg["from"]
        self.emit("compliance_decision", to, payload, depends_on=[msg["message_id"]],
                  provenance=provenance if provenance is not None else msg["data_provenance"])

    def block(self, target: str, reason: str) -> None:
        if target not in self.blocked:
            self.blocked[target] = reason
            self.emit("alert", "human", {"kind": "compliance_block", "severity": "warning", "target": target,
                                         "text": f"Compliance bloque {target} ({reason}). Seul un humain peut débloquer."})

    def on_human_decision(self, msg: dict) -> None:
        p = msg["payload"]
        if p.get("action") == "unblock" and p.get("target") in self.blocked:
            del self.blocked[p["target"]]
            self.note(f"déblocage humain de {p['target']}")

    def status(self) -> dict:
        return {"blocked": self.blocked, "decisions": self.decisions}


if __name__ == "__main__":
    from agents.runtime import main

    main(Compliance, "compliance")
