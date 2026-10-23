"""Observateur du monde : voit TOUS les messages (ce que les agents, locaux, ne voient pas).

Il calcule la contagion (agents ayant utilisé une donnée compromise), les arêtes observées,
les opérations piégées exécutées, les signatures invalides. Il ne décide de rien : c'est la
matière première de la Vigie et du dashboard.
"""

from __future__ import annotations

from collections import defaultdict, deque


class Observer:
    def __init__(self, cfg: dict, store, prefix: str):
        self.cfg = cfg
        self.store = store
        self.prefix = prefix
        self.contaminated: dict[str, dict] = {}
        self.flows: dict[tuple, int] = defaultdict(int)
        self.transitive: dict[tuple, int] = defaultdict(int)
        self.heartbeats: dict[str, dict] = {}
        self.alerts: deque = deque(maxlen=50)
        self.human_decisions: list[dict] = []
        self.invalid: deque = deque(maxlen=50)
        self.service_timeline: list[dict] = []
        self.trapped: list[dict] = []
        self.light = {"light": "VERT", "by": None, "tick": 0}
        self.llm_calls = 0
        self.decisions: dict[str, int] = defaultdict(int)
        self._tainted: set[str] = set()
        self._altered: set[str] = set()

    def is_tainted(self, response_hash: str) -> bool:
        if response_hash in self._tainted:
            return True
        if self.store.sismember(f"{self.prefix}tainted", response_hash):
            self._tainted.add(response_hash)
            return True
        return False

    def is_altered(self, response_hash: str) -> bool:
        if response_hash in self._altered:
            return True
        if self.store.sismember(f"{self.prefix}altered", response_hash):
            self._altered.add(response_hash)
            return True
        return False

    def observe(self, msg: dict, valid: bool) -> list[dict]:
        """Retourne les alertes à publier (payloads)."""
        sender, kind, tick = msg.get("from"), msg.get("type"), msg.get("tick", 0)
        if not valid:
            entry = {"tick": tick, "claimed_from": sender, "type": kind, "message_id": msg.get("message_id"), "to": msg.get("to")}
            self.invalid.append(entry)
            return [{"kind": "invalid_signature", "severity": "critical", **entry,
                     "text": f"Message {kind} rejeté : signature invalide (émetteur annoncé : {sender})."}]
        if kind not in ("tick", "heartbeat"):
            self.flows[(sender, msg.get("to"), kind)] += 1
        if kind == "heartbeat":
            self.heartbeats[sender] = {"tick": tick, **msg["payload"]}
        elif kind == "alert":
            self.alerts.append({"tick": tick, "from": sender, **msg["payload"]})
        elif kind == "human_decision":
            self.human_decisions.append({"tick": tick, "from": sender, **msg["payload"]})
        elif kind == "service_status":
            self.service_timeline.append({"tick": tick, **{k: v for k, v in msg["payload"].items() if k != "meta"}})
        elif kind == "light_change":
            self.light = {"light": msg["payload"]["light"], "by": sender, "tick": tick, "reason": msg["payload"].get("reason", "")}
        elif kind == "llm_trace":
            self.llm_calls += 1
        elif kind in ("compliance_decision", "risk_decision", "execution"):
            p = msg["payload"]
            status = p.get("status") or ("approved" if p.get("approved") else "rejected")
            self.decisions[f"{kind}:{status}"] += 1
        if sender and not sender.startswith("svc:") and sender not in ("clock", "world"):
            tainted = False
            for entry in msg.get("data_provenance", []):
                self.transitive[(sender, entry["service"])] += 1
                if self.is_tainted(entry["response_hash"]):
                    tainted = True
                    info = self.contaminated.setdefault(sender, {"since_tick": tick, "services": [], "first_tick_by_service": {}, "messages": 0})
                    if entry["service"] not in info["services"]:
                        info["services"].append(entry["service"])
                        info["first_tick_by_service"][entry["service"]] = tick
                    info["last_tick"] = tick
            if tainted:
                self.contaminated[sender]["messages"] += 1
        return []

    def record_trapped(self, execution: dict, compromised: list[str], altered: bool, amount: float, substituted: bool) -> None:
        p = execution["payload"]
        self.trapped.append({
            "tick": execution["tick"], "exec_id": p["exec_id"], "kind": p["kind"],
            "agent": p.get("trader") or "payment", "amount_eur": amount,
            "asset": p.get("asset"), "beneficiary_id": p.get("beneficiary_id"),
            "compromised_sources": compromised, "data_altered": altered, "iban_substituted": substituted,
            "message_id": execution["message_id"],
        })
