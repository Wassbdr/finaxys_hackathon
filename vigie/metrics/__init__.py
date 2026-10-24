"""Layer 1 as one object fed message by message, live or replayed on a journal.

Inputs are only what the plan allows: accepted bus messages (heartbeats and llm_trace
included), the CVE alert, services' /meta and /graph's call counters. Never /state.
"""

from __future__ import annotations

from collections import defaultdict, deque
import statistics

from vigie.finding import Finding
from vigie.graph import Graph
from vigie.metrics.agent import AgentMetrics
from vigie.metrics.exec import ExecMetrics
from vigie.metrics.system import SystemMetrics

CALM_TICKS, WINDOW, Z = 30, 10, 4.0
FLOORS = {"decisions": 0.05, "data_age": 1.0, "silent_ticks": 1.0, "order_eur": 1e5}


class Baseline:
    """Rolling mean of each series, z-scored against its first calm ticks."""

    def __init__(self, stats=None):
        self.stats = dict(stats or {})
        self.learning = not self.stats
        self.samples = defaultdict(list)
        self.windows = defaultdict(lambda: deque(maxlen=WINDOW))
        self.z = {}
        self.open = set()

    def update(self, tick, values, calm=True):
        out = []
        for name, (value, evidence) in values.items():
            window = self.windows[name]
            window.append(value)
            m = statistics.fmean(window)
            if self.learning:
                self.samples[name].append(m)
                continue
            if name not in self.stats:
                continue
            mu, sd = self.stats[name]
            z = (m - mu) / max(sd, 0.1 * abs(mu), FLOORS.get(name.rsplit(".", 1)[-1], 1e-6))
            self.z[name] = round(z, 2)
            if abs(z) > Z and name not in self.open:
                self.open.add(name)
                target, metric = name.rsplit(".", 1)
                out.append(Finding(tick, 1, "anomaly", target, "warning", evidence, round(abs(z), 2), {"metric": metric, "value": round(m, 3), "mean": round(mu, 3), "sd": round(sd, 3), "z": round(z, 2), "text": f"{target} {metric} at {m:,.2f} against a calm {mu:,.2f} (z {z:+.1f})"}))
            elif abs(z) < Z / 2:
                self.open.discard(name)
        if self.learning and (tick >= CALM_TICKS - 1 or not calm):
            self.learning = False
            self.stats = {n: (statistics.fmean(s), statistics.pstdev(s)) for n, s in self.samples.items()}
        return out

    def snapshot(self):
        return {
            "learning": self.learning,
            "calm_ticks": CALM_TICKS,
            "window": WINDOW,
            "z_threshold": Z,
            "series": {n: {"mean": round(mu, 3), "sd": round(sd, 3), "z": self.z.get(n)} for n, (mu, sd) in sorted(self.stats.items())},
            "anomalous": sorted(self.open),
        }


class Layer1:
    def __init__(self, cfg, baseline=None):
        self.graph = Graph(cfg)
        self.agents = AgentMetrics(cfg)
        self.system = SystemMetrics(cfg, self.graph)
        self.ceo = ExecMetrics(self.graph)
        self.baseline = Baseline(baseline)
        self.calm = True
        self.tick = None
        self.fresh = []

    def observe(self, msg):
        if self.tick is None:
            self.tick = msg["tick"]
        elif msg["tick"] > self.tick:
            self.close(self.tick)
            self.tick = msg["tick"]
        p = msg["payload"]
        if msg["type"] == "alert" and msg["from"] == "secu" and p.get("kind") == "cve_published":
            self.calm = False
        if msg["type"] == "service_status" and p.get("mode") in {"down", "stale"}:
            self.calm = False
        for part in (self.graph, self.agents, self.system, self.ceo):
            part.observe(msg)
        self.collect()

    def close(self, tick):
        self.fresh += self.baseline.update(tick, self.agents.series(), self.calm)
        self.agents.close()
        self.system.close()

    def topology(self, graph):
        self.graph.topology(graph)
        self.collect()

    def suspect(self, service, reason, evidence=()):
        self.graph.suspect(service, reason, evidence)
        self.collect()

    def collect(self):
        for part in (self.graph, self.agents, self.ceo):
            self.fresh += part.drain()

    def drain(self):
        out, self.fresh = self.fresh, []
        return out

    def metrics(self):
        return {
            "tick": self.tick,
            "agents": self.agents.snapshot(),
            "system": self.system.snapshot(),
            "ceo": self.ceo.snapshot(),
            "baseline": self.baseline.snapshot(),
        }
