"""The Vigie's view of the bank (VigieState in the team plan), rebuilt from signed bus messages only.

What a control tower in a real bank has: the executions the bank signed (shadow ledger),
Quant's price feed and the bank's own fills, service_status as services announce it (an
attacker announces nothing), the news-wire items traders quote in their llm_trace, the
IBANs Payment proposes, and the operator's parameters. Never /state, world:*, the tainted
set nor a service's internal store: the simulator's answer key is for grading only.
"""

from __future__ import annotations

import copy
import re

from vigie.shadow import ShadowLedger

NEWS = re.compile(r'<donnee_externe source="news-wire"[^>]*>\n(.*?)(?:\n</donnee_externe>|$)', re.S)
FIELDS = ("tick", "prices", "anchors", "cash_history", "modes", "news", "ibans", "iban_changes")


class VigieState:
    def __init__(self, cfg, params=None, checkpoint=None):
        self.cfg, self.params = cfg, params
        self.tick = 0
        self.prices = {a: float(cfg["market"]["initial_price"]) for a in cfg["market"]["assets"]}
        self.anchors = {}
        self.cash_history = []
        self.modes = {}
        self.news = []
        self.ibans = {b["id"]: b["iban"] for b in cfg["beneficiaries"]}
        self.iban_changes = {}
        self.shadow = ShadowLedger(cfg, checkpoint["shadow"] if checkpoint else None)
        if checkpoint:
            for name in FIELDS:
                setattr(self, name, copy.deepcopy(checkpoint[name]))

    def observe(self, msg, keyring, distrusted=()):
        """`distrusted`: services and agents the Vigie already holds suspect; their prices are not taken."""
        try:
            if not keyring.verify(msg):
                return
        except (TypeError, ValueError, KeyError, AttributeError):
            return
        sender, kind, p = msg["from"], msg["type"], msg["payload"]
        if kind == "tick" and sender == "clock":
            if msg["tick"] < self.tick:
                # The world restarted under a Vigie restored from its checkpoint: start a new book.
                self.__init__(self.cfg, self.params)
            self.tick = msg["tick"]
            self.shadow.history.append(dict(self.prices))
        elif kind == "price_update" and sender == "quant" and not {"quant", "market-feed"} & set(distrusted):
            for asset, est in p.get("assets", {}).items():
                price = est.get("price") if isinstance(est, dict) else None
                if asset in self.prices and isinstance(price, (int, float)) and price > 0:
                    self.prices[asset] = float(price)
        elif kind == "execution" and sender == "executor" and p.get("status") == "executed":
            self.cash_history = [e for e in self.cash_history if e[0] >= self.tick - 2] + [[msg["tick"], self.shadow.cash]]
            self.shadow.apply(msg)
            if p["kind"] == "order":
                # The fill is the market's own price: the impact it leaves is in the signed payload.
                side = 1 if p["side"] == "buy" else -1
                after = float(p["mid_price"]) * (1 + side * float(p["impact_pct"]) / 100)
                self.prices[p["asset"]] = after
                self.anchors[p["asset"]] = [after, msg["tick"]]
        elif kind == "service_status" and sender == f"svc:{p.get('service')}":
            # An attacker does not announce itself: only what a real service could report counts.
            self.modes[p["service"]] = "normal" if p.get("mode") == "compromised" else p.get("mode")
        elif kind == "llm_trace" and sender.startswith("trader-") and p.get("task") == "news_sentiment":
            for text in NEWS.findall(p.get("prompt_excerpt", "")):
                if text not in self.news:
                    self.news = [*self.news, text][-200:]
        elif kind == "payment_proposal" and sender == "payment":
            ben, iban = p.get("beneficiary_id"), p.get("iban")
            if ben in self.ibans and iban != self.ibans[ben]:
                self.ibans[ben] = iban
                self.iban_changes[ben] = {"iban": iban, "tick": msg["tick"]}

    def read(self):
        state = self.shadow.mark(self.prices, self.tick)
        state.update(
            prices=dict(self.prices),
            anchors=dict(self.anchors),
            shadow_cash_history=[c for t, c in self.cash_history if t >= self.tick - 2] + [self.shadow.cash],
            services={sid: {"mode": self.modes.get(sid, "normal")} for sid in self.cfg["services"]},
            news=list(self.news),
            iban_changes=dict(self.iban_changes),
            params={
                "restricted_list": self.params.get("restricted_list")
                if self.params
                else self.cfg["agents"]["compliance"]["restricted_list"]
            },
        )
        return state

    def checkpoint(self):
        return {"shadow": self.shadow.checkpoint(), **{name: copy.deepcopy(getattr(self, name)) for name in FIELDS}}
