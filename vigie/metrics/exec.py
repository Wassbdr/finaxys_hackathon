"""The CEO's figures: money run on code that cannot be patched, and decisions that can be traced back."""

from __future__ import annotations

from collections import deque

from vigie.finding import Finding
from vigie.graph import executed_eur


class ExecMetrics:
    def __init__(self, graph):
        self.graph = graph
        self.executed_eur = 0.0
        self.unpatchable_eur = 0.0
        self.executions = 0
        self.traced = 0
        self.untraced = deque(maxlen=10)
        self.fresh = []

    def missing(self, id_):
        """Ids in the depends_on chain the bus never showed, or roots that cite no service."""
        gaps, todo, seen = [], [id_], set()
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            known = self.graph.messages.get(current)
            if known is None:
                gaps.append(current)
                continue
            _, parents, entries = known
            if not parents and not entries:
                gaps.append(current)
            todo.extend(parents)
        return gaps

    def observe(self, msg):
        eur = executed_eur(msg)
        if msg["type"] != "execution" or msg["from"] != "executor" or msg["payload"].get("status") != "executed":
            return
        self.executions += 1
        self.executed_eur += eur
        _, _, entries = self.graph.messages.get(msg["message_id"], (None, [], set()))
        if any(not self.graph.patchable(s) for s, _ in entries):
            self.unpatchable_eur += eur
        gaps = self.missing(msg["message_id"])
        if not gaps:
            self.traced += 1
            return
        self.untraced.append({"execution": msg["message_id"], "missing": gaps[:5]})
        self.fresh.append(Finding(msg["tick"], 1, "untraced_decision", msg["message_id"], "warning", [msg["message_id"], *gaps[:5]], eur, {"eur": eur, "missing": gaps[:5], "text": f"{eur:,.0f} EUR executed without a full depends_on chain back to a service"}))

    def drain(self):
        out, self.fresh = self.fresh, []
        return out

    def snapshot(self):
        return {
            "executed_eur": round(self.executed_eur, 2),
            "unpatchable_eur": round(self.unpatchable_eur, 2),
            "unpatchable_share": round(self.unpatchable_eur / self.executed_eur, 3) if self.executed_eur else None,
            "unpatchable_services": sorted(s for s in self.graph.services if not self.graph.patchable(s)),
            "executions": self.executions,
            "reconstitution_rate": round(self.traced / self.executions, 3) if self.executions else None,
            "untraced": list(self.untraced),
        }
