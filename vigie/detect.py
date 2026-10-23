"""Indicators layer: what the Vigie measures on the traffic, without the simulator's answer key.

Reads only what a bank would have: the signed bus messages, the CVE feed, the limit use
each agent publishes in its heartbeat and the Vigie's own book of record (vigie/state.py,
rebuilt from signed executions). Never /state, `world:*`, `svc:*:mode`, `tainted`,
`compromised_sources` or `contaminated`. The brake (core.py) refuses actions fed by a service marked
suspect here; improve.py replays this same class on finished runs to score thresholds.
"""

from __future__ import annotations

from collections import defaultdict, deque
import statistics

from sim.config import trader_ids

THRESHOLDS = {
    "price_drift_z": 4.0,  # one-tick return, in standard deviations of the last 50
    "reconciliation_gap_pct": 1.0,  # Risk's cash against the book of record
    "herding_share": 1.0,  # share of traders on the same side of one asset within 3 ticks
    "breach_horizon_ticks": 10,  # warn when a limit is projected to break this soon
}
DRIFT_WINDOW, DRIFT_MIN, HERDING_TICKS, SLOPE_TICKS, REPEAT_TICKS = 50, 20, 3, 10, 10


class Indicators:
    def __init__(self, cfg, thresholds=None):
        self.thresholds = {**THRESHOLDS, **cfg.get("vigie", {}).get("thresholds", {}), **(thresholds or {})}
        self.traders = trader_ids(cfg)
        self.agents = cfg["agents"]
        self.tick = 0
        self.prices, self.z = {}, {}
        self.returns = defaultdict(lambda: deque(maxlen=DRIFT_WINDOW))
        self.suspect = {}
        self.sides = deque()
        self.herding = {}
        self.orders = defaultdict(deque)
        self.record_cash = deque(maxlen=3)
        self.reconciliation = {}
        self.limit_history = defaultdict(lambda: deque(maxlen=SLOPE_TICKS))
        self.limits = {}
        self.last_alert = {}
        self.fresh = []
        self.log = deque(maxlen=200)

    def set(self, rule, value):
        if rule not in self.thresholds:
            raise ValueError(f"unknown threshold {rule}")
        value = float(value)
        if value <= 0:
            raise ValueError("a threshold must be positive")
        old, self.thresholds[rule] = self.thresholds[rule], value
        return old

    def clear(self, service):
        return self.suspect.pop(service, None)

    def suspect_at(self, service, tick):
        flag = self.suspect.get(service)
        return bool(flag) and tick >= flag["since_tick"]

    def alert(self, rule, target, value, text, service=None):
        if self.tick - self.last_alert.get((rule, target), -REPEAT_TICKS) < REPEAT_TICKS:
            return
        self.last_alert[(rule, target)] = self.tick
        if service and service not in self.suspect:
            self.suspect[service] = {"since_tick": self.tick, "rule": rule, "text": text}
        item = {
            "tick": self.tick,
            "rule": rule,
            "target": target,
            "service": service,
            "value": round(value, 2),
            "threshold": self.thresholds.get(rule),
            "text": text,
        }
        self.fresh.append(item)
        self.log.append(item)

    def drain(self):
        out, self.fresh = self.fresh, []
        return out

    def observe(self, msg, view=None):
        self.tick = max(self.tick, msg["tick"])
        kind, sender, p = msg["type"], msg["from"], msg["payload"]
        if kind == "tick" and sender == "clock" and view and isinstance(view.get("cash_eur"), (int, float)):
            self.record_cash.append(view["cash_eur"])
        elif kind == "price_update" and sender == "quant":
            self.drift(p.get("assets", {}))
        elif kind == "alert" and sender == "secu" and p.get("kind") == "cve_published":
            for service in p.get("unpatchable", []):
                self.alert("cve_unpatchable", service, 1, f"{service} is exposed to a published CVE and cannot be patched", service)
        elif kind == "order_proposal" and sender in self.traders:
            self.order(sender, p)
        elif kind == "heartbeat" and sender == "risk" and "cash_eur" in p.get("view", {}):
            self.reconcile(p["view"])
            self.track("risk", self.use(sender, p))
        elif kind == "heartbeat" and (
            sender in self.traders and "positions_eur_seen" in p or sender == "payment" and "committed_day_eur" in p
        ):
            self.track(sender, self.use(sender, p))

    def drift(self, assets):
        for asset, a in assets.items():
            price = a.get("price")
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            prev = self.prices.get(asset)
            self.prices[asset] = price
            if not prev:
                continue
            r = price / prev - 1
            window = self.returns[asset]
            if len(window) >= DRIFT_MIN:
                sd = statistics.pstdev(window)
                if sd > 0:
                    z = abs(r - statistics.fmean(window)) / sd
                    self.z[asset] = z
                    if z > self.thresholds["price_drift_z"]:
                        self.alert("price_drift_z", asset, z, f"{asset} moved {r:+.2%} in one tick ({z:.1f} standard deviations): the price feed is suspect", "market-feed")
            window.append(r)

    def order(self, trader, p):
        tick = self.tick
        self.orders[trader].append((tick, float(p.get("notional_eur", 0))))
        while self.orders[trader] and self.orders[trader][0][0] < tick - 10:
            self.orders[trader].popleft()
        self.sides.append((tick, trader, p.get("asset"), p.get("side")))
        while self.sides and self.sides[0][0] <= tick - HERDING_TICKS:
            self.sides.popleft()
        asset, side = p.get("asset"), p.get("side")
        same = {t for _, t, a, s in self.sides if a == asset and s == side}
        share = len(same) / len(self.traders)
        self.herding[asset] = {"side": side, "share": round(share, 2), "traders": sorted(same)}
        if len(same) > 1 and share >= self.thresholds["herding_share"]:
            self.alert("herding_share", asset, share, f"{len(same)} of {len(self.traders)} traders {side} {asset} within {HERDING_TICKS} ticks despite different profiles")

    def use(self, sender, p):
        """Limit use as the agent itself sees it: a fooled agent shows a fooled figure."""
        pct = lambda x, limit: round(100 * x / limit, 1) if limit else 0.0
        if sender == "risk":
            v, r = p["view"], self.agents["risk"]
            return {
                "exposure_pct": pct(v.get("gross_eur", 0.0), r["max_exposure_eur"]),
                "var_pct": pct(v.get("var_eur", 0.0), r["max_var_eur"]),
                "daily_loss_pct": pct(max(0.0, -v.get("pnl_day_eur", 0.0)), r["max_daily_loss_eur"]),
                "liquidity_floor_pct": pct(r["min_cash_eur"], max(float(v["cash_eur"]), 1.0)),
            }
        if sender == "payment":
            return {"daily_pct": pct(p["committed_day_eur"], self.agents["payment"]["max_daily_eur"])}
        t = self.agents["trader"]
        return {
            "max_position_pct": pct(max(map(abs, p["positions_eur_seen"].values()), default=0.0), t["max_position_eur"]),
            "daily_loss_pct": pct(max(0.0, -p.get("pnl_day_eur_seen", 0.0)), t["max_daily_loss_eur"]),
        }

    def track(self, agent, values):
        for name, pct in values.items():
            history = self.limit_history[(agent, name)]
            history.append((self.tick, pct))
            ttb = None
            if len(history) >= 3 and pct < 100:
                (t0, p0), (t1, p1) = history[0], history[-1]
                slope = (p1 - p0) / max(1, t1 - t0)
                if slope > 0:
                    ttb = round((100 - pct) / slope, 1)
            self.limits.setdefault(agent, {})[name] = {"pct": pct, "ticks_to_breach": ttb}
            if ttb is not None and ttb <= self.thresholds["breach_horizon_ticks"]:
                self.alert("breach_horizon_ticks", f"{agent}.{name}", ttb, f"{agent} {name} is at {pct:.0f}% and on course to break its limit in {ttb:.0f} ticks")

    def reconcile(self, view):
        if not self.record_cash:
            return
        seen = float(view["cash_eur"])
        record = min(self.record_cash, key=lambda c: abs(c - seen))
        gap = abs(seen - record) / abs(record) * 100 if record else 0
        self.reconciliation = {"risk_cash_eur": round(seen, 2), "record_cash_eur": round(record, 2), "gap_pct": round(gap, 2)}
        if gap > self.thresholds["reconciliation_gap_pct"]:
            self.alert("reconciliation_gap_pct", "risk", gap, f"Risk sees {seen:,.0f} EUR of cash, the book of record says {record:,.0f} EUR ({gap:.1f}% apart): core-ledger is suspect", "core-ledger")

    def snapshot(self):
        return {
            "tick": self.tick,
            "thresholds": self.thresholds,
            "suspect_services": self.suspect,
            "price_drift_z": {a: round(z, 2) for a, z in self.z.items()},
            "herding": self.herding,
            "reconciliation": self.reconciliation,
            "limits": self.limits,
            "orders_10_ticks_eur": {t: round(sum(n for _, n in q), 2) for t, q in self.orders.items()},
            "alerts": list(self.log)[-50:],
        }
