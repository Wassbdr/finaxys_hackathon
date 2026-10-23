"""Gel provisoire de couche 3, limité à un actif, un sens et des traders prouvés."""
from collections import Counter, defaultdict, deque
import json
import math
from pathlib import Path
import re

from vigie.core import Finding
from vigie.herding import Herding


def validate_profile(profile):
    if (profile.get("version") != 1 or not profile.get("training_seeds")
            or 42 in profile["training_seeds"]):
        raise ValueError("La graine 42 est réservée à l'évaluation")
    for field in ("freeze_ticks", "window_ticks", "persistence_ticks", "min_aligned"):
        if type(profile.get(field)) is not int or profile[field] <= 0:
            raise ValueError(f"Paramètre de gel invalide : {field}")
    if profile["min_aligned"] < 2:
        raise ValueError("Un troupeau exige plusieurs traders")
    values = [*profile["reference"].values(), profile["margin"], profile["similarity_threshold"]]
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Référence de gel non finie")
    return profile


def load_profile():
    path = Path(__file__).with_name("freeze_profile.json")
    return validate_profile(json.loads(path.read_text())) if path.exists() else None


class Freeze:
    def __init__(self, state, traders, profile=None):
        self.state, self.traders = state, tuple(traders)
        self.profile = validate_profile(profile) if profile else None
        self.window = profile["window_ticks"] if profile else 30
        self.events = deque()
        self.first_refs = {}
        self.active = {}
        self.since = {}
        self.released = set()
        self.transitions = []
        self.blocked = set()
        self.frames = []
        self.record = False

    def observe(self, msg):
        if msg["type"] in {"order_proposal", "price_update", "execution", "llm_trace", "alert"}:
            self.events.append(msg)
        if msg["type"] == "order_proposal" and msg["from"] in self.traders:
            for ref in msg["payload"].get("news_refs", []):
                if isinstance(ref, dict) and ref.get("id"):
                    self.first_refs.setdefault(ref["id"], msg["tick"])

    def features(self, tick):
        self.events = deque(m for m in self.events if tick - self.window < m["tick"] <= tick)
        orders = [m for m in self.events if m["type"] == "order_proposal" and m["from"] in self.traders]
        assets = sorted({m["payload"].get("asset") for m in orders if m["payload"].get("asset")})
        result = []
        for asset in assets:
            asset_orders = [m for m in orders if m["payload"]["asset"] == asset and m["payload"].get("side") in {"buy", "sell"}]
            votes = defaultdict(int)
            for msg in asset_orders:
                votes[msg["from"]] += 1 if msg["payload"]["side"] == "buy" else -1
            sides = {agent: "buy" if vote > 0 else "sell" for agent, vote in votes.items() if vote}
            if len(sides) < 2:
                continue
            counts = Counter(sides.values())
            if counts["buy"] == counts["sell"]:
                continue
            side = counts.most_common(1)[0][0]
            aligned = sorted(agent for agent in sides if sides[agent] == side)
            relevant = []
            for msg in self.events:
                p, kind = msg["payload"], msg["type"]
                if kind == "order_proposal" and p.get("asset") == asset:
                    relevant.append(msg)
                elif kind == "execution" and p.get("asset") == asset:
                    relevant.append(msg)
                elif kind == "price_update" and msg["from"] == "quant" and asset in p.get("assets", {}):
                    relevant.append({**msg, "payload": {"assets": {asset: p["assets"][asset]}}})
                elif kind == "llm_trace" and msg["from"] in aligned:
                    response = p.get("response", {})
                    text = response.get("justification", "")
                    if asset in response.get("sentiment", {}) or re.search(r"\b" + re.escape(asset) + r"\b", text, re.I):
                        relevant.append(msg)
            # Le calcul livré est réutilisé sans changer sa logique ; le filtre
            # par actif évite qu'un signal sur BOREAL gèle aussi ACME.
            engine = Herding(self.traders, self.window)
            engine.events = deque(relevant)
            metrics = engine.snapshot(tick)
            loops = engine._loop_evidence if side == "sell" else []
            traces = [m["message_id"] for m in relevant if m["type"] == "llm_trace"
                      and m["payload"].get("response", {}).get("justification")]
            sources = defaultdict(list)
            all_sources = defaultdict(set)
            for order in asset_orders:
                for ref in order["payload"].get("news_refs", []):
                    if not isinstance(ref, dict) or not ref.get("id"):
                        continue
                    source = ref.get("source")
                    if not source and any(e.get("service") == "news-wire" for e in order.get("data_provenance", [])):
                        source = "news-wire"
                    if source:
                        all_sources[ref["id"]].add(source)
            for msg in asset_orders:
                if msg["from"] not in aligned or msg["payload"]["side"] != side:
                    continue
                for ref in msg["payload"].get("news_refs", []):
                    if not isinstance(ref, dict) or not ref.get("id"):
                        continue
                    source = ref.get("source")
                    if not source:
                        news_services = {e.get("service") for e in msg.get("data_provenance", []) if e.get("service") == "news-wire"}
                        source = next(iter(news_services)) if len(news_services) == 1 else None
                    if source and self.first_refs.get(ref["id"], -self.window) > tick - self.window:
                        sources[(ref["id"], source)].append(msg)
            corroborated = any(m["type"] == "alert" and m["from"] in {"world", "inject", "secu"}
                and m["payload"].get("asset") == asset
                and m["payload"].get("kind") in {"market_event", "source_report"} for m in self.events)
            unique = []
            if not corroborated:
                for (ref, source), messages in sources.items():
                    same_ref_sources = all_sources[ref]
                    if len(same_ref_sources) == 1 and len({m["from"] for m in messages}) >= 2:
                        unique.extend(m["message_id"] for m in messages)
            result.append({"tick": tick, "asset": asset, "side": side, "aligned": aligned,
                "index": metrics["herding_index"], "loop_evidence": list(dict.fromkeys(loops)),
                "similarity": metrics["llm_similarity"], "trace_evidence": traces,
                "unique_evidence": list(dict.fromkeys(unique)),
                "order_evidence": [m["message_id"] for m in asset_orders if m["from"] in aligned]})
        return result

    @staticmethod
    def signal(frame, profile):
        reference = profile["reference"].get(frame["asset"])
        if (reference is None or frame["index"] is None
                or frame["index"] <= reference + profile["margin"]
                or len(frame["aligned"]) < profile["min_aligned"]):
            return None
        if frame["loop_evidence"]:
            return "reflexive_loop", frame["loop_evidence"]
        if frame["similarity"] is not None and frame["similarity"] >= profile["similarity_threshold"]:
            return "llm_similarity", frame["trace_evidence"]
        if frame["unique_evidence"]:
            return "unique_uncorroborated_source", frame["unique_evidence"]
        return None

    def _end(self, asset, tick, reason, proof=()):
        record = self.active.pop(asset)
        evidence = list(dict.fromkeys([*record["evidence"], *proof]))
        self.transitions.append({"tick": tick, "asset": asset, "event": "end", "reason": reason, "evidence": evidence})
        return Finding(tick, 3, "herding_freeze", asset, "info", evidence, 0.0)

    def on_message(self, msg):
        self.observe(msg)
        tick = self.state.tick
        findings = []
        if msg["type"] == "human_decision" and msg["from"] == "human" and msg["payload"].get("action") in {"unfreeze", "unblock"}:
            target = msg["payload"].get("target")
            for asset in list(self.active):
                if target in {asset, "*"}:
                    findings.append(self._end(asset, tick, "signed_human", [msg["message_id"]]))
                    self.released.add(asset)
        if msg["type"] not in {"tick", "order_proposal"}:
            return findings
        frames = self.features(tick)
        if self.record:
            self.frames.append({"tick": tick, "frames": frames})
        return findings + self.update(frames, tick)

    def update(self, frames, tick):
        findings = []
        if not self.profile:
            return findings
        signals = {}
        for frame in frames:
            witness = self.signal(frame, self.profile)
            if not witness:
                continue
            key = (frame["asset"], frame["side"])
            signals[key] = (frame, witness)
        self.since = {key: self.since.get(key, tick) for key in signals}
        signaled_assets = {key[0] for key in signals}
        self.released.intersection_update(signaled_assets)
        for key, (frame, (witness, proof)) in signals.items():
            asset, side = key
            if asset in self.released or tick - self.since[key] + 1 < self.profile["persistence_ticks"]:
                continue
            if asset in self.active:
                # Le bail reste ciblé sur son sens et sa cohorte d'origine.
                if self.active[asset]["side"] == side:
                    self.active[asset]["until_tick"] = tick + self.profile["freeze_ticks"]
                continue
            evidence = list(dict.fromkeys([*frame["order_evidence"], *proof]))
            self.active[asset] = {"side": side, "traders": frame["aligned"], "since_tick": tick,
                "until_tick": tick + self.profile["freeze_ticks"], "evidence": evidence, "witness": witness}
            self.transitions.append({"tick": tick, "asset": asset, "event": "start", **self.active[asset]})
            findings.append(Finding(tick, 3, "herding_freeze", asset, "warning", evidence, float(frame["index"])))
        for asset in list(self.active):
            if tick >= self.active[asset]["until_tick"]:
                findings.append(self._end(asset, tick, "expired"))
        return findings

    def refusal(self, msg):
        if msg.get("payload", {}).get("kind") != "order":
            return False, []
        source_id = msg["payload"].get("proposal", {}).get("message_id")
        source = self.state.messages.get(source_id)
        if not source or source["type"] != "order_proposal":
            return False, []
        p = source["payload"]
        record = self.active.get(p.get("asset"))
        if (not record or self.state.tick >= record["until_tick"] or p.get("side") != record["side"]
                or source["from"] not in record["traders"]):
            return False, []
        self.blocked.add(msg["message_id"])
        return True, list(dict.fromkeys([*record["evidence"], source_id, msg["message_id"]]))
