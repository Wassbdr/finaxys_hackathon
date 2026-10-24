"""Gel provisoire de couche 3, limité à un actif, un sens et des traders prouvés."""
from collections import Counter, defaultdict, deque
import copy
import json
import math
from pathlib import Path
import re

from vigie.core import Finding
from vigie.herding import Herding
from vigie.validation import valid_message

MAX_EVENTS = 2048
MAX_REFERENCES = 4096
MAX_PROOF = 128


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
    def __init__(self, state, traders, profile=None, *, assets=None, required=False):
        self.state, self.traders = state, tuple(traders)
        self.profile = validate_profile(profile) if profile else None
        self.window = profile["window_ticks"] if profile else 30
        self.assets = set(assets if assets is not None else (profile or {}).get("reference", {}))
        self.enabled = bool(profile) or required
        self.uncertain = set()
        self.clock = None
        self.last_update = None
        self.exposure = {}
        self.risk_exposure = {}
        self.lease_guard = {}
        self.events = deque(maxlen=MAX_EVENTS)
        self.first_refs = {}
        self.active = {}
        self.since = {}
        self.released = set()
        self.transitions = deque(maxlen=256)
        self.blocked = set()
        self.blocked_count = 0
        self.last_refusal = "freeze_state_unavailable"
        self.frames = deque(maxlen=8192)
        self.record = False
        if required and not profile:
            self.degrade("profile_unavailable")

    def degrade(self, reason):
        self.uncertain.add(reason if reason in {"profile_unavailable", "invalid_message", "capacity", "clock_regression",
            "checkpoint_unavailable", "checkpoint_write", "lease_lost", "analysis_error"} else "analysis_error")

    def logical_tick(self):
        return self.state.tick if self.clock is None else self.clock

    def snapshot(self):
        return {"version": 1, "clock": self.clock, "last_update": self.last_update,
            "active": self.active, "lease_guard": self.lease_guard, "risk_exposure": {k:list(v) for k,v in self.risk_exposure.items()}, "exposure": [
                [*key, list(ticks)] for key, ticks in self.exposure.items()], "released": sorted(self.released),
            "uncertain": sorted(self.uncertain), "events": list(self.events), "first_refs": self.first_refs}

    def restore(self, data):
        try:
            if data["version"] != 1 or data["active"] != data["lease_guard"]:
                raise ValueError("Baux incohérents")
            if len(data['active']) > len(self.assets) or len(data['events']) > MAX_EVENTS or len(data['first_refs']) > MAX_REFERENCES:
                raise ValueError("Capacité dépassée")
            if data['clock'] is not None and (type(data['clock']) is not int or data['clock'] < 0):
                raise ValueError("Horloge invalide")
            for asset, record in data['active'].items():
                if (asset not in self.assets or record['side'] not in ('buy','sell')
                    or type(record['until_tick']) is not int or not set(record['traders']) <= set(self.traders)
                    or not record['evidence'] or len(record['evidence']) > MAX_PROOF):
                    raise ValueError("Bail invalide")
            if any(not valid_message(m, self.assets) for m in data['events']):
                raise ValueError("Événement invalide")
            self.clock, self.last_update = data['clock'], data['last_update']
            self.active = copy.deepcopy(data['active'])
            self.lease_guard = copy.deepcopy(data['lease_guard'])
            self.events = deque(data['events'], maxlen=MAX_EVENTS)
            self.first_refs = dict(data['first_refs'])
            self.exposure = {(a,s): deque(ticks, maxlen=self.window) for a,s,ticks in data['exposure']}
            if len(self.exposure) > 2*len(self.assets): raise ValueError("Accumulation invalide")
            self.risk_exposure = {k:deque(v, maxlen=self.window) for k,v in data['risk_exposure'].items()}
            if len(self.risk_exposure) > 128: raise ValueError('Causes saturées')
            self.released = set(data['released'])
            self.uncertain.update(data['uncertain'])
            return True
        except (KeyError, ValueError, TypeError, AttributeError):
            self.degrade("checkpoint_unavailable")
            return False

    def observe(self, msg):
        if not valid_message(msg, self.assets):
            self.degrade("invalid_message")
            return False
        if msg['type']=='llm_trace' and sum(m['type']=='llm_trace' for m in self.events)>=64:
            self.degrade('capacity')
            return False
        if len(self.events) >= MAX_EVENTS:
            self.degrade("capacity")
            return False
        if msg["type"] in {"order_proposal", "price_update", "execution", "llm_trace", "alert"}:
            self.events.append(msg)
        if msg["type"] == "order_proposal" and msg["from"] in self.traders:
            for ref in msg["payload"].get("news_refs", []):
                if isinstance(ref, dict) and ref.get("id"):
                    if ref["id"] not in self.first_refs and len(self.first_refs) >= MAX_REFERENCES:
                        self.degrade("capacity")
                        return False
                    self.first_refs.setdefault(ref["id"], msg["tick"])
        return True

    def features(self, tick, *, directional=False):
        self.events = deque((m for m in self.events if tick - self.window < m["tick"]), maxlen=MAX_EVENTS)
        orders = [m for m in self.events if m["tick"] <= tick and m["type"] == "order_proposal" and m["from"] in self.traders]
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
                if msg['tick'] > tick: continue
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
                    source = None
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
                    source = None
                    if not source:
                        news_services = {e.get("service") for e in msg.get("data_provenance", []) if e.get("service") == "news-wire"}
                        source = next(iter(news_services)) if len(news_services) == 1 else None
                    source = source or 'unverified'
                    if self.first_refs.get(ref["id"], -self.window) > tick - self.window:
                        sources[(ref["id"], source)].append(msg)
            corroborated = any(m['tick'] <= tick and m["type"] == "alert" and m["from"] in {"world", "inject", "secu"}
                and m["payload"].get("asset") == asset
                and m["payload"].get("kind") in {"market_event", "source_report"} for m in self.events)
            unique = []
            if not corroborated:
                for (ref, source), messages in sources.items():
                    if len({m["from"] for m in messages}) >= 2:
                        unique.extend(m["message_id"] for m in messages)
            result.append({"tick": tick, "asset": asset, "side": side, "aligned": aligned,
                "index": metrics["herding_index"], "loop_evidence": list(dict.fromkeys(loops)),
                "similarity": metrics["llm_similarity"], "trace_evidence": traces,
                "unique_evidence": list(dict.fromkeys(unique)),
                "order_evidence": [m["message_id"] for m in asset_orders if m["from"] in aligned],
                "cause_keys": sorted({'news:'+r['id'] for m in asset_orders if m['from'] in aligned
                    for r in m['payload'].get('news_refs', []) if self.first_refs.get(r['id'], -self.window) > tick-self.window}) or ['asset:'+asset]})
        if not directional:
            # Les volumes bruts ne disparaissent pas lorsqu'un petit ordre opposé
            # annule une direction nette ou lorsque le troupeau alterne ses côtés.
            existing = {(f['asset'],f['side']) for f in result}
            for asset in assets:
                asset_orders = [m for m in orders if m['payload']['asset'] == asset]
                sides_by_actor = defaultdict(set)
                for order in asset_orders: sides_by_actor[order['from']].add(order['payload']['side'])
                if not any(len(sides)==2 for sides in sides_by_actor.values()): continue
                for side in ('buy','sell'):
                    if (asset,side) in existing: continue
                    selected = [m for m in asset_orders if m['payload']['side']==side]
                    if len({m['from'] for m in selected}) < (self.profile or {}).get('min_aligned',3): continue
                    clone = Freeze(self.state, self.traders, self.profile, assets=self.assets)
                    clone.first_refs = self.first_refs
                    clone.events = deque((m for m in self.events if m['type']!='order_proposal' or
                        (m['payload'].get('asset')==asset and m['payload'].get('side')==side)), maxlen=MAX_EVENTS)
                    result.extend(clone.features(tick, directional=True))
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

    def compact_proof(self, ids):
        unique=list(dict.fromkeys(ids))
        selected, actors=[], set()
        for mid in unique:
            actor=self.state.messages.get(mid,{}).get('from')
            if actor is not None and actor not in actors:
                actors.add(actor); selected.append(mid)
        return list(dict.fromkeys(selected+unique))[:MAX_PROOF]

    def _end(self, asset, tick, reason, proof=()):
        record = self.active.pop(asset)
        self.lease_guard.pop(asset, None)
        evidence = self.compact_proof([*proof, *record['evidence']])
        self.transitions.append({"tick": tick, "asset": asset, "event": "end", "reason": reason, "evidence": evidence})
        return Finding(tick, 3, "herding_freeze", asset, "info", evidence, 0.0)

    def on_message(self, msg):
        if not self.observe(msg):
            return []
        if msg["type"] == "tick" and msg["from"] == "clock":
            if self.clock is not None and msg["tick"] < self.clock:
                self.degrade("clock_regression")
                return []
            self.clock = msg["tick"]
        tick = self.logical_tick()
        findings = []
        if msg["type"] == "human_decision" and msg["from"] == "human" and msg["payload"].get("action") in {"unfreeze", "unblock"}:
            target = msg["payload"].get("target")
            for asset in list(self.active):
                if target in {asset, "*"}:
                    findings.append(self._end(asset, tick, "signed_human", [msg["message_id"]]))
                    self.released.add(asset)
        if msg["type"] not in {"tick", "order_proposal"}:
            return findings
        try:
            frames = self.features(tick)
        except Exception:
            self.degrade("analysis_error")
            return findings
        if self.record:
            self.frames.append({"tick": tick, "frames": frames})
        try:
            return findings + self.update(frames, tick)
        except Exception:
            self.degrade('analysis_error')
            return findings

    def update(self, frames, tick):
        findings = []
        if self.last_update is not None and tick < self.last_update:
            self.degrade("clock_regression")
            return findings
        self.last_update = tick
        if not self.profile or self.uncertain:
            return findings
        if self.lease_guard != self.active:
            self.degrade("lease_lost")
            return findings
        signals = {}
        for frame in frames:
            witness = self.signal(frame, self.profile)
            if not witness:
                continue
            key = (frame["asset"], frame["side"])
            signals[key] = (frame, witness)
        self.since = {key: self.since.get(key, tick) for key in signals}
        # Une interruption courte, un changement d'actif ou de sens ne remet
        # plus à zéro le danger déjà confirmé dans la fenêtre.
        for key in list(self.exposure):
            self.exposure[key] = deque((t for t in self.exposure[key] if tick-self.window < t <= tick), maxlen=self.window)
            if not self.exposure[key]: self.exposure.pop(key)
        for cause in list(self.risk_exposure):
            self.risk_exposure[cause] = deque((t for t in self.risk_exposure[cause] if tick-self.window < t <= tick), maxlen=self.window)
            if not self.risk_exposure[cause]: self.risk_exposure.pop(cause)
        for key, (frame, _) in signals.items():
            history = self.exposure.setdefault(key, deque(maxlen=self.window))
            if not history or history[-1] != tick: history.append(tick)
            for cause in frame.get('cause_keys', ['asset:'+key[0]]):
                if cause not in self.risk_exposure and len(self.risk_exposure)>=128:
                    self.degrade('capacity')
                    return findings
                history = self.risk_exposure.setdefault(cause, deque(maxlen=self.window))
                if not history or history[-1]!=tick: history.append(tick)
        signaled_assets = {key[0] for key in signals}
        self.released.intersection_update(signaled_assets)
        for key, (frame, (witness, proof)) in signals.items():
            asset, side = key
            accumulated = max([len(self.exposure[key])] + [len(self.risk_exposure.get(c,())) for c in frame.get('cause_keys',['asset:'+asset])])
            if asset in self.released or accumulated < self.profile["persistence_ticks"]:
                continue
            if asset in self.active:
                # Le bail reste ciblé sur son sens et sa cohorte d'origine.
                if side not in self.active[asset].get('sides', [self.active[asset]['side']]):
                    self.active[asset]['sides'] = sorted(set(self.active[asset].get('sides',[self.active[asset]['side']])) | {side})
                    self.active[asset]['evidence'] = self.compact_proof(proof+self.active[asset]['evidence'])
                if side in self.active[asset].get('sides', [self.active[asset]['side']]):
                    self.active[asset]["until_tick"] = tick + self.profile["freeze_ticks"]
                    self.active[asset]["traders"] = sorted(set(self.active[asset]["traders"]) | set(frame["aligned"]))
                    self.lease_guard[asset] = copy.deepcopy(self.active[asset])
                continue
            evidence = self.compact_proof([*proof, *frame['order_evidence']])
            self.active[asset] = {"side": side, "traders": frame["aligned"], "since_tick": tick,
                "until_tick": tick + self.profile["freeze_ticks"], "evidence": evidence, "witness": witness}
            self.lease_guard[asset] = copy.deepcopy(self.active[asset])
            self.transitions.append({"tick": tick, "asset": asset, "event": "start", **self.active[asset]})
            findings.append(Finding(tick, 3, "herding_freeze", asset, "warning", evidence, float(frame["index"])))
        for asset in list(self.active):
            if tick >= self.active[asset]["until_tick"]:
                findings.append(self._end(asset, tick, "expired"))
        return findings

    def refusal(self, msg):
        self.last_refusal = 'freeze_state_unavailable'
        if not self.enabled:
            return False, []
        try:
            if not isinstance(msg, dict) or not isinstance(msg.get("payload"), dict):
                self.degrade("invalid_message")
                return True, []
            p = msg["payload"]
            if p.get("kind") == "payment":
                return bool(self.uncertain), []
            if p.get("kind") != "order":
                return True, []
            source_id = p.get("proposal", {}).get("message_id")
            source = self.state.messages.get(source_id)
            if not source or source.get("type") != "order_proposal":
                return True, []
            order = source["payload"]
            asset = order.get("asset")
            if asset not in self.assets or not self.profile or asset not in self.profile['reference']:
                return True, [source_id]
            if self.lease_guard != self.active: self.degrade("lease_lost")
            record = self.active.get(asset)
            if self.uncertain:
                return True, list((record or self.lease_guard.get(asset, {})).get("evidence", []))[:MAX_PROOF]
            if not record or order.get("side") not in record.get('sides',[record['side']]) or source["from"] not in record["traders"]:
                return False, []
            if len(self.blocked) >= 4096:
                self.degrade("capacity")
            else:
                self.blocked.add(msg["message_id"])
            self.last_refusal = 'herding_freeze'
            self.blocked_count += 1
            return True, self.compact_proof([source_id,msg['message_id'],*record['evidence']])
        except Exception:
            self.degrade("analysis_error")
            return True, []
