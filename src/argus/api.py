"""FastAPI endpoints and WebSocket stream for the ARGUS local MVP."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .investigator import investigate
from .live import LiveMarket
from .store import Store


class StartRequest(BaseModel):
    seed: int = 7


class SpeedRequest(BaseModel):
    speed: float = Field(ge=1, le=40)


class ReviewRequest(BaseModel):
    reviewer: str = Field(default="Demo analyst", min_length=1, max_length=80)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = db_path or os.environ.get("ARGUS_DB_PATH", "data/argus.sqlite3")
    store = Store(path)
    market = LiveMarket(store)
    application = FastAPI(title="ARGUS Surveillance API", version="0.1.0")
    application.state.market = market
    application.state.store = store

    @application.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "ARGUS"}

    @application.get("/api/state")
    async def state() -> dict[str, Any]:
        return market.snapshot()

    @application.post("/api/run/start")
    async def start(body: StartRequest) -> dict[str, Any]:
        market.start(body.seed)
        return market.snapshot()

    @application.post("/api/run/pause")
    async def pause() -> dict[str, Any]:
        market.pause()
        return market.snapshot()

    @application.post("/api/run/resume")
    async def resume() -> dict[str, Any]:
        market.resume()
        return market.snapshot()

    @application.post("/api/run/reset")
    async def reset() -> dict[str, Any]:
        await market.reset()
        return market.snapshot()

    @application.post("/api/run/speed")
    async def speed(body: SpeedRequest) -> dict[str, Any]:
        market.speed = body.speed
        return market.snapshot()

    @application.post("/api/proposals/{proposal_id}/approve")
    async def approve(proposal_id: str, body: ReviewRequest) -> dict[str, Any]:
        if market.status not in ("running", "paused"):
            raise HTTPException(409, "Start or resume the simulation before approving enforcement")
        if not any(row["id"] == proposal_id for row in market.store.proposals(market.run_id or "")):
            raise HTTPException(404, "Proposal not found in the active run")
        try:
            result = market.approve(proposal_id, body.reviewer)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "Proposal not found")
        return market.snapshot()

    @application.post("/api/proposals/{proposal_id}/reject")
    async def reject(proposal_id: str, body: ReviewRequest) -> dict[str, Any]:
        if not any(row["id"] == proposal_id for row in market.store.proposals(market.run_id or "")):
            raise HTTPException(404, "Proposal not found in the active run")
        try:
            result = market.reject(proposal_id, body.reviewer)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "Proposal not found")
        return market.snapshot()

    @application.post("/api/cases/{case_id}/investigate")
    async def investigate_case(case_id: str) -> dict[str, Any]:
        case = store.case(case_id)
        if not case:
            raise HTTPException(404, "Case not found")
        case["alerts"] = store.alerts_for_agents(case["run_id"], case["agents"])
        event_ids = set(case["event_ids"])
        for alert in case["alerts"]:
            event_ids.update(alert["event_ids"])
        events = store.events_by_ids(case["run_id"], sorted(event_ids))
        report = await asyncio.to_thread(investigate, case, events)
        store.save_report(case_id, report)
        store.record_audit(case["run_id"], "investigation_generated",
                           {"source": report.get("source"), "evidence_ids": report.get("evidence_ids", [])},
                           market.now, case_id=case_id)
        return {"case_id": case_id, "report": report}

    @application.websocket("/ws")
    async def websocket_state(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(market.snapshot())
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return

    return application


app = create_app()
