"""Metrics between agents: influence, cascades, single points of failure, concentration, desk exposure."""

from __future__ import annotations

from collections import Counter

from sim.config import trader_ids
from vigie.graph import DECISIONS
from vigie.metrics.agent import PROPOSALS, amounts, light


def herfindahl(counts):
    total = sum(counts.values())
    return round(sum((n / total) ** 2 for n in counts.values()), 3) if total else None


class SystemMetrics:
    def __init__(self, cfg, graph):
        self.graph = graph
        self.traders = trader_ids(cfg)
        self.max_exposure = cfg["agents"]["risk"]["max_exposure_eur"]
        self.acting, self.previous = set(), set()
        self.ticks = 0
        self.acted = Counter()  # ticks in which an agent proposed
        self.after = Counter()  # ticks that follow one where the agent proposed
        self.joint = Counter()  # (a, b): a proposed at t-1, b at t
        self.depth = {}
        self.depths = []
        self.feeds = Counter()  # decisions fed by each service
        self.models = Counter()
        self.positions = {}
        self.beat_ids = {}

    def observe(self, msg):
        kind, sender, p = msg["type"], msg["from"], msg["payload"]
        if kind in PROPOSALS:
            self.acting.add(sender)
        if kind == "llm_trace":
            self.models[str(p.get("model"))] += 1
        if kind == "heartbeat" and sender in self.traders and amounts(p.get("positions_eur_seen")):
            self.positions[sender] = amounts(p["positions_eur_seen"])
            self.beat_ids[sender] = msg["message_id"]
        if kind in DECISIONS and msg["message_id"] in self.graph.messages:
            _, parents, entries = self.graph.messages[msg["message_id"]]
            self.depth[msg["message_id"]] = 1 + max((self.depth.get(i, 0) for i in parents), default=0)
            self.feeds.update({s for s, _ in entries})
            if kind == "execution":
                self.depths.append(self.depth[msg["message_id"]])

    def close(self):
        self.ticks += 1
        for a in self.previous:
            self.after[a] += 1
            for b in self.acting - {a}:
                self.joint[(a, b)] += 1
        self.acted.update(self.acting)
        self.previous, self.acting = self.acting, set()

    def desk(self):
        assets = Counter()
        for book in self.positions.values():
            assets.update(book)
        gross = sum(abs(v) for v in assets.values())
        pct = 100 * gross / self.max_exposure
        return {
            "by_asset_eur": {a: round(v, 2) for a, v in sorted(assets.items())},
            "by_trader_eur": self.positions,
            "gross_eur": round(gross, 2),
            "limit_eur": self.max_exposure,
            "pct": round(pct, 1),
            "light": light(pct),
        }

    def snapshot(self):
        influence = []
        for (a, b), n in sorted(self.joint.items()):
            p = n / self.after[a]
            base = self.acted[b] / self.ticks
            influence.append({"from": a, "to": b, "p": round(p, 3), "base": round(base, 3), "lift": round(p / base, 2) if base else None, "support": self.after[a]})
        g = self.graph
        reach = {sid: g.radius(sid)["share"] for sid in g.services}
        reach.update({a: round(len(g.downstream({a}) - {a}) / len(g.agents), 3) for a in g.agents})
        return {
            "influence": sorted(influence, key=lambda i: -(i["lift"] or 0))[:20],
            "cascade_depth": {"max": max(self.depths, default=0), "mean": round(sum(self.depths) / len(self.depths), 2) if self.depths else None, "executions": len(self.depths)},
            "centrality": dict(sorted(reach.items(), key=lambda kv: -kv[1])),
            "single_points_of_failure": sorted(n for n, share in reach.items() if share >= 0.5),
            "concentration": {"services_hhi": herfindahl(self.feeds), "services": dict(self.feeds), "models_hhi": herfindahl(self.models), "models": dict(self.models)},
            "desk_exposure": self.desk(),
        }
