"""Adaptateur FastAPI : /health, /meta, /chaos et les endpoints métier d'un cœur de service."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from services.base import ServiceCore, ServiceError


class ChaosRequest(BaseModel):
    mode: str
    by: str = "inject"


def make_app(core: ServiceCore) -> FastAPI:
    app = FastAPI(title=f"{core.service_id} (PHP {core.spec['php_version']}, simulé)")

    @app.get("/health")
    def health():
        if core.mode() == "down":
            raise HTTPException(503, core.health())
        return core.health()

    @app.get("/meta")
    def meta():
        return core.meta()

    @app.post("/chaos")
    def chaos(req: ChaosRequest):
        try:
            return core.set_mode(req.mode, by=req.by)
        except ServiceError as err:
            raise HTTPException(err.status, str(err))

    @app.get("/chaos")
    def chaos_state():
        return {"service_id": core.service_id, "mode": core.mode(), "since_tick": core.mode_since()}

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def business(path: str, request: Request):
        body = await request.json() if request.method == "POST" else None
        caller = request.headers.get("X-Agent-Id", "inconnu")
        try:
            response, latency_ms = core.handle(request.method, "/" + path, dict(request.query_params), body, caller)
        except ServiceError as err:
            raise HTTPException(err.status, str(err))
        if latency_ms:
            await asyncio.sleep(latency_ms / 1000)
        return response

    return app
