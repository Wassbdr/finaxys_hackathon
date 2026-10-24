"""Comptabilité financière : positions, cash, P&L, VaR, exposition, attribution des pertes.

Décomposition exacte du P&L : chaque variation de prix ΔP d'un actif rapporte Q·ΔP (Q = position
de la banque à cet instant) à sa cause, et chaque exécution rapporte son glissement q·(mid − fill).
La somme des contributions est égale au P&L de trading. Une contribution d'exécution est ensuite
reliée, via la data_provenance de la décision, aux services qui l'ont alimentée.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque


def percentile(values: list[float], q: float) -> float:
    """Percentile à interpolation linéaire (convention numpy par défaut)."""
    data = sorted(values)
    if not data:
        return 0.0
    pos = (len(data) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def historical_var(positions_eur: dict[str, float], price_history: list[dict[str, float]], confidence: float, horizon: int) -> float:
    """VaR historique : P&L du portefeuille actuel rejoué sur les rendements de la fenêtre."""
    if len(price_history) < 2:
        return 0.0
    scenarios = []
    for prev, cur in zip(price_history, price_history[1:]):
        scenarios.append(sum(v * (cur[a] / prev[a] - 1) for a, v in positions_eur.items() if a in cur))
    return max(0.0, -percentile(scenarios, 1 - confidence)) * math.sqrt(horizon)


class Ledger:
    def __init__(self, cfg: dict, prices: dict[str, float], books: list[str]):
        self.cfg = cfg
        self.assets = list(prices)
        self.prices = dict(prices)
        self.ticks_per_day = cfg["clock"]["ticks_per_day"]
        rm = cfg["risk_metrics"]
        self.var_confidence = rm["var_confidence"]
        self.var_horizon = rm["var_horizon_ticks"]
        self.history: deque = deque([dict(prices)], maxlen=rm["var_window_ticks"] + 1)

        init_eur = cfg["bank"]["initial_position_per_trader_eur"]
        self.positions: dict[str, dict[str, float]] = {}
        self.avg_cost: dict[str, dict[str, float]] = {}
        self.realized: dict[str, float] = defaultdict(float)
        for book in books:
            self.positions[book] = {a: init_eur / prices[a] for a in self.assets}
            self.avg_cost[book] = dict(prices)
        invested = sum(q * prices[a] for b in books for a, q in self.positions[b].items())
        self.cash = cfg["bank"]["capital_eur"] - invested
        self.initial_equity = self.equity()

        self.contrib: dict[str, float] = defaultdict(float)
        self.exec_info: dict[str, dict] = {}
        self.payments_legit_total = 0.0
        self.payments_legit_day = 0.0
        self.fraud_losses: list[dict] = []
        self.day = 0
        self.day_start_trading_pnl = 0.0
        self.book_day_start: dict[str, float] = {b: 0.0 for b in books}

    # --- état -----------------------------------------------------------------------------
    def bank_position(self, asset: str) -> float:
        return sum(book[asset] for book in self.positions.values())

    def positions_eur(self, book: str | None = None) -> dict[str, float]:
        if book is not None:
            return {a: q * self.prices[a] for a, q in self.positions[book].items()}
        return {a: self.bank_position(a) * self.prices[a] for a in self.assets}

    def equity(self) -> float:
        return self.cash + sum(self.bank_position(a) * self.prices[a] for a in self.assets)

    def trading_pnl(self) -> float:
        return self.equity() - self.initial_equity + self.payments_legit_total + self.fraud_total()

    def fraud_total(self) -> float:
        return sum(f["amount"] for f in self.fraud_losses)

    def pnl_day(self) -> float:
        """P&L de trading du jour : les virements (légitimes ou détournés) n'y figurent pas.

        C'est ce que core-ledger expose à Risk : la fraude au virement reste invisible pour lui.
        """
        return self.trading_pnl() - self.day_start_trading_pnl

    # --- événements -----------------------------------------------------------------------
    def on_price_move(self, asset: str, old: float, new: float, cause: str) -> None:
        self.contrib[cause] += self.bank_position(asset) * (new - old)
        self.prices[asset] = new

    def register_execution(self, exec_id: str, info: dict) -> None:
        """info : agent, asset, side, notional, provenance (services), compromised (services)."""
        self.exec_info[exec_id] = info

    def apply_trade(self, book: str, asset: str, qty: float, fill: float, mid: float, exec_id: str) -> None:
        self.cash -= qty * fill
        self.contrib[f"exec:{exec_id}"] += qty * (mid - fill)
        pos = self.positions[book][asset]
        avg = self.avg_cost[book][asset]
        if pos == 0 or (pos > 0) == (qty > 0):
            new_pos = pos + qty
            self.avg_cost[book][asset] = (pos * avg + qty * fill) / new_pos if new_pos else 0.0
        else:
            closing = min(abs(qty), abs(pos))
            self.realized[book] += closing * (fill - avg) * (1 if pos > 0 else -1)
            new_pos = pos + qty
            if abs(qty) > abs(pos):
                self.avg_cost[book][asset] = fill
            elif new_pos == 0:
                self.avg_cost[book][asset] = 0.0
        self.positions[book][asset] = new_pos

    def apply_payment(self, amount: float, exec_id: str, trapped: bool, info: dict) -> None:
        self.cash -= amount
        if trapped:
            self.fraud_losses.append({"exec_id": exec_id, "amount": amount, **info})
        else:
            self.payments_legit_total += amount
            self.payments_legit_day += amount

    def end_tick(self, tick: int) -> None:
        self.history.append(dict(self.prices))
        day = tick // self.ticks_per_day
        if day != self.day:
            self.day = day
            self.day_start_trading_pnl = self.trading_pnl()
            self.payments_legit_day = 0.0
            self.book_day_start = {b: self.book_pnl(b) for b in self.positions}

    # --- mesures --------------------------------------------------------------------------
    def var(self) -> float:
        return historical_var(self.positions_eur(), list(self.history), self.var_confidence, self.var_horizon)

    def book_pnl(self, book: str) -> float:
        return self.realized[book] + self.unrealized(book)

    def book_pnl_day(self, book: str) -> float:
        return self.book_pnl(book) - self.book_day_start.get(book, 0.0)

    def fraud_day(self, tick: int) -> float:
        day = tick // self.ticks_per_day
        return sum(f["amount"] for f in self.fraud_losses if f.get("tick", 0) // self.ticks_per_day == day)

    def unrealized(self, book: str) -> float:
        return sum(q * (self.prices[a] - self.avg_cost[book][a]) for a, q in self.positions[book].items())

    def attribution(self) -> dict:
        """P&L attribué : marché, news réelles, et décisions reliées aux services de leur provenance."""
        by_service_all: dict[str, float] = defaultdict(float)
        by_service_compromised: dict[str, float] = defaultdict(float)
        by_agent: dict[str, float] = defaultdict(float)
        market = news = clean_decisions = tainted_decisions = 0.0
        for cause, value in self.contrib.items():
            if cause == "market":
                market += value
            elif cause.startswith("news:"):
                news += value
            elif cause.startswith("exec:"):
                info = self.exec_info.get(cause[5:], {})
                by_agent[info.get("agent", "?")] += value
                for svc in info.get("provenance", []):
                    by_service_all[svc] += value
                compromised = info.get("compromised", [])
                if compromised:
                    tainted_decisions += value
                    for svc in compromised:
                        by_service_compromised[svc] += value / len(compromised)
                else:
                    clean_decisions += value
        for fraud in self.fraud_losses:
            for svc in fraud.get("compromised", ["payment-gateway"]):
                by_service_compromised[svc] -= fraud["amount"] / len(fraud.get("compromised", ["payment-gateway"]))
            for svc in fraud.get("provenance", ["payment-gateway"]):
                by_service_all[svc] -= fraud["amount"]
            by_agent[fraud.get("agent", "payment")] -= fraud["amount"]
        return {
            "market_eur": market,
            "real_news_eur": news,
            "decisions_clean_eur": clean_decisions,
            "decisions_compromised_eur": tainted_decisions,
            "payment_fraud_eur": -self.fraud_total(),
            "by_service_compromised": {k: round(v, 2) for k, v in sorted(by_service_compromised.items())},
            "by_service_all": {k: round(v, 2) for k, v in sorted(by_service_all.items())},
            "by_agent": {k: round(v, 2) for k, v in sorted(by_agent.items())},
            "losses_by_service_eur": {k: round(-v, 2) for k, v in sorted(by_service_compromised.items()) if v < 0},
        }

    def snapshot(self, liquidation_cost: float | None = None) -> dict:
        pos_eur = self.positions_eur()
        books = {}
        for book in self.positions:
            books[book] = {
                "positions_eur": {a: round(v, 2) for a, v in self.positions_eur(book).items()},
                "realized_eur": round(self.realized[book], 2),
                "unrealized_eur": round(self.unrealized(book), 2),
            }
        realized = sum(self.realized.values())
        unrealized = sum(self.unrealized(b) for b in self.positions)
        return {
            "cash_eur": round(self.cash, 2),
            "equity_eur": round(self.equity(), 2),
            "positions_qty": {a: round(self.bank_position(a), 4) for a in self.assets},
            "positions_eur": {a: round(v, 2) for a, v in pos_eur.items()},
            "books": books,
            "pnl": {
                "realized_eur": round(realized, 2),
                "unrealized_eur": round(unrealized, 2),
                "trading_eur": round(self.trading_pnl(), 2),
                "day_eur": round(self.pnl_day(), 2),
                "payment_fraud_eur": round(-self.fraud_total(), 2),
            },
            "exposure": {
                "gross_eur": round(sum(abs(v) for v in pos_eur.values()), 2),
                "net_eur": round(sum(pos_eur.values()), 2),
            },
            "var_99_1d_eur": round(self.var(), 2),
            "liquidation_cost_eur": round(liquidation_cost or 0.0, 2),
            "payments": {"legit_day_eur": round(self.payments_legit_day, 2), "legit_total_eur": round(self.payments_legit_total, 2)},
        }
