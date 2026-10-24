"""Comptabilité indépendante : configuration initiale et exécutions signées uniquement."""

import copy
import math
from collections import deque
from sim.config import trader_ids
from sim.ledger import historical_var


class ShadowLedger:
    def __init__(self, cfg, checkpoint=None):
        self.cfg = cfg
        self.assets = list(cfg["market"]["assets"])
        price = cfg["market"]["initial_price"]
        initial = cfg["bank"]["initial_position_per_trader_eur"]
        self.qty = {
            a: {s: initial / price for s in self.assets} for a in trader_ids(cfg)
        }
        self.cash = cfg["bank"]["capital_eur"] - initial * len(self.assets) * len(
            self.qty
        )
        self.payments = []
        self.seen = set()
        self.history = deque(maxlen=cfg["risk_metrics"]["var_window_ticks"] + 1)
        self.day = 0
        self.day_start = 0.0
        self.book_start = {a: initial * len(self.assets) for a in self.qty}
        self.book_cash = {a: 0.0 for a in self.qty}
        if checkpoint:
            for name in (
                "qty",
                "cash",
                "payments",
                "day",
                "day_start",
                "book_start",
                "book_cash",
            ):
                setattr(self, name, copy.deepcopy(checkpoint[name]))
            self.seen = set(checkpoint["seen"])
            self.history.extend(checkpoint["history"])

    def apply(self, msg):
        p = msg["payload"]
        if p.get("status") != "executed":
            return
        id_ = p.get("exec_id", msg["message_id"])
        if id_ in self.seen:
            return
        if p["kind"] == "order":
            qty = float(p["qty"])
            price = float(p["fill_price"])
            if not math.isfinite(qty) or not math.isfinite(price) or price <= 0:
                raise ValueError("INVALID_EXECUTION")
            self.qty[p["trader"]][p["asset"]] += qty
            self.cash -= qty * price
            self.book_cash[p["trader"]] -= qty * price
        elif p["kind"] == "payment":
            amount = float(p["amount_eur"])
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("INVALID_EXECUTION")
            self.cash -= amount
            self.payments.append(
                {
                    "tick": msg["tick"],
                    "amount": amount,
                    "fraud": p["iban_requested"] != p["iban_credited"],
                }
            )
        else:
            raise ValueError("INVALID_EXECUTION")
        self.seen.add(id_)

    def mark(self, prices, tick):
        positions = {
            a: sum(book[a] for book in self.qty.values()) * prices[a]
            for a in self.assets
        }
        quantity = {a: sum(book[a] for book in self.qty.values()) for a in self.assets}
        total = sum(p["amount"] for p in self.payments)
        trading = (
            self.cash
            + sum(positions.values())
            - self.cfg["bank"]["capital_eur"]
            + total
        )
        values = {
            a: self.book_cash[a] + sum(q * prices[s] for s, q in book.items())
            for a, book in self.qty.items()
        }
        day = tick // self.cfg["clock"]["ticks_per_day"]
        if day != self.day:
            self.day = day
            self.day_start = trading
            self.book_start = values.copy()
        rm = self.cfg["risk_metrics"]
        return {
            "tick": tick,
            "cash_eur": self.cash,
            "equity_eur": self.cash + sum(positions.values()),
            "positions_eur": positions,
            "positions_qty": quantity,
            "pnl": {
                "trading_eur": trading,
                "day_eur": trading - self.day_start,
                "payment_fraud_eur": -sum(
                    p["amount"] for p in self.payments if p["fraud"]
                ),
            },
            "payments": {
                "legit_day_eur": sum(
                    p["amount"]
                    for p in self.payments
                    if not p["fraud"]
                    and p["tick"] // self.cfg["clock"]["ticks_per_day"] == day
                )
            },
            "books": {
                a: {
                    "positions_eur": {s: q * prices[s] for s, q in book.items()},
                    "pnl_day_eur": values[a] - self.book_start[a],
                }
                for a, book in self.qty.items()
            },
            "var_99_1d_eur": historical_var(
                positions,
                list(self.history),
                rm["var_confidence"],
                rm["var_horizon_ticks"],
            ),
        }

    def check(self, claim):
        differences = []
        if "cash_eur" in claim and (
            not math.isfinite(float(claim["cash_eur"]))
            or abs(float(claim["cash_eur"]) - self.cash) > 0.05
        ):
            differences.append(
                {
                    "field": "cash_eur",
                    "claimed": claim["cash_eur"],
                    "expected": self.cash,
                }
            )
        for a, actual in claim.get("positions_qty", {}).items():
            expected = sum(book.get(a, 0) for book in self.qty.values())
            if (
                not math.isfinite(float(actual))
                or abs(float(actual) - expected) > 0.001
            ):
                differences.append(
                    {
                        "field": "positions_qty." + a,
                        "claimed": actual,
                        "expected": expected,
                    }
                )
        return differences

    def checkpoint(self):
        return {
            **{
                name: copy.deepcopy(getattr(self, name))
                for name in (
                    "qty",
                    "cash",
                    "payments",
                    "day",
                    "day_start",
                    "book_start",
                    "book_cash",
                )
            },
            "seen": sorted(self.seen),
            "history": list(self.history),
        }
