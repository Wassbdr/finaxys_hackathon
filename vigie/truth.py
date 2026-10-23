"""Lecture du même état réel que /state, sans passer par core-ledger ou Risk."""

import json
from vigie.shadow import ShadowLedger


class RedisTruth:
    def __init__(self, client, prefix, cfg=None):
        self.client, self.prefix = client, prefix
        self.executions = {}
        saved = client.get(prefix + "vigie:shadow")
        self.shadow = (
            ShadowLedger(cfg, json.loads(saved) if saved else None) if cfg else None
        )
        self.cash_history = []

    def observe(self, msg, keyring):
        if (
            self.shadow
            and msg.get("from") == "clock"
            and msg.get("type") == "tick"
            and keyring.verify(msg)
        ):
            raw = self.client.get(self.prefix + "world:prices")
            if raw:
                self.shadow.history.append(json.loads(raw))
        if (
            msg.get("from") == "executor"
            and msg.get("type") == "execution"
            and keyring.verify(msg)
            and msg["payload"].get("status") == "executed"
        ):
            self.executions[msg["message_id"]] = msg
            if self.shadow:
                self.cash_history.append((msg["tick"], self.shadow.cash))
                self.shadow.apply(msg)
                self.client.set(
                    self.prefix + "vigie:shadow", json.dumps(self.shadow.checkpoint())
                )

    def read(self):
        def get(name, default):
            raw = self.client.get(self.prefix + name)
            return json.loads(raw) if raw else default

        state = get("state", {})
        if not state:
            return {}
        ledger = get("world:ledger", {})
        snapshot_tick = state["tick"]
        snapshot_prices = dict(state["prices"])
        prices = get("world:prices", state["prices"])
        state.update(tick=get("world:tick", state["tick"]), prices=prices)
        for book in state.get("books", {}).values():
            book["positions_eur"] = {
                a: value / snapshot_prices[a] * prices[a]
                for a, value in book["positions_eur"].items()
            }
        # /state est publié en fin de tick : intégrer les exécutions déjà signées entre deux instantanés.
        self.executions = {
            id_: msg
            for id_, msg in self.executions.items()
            if msg["tick"] > snapshot_tick
        }
        for msg in self.executions.values():
            p = msg["payload"]
            if p["kind"] == "order":
                state["books"][p["trader"]]["positions_eur"][p["asset"]] += (
                    p["qty"] * prices[p["asset"]]
                )
            elif p["kind"] == "payment":
                if p["iban_credited"] != p["iban_requested"]:
                    state["pnl"]["payment_fraud_eur"] -= p["amount_eur"]
                else:
                    state["payments"]["legit_day_eur"] += p["amount_eur"]
        if ledger:
            state.update(
                cash_eur=ledger["cash_eur"], positions_qty=ledger["positions_qty"]
            )
            state["positions_eur"] = {
                a: q * prices[a] for a, q in ledger["positions_qty"].items()
            }
            state["pnl"]["day_eur"] = ledger["pnl_day_eur"]
        if self.shadow:
            state["ledger_discrepancies"] = self.shadow.check(ledger) if ledger else []
            self.cash_history = [
                entry for entry in self.cash_history if entry[0] >= state["tick"] - 2
            ]
            state["shadow_cash_history"] = [cash for _, cash in self.cash_history] + [
                self.shadow.cash
            ]
            state.update(self.shadow.mark(prices, state["tick"]))
            self.client.set(
                self.prefix + "vigie:shadow", json.dumps(self.shadow.checkpoint())
            )
        state["news"] = get("world:news", [])
        state["tainted_hashes"] = list(self.client.smembers(self.prefix + "tainted"))
        for sid, status in state.get("services", {}).items():
            mode = self.client.get(f"{self.prefix}svc:{sid}:mode")
            if mode:
                status["mode"] = mode
        state["iban_changes"] = {
            ben: json.loads(raw)
            for ben, raw in self.client.hgetall(
                self.prefix + "svc:payment-gateway:iban_changes"
            ).items()
        }
        return state
