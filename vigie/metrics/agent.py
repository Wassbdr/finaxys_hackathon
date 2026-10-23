"""Per-agent metrics, read off what each agent itself puts on the bus."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
import re
import statistics

from sim.config import agent_ids, role_of
from vigie.finding import Finding
from vigie.graph import DECISIONS, number

PROPOSALS = {"order_proposal", "payment_proposal"}
REPEAT_TICKS, REPEAT_MAX = 30, 3
JUSTIFICATION = re.compile(r"""['"]justification['"]\s*:\s*['"](.*?)['"]\s*[,}]""")


def light(pct):
    return "VERT" if pct < 80 else "ORANGE" if pct < 100 else "ROUGE"


def mean(values):
    return round(statistics.fmean(values), 3) if values else None


def action_key(msg):
    p = msg["payload"]
    if msg["type"] == "order_proposal":
        return f"{p.get('side')} {p.get('asset')} {round(number(p.get('notional_eur')), -5):,.0f} EUR"
    return f"pay {p.get('beneficiary_id')} {round(number(p.get('amount_eur')), -3):,.0f} EUR"


def amounts(book):
    return {str(k): number(v) for k, v in book.items()} if isinstance(book, dict) else {}


class AgentMetrics:
    def __init__(self, cfg):
        self.cfg = cfg["agents"]
        self.agents = [*agent_ids(cfg), "executor"]
        self.tick = 0
        self.since = {}
        self.acts = Counter()
        self.now = Counter()  # acts in the open tick
        self.order_eur = Counter()  # notional proposed in the open tick
        self.recent = defaultdict(lambda: deque(maxlen=5))
        self.origin = {}  # message id -> agent whose proposal it decides on
        self.proposals = Counter()
        self.refused = defaultdict(set)
        self.limits = {}
        self.ages = defaultdict(lambda: deque(maxlen=50))
        self.age_by_service = defaultdict(dict)
        self.beats = {}
        self.gaps = defaultdict(lambda: deque(maxlen=50))
        self.actions = defaultdict(deque)
        self.repeats = {}
        self.llm = defaultdict(lambda: {"calls": 0, "confidence": deque(maxlen=50), "latency_ms": deque(maxlen=50), "tokens_in": 0, "tokens_out": 0, "models": Counter(), "justifications": deque(maxlen=20)})
        self.fresh = []

    def observe(self, msg):
        self.tick = max(self.tick, msg["tick"])
        kind, sender, p, id_ = msg["type"], msg["from"], msg["payload"], msg["message_id"]
        if sender not in self.agents:
            return
        self.since.setdefault(sender, msg["tick"])
        if kind == "heartbeat":
            self.heartbeat(sender, p, msg["tick"])
        elif kind == "llm_trace":
            self.trace(sender, p)
        if kind not in DECISIONS:
            return
        self.acts[sender] += 1
        self.now[sender] += 1
        self.recent[sender].append(id_)
        ticks = [e["tick"] for e in msg["data_provenance"] if isinstance(e, dict) and isinstance(e.get("tick"), int)]
        if ticks:
            # Age of the freshest input: news age by nature, a decision with no fresh input does not.
            self.ages[sender].append(msg["tick"] - max(ticks))
            for e in msg["data_provenance"]:
                if isinstance(e, dict) and isinstance(e.get("tick"), int):
                    self.age_by_service[sender][e.get("service")] = msg["tick"] - e["tick"]
        if kind in PROPOSALS:
            self.origin[id_] = sender
            self.proposals[sender] += 1
            if kind == "order_proposal":
                self.order_eur[sender] += number(p.get("notional_eur"))
            self.repeat(sender, msg)
            return
        proposer = next((self.origin[d] for d in msg["depends_on"] if d in self.origin), None)
        if proposer is None:
            return
        self.origin[id_] = proposer
        refused = p.get("approved") is False or (kind == "execution" and p.get("status") not in (None, "executed"))
        if refused:
            root = next((d for d in msg["depends_on"] if self.origin.get(d) == proposer), id_)
            self.refused[proposer].add(root)

    def heartbeat(self, agent, p, tick):
        if agent in self.beats and tick > self.beats[agent]:
            self.gaps[agent].append(tick - self.beats[agent])
        self.beats[agent] = tick
        a, used = self.cfg.get(role_of(agent), {}), {}
        if role_of(agent) == "trader":
            scale = number(p.get("limits_scale"), 1.0) or 1.0
            positions = amounts(p.get("positions_eur_seen"))
            if positions:
                used["position"] = max(abs(v) for v in positions.values()) / (a["max_position_eur"] * scale)
            if "pnl_day_eur_seen" in p:
                used["daily_loss"] = max(0.0, -number(p["pnl_day_eur_seen"])) / a["max_daily_loss_eur"]
        elif agent == "payment" and "committed_day_eur" in p:
            used["daily_payments"] = number(p["committed_day_eur"]) / a["max_daily_eur"]
        elif agent == "risk" and isinstance(p.get("view"), dict):
            v = amounts(p["view"])
            used["var"] = v.get("var_eur", 0) / a["max_var_eur"]
            used["exposure"] = v.get("gross_eur", 0) / a["max_exposure_eur"]
            used["daily_loss"] = max(0.0, -v.get("pnl_day_eur", 0)) / a["max_daily_loss_eur"]
            if v.get("cash_eur", 0) > 0:
                used["cash_cushion"] = a["min_cash_eur"] / v["cash_eur"]
        if used:
            self.limits[agent] = {k: {"pct": round(100 * x, 1), "light": light(100 * x)} for k, x in used.items()}

    def trace(self, agent, p):
        s = self.llm[agent]
        s["calls"] += 1
        if isinstance(p.get("declared_confidence"), (int, float)):
            s["confidence"].append(p["declared_confidence"])
        if isinstance(p.get("latency_ms"), (int, float)):
            s["latency_ms"].append(p["latency_ms"])
        tokens = p.get("tokens") or {}
        s["tokens_in"] += int(tokens.get("input") or 0)
        s["tokens_out"] += int(tokens.get("output") or 0)
        s["models"][str(p.get("model"))] += 1
        found = JUSTIFICATION.search(str(p.get("response", "")))
        if found:
            s["justifications"].append(found.group(1))

    def repeat(self, agent, msg):
        window, key = self.actions[agent], action_key(msg)
        window.append((msg["tick"], key, msg["message_id"]))
        while window and window[0][0] <= msg["tick"] - REPEAT_TICKS:
            window.popleft()
        same = [i for _, k, i in window if k == key]
        if len(same) > self.repeats.get(agent, {}).get("max", 0) or self.repeats.get(agent, {}).get("action") == key:
            self.repeats[agent] = {"action": key, "max": len(same), "tick": msg["tick"]}
        if len(same) == REPEAT_MAX + 1:
            self.fresh.append(Finding(msg["tick"], 1, "repetition", agent, "warning", same, float(len(same)), {"action": key, "within_ticks": REPEAT_TICKS, "text": f"{agent} proposed the same action ({key}) {len(same)} times within {REPEAT_TICKS} ticks"}))

    def close(self):
        self.now.clear()
        self.order_eur.clear()

    def series(self):
        out = {}
        for a in self.since:
            out[f"{a}.decisions"] = (self.now[a], list(self.recent[a]))
            if self.ages[a]:
                out[f"{a}.data_age"] = (self.ages[a][-1], list(self.recent[a]))
            if a in self.beats:
                out[f"{a}.silent_ticks"] = (self.tick - self.beats[a], [])
            if role_of(a) == "trader":
                out[f"{a}.order_eur"] = (self.order_eur[a], list(self.recent[a]))
        return out

    def drain(self):
        out, self.fresh = self.fresh, []
        return out

    def snapshot(self):
        out = {}
        for a in self.agents:
            if a not in self.since:
                continue
            gaps, llm = self.gaps[a], self.llm.get(a)
            refused = len(self.refused[a])
            out[a] = {
                "role": role_of(a),
                "decisions": self.acts[a],
                "decision_rate": round(self.acts[a] / (self.tick - self.since[a] + 1), 3),
                "proposals": self.proposals[a],
                "refused": refused,
                "refusal_rate": round(refused / self.proposals[a], 3) if self.proposals[a] else None,
                "limits": self.limits.get(a, {}),
                "data_age_ticks": {
                    "last": self.ages[a][-1] if self.ages[a] else None,
                    "mean": mean(self.ages[a]),
                    "max": max(self.ages[a], default=None),
                    "by_service": self.age_by_service[a],
                },
                "heartbeat": {
                    "last_tick": self.beats.get(a),
                    "silent_ticks": self.tick - self.beats[a] if a in self.beats else None,
                    "mean_gap": mean(gaps),
                    "max_gap": max(gaps, default=None),
                    "regularity": round(sum(g == 1 for g in gaps) / len(gaps), 3) if gaps else None,
                },
                "repetitions": self.repeats.get(a, {"action": None, "max": 0}),
                "llm": {
                    "calls": llm["calls"],
                    "confidence": mean(llm["confidence"]),
                    "latency_ms": mean(llm["latency_ms"]),
                    "tokens": {"input": llm["tokens_in"], "output": llm["tokens_out"]},
                    "models": dict(llm["models"]),
                    "justification_diversity": round(len(set(llm["justifications"])) / len(llm["justifications"]), 3) if llm["justifications"] else None,
                }
                if llm
                else None,
            }
        return out
