"""Herding: is the desk still several independent traders, or one herd?

Fed bus messages one by one (order_proposal, execution, price_update, clock tick), so it
replays on a journal. Three figures over the last WINDOW ticks:
- mimetism index: share of the other traders on the same asset and side within SAME_SIDE_TICKS
  of an order, divided by the share a calm desk shows;
- effective number of independent agents: participation ratio of the eigenvalues of the
  correlation matrix of the traders' decisions;
- reflexive loop: a sale, then a lower price published by the Quant, then a new sale fed by that price.
Justification similarity (llm_trace) is left out: every recorded trace comes from the rules
backend, and without an LLM the order justification is a template that prints the herding knob.
"""

from __future__ import annotations

from collections import deque
import math

from sim.config import trader_ids
from vigie.finding import Finding

SAME_SIDE_TICKS = 5
FLOW_TICKS = 10  # one order stands for the trader's view until its cooldown ends
WINDOW = 50
LOOP_TICKS = 10  # a sale's price impact is looked for this long
REPEAT_TICKS = 10
MIN_ORDERS = 6  # fewer orders in the window say nothing about the desk
# Same-side share of the 300-tick calm reference that compare.py learns (seed 42, herding 0.3).
BASELINE_SHARE = 0.125
THRESHOLDS = {"mimetism_index": 3.0, "effective_share": 0.6, "loop_depth": 4}


def correlation(u, v):
    mu, mv = sum(u) / len(u), sum(v) / len(v)
    su = sum((a - mu) ** 2 for a in u)
    sv = sum((b - mv) ** 2 for b in v)
    if not su or not sv:
        return 0.0
    return sum((a - mu) * (b - mv) for a, b in zip(u, v)) / math.sqrt(su * sv)


class Herding:
    def __init__(self, cfg, baseline=BASELINE_SHARE):
        self.traders = trader_ids(cfg)
        self.assets = list(cfg["market"]["assets"])
        self.baseline = baseline
        self.tick = 0
        self.orders = deque()  # (tick, trader, asset, side, notional, message_id)
        self.sales = {}  # asset -> (tick, execution id, mid price, proposal id)
        self.drops = {}  # (price_update id, asset) -> (tick, execution id, change, proposal id, price_update id)
        self.loops = {}  # proposal id -> (tick, depth, message chain)
        self.links, self.max_depth, self.last_link = 0, 0, None
        self.share, self.index, self.effective, self.rho = 0.0, 0.0, float(len(self.traders)), {}
        self.worst = {"mimetism_index": 0.0, "mimetism_tick": None, "effective_agents": float(len(self.traders)), "effective_tick": None}
        self.findings = deque(maxlen=50)
        self.fresh = []
        self.last_finding = {}

    def observe(self, msg):
        self.tick = max(self.tick, msg["tick"])
        kind, sender, p = msg["type"], msg["from"], msg["payload"]
        if kind == "tick" and sender == "clock":
            self.update()
        elif kind == "order_proposal" and sender in self.traders:
            self.order(msg, p)
        elif kind == "execution" and p.get("kind") == "order" and p.get("status") == "executed" and p.get("side") == "sell":
            self.sales[p["asset"]] = (msg["tick"], msg["message_id"], p.get("mid_price"), p.get("proposal_id"))
        elif kind == "price_update" and sender == "quant":
            self.price(msg, p.get("assets", {}))

    def order(self, msg, p):
        asset, side = p.get("asset"), 1 if p.get("side") == "buy" else -1
        self.orders.append((msg["tick"], msg["from"], asset, side, float(p.get("notional_eur", 0)), msg["message_id"]))
        drop = next((self.drops[(d, asset)] for d in msg.get("depends_on", []) if (d, asset) in self.drops), None)
        if side > 0 or not drop:
            return
        _, sale, change, proposal, update = drop
        _, depth, chain = self.loops.get(proposal, (0, 0, []))
        depth, chain = depth + 1, [*chain, sale, update, msg["message_id"]][-12:]
        self.loops[p.get("proposal_id")] = (msg["tick"], depth, chain)
        self.links += 1
        self.max_depth = max(self.max_depth, depth)
        self.last_link = {"tick": msg["tick"], "asset": asset, "sale": sale, "drop": update, "drop_pct": round(change * 100, 2), "sell": msg["message_id"], "depth": depth}
        if depth >= THRESHOLDS["loop_depth"]:
            self.find("reflexive_loop", asset, depth, chain, {"drop_pct": round(change * 100, 2)})

    def price(self, msg, assets):
        for asset, (tick, sale, mid, proposal) in self.sales.items():
            price = assets.get(asset, {}).get("price")
            if self.tick - tick <= LOOP_TICKS and isinstance(price, (int, float)) and mid and price < mid:
                self.drops[(msg["message_id"], asset)] = (msg["tick"], sale, price / mid - 1, proposal, msg["message_id"])

    def update(self):
        start = self.tick - WINDOW
        while self.orders and self.orders[0][0] < start - FLOW_TICKS:
            self.orders.popleft()
        self.drops = {k: v for k, v in self.drops.items() if v[0] >= self.tick - LOOP_TICKS}
        self.loops = {k: v for k, v in self.loops.items() if v[0] >= start}
        recent = [o for o in self.orders if start <= o[0] < self.tick]
        if len(recent) < MIN_ORDERS:
            self.share, self.index, self.effective, self.rho = 0.0, 0.0, float(len(self.traders)), {}
            return
        self.mimetism(recent)
        self.independence(start)

    def mimetism(self, recent):
        n = len(self.traders) - 1
        joined, evidence, assets = [], set(), {}
        for t, trader, asset, side, _, mid in recent:
            peers = [o for o in recent if o[1] != trader and o[2] == asset and o[3] == side and abs(o[0] - t) < SAME_SIDE_TICKS]
            joined.append(len({o[1] for o in peers}) / n if n else 0.0)
            if peers:
                evidence |= {mid, *(o[5] for o in peers)}
                assets[asset] = assets.get(asset, 0) + 1
        self.share = sum(joined) / len(joined) if joined else 0.0
        self.index = self.share / self.baseline if self.baseline else 0.0
        if self.index > self.worst["mimetism_index"]:
            self.worst.update(mimetism_index=round(self.index, 2), mimetism_tick=self.tick)
        if self.index >= THRESHOLDS["mimetism_index"]:
            self.find("mimetism", max(assets, key=assets.get), self.index, sorted(evidence)[-12:], {"same_side_share": round(self.share, 3), "baseline_share": self.baseline})

    def independence(self, start):
        # A trader's decision on an asset at a tick: its signed flow over the last FLOW_TICKS.
        width = len(self.assets)
        col = {a: i for i, a in enumerate(self.assets)}
        flows = {t: [0.0] * (WINDOW * width) for t in self.traders}
        for t, trader, asset, side, notional, _ in self.orders:
            if trader in flows and asset in col:
                for tick in range(max(t, start), min(t + FLOW_TICKS, self.tick)):
                    flows[trader][(tick - start) * width + col[asset]] += side * notional
        n = len(self.traders)
        # Opposite views offset each other: only co-movement costs the desk its independence.
        self.rho = {
            a: {b: round(max(0.0, correlation(flows[a], flows[b])), 3) for b in self.traders[i + 1 :]}
            for i, a in enumerate(self.traders[:-1])
        }
        # (Σλ)² / Σλ², with Σλ = trace = n and Σλ² = sum of the squared entries of the matrix.
        self.effective = n * n / (n + 2 * sum(r * r for row in self.rho.values() for r in row.values()))
        if self.effective < self.worst["effective_agents"]:
            self.worst.update(effective_agents=round(self.effective, 2), effective_tick=self.tick)
        if self.effective <= THRESHOLDS["effective_share"] * n:
            evidence = [o[5] for o in self.orders if o[0] >= start][-12:]
            self.find("effective_agents", "desk", self.effective, evidence, {"traders": n, "correlation": self.rho})

    def find(self, kind, target, score, evidence, detail):
        if self.tick - self.last_finding.get(kind, -REPEAT_TICKS) < REPEAT_TICKS:
            return
        self.last_finding[kind] = self.tick
        self.findings.append(Finding(self.tick, 3, kind, target, "warning", list(evidence), round(score, 2), detail))
        self.fresh.append(self.findings[-1])

    def drain(self):
        out, self.fresh = self.fresh, []
        return out

    def snapshot(self):
        return {
            "tick": self.tick,
            "window_ticks": WINDOW,
            "traders": len(self.traders),
            "mimetism_index": round(self.index, 2),
            "same_side_share": round(self.share, 3),
            "baseline_share": self.baseline,
            "effective_agents": round(self.effective, 2),
            "correlation": self.rho,
            "worst": dict(self.worst),
            "reflexive_loop": {"links": self.links, "max_depth": self.max_depth, "last": self.last_link},
            "findings": [f.as_dict() for f in self.findings][-20:],
        }
