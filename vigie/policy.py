"""Oubli, deux témoins indépendants et notifications regroupées par cause."""

from collections import defaultdict


class Policy:
    def __init__(self):
        self.scores = defaultdict(float)
        self.witnesses = defaultdict(dict)
        self.layers = defaultdict(lambda: {str(i): "VERT" for i in range(1, 5)})
        self.levels = defaultdict(lambda: "VERT")
        self.incidents = {}
        self.raw = 0
        self.notifications = 0
        self.sequence = 0
        self.last_tick = 0

    def advance(self, tick):
        elapsed = max(0, tick - self.last_tick)
        for agent in self.scores:
            self.scores[agent] *= 2 ** (-elapsed / 30)
        self.last_tick = max(tick, self.last_tick)
        for a, w in self.witnesses.items():
            self.witnesses[a] = {family: t for family, t in w.items() if tick - t <= 30}
        for a in list(self.levels):
            if self.levels[a] == "ORANGE" and self.scores[a] < 10:
                self.levels[a] = "VERT"
                self.layers[a]["3"] = "VERT"
                self.layers[a]["4"] = "VERT"
                incident = self.incidents.get("agent:" + a)
                if incident:
                    incident["status"] = "resolved"
                    incident["level"] = "VERT"

    def record(
        self,
        agent,
        reason,
        layer,
        tick,
        weight,
        proof=(),
        source=None,
        victim=False,
        forced=None,
    ):
        self.raw += 1
        root = "source:" + source if source else "agent:" + agent
        incident = self.incidents.get(root)
        if (
            not incident
            or incident.get("status") == "resolved"
            or tick - incident["last_tick"] > 30
        ):
            self.sequence += 1
            incident = {
                "id": f"incident-{self.sequence}",
                "root": root,
                "agent": agent,
                "first_tick": tick,
                "last_tick": tick,
                "reasons": [],
                "proof": [],
                "raw_count": 0,
                "weighted": {},
                "level": "VERT",
            }
            self.incidents[root] = incident
        incident["last_tick"] = tick
        incident["raw_count"] += 1
        incident["proof"] = list(dict.fromkeys(incident["proof"] + list(proof)))
        if reason not in incident["reasons"]:
            incident["reasons"].append(reason)
        # Un flot de répétitions du même détecteur ne fabrique pas de nouvelles preuves.
        if (
            not victim
            and weight
            and tick - incident["weighted"].get(reason, -1000) > 30
        ):
            self.scores[agent] = min(100, self.scores[agent] + weight)
            self.witnesses[agent][layer] = tick
            incident["weighted"][reason] = tick
        if reason not in {
            "AGENT_SLOWED",
            "AGENT_RATE_LIMIT",
            "QUARANTINED_AGENT",
            "LIGHT_BLOCKED",
        }:
            self.layers[agent][str(layer)] = (
                "ORANGE"
                if victim
                else "ROUGE"
                if forced in {"QUARANTAINE", "ROUGE"}
                else "ORANGE"
            )
        level = forced or (
            "ORANGE" if victim or self.scores[agent] >= 20 else self.levels[agent]
        )
        previous = incident["level"]
        severity = {"VERT": 0, "ORANGE": 1, "QUARANTAINE": 2, "ROUGE": 3}
        if severity[level] < severity[previous]:
            level = previous
        incident["level"] = level
        if not victim:
            self.levels[agent] = level
        notify = level != previous
        if notify:
            self.notifications += 1
        return incident, notify

    def two_witnesses(self, agent, tick):
        return (
            self.scores[agent] >= 50
            and len(
                {
                    f
                    for f, t in self.witnesses[agent].items()
                    if f in {2, 3, 4} and tick - t <= 30
                }
            )
            >= 2
        )

    def status(self):
        return {
            "counters": {
                "raw_alerts": self.raw,
                "incidents": self.sequence,
                "notifications": self.notifications,
            },
            "incidents": [
                {k: v for k, v in i.items() if k != "weighted"}
                for i in self.incidents.values()
            ],
            "indicators": dict(self.layers),
            "levels": dict(self.levels),
        }

    def checkpoint(self):
        return {
            "scores": dict(self.scores),
            "witnesses": dict(self.witnesses),
            "layers": dict(self.layers),
            "levels": dict(self.levels),
            "incidents": self.incidents,
            "raw": self.raw,
            "notifications": self.notifications,
            "sequence": self.sequence,
            "last_tick": self.last_tick,
        }

    def restore(self, saved):
        self.scores.update(saved["scores"])
        self.witnesses.update(
            {
                a: {int(k): v for k, v in w.items()}
                for a, w in saved["witnesses"].items()
            }
        )
        self.layers.update(saved["layers"])
        self.levels.update(saved["levels"])
        for name in ("incidents", "raw", "notifications", "sequence", "last_tick"):
            setattr(self, name, saved[name])
