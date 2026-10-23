"""Format des messages du bus, signature HMAC-SHA256 et data_provenance."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
from typing import Any, Iterable

MESSAGE_TYPES = {
    "tick",
    "price_update",
    "order_proposal",
    "payment_proposal",
    "compliance_decision",
    "risk_decision",
    "execution",
    "alert",
    "heartbeat",
    "light_change",
    "human_decision",
    "service_status",
    "llm_trace",
}

FIELDS = ("message_id", "tick", "from", "to", "type", "payload", "depends_on", "data_provenance", "signature")

DEV_MASTER_SECRET = "banque-horizon-dev-secret"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(obj: Any) -> str:
    return sha256(canonical(obj))[:16]


def env_key_name(sender: str) -> str:
    return "HMAC_KEY_" + "".join(c if c.isalnum() else "_" for c in sender).upper()


class Keyring:
    """Une clé HMAC par émetteur. Un émetteur inconnu n'a pas de clé : ses messages sont rejetés."""

    def __init__(self, known_senders: Iterable[str], keys: dict[str, str] | None = None):
        self.known = set(known_senders)
        self._keys: dict[str, bytes] = {}
        master = os.environ.get("HMAC_MASTER_SECRET", DEV_MASTER_SECRET)
        for sender in self.known:
            explicit = (keys or {}).get(sender) or os.environ.get(env_key_name(sender))
            if explicit:
                self._keys[sender] = explicit.encode()
            else:
                self._keys[sender] = hmac.new(master.encode(), sender.encode(), hashlib.sha256).hexdigest().encode()

    def key_for(self, sender: str) -> bytes | None:
        return self._keys.get(sender)

    @staticmethod
    def compute(msg: dict, key: bytes) -> str:
        body = {k: msg.get(k) for k in FIELDS if k != "signature"}
        return hmac.new(key, canonical(body).encode("utf-8"), hashlib.sha256).hexdigest()

    def sign(self, msg: dict, key: bytes | None = None) -> dict:
        key = key or self.key_for(msg["from"])
        if key is None:
            raise KeyError(f"aucune clé pour l'émetteur {msg['from']!r}")
        msg["signature"] = self.compute(msg, key)
        return msg

    def verify(self, msg: dict) -> bool:
        key = self.key_for(msg.get("from", ""))
        if key is None or not msg.get("signature"):
            return False
        return hmac.compare_digest(self.compute(msg, key), msg["signature"])


def generate_env_keys(senders: Iterable[str]) -> str:
    lines = [f"{env_key_name(s)}={secrets.token_hex(32)}" for s in sorted(senders)]
    return "\n".join(lines) + "\n"


class MessageFactory:
    """Fabrique les messages d'un émetteur. Identifiants déterministes en lockstep."""

    def __init__(self, sender: str, keyring: Keyring | None = None, deterministic: bool = True):
        self.sender = sender
        self.keyring = keyring
        self.seq = 0
        self.nonce = "" if deterministic else secrets.token_hex(3) + ":"

    def make(
        self,
        tick: int,
        type: str,
        to: str,
        payload: dict | None = None,
        depends_on: list[str] | None = None,
        data_provenance: list[dict] | None = None,
    ) -> dict:
        if type not in MESSAGE_TYPES:
            raise ValueError(f"type de message inconnu : {type}")
        self.seq += 1
        msg = {
            "message_id": f"{self.sender}:{self.nonce}{tick}:{self.seq}",
            "tick": tick,
            "from": self.sender,
            "to": to,
            "type": type,
            "payload": payload or {},
            "depends_on": list(depends_on or []),
            "data_provenance": list(data_provenance or []),
            "signature": None,
        }
        # Copie figée : un message signé ne doit plus partager d'objet mutable avec son émetteur
        msg = json.loads(canonical(msg))
        if self.keyring is not None:
            self.keyring.sign(msg)
        return msg


def provenance_entry(service: str, version: str, response_hash: str, tick: int) -> dict:
    return {"service": service, "version": version, "response_hash": response_hash, "tick": tick}


def merge_provenance(*lists: Iterable[dict] | None) -> list[dict]:
    """Union dédupliquée, ordre stable : c'est l'héritage transitif de la provenance."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for entries in lists:
        for entry in entries or []:
            key = (entry["service"], entry["response_hash"])
            if key not in seen:
                seen.add(key)
                out.append(entry)
    return out


def matches(msg: dict, agent_id: str, role: str, observes: Iterable[str] = ()) -> bool:
    """Le message concerne-t-il cet agent ? `to` vaut un id, un rôle, un groupe (`traders`) ou `*`."""
    to = msg.get("to")
    if to in ("*", agent_id, role):
        return True
    if to == "traders" and role == "trader":
        return True
    return msg.get("type") in set(observes)


def warn(text: str) -> None:
    print(f"[horizon] {text}", file=sys.stderr)
