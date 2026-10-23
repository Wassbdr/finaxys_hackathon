"""Lecture du même état réel que /state, sans passer par core-ledger ou Risk."""

import json


class RedisTruth:
    def __init__(self, client, prefix):
        self.client, self.prefix = client, prefix
        self.executions = {}

    def observe(self, msg, keyring):
        if (
            msg.get("from") == "executor"
            and msg.get("type") == "execution"
            and keyring.verify(msg)
            and msg["payload"].get("status") == "executed"
        ):
            self.executions[msg["message_id"]] = msg

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
