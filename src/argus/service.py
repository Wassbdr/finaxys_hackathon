"""One authoritative market state; API commands and steps are serialized by the API lock."""
from collections import deque
from datetime import datetime, timezone
import os
from uuid import uuid4
from .market import LiveMarket
from .detection import Coordination, Surveillance
from .store import Store


def uid(prefix):
    return prefix + "-" + uuid4().hex[:8]


class Argus:
    def __init__(self, db_path="data/argus.sqlite", model_dir="models/argus"):
        self.store = Store(db_path)
        self.model_dir = model_dir
        # Historical runs stay queryable; a process restart never resumes an uncheckpointed exchange.
        for r in self.store.list("run"):
            if r["status"] not in ("archived", "completed", "interrupted"):
                r["status"] = "interrupted"
                self.store.save("run", r)
        self.reset(7, "coordinated")

    def reset(self, seed, scenario):
        if hasattr(self, "run"):
            self.run["status"] = "archived"
            self.run["time"] = self.market.now / 1e9
            self.store.save("run", self.run)
            self.audit("run_archived", "Previous simulation ended; its restrictions are no longer executable.")
        rid = uid("run")
        self.run = dict(id=rid, run_id=rid, seed=seed, scenario=scenario, status="paused", time=0,
                        created_at=datetime.now(timezone.utc).isoformat())
        self.speed = 2
        self.pause_on_case = True
        self.detector = Surveillance(self.model_dir)
        self.coordination = Coordination()
        self.recent = deque(maxlen=60)
        self.alerts = deque(maxlen=100)
        self.cases = []
        self.proposals = []
        self.restrictions = []
        self.counters = {i: dict(orders=0, trades=0, volume=0, position=0, rejected=0) for i in range(1,11)}
        self.event_count = self.trade_volume = self.trade_count = 0
        self.market = LiveMarket(seed, scenario, self.on_event)
        self.store.save("run", self.run)
        self.audit("run_created", f"Ten agents initialized with seed {seed}.")
        self.store.commit()

    def audit(self, action, detail, **extra):
        self.store.audit(self.run["id"], dict(action=action, detail=detail, time=self.market.now / 1e9,
            wall_time=datetime.now(timezone.utc).isoformat(), **extra))

    def on_event(self, e):
        self.event_count += 1
        self.recent.append(e)
        self.store.event(self.run["id"], e)
        c = self.counters[e["agent"]]
        if e["type"] == "NEW": c["orders"] += 1
        if e["type"] == "REJECT": c["rejected"] += 1
        if e["type"] == "EXEC":
            c["trades"] += 1
            c["volume"] += e["qty"]
            c["position"] += e["qty"] * e["side"]
            if e["side"] == 1:
                self.trade_volume += e["qty"]
                self.trade_count += 1
        self.coordination.process(e)
        for a in self.detector.process(e):
            a.update(id=uid("alert"), run_id=self.run["id"], at=e["ts"]/1e9)
            self.alerts.appendleft(a)
            self.store.save("alert", a)

    def advance(self, seconds):
        self.market.advance(seconds)
        self.run["time"] = self.market.now / 1e9
        edges, groups = self.coordination.graph()
        for agents in groups:
            if any(c["agents"] == agents for c in self.cases): continue
            links = [e for e in edges if e["kind"] == "synchrony" and e["source"] in agents and e["target"] in agents]
            ids = sorted({i for e in links for i in e["event_ids"]})
            case = dict(id=uid("case"), run_id=self.run["id"], title="Repeated collective order synchrony", agents=agents,
                status="open", opened_at=self.run["time"], evidence_ids=ids, evidence_version=1,
                metrics=links, notes=[], report=None, investigation_status="idle", investigation_error=None)
            self.cases.append(case)
            self.store.save("case", case)
            self.audit("case_opened", "Three pairwise relationships meet the repetition policy.", case_id=case["id"])
            if self.pause_on_case:
                self.run["status"] = "paused"
        for r in self.restrictions:
            if r["status"] == "active" and self.run["time"] >= r["expires_at"]:
                r["status"] = "expired"
                r["rejected_orders"] = sum(self.counters[a]["rejected"] for a in r["agents"]) - r["rejected_before"]
                self.store.save("restriction", r)
                self.audit("restriction_expired", "Trading access restored.", restriction_id=r["id"], rejected_orders=r["rejected_orders"])
        self.store.save("run", self.run)
        self.store.commit()

    def case(self, cid):
        for c in self.cases:
            if c["id"] == cid: return c
        raise ValueError("Case does not belong to the active run")

    def propose(self, cid):
        c = self.case(cid)
        if c["status"] in ("resolved", "dismissed"):
            raise ValueError("Reopen the case before proposing an intervention")
        if not c["report"]:
            raise ValueError("Complete the investigation before proposing an intervention")
        existing = next((p for p in self.proposals if p["case_id"] == cid), None)
        if existing: return existing
        p = dict(id=uid("proposal"), run_id=self.run["id"], case_id=cid, agents=c["agents"],
            status="pending", action="temporary_suspension", duration_s=30, evidence_version=c["evidence_version"],
            reason="Policy COORD-01: all three pairs show at least 3 matched placement and cancellation bursts in 30 seconds. A temporary simulation restriction is eligible for human review.")
        self.proposals.append(p)
        self.store.save("proposal", p)
        self.audit("intervention_proposed", p["reason"], proposal_id=p["id"])
        self.store.commit()
        return p

    def decide(self, pid, decision, reviewer, reason):
        p = next((p for p in self.proposals if p["id"] == pid), None)
        if not p: raise ValueError("Unknown proposal in this run")
        if p["status"] != "pending":
            if p["status"] == decision: return p
            raise ValueError("This proposal already has a different decision")
        c = self.case(p["case_id"])
        if c["status"] in ("resolved", "dismissed") or c["evidence_version"] != p["evidence_version"]:
            raise ValueError("Case is closed or proposal evidence is stale")
        p.update(status=decision, reviewer=reviewer, review_reason=reason, decided_at=self.run["time"])
        self.audit("human_" + decision, reason, reviewer=reviewer, proposal_id=pid)
        if decision == "approved":
            r = dict(id=uid("restriction"), run_id=self.run["id"], proposal_id=pid, agents=p["agents"],
                status="active", started_at=self.run["time"], expires_at=self.run["time"]+p["duration_s"],
                rejected_before=sum(self.counters[a]["rejected"] for a in p["agents"]), rejected_orders=0)
            try:
                self.market.exchange.suspend(p["agents"], int(r["expires_at"]*1e9), self.market.now)
            except Exception:
                r["status"] = "failed"
                self.run["status"] = "paused"
                self.audit("enforcement_failed", "Market paused for review; inspect exchange state.", restriction_id=r["id"])
            else:
                self.audit("restriction_applied", "Resting orders cancelled; new orders and modifications blocked.", restriction_id=r["id"])
            self.restrictions.append(r)
            self.store.save("restriction", r)
        self.store.save("proposal", p)
        self.store.commit()
        return p

    def snapshot(self):
        edges, groups = self.coordination.graph()
        involved = {a for c in self.cases if c["status"] not in ("resolved", "dismissed") for a in c["agents"]}
        agents = [dict(id=i, name=f"Agent {i:02d}", role=role, **self.counters[i],
                       status="restricted" if self.market.exchange.restricted.get(i,0)>self.market.now else "review" if i in involved else "active") for i, role in self.market.roles.items()]
        return dict(run=self.run, speed=self.speed, agents=agents, graph=edges, cases=self.cases,
            proposals=self.proposals, restrictions=[dict(r, rejected_orders=sum(self.counters[a]["rejected"] for a in r["agents"])-r["rejected_before"]) if r["status"]=="active" else r for r in self.restrictions],
            alerts=list(self.alerts), events=list(reversed(self.recent)), audit=self.store.audits(self.run["id"])[:80],
            history=list(self.market.history), book=self.market.book(), metrics=dict(events=self.event_count,
                trades=self.trade_count, volume=self.trade_volume, mid=self.market.exchange.mid(), rejected=self.market.exchange.rejections),
            model_status="LightGBM + rules + SHAP" if self.detector.booster else "Rules only — train the model to enable LightGBM",
            investigator="OpenAI" if os.getenv("OPENAI_API_KEY") and os.getenv("ARGUS_LLM_MODEL") else "Local evidence summary")
