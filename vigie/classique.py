"""Portique classique autonome : règles fixes sur les seuls messages signés du bus."""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import time
from collections import deque
from pathlib import Path

from bus.gate import Decision
from bus.message import FIELDS, Keyring, MessageFactory, canonical
from sim.config import known_senders, load_config, trader_ids


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


class Classique:
    id = "vigie"

    def __init__(self, cfg, keyring=None, *, deterministic=True):
        # Les limites sont copiées au démarrage ; aucun apprentissage pendant le run.
        self.cfg = copy.deepcopy(cfg)
        self.keys = keyring or Keyring(known_senders(cfg))
        self.factory = MessageFactory("vigie", self.keys, deterministic)
        self.traders = set(trader_ids(cfg))
        self.ibans = {b["id"]: b["iban"] for b in cfg["beneficiaries"]}
        self.order_cap = cfg["agents"]["trader"]["max_order_eur"]
        self.transfer_cap = cfg["agents"]["payment"]["max_transfer_eur"]
        self.day_cap = cfg["agents"]["payment"]["max_daily_eur"]
        self.loss_cap = cfg["agents"]["risk"]["max_daily_loss_eur"]
        self.day_ticks = cfg["clock"]["ticks_per_day"]
        self.max_age = cfg["vigie"]["heartbeat_timeout_ticks"]
        self.wall_lease = self.max_age * cfg["clock"]["tick_seconds"] / cfg["clock"]["accelerator"]
        self.tick = -1
        self.last_clock = None
        self.day = -1
        self.spent = 0.0
        self.red = False
        self.proposals = {}
        self.visas = {}
        self.used = set()
        self.seen = set()
        self.incidents = []
        self.pending = []
        self.decisions = []
        self.latencies = deque(maxlen=10000)

    def valid(self, msg):
        try:
            return (isinstance(msg, dict) and set(FIELDS) <= msg.keys()
                    and isinstance(msg["message_id"], str) and bool(msg["message_id"])
                    and type(msg["tick"]) is int and msg["tick"] >= 0
                    and isinstance(msg["payload"], dict)
                    and self.keys.verify(msg))
        except (TypeError, ValueError, KeyError, OverflowError):
            return False

    def incident(self, reason, *, red=False):
        was_red = self.red
        if red:
            self.red = True
        if self.red and not was_red and self.tick >= 0:
            self.pending.extend(self.lights())
        event = {"tick": self.tick, "reason": reason, "light": "ROUGE" if self.red else "VERT"}
        # Une même règle au même tick produit une notification, sans masquer les refus.
        if event not in self.incidents:
            self.incidents.append(event)
            self.pending.append(self.factory.make(max(0, self.tick), "alert", "human", {
                "kind": "classique_control", "severity": "critical" if self.red else "warning", **event}))

    def observe_loss(self, view):
        if isinstance(view, dict) and number(view.get("pnl_day_eur")):
            if view["pnl_day_eur"] < -self.loss_cap:
                self.incident("LOSS_LIMIT", red=True)

    def lights(self):
        return [self.factory.make(self.tick, "heartbeat", "*", {"agent": "vigie", "role": "vigie", "stub": False}),
                self.factory.make(self.tick, "light_change", "*", {
                    "light": "ROUGE" if self.red else "VERT", "expiry_tick": self.tick + self.max_age,
                    "reason": "classique : règles fixes"})]

    def on_message(self, msg):
        if not self.valid(msg):
            self.incident("AUTHENTICATION")
            return self.flush()
        mid = msg["message_id"]
        if mid in self.seen:
            return []
        self.seen.add(mid)
        sender, kind, p = msg["from"], msg["type"], msg["payload"]
        if sender == "clock" and kind == "tick":
            if msg["tick"] <= self.tick or p.get("tick") != msg["tick"]:
                return []
            self.tick = msg["tick"]
            self.last_clock = time.monotonic()
            day = self.tick // self.day_ticks
            if day != self.day:
                self.day, self.spent = day, 0.0
            # Le cache porte sur l'authenticité des visas, jamais sur une chaîne causale.
            self.proposals = {k:v for k,v in self.proposals.items() if self.tick-v["tick"] <= self.max_age}
            self.visas = {k:v for k,v in self.visas.items() if self.tick-v["tick"] <= self.max_age}
            return self.lights() + self.flush()
        if kind == "order_proposal" and sender in self.traders and p.get("trader") == sender:
            self.proposals[mid] = copy.deepcopy(msg)
        elif kind == "payment_proposal" and sender == "payment":
            self.proposals[mid] = copy.deepcopy(msg)
        elif kind == "compliance_decision" and sender == "compliance" and p.get("approved") is True:
            proposal = p.get("proposal")
            if isinstance(proposal, dict) and isinstance(proposal.get("message_id"), str):
                self.visas[proposal["message_id"]] = copy.deepcopy(msg)
        elif kind == "heartbeat" and sender == "risk":
            self.observe_loss(p.get("view"))
        elif kind == "alert" and sender == "risk" and p.get("kind") == "risk_halt":
            self.incident("LOSS_LIMIT", red=True)
        elif kind == "execution" and sender == "executor" and p.get("kind") == "payment" and p.get("status") == "executed":
            if self.ibans.get(p.get("beneficiary_id")) != p.get("iban_credited"):
                self.incident("IBAN_RECEIPT", red=True)
        elif kind == "human_decision" and sender == "human" and p.get("action") in {"resume", "resume_trading"}:
            if 0 <= self.tick - msg["tick"] <= 1:
                self.red = False
                self.pending.extend(self.lights())
        return self.flush()

    def flush(self):
        out, self.pending = self.pending, []
        return out

    def request_approval(self, msg):
        start = time.perf_counter()
        try:
            reason = self.check(msg)
        except Exception:
            # Une entrée inattendue ne doit jamais ouvrir le portique.
            reason = "INVALID_INPUT"
        approved = reason == "OK"
        elapsed = (time.perf_counter()-start)*1000
        self.latencies.append(elapsed)
        self.decisions.append({"tick": self.tick, "approved": approved, "reason": reason, "latency_ms": elapsed})
        if not approved:
            self.incident(reason)
        return Decision(approved, reason, "vigie-classique")

    @staticmethod
    def business(proposal):
        p = proposal["payload"]
        fields = (("payment_id", "beneficiary_id", "iban", "amount_eur") if proposal["type"] == "payment_proposal"
                  else ("proposal_id", "trader", "asset", "side", "notional_eur"))
        return (proposal["message_id"], proposal["from"], proposal["type"], tuple(p.get(k) for k in fields))

    def check(self, msg):
        if not self.valid(msg):
            return "AUTHENTICATION"
        if msg["from"] != "risk" or msg["to"] != "executor" or msg["type"] != "risk_decision":
            return "AUTHORIZATION"
        if (self.last_clock is None or time.monotonic()-self.last_clock >= self.wall_lease
                or not 0 <= self.tick-msg["tick"] <= self.max_age):
            return "STALE"
        p = msg["payload"]
        if p.get("approved") is not True:
            return "NOT_APPROVED"
        self.observe_loss(p.get("risk_view"))
        if self.red:
            return "BANK_RED"
        view = p.get("risk_view")
        if not isinstance(view, dict) or not number(view.get("pnl_day_eur")):
            return "MISSING_RISK_VIEW"
        proposal = p["proposal"]
        original = self.proposals.get(proposal["message_id"])
        visa = self.visas.get(proposal["message_id"])
        if original is None or visa is None:
            return "MISSING_SIGNED_VISA"
        if not 0 <= self.tick-original["tick"] <= self.max_age or not 0 <= self.tick-visa["tick"] <= self.max_age:
            return "STALE_VISA"
        if self.business(original) != self.business(proposal) or self.business(visa["payload"]["proposal"]) != self.business(original):
            return "IDENTITY_MISMATCH"
        if proposal["message_id"] in self.used:
            return "REPLAY"
        amount = p.get("notional_eur")
        if not number(amount) or amount <= 0:
            return "AMOUNT"
        q = original["payload"]
        if p.get("kind") == "order" and original["type"] == "order_proposal":
            if original["from"] not in self.traders or q.get("trader") != original["from"]:
                return "IDENTITY_MISMATCH"
            if q.get("asset") not in self.cfg["market"]["assets"] or q.get("side") not in {"buy", "sell"}:
                return "INSTRUMENT"
            if not number(q.get("notional_eur")) or not amount <= q["notional_eur"] <= self.order_cap:
                return "ORDER_LIMIT"
        elif p.get("kind") == "payment" and original["type"] == "payment_proposal" and original["from"] == "payment":
            if not number(q.get("amount_eur")) or not amount == q["amount_eur"] <= self.transfer_cap:
                return "TRANSFER_LIMIT"
            if q.get("beneficiary_id") not in self.ibans or q.get("iban") != self.ibans[q["beneficiary_id"]]:
                return "IBAN_UNKNOWN"
            if self.spent + amount > self.day_cap:
                return "DAY_LIMIT"
            # Réserver avant réponse empêche deux requêtes concurrentes de dépasser le plafond.
            self.spent += amount
        else:
            return "ACTION_TYPE"
        self.used.add(proposal["message_id"])
        return "OK"

    def status(self):
        values = sorted(self.latencies)
        return {"implementation": "classique", "tick": self.tick, "light": "ROUGE" if self.red else "VERT",
                "decisions": len(self.decisions), "refusals": sum(not d["approved"] for d in self.decisions),
                "incidents": self.incidents, "notifications": len(self.incidents), "payment_reserved_eur": self.spent,
                "gate_p50_ms": values[(len(values)-1)//2] if values else None,
                "gate_p95_ms": values[math.ceil(.95*len(values))-1] if values else None}


class RedisPort:
    """Liste blanche : aucun accès aux bases de données de la simulation."""
    def __init__(self, client, prefix):
        self.client, self.prefix = client, prefix

    def open(self):
        sub = self.client.pubsub(ignore_subscribe_messages=True)
        sub.subscribe(self.prefix+"bus", self.prefix+"vigie.approval_requests")
        self.client.set(self.prefix+"agent:vigie:pid", os.getpid())
        return sub

    def publish(self, channel, value):
        if channel not in {"bus", "vigie.approval_responses"}:
            raise ValueError("canal hors liste blanche")
        self.client.publish(self.prefix+channel, canonical(value))

    def status(self, value):
        self.client.set(self.prefix+"vigie:status", canonical(value))


def handle(port, controller, raw):
    try:
        value = json.loads(raw["data"])
        channel = raw["channel"]
        if isinstance(channel, bytes):
            channel = channel.decode()
        if channel == port.prefix+"vigie.approval_requests":
            request_id = value.get("request_id")
            msg = value.get("message")
            if not isinstance(request_id, str) or not isinstance(msg, dict) or request_id != msg.get("message_id"):
                return
            decision = controller.request_approval(msg)
            port.publish("vigie.approval_responses", {"request_id": request_id, "approved": decision.approved,
                         "reason": decision.reason, "decided_by": decision.decided_by})
            outputs = controller.flush()
        elif channel == port.prefix+"bus":
            outputs = controller.on_message(value)
        else:
            return
        for msg in outputs:
            port.publish("bus", msg)
        port.status(controller.status())
    except (ValueError, TypeError, KeyError, AttributeError):
        # Sans réponse valable, BusGate conserve son refus sur expiration.
        return


def launch(cfg):
    import yaml
    from sim.launch import parse_procfile, procfile, python_bin, supervise
    cfg = copy.deepcopy(cfg)
    cfg["vigie"].update(enabled=True, stub=False)
    cfg["gate"].update(impl="bus", default_on_timeout="reject")
    cfg["llm"]["enabled"] = False
    target = Path("runs/classique-config.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    cfg.pop("_path", None)
    target.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    os.environ["HORIZON_CONFIG"] = str(target)
    os.environ["HORIZON_NO_LLM"] = "1"
    os.environ.pop("HORIZON_NO_VIGIE", None)
    entries = [entry for entry in parse_procfile(procfile(cfg, True, False))
               if entry[0] not in {"vigie", "vigie_stub"}]
    entries.append(("vigie", f"{python_bin()} -m vigie.classique"))
    return supervise(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true", help="lancer toute l'équipe avec le portique Redis")
    args = parser.parse_args()
    cfg = load_config()
    if args.launch:
        raise SystemExit(launch(cfg))
    from bus.redis_bus import connect
    port = RedisPort(connect(cfg["redis"]["url"]), cfg["redis"]["prefix"])
    controller = Classique(cfg, deterministic=False)
    sub = port.open()
    print("[vigie-classique] portique Redis, règles fixes, LLM désactivé", flush=True)
    try:
        while True:
            raw = sub.get_message(timeout=1)
            if raw is not None:
                handle(port, controller, raw)
    finally:
        sub.close()


if __name__ == "__main__":
    main()
