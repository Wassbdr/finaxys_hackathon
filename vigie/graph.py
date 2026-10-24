"""Layer 1: the live dependency graph, rebuilt from what crosses the bus.

What agents declare in their heartbeats (`depends_on`) is kept apart and never trusted.
An agent → service edge is observed when one of the agent's decisions carries a service
response that none of its parent messages carried (the agent made that call itself), or
when /graph's call counters show it (network telemetry). /graph's own `declared`,
`hidden`, `contaminated` and `mode` fields are not read.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math

from sim.config import agent_ids
from vigie.finding import Finding

DECISIONS = {
    "price_update",
    "order_proposal",
    "payment_proposal",
    "compliance_decision",
    "risk_decision",
    "execution",
}
EVIDENCE = 5


def services_of(msg):
    return {
        (e["service"], e.get("response_hash"))
        for e in msg.get("data_provenance", [])
        if isinstance(e, dict) and isinstance(e.get("service"), str)
    }


def number(value, default=0.0):
    """An agent's figure is data: a bad one counts as the default, it never stops the Vigie."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def executed_eur(msg):
    p = msg["payload"]
    if msg["type"] != "execution" or p.get("status") != "executed":
        return 0.0
    return number(p.get("notional_eur") or p.get("amount_eur"))


class Graph:
    def __init__(self, cfg):
        self.agents = [*agent_ids(cfg), "executor"]
        self.services = {sid: dict(meta) for sid, meta in cfg["services"].items()}
        self.declared = {}
        self.declared_by = {}  # agent -> heartbeat that made the declaration
        self.uses = defaultdict(list)  # (agent, service) -> ids of messages proving the call
        self.calls = {}  # (agent, service) -> calls counted by /graph
        self.flows = Counter()  # (upstream agent, downstream agent) through depends_on
        self.unlinked = set()  # flows that reuse another agent's data without a depends_on
        self.first = {}  # (service, response_hash) -> agent that published it first
        self.messages = {}  # id -> (sender, depends_on, {(service, hash)})
        self.eur = defaultdict(float)  # service -> executed EUR whose provenance holds it
        self.suspects = {}
        self.reported = set()
        self.fresh = []
        self.tick = 0

    def observe(self, msg):
        self.tick = max(self.tick, msg["tick"])
        kind, sender, p = msg["type"], msg["from"], msg["payload"]
        if kind == "heartbeat" and sender in self.agents and isinstance(p.get("depends_on"), list):
            self.declared[sender] = {s for s in p["depends_on"] if isinstance(s, str)}
            self.declared_by[sender] = msg["message_id"]
            for agent, sid in self.observed():
                if agent == sender:
                    self.check(agent, sid)
        elif kind == "service_status" and isinstance(p.get("meta"), dict) and isinstance(p.get("service"), str):
            self.services.setdefault(p["service"], {}).update(p["meta"])
        elif kind == "alert" and sender == "secu" and p.get("kind") == "cve_published":
            for sid in p.get("affected_services") or []:
                if isinstance(sid, str):
                    self.suspect(sid, "cve_published", [msg["message_id"]])
        if kind not in DECISIONS or sender not in self.agents:
            return
        entries = services_of(msg)
        parents = [self.messages.get(i) for i in msg["depends_on"]]
        self.messages[msg["message_id"]] = (sender, list(msg["depends_on"]), entries)
        for sid in {s for s, _ in entries}:
            self.eur[sid] += executed_eur(msg)
        if None in parents:
            return  # an unknown parent hides who made which call
        inherited = set().union(*(e for _, _, e in parents))
        ups = {up for up, _, _ in parents}
        direct = set()
        for entry in entries - inherited:
            # A response first published by another agent was read off the bus, not fetched.
            origin = self.first.setdefault(entry, sender)
            if origin == sender:
                direct.add(entry[0])
            else:
                ups.add(origin)
                self.unlinked.add((origin, sender))
        for up in ups - {sender}:
            self.flows[(up, sender)] += 1
        for sid in sorted(direct):
            if len(self.uses[(sender, sid)]) < 50:
                self.uses[(sender, sid)].append(msg["message_id"])
            self.check(sender, sid)

    def topology(self, graph):
        """Cross with /graph's service call counters, the bank's network telemetry."""
        for e in (graph or {}).get("edges", []):
            if e.get("kind") == "data" and not e.get("transitive") and e.get("calls"):
                if e.get("source") in self.agents and e.get("target") in self.services:
                    self.calls[(e["source"], e["target"])] = int(e["calls"])
                    self.check(e["source"], e["target"])

    def observed(self):
        return sorted({*self.uses, *self.calls})

    def patchable(self, sid):
        return self.services.get(sid, {}).get("patchable", True)

    def check(self, agent, sid):
        if agent not in self.declared or sid in self.declared[agent]:
            return
        if ("hidden", agent, sid) in self.reported:
            return
        self.reported.add(("hidden", agent, sid))
        seen_by = [n for n, d in (("bus", self.uses), ("telemetry", self.calls)) if (agent, sid) in d]
        self.fresh.append(
            Finding(
                self.tick,
                1,
                "hidden_dependency",
                f"{agent}->{sid}",
                "warning" if self.patchable(sid) else "critical",
                [self.declared_by[agent], *self.uses.get((agent, sid), [])[:EVIDENCE]],
                1.0,
                {
                    "agent": agent,
                    "service": sid,
                    "declared": sorted(self.declared[agent]),
                    "seen_by": seen_by,
                    "text": f"{agent} calls {sid} but declares only {', '.join(sorted(self.declared[agent])) or 'nothing'}",
                },
            )
        )

    def downstream(self, start):
        reached, todo = set(start), list(start)
        while todo:
            node = todo.pop()
            for up, down in self.flows:
                if up == node and down not in reached:
                    reached.add(down)
                    todo.append(down)
        return reached

    def radius(self, sid):
        direct = {a for a, s in self.observed() if s == sid}
        transitive = self.downstream(direct) - direct
        return {
            "service": sid,
            "direct": sorted(direct),
            "transitive": sorted(transitive),
            "agents": len(direct) + len(transitive),
            "share": round((len(direct) + len(transitive)) / len(self.agents), 3),
            "eur": round(self.eur[sid], 2),
            "patchable": self.patchable(sid),
        }

    def suspect(self, sid, reason, evidence=()):
        """A service under doubt (CVE, data anomaly): report who and how much depends on it."""
        # One report per service and tick: detect.py's alert on the same CVE would only repeat it.
        if (sid, reason) in self.suspects or any(s == sid and t == self.tick for (s, _), t in self.suspects.items()):
            return
        r = self.radius(sid)
        self.suspects[(sid, reason)] = self.tick
        recent = [i for (_, s), ids in self.uses.items() if s == sid for i in ids[-2:]]
        self.fresh.append(
            Finding(
                self.tick,
                1,
                "impact_radius",
                sid,
                "warning" if r["patchable"] else "critical",
                [*evidence, *recent][: EVIDENCE + len(evidence)],
                r["share"],
                {
                    **r,
                    "reason": reason,
                    "text": f"{sid} ({reason}): {len(r['direct'])} agents call it, {len(r['transitive'])} more act on its data, {r['eur']:,.0f} EUR executed on it",
                },
            )
        )

    def drain(self):
        out, self.fresh = self.fresh, []
        return out

    def snapshot(self):
        edges = []
        for agent, sid in self.observed():
            edges.append(
                {
                    "source": agent,
                    "target": sid,
                    "kind": "data",
                    "declared": sid in self.declared.get(agent, set()),
                    "observed": True,
                    "hidden": agent in self.declared and sid not in self.declared[agent],
                    "messages": len(self.uses.get((agent, sid), [])),
                    "calls": self.calls.get((agent, sid), 0),
                }
            )
        for agent, deps in sorted(self.declared.items()):
            for sid in sorted(deps):
                if (agent, sid) not in self.uses and (agent, sid) not in self.calls:
                    edges.append({"source": agent, "target": sid, "kind": "data", "declared": True, "observed": False, "hidden": False, "messages": 0, "calls": 0})
        edges += [
            {"source": up, "target": down, "kind": "flow", "messages": n, "linked": (up, down) not in self.unlinked}
            for (up, down), n in sorted(self.flows.items())
        ]
        return {
            "tick": self.tick,
            "nodes": [
                *({"id": a, "kind": "agent", "declared": sorted(self.declared.get(a, []))} for a in self.agents),
                *(
                    {"id": sid, "kind": "service", "version": meta.get("version"), "patchable": self.patchable(sid), "radius": self.radius(sid)}
                    for sid, meta in self.services.items()
                ),
            ],
            "edges": edges,
            "suspects": [{"service": s, "reason": r, "tick": t} for (s, r), t in self.suspects.items()],
        }
