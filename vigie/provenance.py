"""Graphe de preuves : une donnée piégée contamine l'action, pas son destinataire."""


class Provenance:
    def __init__(self, services):
        self.services = set(services)
        self.messages = {}
        self.rejected = set()
        self.isolated = {}
        self.verified = {s: 0 for s in services}
        self.historical = {}

    def add(self, msg):
        self.messages[msg["message_id"]] = msg
        sources, _ = self.sources(msg["message_id"])
        bad = sources & set(self.isolated)
        if bad:
            self.historical[msg["message_id"]] = bad

    def reject(self, id_):
        if id_:
            self.rejected.add(id_)

    def isolate(self, service, tick, proof=()):
        if service in self.isolated:
            return
        self.isolated[service] = {
            "since_tick": tick,
            "last_verified_tick": self.verified.get(service, 0),
            "proof": list(proof),
        }
        for id_, msg in self.messages.items():
            sources, _ = self.sources(id_)
            if service in sources and msg["tick"] >= self.verified.get(service, 0):
                self.historical.setdefault(id_, set()).add(service)

    def release(self, service, tick):
        self.isolated.pop(service, None)
        self.verified[service] = tick

    def sources(self, id_):
        pending = [id_]
        seen = set()
        sources = set()
        invalid = []
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in self.rejected:
                invalid.append(current)
            msg = self.messages.get(current)
            if not msg:
                invalid.append(current)
                continue
            for e in msg.get("data_provenance", []):
                if isinstance(e, dict) and isinstance(e.get("service"), str):
                    sources.add(e["service"])
            pending.extend(msg.get("depends_on", []))
        return sources, invalid

    def contaminated(self, id_):
        sources, invalid = self.sources(id_)
        inherited = set()
        pending = [id_]
        visited = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            inherited.update(self.historical.get(current, set()))
            pending.extend(self.messages.get(current, {}).get("depends_on", []))
        return sorted(
            inherited | sources & set(self.isolated) | (sources - self.services)
        ), invalid

    def graph(self):
        nodes = [
            {
                "id": sid,
                "kind": "service",
                "mode": "isolated" if sid in self.isolated else "normal",
                "contaminated": sid in self.isolated,
            }
            for sid in sorted(self.services | set(self.isolated))
        ]
        edges = []
        for id_, msg in self.messages.items():
            taint, invalid = self.contaminated(id_)
            nodes.append(
                {
                    "id": id_,
                    "kind": "message",
                    "agent": msg["from"],
                    "tick": msg["tick"],
                    "type": msg["type"],
                    "contaminated": bool(taint or invalid),
                    "sources": taint,
                }
            )
            edges.extend(
                {
                    "source": parent,
                    "target": id_,
                    "kind": "message",
                    "type": "depends_on",
                    "transitive": True,
                }
                for parent in msg["depends_on"]
            )
            edges.extend(
                {
                    "source": e["service"],
                    "target": id_,
                    "kind": "data",
                    "response_hash": e.get("response_hash"),
                    "transitive": False,
                }
                for e in msg["data_provenance"]
                if isinstance(e, dict) and e.get("service")
            )
        return {"nodes": nodes, "edges": edges}

    def agent_graph(self):
        # Le contrat dashboard attend des agents/services : les preuves détaillées
        # restent dans le journal, sans republier tout son historique à chaque tick.
        agents = {
            m["from"]
            for m in self.messages.values()
            if not m["from"].startswith("svc:")
        }
        nodes = {
            sid: {
                "id": sid,
                "kind": "service",
                "mode": "isolated" if sid in self.isolated else "normal",
                "contaminated": sid in self.isolated,
            }
            for sid in self.services | set(self.isolated)
        }
        nodes.update(
            {
                a: {
                    "id": a,
                    "kind": "agent",
                    "role": a.rsplit("-", 1)[0] if a.startswith("trader-") else a,
                    "contaminated": False,
                }
                for a in agents
            }
        )
        edges = {}

        def edge(source, target, kind, id_, tainted, transitive):
            key = (source, target, kind)
            record = edges.setdefault(
                key,
                {
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "count": 0,
                    "messages": 0,
                    "contaminated": False,
                    "transitive": transitive,
                    "proof": [],
                },
            )
            record["count"] += 1
            record["messages"] += 1
            record["contaminated"] |= tainted
            record["proof"] = (record["proof"] + [id_])[-3:]

        for id_, m in self.messages.items():
            bad, invalid = self.contaminated(id_)
            sources, _ = self.sources(id_)
            if m["from"] in nodes:
                nodes[m["from"]]["contaminated"] |= bool(bad or invalid)
            for source in sources:
                edge(
                    source, m["from"], "data", id_, source in bad, bool(m["depends_on"])
                )
            for parent in m["depends_on"]:
                previous = self.messages.get(parent)
                if previous:
                    edge(
                        previous["from"],
                        m["from"],
                        "message",
                        id_,
                        bool(bad or invalid),
                        True,
                    )
        return {
            "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": list(edges.values()),
        }
