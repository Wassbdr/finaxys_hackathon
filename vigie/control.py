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
from vigie.detect import Indicators
from vigie.main import Runtime
from vigie.core import Finding
from vigie.herding_marius import Herding
from vigie.metrics import Layer1
from vigie.provenance import Provenance
from vigie.policy import Policy
from vigie.statistics import Behavior, structuring
from vigie.validation import cost


def kind_for_graph(msg):
    return bool(msg.get("depends_on")) or msg["type"] in {
        "price_update",
        "order_proposal",
        "payment_proposal",
        "compliance_decision",
        "risk_decision",
        "execution",
        "service_status",
    }


class Refused(ValueError):
    pass


class Controller:
    def __init__(self, cfg, keyring, profiles=None, deterministic=True, *, meta=None, checkpoint=None, fresh_checkpoint=False):
        self.cfg, self.keyring = cfg, keyring
        self.factory = MessageFactory("vigie", keyring, deterministic)
        self.layer3 = Runtime(cfg, keyring, meta=meta, deterministic=deterministic,
                              checkpoint=checkpoint, fresh_checkpoint=fresh_checkpoint)
        self.layer3.factory = self.factory
        self.layer3.propositions.factory = self.factory
        self.state = self.layer3.state
        self.tick, self.light = 0, "VERT"
        self.last_clock_tick = None
        self.last_clock_at = None
        self.seen = {}
        self.profiles = profiles or {}
        self.orders = defaultdict(lambda: deque(maxlen=200))
        self.policy = Policy()
        for a in [*agent_ids(cfg), "executor"]:
            self.policy.layers[a]
        self.suspicion = self.policy.scores
        self.detector = Behavior(self.profiles)
        self.last_approved = {}
        self.behavior_details = {}
        self.known_bad_messages = set()
        self.quarantined = set()
        self.approved_proposals = set()
        self.out = []
        self.history = deque(maxlen=cfg["risk_metrics"]["var_window_ticks"] + 1)
        self.quant = None
        self.analyzed = set()
        self.propagation = {}
        self.contagion_blocked = 0
        self.isolated_services = set()
        self.causality = Provenance(cfg["services"])
        self.ledger_evidence = []
        self.claim_evidence = []
        self.auditor_enabled = False
        self.detect = Indicators(cfg)
        self.herding = Herding(cfg)
        self.actions = deque(maxlen=300)
        self.layer1 = Layer1(cfg)
        self.findings = deque(maxlen=200)
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
        self.actions.append(
            {
                "tick": self.tick,
                "agent": agent,
                "action": action,
                "reason": reason,
                "layer": layer,
                "suspicion": round(self.suspicion.get(agent, 0.0), 1),
            }
        )
        if action == "green" and agent != "human":
            self.policy.layers[agent][str(layer)] = "VERT"
            return
        source = None
        for id_ in parents:
            contaminated, _ = self.causality.contaminated(id_)
            if contaminated:
                source = contaminated[0]
                break
        if agent in self.isolated_services:
            source = agent
        unverified = reason in {"UNKNOWN_AGENT", "INVALID_SIGNATURE"}
        if unverified:
            source = "unverified:" + agent
        victim = source is not None and agent not in self.isolated_services
        no_fault = {
            "UNTRUSTED_PROVENANCE",
            "QUARANTINED_SOURCE",
            "LIGHT_BLOCKED",
            "STALE_LEASE",
            "STATE_UNAVAILABLE",
            "AGENT_SLOWED",
            "AGENT_RATE_LIMIT",
            "QUARANTINED_AGENT",
            "BEHAVIOR_CUMULATIVE",
        }
        weight = {
            "BEHAVIOR_CUMULATIVE": 25,
            "BEHAVIOR_CUSUM": 15,
            "UNRELIABLE_NEWS": 20,
            "INCOHERENT_CONFIDENCE": 20,
            "MISSING_COMPLIANCE_PROOF": 30,
        }.get(reason, 30 if layer == 2 and reason not in no_fault else 0)
        forced = (
            "ROUGE"
            if unverified
            else "QUARANTAINE"
            if action == "quarantine"
            else "ROUGE"
            if action == "red" or agent in self.isolated_services
            else None
        )
        incident, notify = self.policy.record(
            agent,
            reason,
            layer,
            self.tick,
            0 if victim else weight,
            parents,
            source,
            victim,
            forced,
        )
        if notify or agent == "human":
            if agent == "human" and not notify:
                self.policy.notifications += 1
            self.emit(
                "alert",
                "human",
                {
                    "kind": "vigie_incident",
                    "notification": True,
                    "severity": "critical"
                    if incident["level"] in {"ROUGE", "QUARANTAINE"}
                    else "warning",
                    "incident_id": incident["id"],
                    "agent": agent,
                    "action": action,
                    "reason": reason,
                    "layer": layer,
                    "proof": list(parents),
                    "source": source,
                    "victim": victim,
                    "attribution_verified": not unverified,
                    "text": f"{agent} : {reason} ; preuves {', '.join(parents) or 'registre'} ; action {action} ; décision attendue : examiner l'incident.",
                },
                parents,
            )
        profile = self.profiles.get(agent, {})
        warm = (
            profile.get("clean")
            and profile.get("reference_ticks", 0) >= 300
            or self.tick >= 50
        )
        if (
            action != "quarantine"
            and not victim
            and agent in self.registry
            and agent not in self.quarantined
            and warm
            and self.policy.two_witnesses(agent, self.tick)
        ):
            self.quarantine(agent, "TWO_WITNESSES", parents)

    def consume_findings(self, findings):
        for finding in findings:
            self.findings.append(finding.as_dict())
            if finding.severity != 'info':
                # Le Dev 1 garde ses poids, ses témoins et ses feux ; la couche
                # productrice ne transforme pas son score en politique implicite.
                self.summary(finding.target, 'flag', finding.kind, finding.layer, finding.evidence)
                if finding.layer == 3 and finding.kind == 'ledger_mismatch':
                    self.isolate_service(finding.target, 'LEDGER_CONSERVATION', finding.evidence)

    def layer3_observe(self, msg):
        out = self.layer3.on_message(msg)
        mid = msg.get('message_id') if isinstance(msg, dict) else None
        known = (isinstance(mid, str) and cost(msg) is not None and mid in self.state.messages
                 and self.layer3.admission.same_message(msg))
        admitted = known and mid not in self.layer3.admission.unanalysed
        if admitted and msg['type'] == 'service_status':
            payload = msg['payload']
            sid = payload.get('service')
            if sid in self.cfg['services'] and msg['from'] == 'svc:'+sid and isinstance(payload.get('meta'), dict):
                # Le /meta publié et signé par le service est une preuve du bus,
                # contrairement aux métadonnées d'un état interne du simulateur.
                self.state.meta[sid] = {k: payload['meta'][k] for k in
                    ('version', 'language', 'php_version', 'patchable') if k in payload['meta']}
        for message in out:
            payload = message['payload']
            if payload.get('kind') == 'layer3_finding':
                finding = Finding(**payload['finding'])
                self.consume_findings([finding])
                if finding.severity != 'info':
                    self.out.append(message)
            else:
                self.out.append(message)
        return known

    def drain(self):
        out, self.out = self.out, []
        return out

    def advance(self, tick):
        previous = dict(self.policy.levels)
        self.policy.advance(tick)
        self.tick = max(self.tick, tick)
        for a, old in previous.items():
            if old == "ORANGE" and self.policy.levels[a] == "VERT":
                self.policy.notifications += 1
                self.emit(
                    "alert",
                    "human",
                    {
                        "kind": "vigie_incident",
                        "notification": True,
                        "agent": a,
                        "action": "green",
                        "reason": "SUSPICION_DECAY",
                        "text": f"{a} : suspicion revenue sous 10, reprise des limites normales.",
                    },
                )

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

    def is_analysed(self, msg):
        mid = msg.get('message_id') if isinstance(msg, dict) else None
        return (isinstance(mid, str) and cost(msg) is not None and mid in self.state.messages
                and mid not in self.layer3.admission.unanalysed
                and self.layer3.admission.same_message(msg))

    def unverified_notice(self, msg, reason):
        if reason in {'INVALID_SIGNATURE', 'UNKNOWN_AGENT'}:
            sender = msg.get('from') if isinstance(msg, dict) else None
            sender = sender if isinstance(sender, str) and sender in self.registry else 'unverified'
            # Le nom forgé est une donnée hostile, pas une nouvelle clé de
            # politique ; les émetteurs connus gardent leur notification groupée.
            self.summary(sender, 'block', reason, 1)

    def reject_analysis(self, msg):
        if not self.is_analysed(msg):
            return
        # Une projection partiellement calculée ne prouve pas son innocuité ;
        # seules les opérations qui la réutilisent perdent leur autorisation.
        self.layer3.admission.unanalysed.add(msg['message_id'])
        gap = (msg['from'] == 'executor' and msg['type'] == 'execution'
               and msg['payload'].get('status') == 'executed')
        self.layer3.reject_input(msg, 'legacy_analysis', gap=gap)
        self.out.extend(self.layer3.diagnostics())

    def observe(self, msg, state=None):
        try:
            return self._observe_native(msg, state)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            self.reject_analysis(msg)

    def _observe_native(self, msg, state=None):
        # Le filtre borné précède aussi les moteurs historiques : une entrée
        # non vérifiée ne doit pas créer un acteur dans leurs dictionnaires.
        if not self.layer3_observe(msg):
            if cost(msg) is not None:
                try:
                    self.identity(msg)
                except (Refused, OSError, TypeError, ValueError) as exc:
                    self.unverified_notice(msg, str(exc))
            return
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
            if isinstance(msg, dict):
                self.causality.reject(msg.get("message_id"))
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
        self.policy.layers[msg["from"]]["1"] = "VERT"
        id_ = msg["message_id"]
        if id_ in self.seen:
            return
        self.seen[id_] = msg
        if kind_for_graph(msg):
            self.causality.add(msg)
        if state:
            for sid, status in state.get("services", {}).items():
                if status.get("mode") in {"stale", "down"}:
                    self.isolate_service(sid, "UNTRUSTED_PROVENANCE", [id_])
        kind, p = msg["type"], msg["payload"]
        self.detect.observe(msg, state)
        self.herding.observe(msg)
        self.consume_findings(self.herding.drain())
        for found in self.detect.drain():
            self.actions.append({**found, "agent": found["target"], "action": "flag", "reason": found["rule"], "layer": "metrics"})
            self.emit("alert", "human", {"kind": "vigie_indicator", "severity": "warning", **found}, [id_])
            if found.get("service"):
                self.layer1.suspect(found["service"], found["rule"], [id_])
        if kind == 'heartbeat' and msg['from'] == 'world' and p.get('kind') == 'network_calls':
            # Seuls les compteurs publiés et signés passent ici, jamais le graphe
            # enrichi par la vérité terrain dans le store du simulateur.
            edges = p.get('edges', [])
            if isinstance(edges, list):
                self.layer1.topology({'edges': [{k: e[k] for k in
                    ('source', 'target', 'kind', 'transitive', 'calls') if k in e}
                    for e in edges[:256] if isinstance(e, dict)]})
        self.layer1.observe(msg)
        self.consume_findings(self.layer1.drain())
        if kind == "tick" and msg["from"] == "clock":
            self.advance(msg["tick"])
            self.last_clock_tick = msg["tick"]
            self.last_clock_at = time.monotonic()
            self.detector.profiles = self.profiles
            for agent, orders in self.orders.items():
                if agent not in self.quarantined and self.detector.tick(
                    agent, list(orders), self.tick
                ):
                    # Le CUSUM garde ses preuves même après leur sortie de la fenêtre.
                    proof = sorted(self.detector.evidence.get(agent, set()))
                    if proof and not any(
                        any(self.causality.contaminated(id_)) for id_ in proof
                    ):
                        self.summary(agent, "slow", "BEHAVIOR_CUSUM", 3, proof)
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
            for agent, level in list(self.policy.levels.items()):
                if (
                    level == "ORANGE"
                    and agent in self.registry
                    and agent not in self.quarantined
                ):
                    self.emit(
                        "light_change",
                        agent,
                        {"light": "ORANGE", "reason": "SUSPICION"},
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
            if (
                p.get("action") == "unblock"
                and p.get("target") in self.isolated_services
            ):
                service = p["target"]
                self.isolated_services.remove(service)
                self.causality.release(service, self.tick)
                self.policy.layers[service] = {str(i): "VERT" for i in range(1, 5)}
            if p.get("action") == "unblock" and p.get("target") in self.quarantined:
                agent = p["target"]
                self.quarantined.remove(agent)
                self.registry[agent]["tools"] = set(self.registry[agent]["types"])
                self.suspicion[agent] = 0.0
                self.policy.witnesses[agent] = {}
                self.policy.levels[agent] = "VERT"
                self.policy.layers[agent] = {str(i): "VERT" for i in range(1, 5)}
                incident = self.policy.incidents.get("agent:" + agent)
                if incident:
                    incident["status"] = "resolved"
                    incident["level"] = "VERT"
                self.emit(
                    "light_change",
                    agent,
                    {
                        "light": self.light,
                        "reason": "quarantaine levée par signature humaine",
                    },
                )
            if p.get("action") == "unblock" and self.detect.clear(p.get("target")):
                self.summary(p["target"], "green", "SERVICE_CLEARED", 0, [id_])
            if p.get("action") == "set_threshold":
                try:
                    old = self.detect.set(p.get("rule"), p.get("value"))
                    self.summary(
                        "vigie",
                        "tune",
                        f"{p['rule']} {old} -> {self.detect.thresholds[p['rule']]}",
                        0,
                        [id_],
                    )
                except (TypeError, ValueError) as exc:
                    self.summary("human", "block", f"THRESHOLD_REFUSED: {exc}", 0, [id_])
            self.summary("human", "green", "HUMAN_DECISION", 0, [id_])
        elif kind == "heartbeat" and msg["from"] == "risk" and state:
            view = p.get("view", {})
            cash = view.get("cash_eur")
            history = state.get("shadow_cash_history", [])
            if (
                cash is not None
                and "as_of_tick" not in view
                and history
                and min(abs(float(cash) - v) for v in history) > 0.05
            ):
                self.isolate_service(
                    "core-ledger",
                    "LEDGER_CONSERVATION",
                    [id_],
                    {"claimed": cash, "expected": history[-1]},
                )
        elif kind == "price_update" and msg["from"] == "quant":
            self.quant = msg
            if state and msg["tick"] == self.last_clock_tick:
                for asset, estimate in p.get("assets", {}).items():
                    # Anchor: the price the bank last traded at, as the executor signed it;
                    # the tolerance grows by the most the market's noise can move per tick.
                    anchor, at = state.get("anchors", {}).get(asset, (None, None))
                    age = msg["tick"] - at if anchor else 0
                    if (
                        1 <= age <= 5
                        and abs(float(estimate.get("price", anchor)) - anchor) / anchor
                        > 0.02 + 0.005 * age
                    ):
                        self.isolate_service(
                            "market-feed",
                            "ANCHOR_DIVERGENCE",
                            [id_],
                            {
                                "asset": asset,
                                "claimed": estimate.get("price"),
                                "anchor": anchor,
                            },
                        )
        elif kind == "order_proposal":
            try:
                amount = float(p["notional_eur"])
            except (ValueError, TypeError, KeyError):
                return
            if math.isfinite(amount):
                self.orders[msg["from"]].append(
                    (msg["tick"], amount, p.get("asset"), id_)
                )
                if state and not any(self.causality.contaminated(id_)):
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

    def isolate_service(self, service, reason, parents=(), evidence=None):
        first = service not in self.isolated_services
        self.isolated_services.add(service)
        self.causality.isolate(service, self.tick, parents)
        if evidence:
            self.ledger_evidence.append(
                {
                    "service": service,
                    "reason": reason,
                    "proof": list(parents),
                    **evidence,
                }
            )
        critical = service == "core-ledger" and reason == "LEDGER_CONSERVATION"
        bank_changed = critical and self.light != "ROUGE"
        if bank_changed:
            self.light = "ROUGE"
            self.emit("light_change", "*", {"light": "ROUGE", "reason": reason})
        if first or critical:
            count = self.policy.notifications
            self.summary(service, "red" if critical else "block", reason, 2, parents)
            if bank_changed and self.policy.notifications == count:
                self.policy.notifications += 1
                incident = self.policy.incidents.get("source:" + service, {})
                self.emit(
                    "alert",
                    "human",
                    {
                        "kind": "vigie_incident",
                        "notification": True,
                        "incident_id": incident.get("id"),
                        "agent": service,
                        "action": "red",
                        "reason": reason,
                        "layer": 2,
                        "proof": list(parents),
                        "source": service,
                        "victim": False,
                        "text": f"{service} : conservation violée, banque passée au rouge ; examiner les preuves avant reprise humaine.",
                    },
                    parents,
                )

    def quarantine(self, agent, reason, parents=()):
        self.quarantined.add(agent)
        self.registry[agent]["tools"] = set()
        if self.suspicion[agent] < 50:
            self.suspicion[agent] = 100.0
        self.policy.levels[agent] = "QUARANTAINE"
        for family, tick in self.policy.witnesses[agent].items():
            if self.tick - tick <= 30:
                self.policy.layers[agent][str(family)] = "ROUGE"
        if reason == "CONFIG_CHANGED":
            self.policy.layers[agent]["1"] = "ROUGE"
        if len(self.quarantined) >= 2 and self.light != "ROUGE":
            self.light = "ROUGE"
            self.emit(
                "light_change",
                "*",
                {"light": "ROUGE", "reason": "MULTIPLE_QUARANTINES"},
            )
            self.summary("bank", "red", "MULTIPLE_QUARANTINES", 2, parents)
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
        ancestors = {}
        pending = list(msg["depends_on"])
        while pending:
            id_ = pending.pop()
            if id_ in ancestors:
                continue
            parent = self.seen.get(id_)
            if not parent:
                raise Refused("INVALID_ANCESTRY")
            ancestors[id_] = parent
            pending.extend(parent["depends_on"])
        if source["message_id"] not in ancestors:
            raise Refused("INVALID_ANCESTRY")
        compliance = list(ancestors.values())
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
            raise Refused(
                "MISSING_COMPLIANCE_PROOF"
                if source["type"] == "payment_proposal"
                else "INVALID_ANCESTRY"
            )
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
        if source["type"] == "payment_proposal" or source["payload"].get("client_id"):
            client = source["payload"].get(
                "client_id", source["payload"].get("beneficiary_id")
            )
            proof_client = compliance_proposal.get("payload", {}).get(
                "client_id",
                compliance_proposal.get("payload", {}).get("beneficiary_id"),
            )
            if not client or proof_client != client:
                raise Refused("MISSING_COMPLIANCE_PROOF")
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
        contaminated, invalid = self.causality.contaminated(msg["message_id"])
        if contaminated:
            for sid in contaminated:
                if (
                    sid not in self.isolated_services
                    and sid in self.cfg["services"]
                    and state.get("services", {}).get(sid, {}).get("mode") not in {"stale", "down"}
                    and not self.detect.suspect.get(sid)
                ):
                    continue
                self.isolate_service(sid, "UNTRUSTED_PROVENANCE", [msg["message_id"]])
            raise Refused("UNTRUSTED_PROVENANCE")
        if invalid:
            raise Refused("INVALID_ANCESTRY")
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
                or sid in self.isolated_services
                or status.get("mode") in {"stale", "down"}
                or self.detect.suspect_at(sid, entry.get("tick", -1))
            ):
                if isinstance(sid, str):
                    self.isolate_service(
                        sid, "UNTRUSTED_PROVENANCE", [msg["message_id"]]
                    )
                raise Refused("UNTRUSTED_PROVENANCE")
            if entry.get("version") != self.cfg["services"][sid]["version"]:
                self.isolate_service(sid, "UNTRUSTED_PROVENANCE", [msg["message_id"]])
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
        if self.policy.levels[source["from"]] == "ORANGE":
            if (
                amount
                > self.cfg["agents"][
                    "trader" if source["type"] == "order_proposal" else "payment"
                ][
                    "max_order_eur"
                    if source["type"] == "order_proposal"
                    else "max_transfer_eur"
                ]
                * 0.5
            ):
                raise Refused("AGENT_SLOWED")
            if self.tick - self.last_approved.get(source["from"], -1000) < 5:
                raise Refused("AGENT_RATE_LIMIT")
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
        confidence = float(order.get("quant_confidence", 0))
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise Refused("INVALID_CONFIDENCE")
        if confidence < t["min_confidence"]:
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
        data = structuring(
            list(self.orders[agent]),
            self.tick,
            self.cfg["agents"]["trader"]["max_order_eur"],
            self.profiles.get(agent, {}),
        )
        self.behavior_details[agent] = data
        return data["alert"]

    def coherence(self, source, state):
        p = source["payload"]
        if source["type"] == "order_proposal":
            quant = next(
                (
                    self.seen.get(id_)
                    for id_ in source["depends_on"]
                    if self.seen.get(id_, {}).get("from") == "quant"
                    and self.seen.get(id_, {}).get("type") == "price_update"
                ),
                None,
            )
            est = (quant or {}).get("payload", {}).get("assets", {}).get(p["asset"])
            if est and abs(
                float(p.get("quant_confidence", 0)) - float(est["confidence"])
            ) > self.profiles.get(source["from"], {}).get(
                "confidence_gap_threshold", 0.3
            ):
                return "INCOHERENT_CONFIDENCE"
            if (
                est
                and float(p.get("quant_confidence", 0))
                >= self.cfg["agents"]["trader"]["min_confidence"]
                and est["confidence"] < self.cfg["agents"]["trader"]["min_confidence"]
            ):
                return "INCOHERENT_CONFIDENCE"
            if "faillite" in p.get("justification", "").casefold():
                reliable = any(
                    p["asset"] in text and "faillite" in text.casefold()
                    for text in state.get("news", [])
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
            self.summary(agent, "block", mismatch, 4, [source["message_id"]])
            self.known_bad_messages.add(source["message_id"])
            return mismatch
        return "BEHAVIOR_CUMULATIVE" if anomaly else None

    def approve(self, msg, state):
        if self.layer3.authenticate(msg) is None:
            reason = 'INVALID_SCHEMA'
            if cost(msg) is not None:
                try:
                    self.identity(msg)
                except (Refused, OSError, TypeError, ValueError) as exc:
                    reason = str(exc) if isinstance(exc, (Refused, OSError)) else 'INVALID_SCHEMA'
            self.unverified_notice(msg, reason)
            return Decision(False, reason, 'vigie')
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
            self.policy.layers[agent]["1"] = "VERT"
            self.policy.layers[agent]["2"] = "VERT"
            layer = 4
            self.provenance(msg, state, source)
            if source["type"] == "order_proposal":
                layer = 3
                anomaly = self.analyze(source, state)
                if anomaly:
                    raise Refused(anomaly)
                if self.behavior(source):
                    raise Refused("BEHAVIOR_CUMULATIVE")
            if source["message_id"] in self.known_bad_messages:
                raise Refused("INCOHERENT_DECISION")
            layer = 3
            constraint = self.layer3.request_approval(msg)
            if not constraint.approved:
                raise Refused(constraint.reason)
            self.approved_proposals.add(source["message_id"])
            self.last_approved[agent] = self.tick
            self.summary(agent, "green", "APPROVED", 2, [msg["message_id"]])
            return Decision(True, "APPROVED", "vigie")
        except (Refused, KeyError, TypeError, ValueError, OSError) as exc:
            reason = str(exc) if isinstance(exc, Refused) else "INVALID_SCHEMA"
            if (
                reason in {"MISSING_COMPLIANCE_PROOF", "INVALID_ANCESTRY"}
                and isinstance(msg, dict)
                and msg.get("payload", {}).get("kind") == "payment"
            ):
                proposal = msg["payload"].get("proposal", {})
                p = proposal.get("payload", {})
                self.claim_evidence.append(
                    {
                        "agent": agent,
                        "client_id": p.get("client_id", p.get("beneficiary_id")),
                        "assertion": p.get(
                            "kyc_justification",
                            p.get("justification", "client pré-validé annoncé"),
                        ),
                        "truth": "Aucune compliance_decision favorable, signée et liée à ce client dans les dépendances valides.",
                        "message_id": msg.get("message_id"),
                        "proof": list(msg.get("depends_on", [])),
                    }
                )
            if reason == "UNTRUSTED_PROVENANCE":
                self.contagion_blocked += 1
            if reason in {"LOSS_LIMIT", "VAR_LIMIT", "EXPOSURE_LIMIT"}:
                self.light = "ROUGE"
                self.emit("light_change", "*", {"light": "ROUGE", "reason": reason})
            if reason == "CONFIG_CHANGED" and agent in self.registry:
                self.quarantine(agent, reason)
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
            "schema_version": 1,
            "tick": self.tick,
            "light": self.light,
            "agents": {
                a: {
                    "role": role_of(a),
                    "lights": dict(self.policy.layers[a]),
                    "suspicion": self.suspicion[a],
                    "level": "QUARANTAINE"
                    if a in self.quarantined
                    else self.policy.levels[a],
                    "quarantined": a in self.quarantined,
                }
                for a in [*agent_ids(self.cfg), "executor"]
            },
            "suspicion": dict(self.suspicion),
            "quarantined": sorted(self.quarantined),
            "contagion_blocked": self.contagion_blocked,
            "isolated_services": sorted(self.isolated_services),
            "ledger_evidence": self.ledger_evidence[-20:],
            "claim_evidence": self.claim_evidence[-20:],
            "profiles": self.profiles,
            **self.policy.status(),
            "behavior": self.behavior_details,
            "cusum": self.detector.observations,
            "contamination_graph": self.causality.agent_graph(),
            "orders": {
                a: [
                    {
                        "tick": o[0],
                        "notional_eur": o[1],
                        "asset": o[2],
                        "message_id": o[3],
                    }
                    for o in list(orders)[-20:]
                ]
                for a, orders in self.orders.items()
            },
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
                    "messages": record["messages"][-8:],
                    "agents": sorted(record["agents"]),
                    "reaction_count": len(record["messages"]),
                }
                for key, record in list(self.propagation.items())[-50:]
            },
            "auditor_llm": self.auditor_enabled,
            "metrics": self.detect.snapshot(),
            "herding": self.herding.snapshot(),
            "herding_couche3": self.layer3.analytics.herding.snapshot(self.layer3.state.tick),
            "actions": list(self.actions)[-150:],
            "graph": self.layer1.graph.snapshot(),
            "layer1": self.layer1.metrics(),
            "findings": list(self.findings),
        }
