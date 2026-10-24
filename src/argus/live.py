"""A controllable one-instrument market that streams Sentinel exchange events."""
from __future__ import annotations

import asyncio
import heapq
import math
import os
import random
import json
from pathlib import Path
from typing import Any

import numpy as np

from sentinel.detect.dataset import CLASS_INDEX, CLASSES
from sentinel.detect.explain import ModelExplainer
from sentinel.detect.model import load_model, predict_proba, predict_types
from sentinel.detect.rules import GENERIC_RULE, TEXTBOOK_RULES
from sentinel.features.extractor import FEATURE_NAMES, WindowFeatureExtractor
from sentinel.sim.agents import MarketMaker, MomentumTrader, NoiseTrader, lognormal_qty
from sentinel.sim.exchange import Exchange
from sentinel.sim.types import ASK, BID, CANCEL, EVENT_TYPES, EXEC, MODIFY, NEW, NS_PER_SEC

from .coordination import CoordinationDetector
from .store import Store


class CoordinatedTrader:
    """Synthetic demo participant that repeats synchronized same-side order cycles."""

    def __init__(self, trader_id: int, offset_ms: int) -> None:
        self.trader_id = trader_id
        self.offset_ms = offset_ms
        self.orders: list[int] = []
        self.posted = False

    def act(self, exchange: Exchange, now: int, rng: random.Random) -> int | None:
        if not self.posted:
            price = max(1, math.floor(exchange.mid()) - 2)
            self.orders = [exchange.new_limit(self.trader_id, BID, price, lognormal_qty(rng, 135, 0.15), now)]
            self.posted = True
            return 400_000_000
        for order_id in self.orders:
            exchange.cancel(order_id, now)
        self.orders = []
        self.posted = False
        return max(1, 3_600_000_000 - self.offset_ms * 1_000_000)


class LiveMarket:
    """Owns the simulation clock, event stream, detectors, and exchange controls."""

    roles = {
        0: ("Market maker 01", "Market maker"), 1: ("Market maker 02", "Market maker"),
        2: ("Momentum 03", "Momentum"), 3: ("Momentum 04", "Momentum"),
        4: ("Flow agent 05", "Noise"), 5: ("Flow agent 06", "Noise"),
        6: ("Flow agent 07", "Noise trader"), 7: ("Flow agent 08", "Noise trader"),
        8: ("Flow agent 09", "Noise trader"), 9: ("Flow agent 10", "Noise trader"),
    }
    code_for = {"NEW": NEW, "CANCEL": CANCEL, "MODIFY": MODIFY, "EXEC": EXEC}

    def __init__(self, store: Store) -> None:
        self.store = store
        self.run_id: str | None = None
        self.seed = 7
        self.status = "ready"
        self.speed = 8.0
        self.now = 0
        self.volume = 0
        self.event_count = 0
        self.rejected_count = 0
        self.last_activity: dict[int, int] = {}
        self.exchange: Exchange | None = None
        self.detector: CoordinationDetector | None = None
        self.extractor: WindowFeatureExtractor | None = None
        self.scheduler: list[tuple[int, int, Any]] = []
        self.seq = 0
        self.actors: list[Any] = []
        self.restrictions: dict[int, int] = {}
        self.case_id: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.error: str | None = None
        self.booster = None
        self.thresholds: dict[str, float] = {}
        self.explainer: ModelExplainer | None = None
        self.model_status = self._load_models()

    def _load_models(self) -> str:
        model_dir = Path("models")
        if (model_dir / "lgbm.txt").exists() and (model_dir / "thresholds.json").exists():
            try:
                self.booster, self.thresholds = load_model(model_dir)
                reference_path = model_dir / "normal_reference.json"
                if reference_path.exists():
                    reference = json.loads(reference_path.read_text(encoding="utf-8"))
                else:
                    reference = {name: 0.0 for name in FEATURE_NAMES}
                self.explainer = ModelExplainer(self.booster, reference)
                return "Sentinel LightGBM and SHAP active"
            except Exception as exc:
                return f"Sentinel model unavailable ({type(exc).__name__}); rules active"
        return "Sentinel rules active; train model artifacts to enable LightGBM and SHAP"

    def start(self, seed: int = 7) -> str:
        if self.task and not self.task.done():
            self.task.cancel()
        if self.run_id and self.status not in ("ready", "stopped"):
            self.store.set_run_status(self.run_id, "stopped")
        self.seed = int(seed)
        self.run_id = self.store.new_run(self.seed)
        self.status = "running"
        self.now = 0
        self.volume = 0
        self.event_count = 0
        self.rejected_count = 0
        self.last_activity = {}
        self.restrictions = {}
        self.case_id = None
        self.error = None
        rng = random.Random(self.seed)
        self.exchange = Exchange("ARGUS", 0, retain_history=False)
        agents = [{"id": key, "name": name, "role": role}
                  for key, (name, role) in self.roles.items()]
        self.detector = CoordinationDetector(agents)
        self.extractor = WindowFeatureExtractor(window_s=10)
        self.actors = [MarketMaker(0, 8.0), MarketMaker(1, 8.0),
                       MomentumTrader(2, 0.05), MomentumTrader(3, 0.05),
                       NoiseTrader(4, 2.0), NoiseTrader(5, 2.0), NoiseTrader(6, 2.0),
                       CoordinatedTrader(7, 0), CoordinatedTrader(8, 30), CoordinatedTrader(9, 60)]
        self.scheduler = []
        self.seq = 0
        for actor in self.actors:
            first = int(rng.random() * NS_PER_SEC)
            if isinstance(actor, CoordinatedTrader):
                first = 5 * NS_PER_SEC + actor.offset_ms * 1_000_000
            heapq.heappush(self.scheduler, (first, self.seq, actor))
            self.seq += 1
        self.task = asyncio.create_task(self._run(), name=f"argus-{self.run_id}")
        return self.run_id

    def pause(self) -> None:
        if self.status == "running":
            self.status = "paused"
            if self.run_id:
                self.store.set_run_status(self.run_id, self.status)

    def resume(self) -> None:
        if self.status == "paused":
            self.status = "running"
            if self.run_id:
                self.store.set_run_status(self.run_id, self.status)

    async def reset(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.run_id:
            self.store.set_run_status(self.run_id, "stopped")
        self.run_id = None
        self.status = "ready"
        self.exchange = None
        self.detector = None
        self.scheduler = []
        self.restrictions = {}

    async def _run(self) -> None:
        try:
            while self.status not in ("ready", "stopped", "error") and self.scheduler:
                if self.status == "paused":
                    await asyncio.sleep(0.05)
                    continue
                self.now, _, actor = heapq.heappop(self.scheduler)
                self._expire_restrictions()
                delay = actor.act(self.exchange, self.now, random.Random(self.seed + self.seq))
                if delay is not None:
                    heapq.heappush(self.scheduler, (self.now + max(1, int(delay)), self.seq, actor))
                    self.seq += 1
                await self._ingest()
                await asyncio.sleep(0.04 / max(1.0, self.speed))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.status = "error"
            if self.run_id:
                self.store.set_run_status(self.run_id, self.status)

    async def _ingest(self) -> None:
        if not (self.exchange and self.run_id and self.detector and self.extractor):
            return
        raw = self.exchange.drain_live_events()
        stored = self.store.append_events(self.run_id, raw) if raw else []
        self.event_count += len(stored)
        for event in stored:
            trader_id = int(event["trader_id"])
            self.last_activity[trader_id] = int(event["ts"])
            if event["event_type"] == "EXEC":
                self.volume += int(event["qty"])
            coord = self.detector.process(event)
            if coord and not self.case_id:
                self.case_id = self.store.create_case(
                    self.run_id, "Repeated synchronized order behavior", coord["first_ts"],
                    coord["agents"], coord["graph"], coord["event_ids"])
            self._score_closed_windows(event)
        rejected = self.exchange.drain_rejected_actions()
        if rejected:
            self.rejected_count += len(rejected)
            self.store.record_audit(self.run_id, "order_rejected_by_restriction",
                                    {"attempts": rejected}, self.now)

    def _score_closed_windows(self, event: dict[str, Any]) -> None:
        assert self.extractor is not None and self.run_id is not None
        rows = self.extractor.process(
            int(event["ts"]), str(event["instrument"]), self.code_for[str(event["event_type"])],
            int(event["order_id"]), int(event["trader_id"]), int(event.get("counterparty_id", 0)),
            int(event["side"]), int(event["price"]), int(event["qty"]), int(event["best_bid"]),
            int(event["best_ask"]), int(event["bid_qty1"]), int(event["ask_qty1"]),
            int(event["bid_depth5"]), int(event["ask_depth5"]))
        for row in rows:
            values = dict(zip(FEATURE_NAMES, row.values))
            for name, rule in [*TEXTBOOK_RULES.items(), ("generic", GENERIC_RULE)]:
                fired = [condition for condition in rule.conditions
                         if bool(condition.holds(__import__("numpy").asarray(values[condition.feature])))]
                flagged = (all(c in fired for c in rule.conditions) if rule.mode == "all" else bool(fired))
                if flagged:
                    self.store.save_alert(self.run_id, {
                        "ts": row.window * self.extractor.window_ns, "trader_id": row.trader_id,
                        "kind": rule.kind, "source": f"rule:{name}", "score": 1.0,
                        "evidence": [{"feature": c.feature, "value": values[c.feature],
                                      "condition": c.describe(values[c.feature])} for c in fired],
                    })
            if self.booster is not None:
                proba = predict_proba(self.booster, np.asarray(row.values, dtype=float).reshape(1, -1))
                predicted = int(predict_types(proba, self.thresholds)[0])
                if predicted:
                    kind = CLASSES[predicted]
                    contributions = self.explainer.explain(np.asarray(row.values, dtype=float), predicted) \
                        if self.explainer else []
                    self.store.save_alert(self.run_id, {
                        "ts": row.window * self.extractor.window_ns, "trader_id": row.trader_id,
                        "kind": kind, "source": "model", "score": float(proba[0, CLASS_INDEX[kind]]),
                        "evidence": [{"feature": item.feature, "value": item.value,
                                      "weight": item.contribution, "text": item.phrase}
                                     for item in contributions],
                    })

    def _expire_restrictions(self) -> None:
        if not (self.exchange and self.run_id):
            return
        for agent_id, expiry in list(self.restrictions.items()):
            if self.now >= expiry:
                self.exchange.restore_trader(agent_id)
                del self.restrictions[agent_id]
                self.store.record_audit(self.run_id, "restriction_expired", {"agent_id": agent_id}, self.now)

    def approve(self, proposal_id: str, reviewer: str) -> dict[str, Any] | None:
        if not (self.run_id and self.exchange):
            raise ValueError("there is no active simulation")
        decision = self.store.review_proposal(proposal_id, "approved", reviewer, self.now)
        if not decision:
            return None
        expiry = self.now + int(decision["duration_ns"])
        cancelled: dict[int, list[int]] = {}
        for agent_id in decision["agents"]:
            cancelled[int(agent_id)] = self.exchange.restrict_trader(int(agent_id), self.now)
            self.restrictions[int(agent_id)] = expiry
        self.store.record_audit(self.run_id, "restriction_applied",
                                {"agents": decision["agents"], "expires_ts": expiry,
                                 "cancelled_orders": cancelled}, self.now,
                                case_id=decision["case_id"], proposal_id=proposal_id)
        return decision

    def reject(self, proposal_id: str, reviewer: str) -> dict[str, Any] | None:
        if not self.run_id:
            raise ValueError("there is no active simulation")
        return self.store.review_proposal(proposal_id, "rejected", reviewer, self.now)

    def snapshot(self) -> dict[str, Any]:
        agents = []
        for agent_id, (name, role) in self.roles.items():
            restricted = bool(self.exchange and agent_id in self.exchange.restricted_traders)
            agents.append({"id": agent_id, "name": name, "role": role,
                           "status": "restricted" if restricted else "active",
                           "last_activity": self.last_activity.get(agent_id, 0)})
        bid = ask = 0
        if self.exchange:
            bid, ask = self.exchange.snap[:2]
        spread = ask - bid if ask and bid else 0
        graph = self.detector.snapshot() if self.detector else {"nodes": [], "edges": []}
        run_id = self.run_id
        return {"run_id": run_id, "seed": self.seed, "status": self.status, "speed": self.speed,
                "sim_time_s": self.now / NS_PER_SEC, "error": self.error,
                "model_status": self.model_status,
                "market": {"instrument": "ARGUS", "mid": round(self.exchange.mid(), 2) if self.exchange else 10_000,
                           "bid": bid, "ask": ask, "spread": spread, "volume": self.volume,
                           "event_count": self.event_count, "rejected_count": self.rejected_count},
                "agents": agents, "graph": graph,
                "events": self.store.recent_events(run_id, 40) if run_id else [],
                "alerts": self.store.alerts(run_id, 30) if run_id else [],
                "cases": self.store.cases(run_id) if run_id else [],
                "proposals": self.store.proposals(run_id) if run_id else [],
                "restrictions": [{"agent_id": agent, "expires_ts": ts}
                                 for agent, ts in self.restrictions.items()],
                "audit": self.store.audit(run_id) if run_id else []}
