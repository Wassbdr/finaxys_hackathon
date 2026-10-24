"""Explainable rolling detector for repeated synchronized order behavior."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

import networkx as nx


class CoordinationDetector:
    sync_window_ns = 150_000_000
    evidence_window_ns = 20_000_000_000
    pair_threshold = 3

    def __init__(self, agents: list[dict[str, Any]]) -> None:
        self.agent_rows = agents
        self.recent: deque[dict[str, Any]] = deque()
        self.matches: dict[tuple[int, int], deque[dict[str, Any]]] = defaultdict(deque)
        self.trades: dict[tuple[int, int], deque[dict[str, Any]]] = defaultdict(deque)
        self.triggered = False

    def process(self, event: dict[str, Any]) -> dict[str, Any] | None:
        ts = int(event["ts"])
        if event["event_type"] == "EXEC":
            trader, counterparty = int(event["trader_id"]), int(event.get("counterparty_id", 0))
            if counterparty and trader < counterparty:
                pair = (trader, counterparty)
                self.trades[pair].append({"ts": ts, "event_ids": [int(event["id"])], "qty": int(event["qty"])})
            self._prune(ts)
            return None
        if event["event_type"] not in ("NEW", "CANCEL") or int(event.get("side", 0)) == 0:
            self._prune(ts)
            return None
        while self.recent and ts - int(self.recent[0]["ts"]) > self.sync_window_ns:
            self.recent.popleft()
        while self.recent and ts - int(self.recent[0]["ts"]) > self.evidence_window_ns:
            self.recent.popleft()
        for previous in self.recent:
            if (int(previous["trader_id"]) == int(event["trader_id"])
                    or previous["event_type"] != event["event_type"]
                    or int(previous["side"]) != int(event["side"])):
                continue
            pair = tuple(sorted((int(previous["trader_id"]), int(event["trader_id"]))))
            self.matches[pair].append({"ts": ts, "event_ids": [int(previous["id"]), int(event["id"])],
                                       "event_type": event["event_type"], "side": int(event["side"]),
                                       "lag_ms": round((ts - int(previous["ts"])) / 1_000_000, 1)})
        self.recent.append(event)
        self._prune(ts)
        graph = self.graph()
        if self.triggered:
            return None
        behavior = self.behavior_graph()
        active = [component for component in nx.connected_components(behavior) if len(component) >= 3]
        if not active:
            return None
        component = max(active, key=len)
        behavior_sub = behavior.subgraph(component).copy()
        if behavior_sub.number_of_edges() < 2:
            return None
        self.triggered = True
        evidence = []
        for left, right, attrs in behavior_sub.edges(data=True):
            evidence.extend(attrs["matches"])
        evidence.sort(key=lambda row: row["ts"])
        context_graph = graph.subgraph(component).copy()
        event_ids = {event_id for row in evidence for event_id in row["event_ids"]}
        for _, _, attrs in context_graph.edges(data=True):
            if attrs.get("kind") == "counterparty":
                event_ids.update(event_id for row in attrs["matches"] for event_id in row["event_ids"])
        return {"agents": sorted(int(agent) for agent in component), "graph": self.graph_payload(context_graph),
                "event_ids": sorted(event_ids),
                "match_count": sum(int(attrs["weight"]) for _, _, attrs in behavior_sub.edges(data=True)),
                "first_ts": min(row["ts"] for row in evidence), "last_ts": max(row["ts"] for row in evidence)}

    def _prune(self, ts: int) -> None:
        for collection in (self.matches, self.trades):
            for pair, items in list(collection.items()):
                while items and ts - int(items[0]["ts"]) > self.evidence_window_ns:
                    items.popleft()
                if not items:
                    collection.pop(pair, None)

    def behavior_graph(self) -> nx.Graph:
        graph = nx.Graph()
        for (left, right), matches in self.matches.items():
            if len(matches) >= self.pair_threshold:
                graph.add_edge(left, right, weight=len(matches), matches=list(matches),
                               reason="Repeated same-side orders or cancellations within 150 ms")
        return graph

    def graph(self) -> nx.MultiGraph:
        graph = nx.MultiGraph()
        for agent in self.agent_rows:
            graph.add_node(int(agent["id"]), label=agent["name"], role=agent["role"])
        for (left, right), matches in self.matches.items():
            if len(matches) >= self.pair_threshold:
                graph.add_edge(left, right, key="behavior", kind="behavior", weight=len(matches), matches=list(matches),
                               reason="Repeated same-side orders or cancellations within 150 ms")
        for (left, right), matches in self.trades.items():
            graph.add_edge(left, right, key="counterparty", kind="counterparty",
                           weight=sum(int(match["qty"]) for match in matches), matches=list(matches),
                           reason="Executed trades between these agents")
        return graph

    @staticmethod
    def graph_payload(graph: nx.Graph) -> dict[str, Any]:
        return {"nodes": [{"id": str(node), **attrs} for node, attrs in graph.nodes(data=True)],
                "edges": [{"source": str(left), "target": str(right), "kind": attrs["kind"],
                           "weight": attrs["weight"], "reason": attrs["reason"], "matches": attrs["matches"]}
                          for left, right, attrs in graph.edges(data=True)]}

    def snapshot(self) -> dict[str, Any]:
        return self.graph_payload(self.graph())
