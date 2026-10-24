"""Détection et propagation sur preuves publiques, sans accès au store du monde."""
from collections import defaultdict, deque
import math

from sim.config import trader_ids
from vigie.core import Finding
from vigie.injection import Injection


def version(value):
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * max(0, 3 - len(parts))


class Contagion:
    def __init__(self, cfg, state):
        self.state = state
        # L'ouverture du grand livre est un paramètre connu, jamais un solde du moteur.
        self.cash = (cfg["bank"]["capital_eur"] - cfg["bank"]["initial_position_per_trader_eur"]
                     * len(cfg["market"]["assets"]) * len(trader_ids(cfg)))
        self.cash_samples = deque([(0, self.cash)], maxlen=20000)
        self.cash_complete = True
        self.ledger_clock = 0
        self.pending_balances = deque(maxlen=128)
        self.suspects = {}
        self.processed = set()
        self.executions = set()
        self.prices = defaultdict(lambda: deque(maxlen=7))
        self.cve_ticks = {}
        self.radius_ticks = {}
        self.quarantine_ticks = {}
        self.blocked = set()
        self.injection = Injection(state)

    def suspect(self, sid, kind, evidence):
        if sid in self.suspects:
            return []
        finding = Finding(self.state.tick, 3, kind, sid, "warning", list(evidence), 1.0)
        self.suspects[sid] = finding
        # Aucun statut de compromission n'est affirmé : une CVE est une suspicion.
        return [finding]

    def on_message(self, raw):
        id_ = raw.get("message_id")
        msg = self.state.messages.get(id_)
        if msg is None or id_ in self.processed:
            return []
        self.processed.add(id_)
        p, kind, sender = msg["payload"], msg["type"], msg["from"]
        findings = self.injection.on_message(msg)
        for finding in findings:
            self.suspects[finding.target] = finding
        if kind == "tick" and sender == "clock":
            self.ledger_clock = max(self.ledger_clock, msg["tick"])
            self.cash_samples.append((msg["tick"], self.cash))
            pending = self.pending_balances
            self.pending_balances = deque(maxlen=128)
            for heartbeat, as_of in pending:
                if self.ledger_clock >= as_of + 2:
                    findings += self.check_balance(heartbeat, as_of)
                else:
                    self.pending_balances.append((heartbeat, as_of))
        if kind == "alert" and sender == "secu" and p.get("kind") == "cve_published":
            affects = p.get("cve", {}).get("affects", {})
            for sid, meta in sorted(self.state.meta.items()):
                try:
                    vulnerable = (meta.get("language") == affects.get("language")
                                  and version(meta["php_version"]) < version(affects["version_lt"]))
                except (KeyError, ValueError, AttributeError):
                    vulnerable = False
                if vulnerable:
                    self.cve_ticks.setdefault(sid, msg["tick"])
                    findings += self.suspect(sid, "published_cve", [id_])
        if kind == "execution" and sender == "executor" and p.get("status") == "executed":
            exec_id = p.get("exec_id", id_)
            if exec_id not in self.executions:
                try:
                    if p.get("kind") == "order":
                        qty, price = float(p["qty"]), float(p["fill_price"])
                        if not math.isfinite(qty) or not math.isfinite(price) or price <= 0:
                            raise ValueError("exécution non finie")
                        delta = -qty * price
                    elif p.get("kind") == "payment":
                        amount = float(p["amount_eur"])
                        if not math.isfinite(amount) or amount <= 0:
                            raise ValueError("virement non fini")
                        delta = -amount
                    else:
                        raise ValueError("exécution inconnue")
                    self.cash += delta
                    self.executions.add(exec_id)
                    self.cash_samples.append((max(self.ledger_clock, msg["tick"]), self.cash))
                except (KeyError, TypeError, ValueError):
                    pass
        if sender == "risk" and kind == "heartbeat" and self.cash_complete:
            view = p.get("view", {})
            if view.get("cash_eur") is not None:
                as_of = view.get("as_of_tick", msg["tick"])
                if type(as_of) is not int or as_of < 0 or as_of > msg["tick"] + 1:
                    # Une référence impossible ne devient pas une suspicion de service.
                    raise ValueError("Tick de vue Risk incohérent")
                if "as_of_tick" in view and self.ledger_clock < as_of + 2:
                    if len(self.pending_balances) == self.pending_balances.maxlen:
                        raise ValueError("Trop de vues Risk en attente")
                    self.pending_balances.append((msg, as_of))
                else:
                    findings += self.check_balance(msg, as_of)
        if kind == "price_update" and sender == "quant":
            sources = {e.get("service") for e in msg["data_provenance"]}
            if "market-feed" in sources:
                for asset, estimate in p.get("assets", {}).items():
                    try:
                        price = float(estimate["price"])
                        if not math.isfinite(price) or price <= 0:
                            continue
                    except (KeyError, TypeError, ValueError):
                        continue
                    series = self.prices[asset]
                    if series and series[-1][0] == msg["tick"]:
                        continue
                    series.append((msg["tick"], price, id_))
                    prices = [v[1] for v in series]
                    reason = None
                    if len(prices) >= 2 and abs(prices[-1] / prices[-2] - 1) > 0.20:
                        reason = "price_jump"
                    elif len(prices) == 7:
                        changes = [b / a - 1 for a, b in zip(prices, prices[1:])]
                        if all(abs(c) < 1e-9 for c in changes):
                            reason = "price_frozen"
                        elif (all(c < -0.005 for c in changes) or all(c > 0.005 for c in changes)) and abs(prices[-1] / prices[0] - 1) > 0.08:
                            reason = "monotone_drift"
                    if reason:
                        findings += self.suspect("market-feed", reason, [v[2] for v in series])
        candidates = self.state.messages.values() if findings else [msg]
        for candidate in candidates:
            for sid in self.trace(candidate):
                self.radius_ticks.setdefault(sid, self.state.tick)
        return findings

    def check_balance(self, heartbeat, as_of):
        if not self.cash_complete: return []
        # La comparaison reste sur le tick DÉCLARÉ de la vue, même si le
        # heartbeat arrive tard. ±1 tick couvre les lectures HTTP à la frontière
        # du tick ; deux ticks d'attente couvrent la publication de l'exécution
        # après la lecture du ledger. Aucun délai LLM ne dilate cette fenêtre.
        samples = [cash for tick, cash in self.cash_samples if as_of - 1 <= tick <= as_of + 1]
        try:
            claim = float(heartbeat["payload"]["view"]["cash_eur"])
            if samples and (not math.isfinite(claim) or min(abs(claim-v) for v in samples) > .05):
                return self.suspect("core-ledger", "ledger_mismatch",
                                    [heartbeat["message_id"]] + self.execution_evidence())
        except (TypeError, ValueError):
            pass
        return []

    def execution_evidence(self):
        return [id_ for id_, m in self.state.messages.items()
                if m["from"] == "executor" and m["type"] == "execution"
                and m["payload"].get("status") == "executed"]

    def trace(self, msg):
        """Une chaîne par source, avec la preuve qui a fait naître sa suspicion."""
        pending = [(msg, [msg["message_id"]])]
        visited, chains = set(), {}
        while pending:
            current, path = pending.pop()
            id_ = current["message_id"]
            if id_ in visited:
                continue
            visited.add(id_)
            for target in self.injection.targets(current):
                if target not in chains:
                    chains[target] = list(reversed(path)) + self.suspects[target].evidence
            for entry in current.get("data_provenance", []):
                sid = entry.get("service")
                if sid in self.suspects and sid not in chains:
                    chains[sid] = list(reversed(path)) + self.suspects[sid].evidence
            for parent in current.get("depends_on", []):
                if parent in self.state.messages:
                    pending.append((self.state.messages[parent], path + [parent]))
        return chains

    def marked(self, msg):
        chains = self.trace(self.state.messages.get(msg["message_id"], msg))
        evidence = list(dict.fromkeys(id_ for sid in sorted(chains) for id_ in chains[sid]))
        return bool(chains), evidence

    def quarantine(self, msg):
        """Le portique refuse cette opération seulement ; aucune sanction d'agent."""
        chains = self.trace(self.state.messages.get(msg["message_id"], msg))
        for sid in chains:
            self.quarantine_ticks.setdefault(sid, self.state.tick)
        if chains:
            self.blocked.add(msg["message_id"])
        return self.marked(msg)

    def metrics(self):
        roots, marked_agents, marked_ids = set(), set(), set()
        root_amount = total_amount = 0.0
        for id_, msg in self.state.messages.items():
            chains = self.trace(msg)
            if not chains:
                continue
            marked_ids.add(id_)
            if msg["from"] not in {"clock", "world", "vigie", "secu", "inject", "human"} and not msg["from"].startswith("svc:"):
                marked_agents.add(msg["from"])
            for sid in chains:
                self.radius_ticks.setdefault(sid, self.state.tick)
            direct = bool(self.injection.targets(msg)) or any(e.get("service") in self.suspects for e in msg["data_provenance"])
            # Une racine n'hérite pas déjà d'un parent marqué : les montants
            # décision/compliance/exécution d'une même opération ne s'additionnent pas.
            inherited = any(self.trace(self.state.messages[parent]) for parent in msg["depends_on"] if parent in self.state.messages)
            if direct and not inherited:
                roots.add(id_)
            if msg["type"] in {"order_proposal", "payment_proposal"}:
                amount = float(msg["payload"].get("notional_eur", msg["payload"].get("amount_eur", 0)))
                total_amount += amount
                if direct:
                    root_amount += amount
        return {
            "marked_messages": len(marked_ids), "marked_agents": sorted(marked_agents),
            "root_messages": len(roots),
            "r0": len(marked_agents) / len(roots) if roots else 0.0,
            "impact_multiplier": total_amount / root_amount if root_amount else None,
            "impact_eur": round(total_amount, 2),
            "mastery": {sid: {"cve_tick": self.cve_ticks.get(sid), "detected_tick": f.tick,
                "radius_tick": self.radius_ticks.get(sid), "quarantine_tick": self.quarantine_ticks.get(sid),
                "cve_to_radius_ticks": self.radius_ticks[sid] - self.cve_ticks[sid]
                    if sid in self.radius_ticks and sid in self.cve_ticks else None,
                "cve_to_quarantine_ticks": self.quarantine_ticks[sid] - self.cve_ticks[sid]
                    if sid in self.quarantine_ticks and sid in self.cve_ticks else None}
                for sid, f in sorted(self.suspects.items())},
        }
