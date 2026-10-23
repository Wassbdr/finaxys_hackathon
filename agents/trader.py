"""Traders (momentum, value, news) : PROPOSENT des ordres, n'exécutent rien.

Ils voient les ordres de leurs pairs : `herding` (0 à 1) fixe le poids donné à ces décisions,
qui s'ajoute au signal propre (signal = propre + herding × pairs).
Dépendance cachée volontaire : chaque décision consulte payment-gateway (statut de règlement)
sans que ce service figure dans `depends_on`.
"""

from __future__ import annotations

from collections import deque

from agents.base import Agent
from bus.message import merge_provenance
from services.client import ServiceUnavailable
from sim.config import trader_profile


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class Trader(Agent):
    observes = frozenset({"order_proposal"})

    def __init__(self, agent_id, cfg, ctx):
        super().__init__(agent_id, cfg, ctx)
        self.t = cfg["agents"]["trader"]
        self.profile = trader_profile(cfg, agent_id)
        self.assets = list(cfg["market"]["assets"])
        p0 = cfg["market"]["initial_price"]
        init_eur = cfg["bank"]["initial_position_per_trader_eur"]
        self.qty = {a: init_eur / p0 for a in self.assets}
        self.cash = 0.0
        self.initial_value = init_eur * len(self.assets)
        self.day_start_pnl = 0.0
        self.stopped = False
        self.limits_scale = 1.0
        self.last_update: dict | None = None
        self.price_hist: dict[str, deque] = {a: deque(maxlen=self.t["momentum_window_ticks"] + 1) for a in self.assets}
        self.news_seen_tick = -1
        self.sentiment: dict[str, tuple[float, int]] = {}
        self.news_refs: dict[str, list[dict]] = {}
        self.news_provenance: list[dict] = []
        self.sell_all_pending = False
        self.peers: deque = deque()
        self.last_order_tick: dict[str, int] = {}
        self.last_llm_tick = -10**9
        self.last_justification = ""
        self.proposals = 0

    # --- comptabilité locale (aux prix du Quant : c'est sa seule vue) ------------------------
    def price(self, asset: str) -> float | None:
        if not self.last_update or asset not in self.last_update["payload"]["assets"]:
            return None
        return self.last_update["payload"]["assets"][asset]["price"]

    def pnl(self) -> float:
        value = sum(q * (self.price(a) or 0.0) for a, q in self.qty.items())
        return value + self.cash - self.initial_value

    def pnl_day(self) -> float:
        return self.pnl() - self.day_start_pnl

    def position_eur(self, asset: str) -> float:
        return self.qty[asset] * (self.price(asset) or 0.0)

    # --- messages -------------------------------------------------------------------------
    def on_tick(self) -> None:
        if self.tick % self.cfg["clock"]["ticks_per_day"] == 0 and self.tick > 0:
            self.day_start_pnl = self.pnl()
            if self.stopped:
                self.stopped = False
                self.note("nouvelle journée : stop levé")

    def on_order_proposal(self, msg: dict) -> None:
        p = msg["payload"]
        if p.get("trader") and p["trader"] != self.id:
            self.peers.append((msg["tick"], p["trader"], p["asset"], 1 if p["side"] == "buy" else -1, p["notional_eur"]))

    def on_execution(self, msg: dict) -> None:
        p = msg["payload"]
        if p.get("kind") != "order" or p.get("trader") != self.id or p.get("status") != "executed":
            return
        self.qty[p["asset"]] += p["qty"]
        self.cash -= p["qty"] * p["fill_price"]

    def on_risk_decision(self, msg: dict) -> None:
        p = msg["payload"]
        if p.get("kind") == "limits_scale":
            self.limits_scale = p["scale"]
            self.note(f"limites x{p['scale']} (Risk)")

    def on_price_update(self, msg: dict) -> None:
        self.last_update = msg
        for asset, est in msg["payload"]["assets"].items():
            if asset in self.price_hist:
                self.price_hist[asset].append(est["price"])
        self.decide()

    # --- décision -------------------------------------------------------------------------
    def peer_signal(self, asset: str) -> float:
        window = self.t["herding_window_ticks"]
        while self.peers and self.peers[0][0] < self.tick - window:
            self.peers.popleft()
        total = sum(side * min(1.0, notional / self.t["max_order_eur"]) for _, _, a, side, notional in self.peers if a == asset)
        return clip(total)

    def own_signal(self, asset: str, est: dict) -> float:
        news = 0.0
        if asset in self.sentiment:
            score, at = self.sentiment[asset]
            if self.tick - at <= self.t["news_memory_ticks"]:
                news = score
        weight = self.t["news_weight"][self.profile]
        base = 0.0
        if self.profile == "momentum":
            hist = self.price_hist[asset]
            if len(hist) == hist.maxlen and hist[0] > 0:
                base = (hist[-1] / hist[0] - 1) * 100 / self.t["momentum_scale_pct"]
        elif self.profile == "value" and est["price"] > 0:
            base = (est["fair"] - est["price"]) / est["price"] * 100 / self.t["value_scale_pct"]
        return clip(base + weight * news)

    def read_news(self) -> list[dict]:
        try:
            result = self.ctx.services.get("news-wire", "/news", {"since": self.news_seen_tick})
        except ServiceUnavailable as err:
            self.note(f"news-wire {err.kind}")
            return []
        items = result.data["items"]
        self.news_seen_tick = result.data["as_of_tick"]
        if not items:
            return []
        self.news_provenance = merge_provenance([result.provenance])
        out = self.ask_llm(
            "news_sentiment",
            f"Profil {self.profile}. Évalue l'effet de ces dépêches sur les actifs {', '.join(self.assets)}.",
            {"assets": self.assets, "positions_eur": {a: round(self.position_eur(a)) for a in self.assets}},
            [{"source": "news-wire", "id": n["id"], "text": f"{n['headline']}. {n['body']}"} for n in items],
            merge_provenance(self.last_update["data_provenance"], [result.provenance]),
        )
        self.last_llm_tick = self.tick
        for asset, score in (out.get("sentiment") or {}).items():
            if asset in self.assets:
                self.sentiment[asset] = (clip(float(score)), self.tick)
                self.news_refs[asset] = [{"id": n["id"], "confidential": n.get("confidential", False)} for n in items]
        if out.get("action") == "sell_all":
            self.sell_all_pending = True
        self.last_justification = out.get("justification", "")
        return items

    def decide(self) -> None:
        # Dépendance non déclarée : sans statut de règlement, le trader ne trade pas
        try:
            settlement = self.ctx.services.get("payment-gateway", "/settlement_status")
        except ServiceUnavailable as err:
            self.note(f"payment-gateway {err.kind} : pas de trading")
            return
        self.read_news()
        if self.stopped:
            return
        if self.pnl_day() < -self.t["max_daily_loss_eur"]:
            self.stopped = True
            self.emit("alert", "human", {"kind": "trader_stop", "severity": "warning",
                                         "text": f"{self.id} : perte du jour {self.pnl_day():,.0f} € > limite, arrêt du trading."})
            return
        provenance = merge_provenance(self.last_update["data_provenance"], self.news_provenance, [settlement.provenance])
        scale = self.limits_scale * self.amount_factor
        max_order = self.t["max_order_eur"] * scale
        herding = float(self.ctx.params.get("herding"))
        orders = []
        for asset, est in self.last_update["payload"]["assets"].items():
            if est["confidence"] < self.t["min_confidence"]:
                continue
            if self.sell_all_pending:
                if self.position_eur(asset) > 0:
                    orders.append((asset, -1, min(max_order, self.position_eur(asset)), {"sell_all": True}))
                continue
            if self.tick - self.last_order_tick.get(asset, -10**9) < self.t["cooldown_ticks"]:
                continue
            own = self.own_signal(asset, est)
            peer = self.peer_signal(asset)
            # Le mimétisme s'ajoute au signal propre : à herding élevé, un seul vendeur entraîne les autres
            combined = clip(own + herding * peer)
            if abs(combined) < self.t["signal_threshold"]:
                continue
            side = 1 if combined > 0 else -1
            signals = {"own": round(own, 3), "peer": round(peer, 3), "herding": herding, "combined": round(combined, 3)}
            orders.append((asset, side, min(1.0, abs(combined)) * max_order, signals))
        self.sell_all_pending = False
        for asset, side, notional, signals in orders:
            notional = self.apply_limits(asset, side, notional, max_order)
            if notional > 0:
                self.propose(asset, side, notional, signals, provenance)

    def apply_limits(self, asset: str, side: int, notional: float, max_order: float) -> float:
        """Limites du trader : des `if`, jamais des consignes de prompt."""
        notional = min(notional, max_order)
        if notional > self.t["max_order_eur"]:
            notional = self.t["max_order_eur"]
        new_position = self.position_eur(asset) + side * notional
        limit = self.t["max_position_eur"] * self.limits_scale * self.amount_factor
        if abs(new_position) > limit:
            room = limit - side * self.position_eur(asset)
            notional = min(notional, max(0.0, room))
        return round(notional, 2)

    def propose(self, asset: str, side: int, notional: float, signals: dict, provenance: list[dict]) -> None:
        verb = "achat" if side > 0 else "vente"
        if self.tick - self.last_llm_tick >= self.cfg["llm"]["min_ticks_between_calls"] and self.cfg["llm"]["enabled"]:
            out = self.ask_llm("justify", f"Justifie en une phrase la {verb} de {notional:,.0f} € de {asset}.",
                               {"asset": asset, "side": verb, "notional_eur": notional, "signals": signals, "profile": self.profile,
                                "rationale": self.rationale(asset, side, signals)}, [], provenance)
            justification = out.get("justification") or self.rationale(asset, side, signals)
            self.last_llm_tick = self.tick
        else:
            justification = self.last_justification if self.last_justification and self.tick == self.last_llm_tick else self.rationale(asset, side, signals)
        self.last_order_tick[asset] = self.tick
        self.sentiment.pop(asset, None)  # une news ne motive qu'un ordre
        self.proposals += 1
        self.emit("order_proposal", "compliance", {
            "proposal_id": f"{self.id}-{self.tick}-{asset}",
            "trader": self.id,
            "profile": self.profile,
            "asset": asset,
            "side": "buy" if side > 0 else "sell",
            "notional_eur": notional,
            "price_seen": self.price(asset),
            "quant_confidence": self.last_update["payload"]["assets"][asset]["confidence"],
            "signals": signals,
            "news_refs": self.news_refs.get(asset, []),
            "justification": justification,
        }, depends_on=[self.last_update["message_id"]], provenance=provenance)

    def rationale(self, asset: str, side: int, signals: dict) -> str:
        if signals.get("sell_all"):
            return f"Vente de {asset} : consigne « tout vendre » retenue à la lecture du fil."
        verb = "Achat" if side > 0 else "Vente"
        return (f"{verb} de {asset} ({self.profile}) : signal propre {signals['own']:+.2f}, "
                f"pairs {signals['peer']:+.2f}, mimétisme {signals['herding']:.1f}.")

    def status(self) -> dict:
        return {
            "profile": self.profile,
            "pnl_day_eur_seen": round(self.pnl_day(), 2) if self.last_update else 0.0,
            "positions_eur_seen": {a: round(self.position_eur(a), 2) for a in self.assets} if self.last_update else {},
            "stopped": self.stopped,
            "limits_scale": self.limits_scale,
            "proposals": self.proposals,
        }


if __name__ == "__main__":
    from agents.runtime import main

    main(Trader, None)
