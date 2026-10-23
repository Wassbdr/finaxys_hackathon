"""Point d'accroche de la Vigie : toute action irréversible passe par gate.request_approval(msg).

L'équipe Vigie remplace StubGate par sa propre implémentation, ou se branche sur BusGate :
  - requête publiée sur `{prefix}vigie.approval_requests` : {"request_id", "message"}
  - réponse attendue sur `{prefix}vigie.approval_responses` :
    {"request_id", "approved": bool, "reason": str, "decided_by": str}
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass


@dataclass
class Decision:
    approved: bool
    reason: str
    decided_by: str


class Gate:
    timeout_s = 0.0

    def request_approval(self, msg: dict) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError


class StubGate(Gate):
    """Approuve tout. Le délai est configurable pour imiter une Vigie qui délibère."""

    def __init__(self, timeout_s: float = 2.0):
        self.timeout_s = timeout_s
        self.requests: list[dict] = []

    def request_approval(self, msg: dict) -> Decision:
        self.requests.append(msg)
        return Decision(True, "stub : approbation automatique", "gate-stub")


class PassThroughGate(Gate):
    """--no-vigie : aucune approbation demandée."""

    def request_approval(self, msg: dict) -> Decision:
        return Decision(True, "pass-through (--no-vigie)", "none")


class BusGate(Gate):
    """Délègue la décision à la Vigie par Redis ; décision par défaut si elle ne répond pas à temps."""

    def __init__(self, client, prefix: str, timeout_s: float, default_on_timeout: str = "reject"):
        self.client = client
        self.prefix = prefix
        self.timeout_s = timeout_s
        self.default_approve = default_on_timeout == "approve"
        self._lock = threading.Lock()

    def request_approval(self, msg: dict) -> Decision:
        request_id = msg["message_id"]
        with self._lock:
            pubsub = self.client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(f"{self.prefix}vigie.approval_responses")
            try:
                self.client.publish(f"{self.prefix}vigie.approval_requests", json.dumps({"request_id": request_id, "message": msg}))
                deadline = time.monotonic() + self.timeout_s
                while (left := deadline - time.monotonic()) > 0:
                    raw = pubsub.get_message(timeout=left)
                    if raw is None:
                        continue
                    data = json.loads(raw["data"])
                    if data.get("request_id") == request_id:
                        return Decision(bool(data.get("approved")), data.get("reason", ""), data.get("decided_by", "vigie"))
            finally:
                pubsub.close()
        return Decision(self.default_approve, f"délai de {self.timeout_s}s dépassé sans réponse de la Vigie", "timeout")


def make_gate(cfg: dict, client=None) -> Gate:
    g = cfg["gate"]
    if not cfg["vigie"]["enabled"] or g["impl"] == "passthrough":
        return PassThroughGate()
    if g["impl"] == "bus":
        return BusGate(client, cfg["redis"]["prefix"], g["timeout_s"], g["default_on_timeout"])
    return StubGate(g["timeout_s"])
