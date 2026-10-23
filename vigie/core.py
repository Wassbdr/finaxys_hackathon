"""Quatre couches de contrôle ; les données des agents ne sont jamais des instructions."""

from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import math
import time
from pathlib import Path

from bus.gate import Decision
from bus.message import FIELDS, MessageFactory, canonical
from sim.config import agent_ids, role_of
from sim.ledger import historical_var


class Refused(ValueError):
    pass


class Controller:
    def __init__(self, cfg, keyring, profiles=None, deterministic=True):
        self.cfg, self.keyring = cfg, keyring
        self.factory = MessageFactory("vigie", keyring, deterministic)
        self.tick, self.light = 0, "VERT"
        self.last_clock_tick = None
        self.last_clock_at = None
        self.seen = {}
        self.profiles = profiles or {}
        self.orders = defaultdict(lambda: deque(maxlen=200))
        self.suspicion = defaultdict(float)
        self.quarantined = set()
        self.approved_proposals = set()
        self.out = []
        self.history = deque(maxlen=cfg["risk_metrics"]["var_window_ticks"] + 1)
        self.quant = None
        self.analyzed = set()
        self.propagation = {}
        self.contagion_blocked = 0
        self.auditor_enabled = False
        permissions = {
            "quant": {"price_update"},
            "trader": {"order_proposal"},
            "compliance": {"compliance_decision"},
            "risk": {"risk_decision"},
            "payment": {"payment_proposal"},
            "executor": {"execution"},
            "clock": {"tick"},
            "human": {"human_decision", "light_change"},
            "inject": {"alert", "light_change"},
            "secu": {"alert"},
            "world": {"alert"},
            "vigie": {"light_change"},
        }
        peers = {
            "quant": {"*", "traders"},
            "trader": {"compliance", "human", "*"},
            "compliance": {"risk", "human", "*", *agent_ids(cfg)},
            "risk": {"executor", "traders", "human", "*", *agent_ids(cfg)},
            "payment": {"compliance", "human", "*"},
            "executor": {"*", "human", *agent_ids(cfg)},
        }
        self.registry = {}
        for sender in keyring.known:
            role = role_of(sender)
            config = {
                "agent": cfg["agents"].get(role, {}),
                "model": cfg["llm"]["model"],
                "version": "bus-v1",
            }
            fingerprint = hashlib.sha256(canonical(config).encode()).hexdigest()
            file = Path(__file__).resolve().parents[1] / "agents" / f"{role}.py"
            self.registry[sender] = {
                "fingerprint": fingerprint,
                "types": permissions.get(
                    role, {"service_status"} if sender.startswith("svc:") else set()
                )
                | {"heartbeat", "alert", "llm_trace"},
                "tools": permissions.get(
                    role, {"service_status"} if sender.startswith("svc:") else set()
                )
                | {"heartbeat", "alert", "llm_trace"},
                "peers": peers.get(role, {"*", "human", "world", "payment"}),
                "file": file if file.exists() else None,
                "file_hash": hashlib.sha256(file.read_bytes()).hexdigest()
                if file.exists()
                else None,
            }

    def emit(self, kind, to, payload, parents=()):
        if kind == "light_change":
            payload = {
                **payload,
                "expires_at_tick": payload.get(
                    "expires_at_tick",
                    self.tick + self.cfg["vigie"]["heartbeat_timeout_ticks"],
                ),
            }
        self.out.append(
            self.factory.make(self.tick, kind, to, payload, depends_on=list(parents))
        )
        if kind == "light_change" and to not in {"*", "executor"}:
            # World livre aussi les commandes ciblées à l'exécuteur ; réaffirmer
            # son feu global évite qu'une quarantaine individuelle arrête la banque.
            self.out.append(
                self.factory.make(
                    self.tick,
                    "light_change",
                    "executor",
                    {
                        "light": self.light,
                        "reason": "feu global du portique après commande ciblée",
                        "expires_at_tick": self.tick
                        + self.cfg["vigie"]["heartbeat_timeout_ticks"],
                    },
                    depends_on=list(parents),
                )
            )

    def summary(self, agent, action, reason, layer, parents=()):
        self.emit(
            "alert",
            "human",
            {
                "kind": "vigie_decision",
                "severity": "critical"
                if action in {"quarantine", "red"}
                or reason in {"UNKNOWN_AGENT", "INVALID_SIGNATURE", "CONFIG_CHANGED"}
                else "info"
                if action == "green"
                else "warning",
                "agent": agent,
                "action": action,
                "reason": reason,
                "layer": layer,
                "suspicion": self.suspicion[agent],
                "text": f"{agent} : {reason}. Couche {layer}, action {action}, suspicion {self.suspicion[agent]:.0f}/100.",
            },
            parents,
        )

    def drain(self):
        out, self.out = self.out, []
        return out

    def advance(self, tick):
        elapsed = max(0, tick - self.tick)
        for agent in self.suspicion:
            self.suspicion[agent] = max(0, self.suspicion[agent] - elapsed)
        self.tick = max(self.tick, tick)

    def identity(self, msg):
        if not isinstance(msg, dict) or msg.get("from") not in self.registry:
            raise Refused("UNKNOWN_AGENT")
        if (
            set(msg) != set(FIELDS)
            or not isinstance(msg.get("payload"), dict)
            or not isinstance(msg.get("depends_on"), list)
            or not isinstance(msg.get("data_provenance"), list)
        ):
            raise Refused("INVALID_SCHEMA")
        if (
            type(msg["tick"]) is not int
            or msg["tick"] < 0
            or not isinstance(msg["message_id"], str)
        ):
            raise Refused("INVALID_SCHEMA")
        try:
            valid = self.keyring.verify(msg)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise Refused("INVALID_SIGNATURE")
        sender = msg["from"]
        entry = self.registry[sender]
        if sender in self.quarantined:
            raise Refused("QUARANTINED_AGENT")
        claimed = msg["payload"].get("configuration_fingerprint", entry["fingerprint"])
        if claimed != entry["fingerprint"] or (
            entry["file"]
            and hashlib.sha256(entry["file"].read_bytes()).hexdigest()
            != entry["file_hash"]
        ):
            raise Refused("CONFIG_CHANGED")
        if (
            msg["type"] not in entry["types"]
            or msg["payload"].get("tool", msg["type"]) not in entry["tools"]
        ):
            raise Refused("TOOL_DENIED")
        if msg["to"] not in entry["peers"]:
            raise Refused("RECIPIENT_DENIED")

    def observe(self, msg, state=None):
        if isinstance(msg, dict) and msg.get("from") == "vigie":
            return
        if (
            isinstance(msg, dict)
            and msg.get("from") in self.quarantined
            and msg.get("type") == "heartbeat"
        ):
            return
        try:
            self.identity(msg)
        except (Refused, OSError) as exc:
            if str(exc) == "CONFIG_CHANGED" and msg.get("from") in self.registry:
                self.quarantine(msg["from"], str(exc))
            self.summary(
                msg.get("from", "inconnu") if isinstance(msg, dict) else "inconnu",
                "block",
                str(exc),
                1,
            )
            return
        if state and msg["tick"] > self.tick and msg["tick"] == state.get("tick"):
            self.advance(msg["tick"])
        id_ = msg["message_id"]
        if id_ in self.seen:
            return
        self.seen[id_] = msg
        kind, p = msg["type"], msg["payload"]
        if kind == "tick" and msg["from"] == "clock":
            self.advance(msg["tick"])
            self.last_clock_tick = msg["tick"]
            self.last_clock_at = time.monotonic()
            self.emit(
                "heartbeat",
                "*",
                {"agent": "vigie", "role": "vigie", "stub": False, "light": self.light},
            )
            self.emit(
                "light_change",
                "*",
                {
                    "light": self.light,
                    "reason": "bail renouvelé",
                    "expires_at_tick": self.tick
                    + self.cfg["vigie"]["heartbeat_timeout_ticks"],
                },
            )
            for agent in self.quarantined:
                self.emit(
                    "light_change",
                    agent,
                    {
                        "light": "ROUGE",
                        "reason": "QUARANTINE",
                        "expires_at_tick": self.tick
                        + self.cfg["vigie"]["heartbeat_timeout_ticks"],
                    },
                )
        elif kind == "human_decision" and msg["from"] == "human":
            if p.get("action") == "resume":
                self.light = "VERT"
                self.emit(
                    "light_change",
                    "*",
                    {"light": "VERT", "reason": "reprise humaine signée"},
                )
            if p.get("action") == "unblock" and p.get("target") in self.quarantined:
                agent = p["target"]
                self.quarantined.remove(agent)
                self.registry[agent]["tools"] = set(self.registry[agent]["types"])
                self.emit(
                    "light_change",
                    agent,
                    {
                        "light": self.light,
                        "reason": "quarantaine levée par signature humaine",
                    },
                )
            self.summary("human", "green", "HUMAN_DECISION", 0, [id_])
        elif kind == "price_update" and msg["from"] == "quant":
            self.quant = msg
        elif kind == "order_proposal":
            try:
                amount = float(p["notional_eur"])
            except (ValueError, TypeError, KeyError):
                return
            if math.isfinite(amount):
                self.orders[msg["from"]].append(
                    (msg["tick"], amount, p.get("asset"), id_)
                )
                if state:
                    self.analyze(msg, state)
        if kind in {
            "price_update",
            "order_proposal",
            "compliance_decision",
            "risk_decision",
        }:
            for entry in msg["data_provenance"]:
                if (
                    isinstance(entry, dict)
                    and entry.get("service")
                    and entry.get("response_hash")
                ):
                    key = entry["service"] + ":" + entry["response_hash"]
                    record = self.propagation.setdefault(
                        key,
                        {
                            "service": entry["service"],
                            "source_tick": entry.get("tick"),
                            "agents": set(),
                            "messages": [],
                        },
                    )
                    record["agents"].add(msg["from"])
                    record["messages"].append(id_)

    def quarantine(self, agent, reason, parents=()):
        self.quarantined.add(agent)
        self.registry[agent]["tools"] = set()
        self.suspicion[agent] = min(100, self.suspicion[agent] + 60)
        self.emit(
            "light_change",
            agent,
            {
                "light": "ROUGE",
                "reason": "QUARANTINE",
                "expires_at_tick": self.tick
                + self.cfg["vigie"]["heartbeat_timeout_ticks"],
            },
            parents,
        )
        self.summary(agent, "quarantine", reason, 4, parents)

    def ancestry(self, msg):
        p = msg["payload"]
        if (
            msg["from"] != "risk"
            or msg["type"] != "risk_decision"
            or msg["to"] != "executor"
            or p.get("approved") is not True
        ):
            raise Refused("INVALID_APPROVAL")
        if msg["message_id"] not in self.seen or canonical(
            self.seen[msg["message_id"]]
        ) != canonical(msg):
            raise Refused("INVALID_ANCESTRY")
        proposal = p.get("proposal", {})
        source = self.seen.get(proposal.get("message_id"))
        if not source:
            raise Refused("INVALID_ANCESTRY")
        self.identity(source)
        if source["from"] != proposal.get("from") or source["type"] != proposal.get(
            "type"
        ):
            raise Refused("INVALID_ANCESTRY")
        if any(
            proposal.get("payload", {}).get(k) != v
            for k, v in source["payload"].items()
        ):
            raise Refused("INVALID_ANCESTRY")
        if source["message_id"] not in msg["depends_on"]:
            raise Refused("INVALID_ANCESTRY")
        compliance = [self.seen.get(id_) for id_ in msg["depends_on"]]
        valid = [
            m
            for m in compliance
            if m
            and m["from"] == "compliance"
            and m["type"] == "compliance_decision"
            and m["payload"].get("approved") is True
            and m["payload"].get("proposal", {}).get("message_id")
            == source["message_id"]
        ]
        if not valid:
            raise Refused("INVALID_ANCESTRY")
        self.identity(valid[0])
        compliance_proposal = valid[0]["payload"]["proposal"]
        if (
            compliance_proposal.get("from") != source["from"]
            or compliance_proposal.get("type") != source["type"]
            or any(
                compliance_proposal.get("payload", {}).get(k) != v
                for k, v in source["payload"].items()
            )
        ):
            raise Refused("INVALID_ANCESTRY")
        if source["type"] == "order_proposal":
            parents = [self.seen.get(id_) for id_ in source["depends_on"]]
            if not any(
                parent
                and parent["from"] == "quant"
                and parent["type"] == "price_update"
                for parent in parents
            ):
                raise Refused("INVALID_ANCESTRY")
        if source["message_id"] in self.approved_proposals:
            raise Refused("REPLAY_ACTION")
        return source

    def provenance(self, msg, state, source):
        # L'union des parents empêche Risk de faire disparaître une provenance gênante.
        pending = [msg, source]
        visited = set()
        entries = []
        while pending:
            current = pending.pop()
            if current["message_id"] in visited:
                continue
            visited.add(current["message_id"])
            entries += current["data_provenance"]
            for parent in current["depends_on"]:
                ancestor = self.seen.get(parent)
                if not ancestor:
                    raise Refused("INVALID_ANCESTRY")
                if ancestor:
                    if ancestor["from"] in self.quarantined:
                        raise Refused("QUARANTINED_SOURCE")
                    pending.append(ancestor)
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"service", "version", "response_hash", "tick"}
                or not isinstance(entry["response_hash"], str)
                or not entry["response_hash"]
            ):
                raise Refused("INVALID_PROVENANCE")
            sid = entry.get("service")
            status = state.get("services", {}).get(sid)
            if (
                not status
                or status.get("mode") in {"compromised", "stale", "down"}
                or entry.get("response_hash") in state.get("tainted_hashes", [])
            ):
                raise Refused("UNTRUSTED_PROVENANCE")
            if entry.get("version") != self.cfg["services"][sid]["version"]:
                raise Refused("UNTRUSTED_PROVENANCE")
            if type(entry.get("tick")) is not int or entry["tick"] > self.tick:
                raise Refused("INVALID_PROVENANCE")
            if (
                sid == "market-feed"
                and self.tick - entry["tick"]
                > self.cfg["agents"]["quant"]["max_data_age_ticks"]
            ):
                raise Refused("UNTRUSTED_PROVENANCE")

    def fixed_rules(self, msg, source, state):
        p = msg["payload"]
        order = source["payload"]
        r = self.cfg["agents"]["risk"]
        t = self.cfg["agents"]["trader"]
        pay = self.cfg["agents"]["payment"]
        try:
            amount = float(p["notional_eur"])
        except (ValueError, TypeError, KeyError):
            raise Refused("INVALID_AMOUNT")
        if not math.isfinite(amount) or amount <= 0:
            raise Refused("INVALID_AMOUNT")
        if self.light != "VERT":
            raise Refused("LIGHT_BLOCKED")
        lease_seconds = (
            self.cfg["vigie"]["heartbeat_timeout_ticks"]
            * self.cfg["clock"]["tick_seconds"]
            / self.cfg["clock"]["accelerator"]
        )
        if (
            self.last_clock_at is None
            or time.monotonic() - self.last_clock_at > lease_seconds
        ):
            raise Refused("STALE_LEASE")
        if (
            not state
            or not isinstance(state.get("tick"), int)
            or not 0 <= self.tick - state["tick"] <= 1
        ):
            raise Refused("STATE_UNAVAILABLE")
        pnl = state.get("pnl", {})
        loss = float(pnl["day_eur"]) + float(pnl.get("payment_fraud_eur", 0))
        if loss < -r["max_daily_loss_eur"]:
            raise Refused("LOSS_LIMIT")
        cash = float(state["cash_eur"])
        if p["kind"] == "payment":
            if source["type"] != "payment_proposal" or source["from"] != "payment":
                raise Refused("INVALID_APPROVAL")
            if amount > pay["max_transfer_eur"] or amount > float(order["amount_eur"]):
                raise Refused("PAYMENT_LIMIT")
            if amount + state["payments"]["legit_day_eur"] > pay["max_daily_eur"]:
                raise Refused("PAYMENT_DAILY_LIMIT")
            if cash - amount < r["min_cash_eur"]:
                raise Refused("LIQUIDITY_LIMIT")
            ben = next(
                (
                    b
                    for b in self.cfg["beneficiaries"]
                    if b["id"] == order["beneficiary_id"]
                ),
                None,
            )
            if (
                not ben
                or not ben["docs_complete"]
                or ben["country"]
                in self.cfg["agents"]["compliance"]["high_risk_countries"]
            ):
                raise Refused("KYC_BLOCKED")
            change = state.get("iban_changes", {}).get(ben["id"])
            if (
                change
                and self.tick - int(change["tick"]) < pay["iban_change_lock_ticks"]
            ):
                raise Refused("IBAN_CHANGED")
            if order["iban"] != ben["iban"]:
                raise Refused("IBAN_MISMATCH")
            return
        if (
            p["kind"] != "order"
            or source["type"] != "order_proposal"
            or not source["from"].startswith("trader-")
            or order["trader"] != source["from"]
        ):
            raise Refused("INVALID_APPROVAL")
        if amount > t["max_order_eur"] or amount > float(order["notional_eur"]):
            raise Refused("ORDER_LIMIT")
        asset = order["asset"]
        if asset not in state["prices"] or order["side"] not in {"buy", "sell"}:
            raise Refused("INVALID_ASSET")
        if asset in state.get("params", {}).get("restricted_list", []):
            raise Refused("RESTRICTED_ASSET")
        if any(ref.get("confidential") for ref in order.get("news_refs", [])):
            raise Refused("CONFIDENTIAL_NEWS")
        if float(order.get("quant_confidence", 0)) < t["min_confidence"]:
            raise Refused("LOW_CONFIDENCE")
        side = 1 if order["side"] == "buy" else -1
        position = (
            state["books"][source["from"]]["positions_eur"][asset] + side * amount
        )
        if abs(position) > t["max_position_eur"]:
            raise Refused("POSITION_LIMIT")
        bank = dict(state["positions_eur"])
        bank[asset] += side * amount
        if sum(abs(v) for v in bank.values()) > r["max_exposure_eur"]:
            raise Refused("EXPOSURE_LIMIT")
        rm = self.cfg["risk_metrics"]
        var = max(
            float(state["var_99_1d_eur"]),
            historical_var(
                bank, list(self.history), rm["var_confidence"], rm["var_horizon_ticks"]
            ),
        )
        if var > r["max_var_eur"]:
            raise Refused("VAR_LIMIT")
        if (
            var >= r["max_var_eur"] * r["var_warning_ratio"]
            and amount > t["max_order_eur"] * r["var_warning_scale"]
        ):
            raise Refused("VAR_WARNING")
        if cash - (amount if side > 0 else 0) < r["min_cash_eur"]:
            raise Refused("LIQUIDITY_LIMIT")
        book = state["books"][source["from"]]
        book_pnl = book.get(
            "pnl_day_eur", book.get("realized_eur", 0) + book.get("unrealized_eur", 0)
        )
        if book_pnl < -t["max_daily_loss_eur"]:
            raise Refused("TRADER_LOSS_LIMIT")

    def behavior(self, source):
        agent = source["from"]
        recent = [
            e
            for e in self.orders[agent]
            if self.tick - 10 <= e[0] <= self.tick
            and e[1] >= self.cfg["agents"]["trader"]["max_order_eur"] * 0.95
        ]
        profile = self.profiles.get(agent, {})
        return len(recent) >= 3 and sum(e[1] for e in recent) > max(
            10_000_000, profile.get("mean_order_eur", 1_000_000) * 3
        )

    def coherence(self, source, state):
        p = source["payload"]
        if source["type"] == "order_proposal":
            est = (
                (self.quant or {}).get("payload", {}).get("assets", {}).get(p["asset"])
            )
            if (
                est
                and float(p.get("quant_confidence", 0))
                >= self.cfg["agents"]["trader"]["min_confidence"]
                and est["confidence"] < self.cfg["agents"]["trader"]["min_confidence"]
            ):
                return "INCOHERENT_CONFIDENCE"
            if "faillite" in p.get("justification", "").casefold():
                reliable = any(
                    n.get("asset") == p["asset"]
                    and "faillite"
                    in (n.get("headline", "") + " " + n.get("body", "")).casefold()
                    and n.get("source") == "Horizon Wire"
                    for n in state.get("news", [])
                )
                if not reliable:
                    return "UNRELIABLE_NEWS"
        return None

    def analyze(self, source, state):
        if source["message_id"] in self.analyzed:
            return None
        self.analyzed.add(source["message_id"])
        agent = source["from"]
        anomaly = self.behavior(source)
        if anomaly:
            self.suspicion[agent] = min(100, self.suspicion[agent] + 30)
            self.summary(
                agent, "slow", "BEHAVIOR_CUMULATIVE", 3, [source["message_id"]]
            )
            self.emit(
                "light_change",
                agent,
                {
                    "light": "ORANGE",
                    "reason": "BEHAVIOR_CUMULATIVE",
                    "expires_at_tick": self.tick + 1,
                },
            )
        mismatch = self.coherence(source, state)
        if mismatch:
            self.quarantine(agent, mismatch, [source["message_id"]])
            return mismatch
        return "BEHAVIOR_CUMULATIVE" if anomaly else None

    def approve(self, msg, state):
        source = None
        agent = msg.get("from", "inconnu") if isinstance(msg, dict) else "inconnu"
        layer = 1
        try:
            self.identity(msg)
            if (
                self.last_clock_tick is None
                or not 0 <= self.last_clock_tick - msg["tick"] <= 1
            ):
                raise Refused("STALE_LEASE")
            candidate = self.seen.get(
                msg["payload"].get("proposal", {}).get("message_id")
            )
            if candidate:
                agent = candidate["from"]
            source = self.ancestry(msg)
            agent = source["from"]
            layer = 2
            self.fixed_rules(msg, source, state)
            layer = 4
            self.provenance(msg, state, source)
            if source["type"] == "order_proposal":
                layer = 3
                anomaly = self.analyze(source, state)
                if anomaly:
                    raise Refused(anomaly)
                if self.behavior(source):
                    raise Refused("BEHAVIOR_CUMULATIVE")
            self.approved_proposals.add(source["message_id"])
            self.summary(agent, "green", "APPROVED", 2, [msg["message_id"]])
            return Decision(True, "APPROVED", "vigie")
        except (Refused, KeyError, TypeError, ValueError, OSError) as exc:
            reason = str(exc) if isinstance(exc, Refused) else "INVALID_SCHEMA"
            if reason == "UNTRUSTED_PROVENANCE":
                self.contagion_blocked += 1
            if reason in {"LOSS_LIMIT", "VAR_LIMIT", "EXPOSURE_LIMIT"}:
                self.light = "ROUGE"
                self.emit("light_change", "*", {"light": "ROUGE", "reason": reason})
            if reason == "CONFIG_CHANGED" and agent in self.registry:
                self.quarantine(agent, reason)
            self.suspicion[agent] = min(100, self.suspicion[agent] + 10)
            self.summary(
                agent,
                "red"
                if reason in {"LOSS_LIMIT", "VAR_LIMIT", "EXPOSURE_LIMIT"}
                else "block",
                reason,
                layer,
                [msg["message_id"]]
                if isinstance(msg, dict) and msg.get("message_id") in self.seen
                else [],
            )
            return Decision(False, reason, "vigie")

    def auditor_advice(self, agent, delta, model):
        if not self.auditor_enabled or delta < 0 or model == self.cfg["llm"]["model"]:
            return False
        self.suspicion[agent] = min(100, self.suspicion[agent] + delta)
        return True

    def status(self):
        return {
            "tick": self.tick,
            "light": self.light,
            "suspicion": dict(self.suspicion),
            "quarantined": sorted(self.quarantined),
            "contagion_blocked": self.contagion_blocked,
            "profiles": self.profiles,
            "registry": {
                a: {
                    "configuration_fingerprint": e["fingerprint"],
                    "tools": sorted(e["tools"]),
                    "peers": sorted(e["peers"]),
                    "key_revoked": a in self.quarantined,
                }
                for a, e in self.registry.items()
            },
            "propagation": {
                key: {
                    **record,
                    "agents": sorted(record["agents"]),
                    "reaction_count": len(record["messages"]),
                }
                for key, record in self.propagation.items()
            },
            "auditor_llm": False,
        }
