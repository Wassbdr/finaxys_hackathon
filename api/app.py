"""API pour l'équipe dashboard et l'équipe Vigie.

    GET  /state       prix, positions, P&L, VaR, limites (%), pertes par service, contagion, modes
    GET  /graph       agents et services, arêtes déclarées ET observées (flag `hidden`)
    GET  /llm_trace   historique des appels LLM
    WS   /stream      tous les messages du bus en temps réel (?types=execution,alert)
    POST /inject      {"command": "compromise market-feed"} ou {"scenario": "php2shell_day"}
    POST /record      {"name": "demo1"} : copie la partie en cours dans recordings/
    GET  /recordings  parties enregistrées
    GET  /vigie       état de la Vigie (indicateurs, actions, décisions du portique)
    GET  /vigie/graph    layer 1 graph rebuilt from the bus (hidden edges, impact radius)
    GET  /vigie/metrics  layer 1 metrics (agents, system, CEO figures, baselines)
    GET  /vigie/findings latest Findings of every layer
    GET  /improvements dernier rapport de `python -m vigie.improve`
    GET  /guardrails  the limits of each agent and the Vigie's settings, from config.yaml
    GET  /            page de présentation de la Vigie (le récit en 9 fiches, les chiffres du banc A/B)
    GET  /dashboard   tableau de bord de la Vigie (7 onglets) ; ?embed=<onglet> montre l'onglet seul, sans menu
    GET  /dashboard/{dashboard.js,dashboard-demo.json}  its script and the sample data it shows when no run is live
    GET  /dashboard/{simulation.js,dashboard-sim.json}  the Simulation tab's 3D scene and the bench replays it plays
    GET  /dashboard/sim-layout.json  where its nodes stand, once saved; POST /dashboard/sim-layout {"quant": [x, z], …, "camera": [azimuth, polar]} saves it

Toutes les routes acceptent ?world=A|B en mode comparaison.
    python -m api.app
"""

from __future__ import annotations

import json
import shlex
import shutil
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from sim.config import ROOT, load_config

RECORDINGS = ROOT / "recordings"
SIM_LAYOUT = ROOT / "vigie" / "sim-layout.json"


class InjectRequest(BaseModel):
    command: str | None = None
    args: list[str] | None = None
    scenario: str | None = None


class RecordRequest(BaseModel):
    name: str


def create_app(sources: dict, targets: dict | None = None, default_world: str = "main") -> FastAPI:
    """sources : monde -> LiveSource | ReplaySource ; targets : monde -> cible d'injection."""
    app = FastAPI(title="Banque Horizon : API de simulation", version="1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    from vigie.console.impact_api import impact_router
    app.include_router(impact_router)
    targets = targets or {}
    from vigie.console.details_api import attach_details
    attach_details(app)

    def source(world: str | None):
        name = world or default_world
        if name not in sources:
            raise HTTPException(404, f"monde inconnu : {name} (disponibles : {', '.join(sources)})")
        return sources[name]

    @app.get("/health")
    def health():
        return {"status": "ok", "worlds": list(sources), "mode": next(iter(sources.values())).kind}

    @app.get("/state")
    def state(world: str | None = None):
        data = source(world).state()
        if data is None:
            raise HTTPException(503, "monde pas encore démarré")
        return data

    @app.get("/graph")
    def graph(world: str | None = None):
        data = source(world).graph()
        if data is None:
            raise HTTPException(503, "monde pas encore démarré")
        return data

    @app.get("/llm_trace")
    def llm_trace(world: str | None = None, agent: str | None = None, since_tick: int | None = None,
                  limit: int = Query(200, ge=1, le=5000)):
        return source(world).llm_trace(agent=agent, since_tick=since_tick, limit=limit)

    @app.websocket("/stream")
    async def stream(ws: WebSocket, world: str | None = None, types: str | None = None):
        await ws.accept()
        wanted = set(types.split(",")) if types else None
        try:
            async for msg in source(world).stream(wanted):
                await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.post("/inject")
    def inject(req: InjectRequest, world: str | None = None):
        from sim.inject import load_scenario, run_command, run_scenario

        if not targets:
            raise HTTPException(409, "injection indisponible (replay)")
        if "all" in targets:
            names = ["all"]
        elif world in (None, "all") and len(targets) > 1:
            names = list(targets)
        else:
            names = [world or default_world]
        if not targets:
            raise HTTPException(409, "injection indisponible (replay)")
        results = {}
        for name in names:
            target = targets.get(name)
            if target is None:
                raise HTTPException(404, f"monde inconnu : {name}")
            try:
                if req.scenario and name == "all":
                    results[name] = run_command(target, ["scenario", req.scenario])
                elif req.scenario:
                    path = Path(req.scenario)
                    if not path.exists():
                        path = ROOT / "scenarios" / f"{req.scenario.removesuffix('.yaml')}.yaml"
                    scenario = load_scenario(path)
                    threading.Thread(target=run_scenario, args=(target, scenario, lambda s: None), daemon=True).start()
                    results[name] = f"scénario {scenario['name']} lancé"
                else:
                    args = req.args or shlex.split(req.command or "")
                    results[name] = run_command(target, args)
            except (ValueError, KeyError) as err:
                raise HTTPException(400, str(err))
        return {"ok": True, "results": results}

    @app.get("/vigie")
    def vigie(world: str | None = None):
        src = source(world)
        if src.kind != "live":
            raise HTTPException(409, "the Vigie state is only kept live")
        decisions = src.client.lrange(f"{src.prefix}vigie:decisions", -200, -1)
        return {"status": src._json("vigie:status") or {}, "decisions": [json.loads(d) for d in decisions]}

    def vigie_part(world, key, empty):
        src = source(world)
        if src.kind != "live":
            raise HTTPException(409, "the Vigie state is only kept live")
        return (src._json("vigie:status") or {}).get(key, empty)

    @app.get("/vigie/graph")
    def vigie_graph(world: str | None = None):
        return vigie_part(world, "graph", {})

    @app.get("/vigie/metrics")
    def vigie_metrics(world: str | None = None):
        return vigie_part(world, "layer1", {})

    @app.get("/vigie/findings")
    def vigie_findings(world: str | None = None):
        return vigie_part(world, "findings", [])

    @app.get("/improvements")
    def improvements():
        path = ROOT / load_config()["runs_dir"] / "vigie-improve" / "improvements.json"
        if not path.exists():
            raise HTTPException(404, "no report yet: run python -m vigie.improve")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/guardrails")
    def guardrails():
        """The limits each agent enforces and the Vigie's settings, as config.yaml sets them."""
        cfg = load_config()
        return {"agents": cfg["agents"], "vigie": cfg["vigie"], "gate": cfg["gate"],
                "llm": {"enabled": cfg["llm"]["enabled"], "guardrails": cfg["llm"]["guardrails"]}}

    @app.get("/")
    def landing():
        return FileResponse(ROOT / "vigie" / "landing.html")

    @app.get("/argus")
    def argus():
        return FileResponse(ROOT / "vigie" / "argus.html", headers=FRESH)

    @app.get("/argus/{name}")
    def argus_file(name: str):
        if name not in ("accueil.js", "accueil.css"):
            raise HTTPException(404)
        return FileResponse(ROOT / "vigie" / "console" / name, headers=FRESH)
    from vigie.console.conformite import router as conformite_router
    app.include_router(conformite_router)

    @app.get("/dashboard")
    def dashboard():
        return FileResponse(ROOT / "vigie" / "dashboard.html")

    @app.get("/dashboard/{name}")
    def dashboard_file(name: str):
        if name == "sim-layout.json":
            return FileResponse(SIM_LAYOUT) if SIM_LAYOUT.exists() else {}
        if name not in ("dashboard.js", "dashboard-demo.json", "simulation.js", "dashboard-sim.json"):
            raise HTTPException(404)
        return FileResponse(ROOT / "vigie" / name)

    @app.post("/dashboard/sim-layout")
    def save_sim_layout(layout: dict[str, list[float]]):
        """The Simulation tab's Layout › Save as default: where each node stands on the floor, and the camera's angle."""
        if len(layout) > 50 or any(not k.replace("-", "").isalnum() or len(p) != 2 or not all(-50 <= c <= 50 for c in p)
                                   for k, p in layout.items()):
            raise HTTPException(400, "expected {node id: [x, z]}, |x|, |z| <= 50")
        rows = ",\n".join(f"  {json.dumps(k)}: {json.dumps(p)}" for k, p in layout.items())
        SIM_LAYOUT.write_text("{\n" + rows + "\n}\n", encoding="utf-8")
        return {"ok": True}

    @app.get("/compare")
    def compare():
        """Indicateurs clés de chaque monde côte à côte (mode comparaison)."""
        out = {}
        for name, src in sources.items():
            s = src.state()
            if s:
                out[name] = {"tick": s["tick"], "vigie": s["vigie"], "pnl": s["pnl"], "var_99_1d_eur": s["var_99_1d_eur"],
                             "exposure": s["exposure"], "losses_by_service_eur": s["losses"]["losses_by_service_eur"],
                             "trapped_operations": len(s["trapped_operations"]),
                             "contaminated_agents": [c["agent"] for c in s["contaminated_agents"]]}
        return out

    @app.post("/record")
    def record(req: RecordRequest, world: str | None = None):
        src = source(world)
        if src.kind != "live" or src.run_dir() is None:
            raise HTTPException(409, "rien à enregistrer")
        safe = "".join(c for c in req.name if c.isalnum() or c in "-_") or "partie"
        dest = RECORDINGS / safe
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src.run_dir(), dest)
        return {"ok": True, "path": str(dest.relative_to(ROOT)), "replay": f"make replay RUN={dest.relative_to(ROOT)}"}

    @app.get("/recordings")
    def recordings():
        if not RECORDINGS.exists():
            return []
        return sorted(p.name for p in RECORDINGS.iterdir() if (p / "states.jsonl").exists())

    return app


def live_app(cfg: dict | None = None) -> FastAPI:
    from api.sources import LiveSource
    from bus.redis_bus import connect
    from sim.inject import ProcessTarget, QueueTarget

    cfg = cfg or load_config()
    client = connect(cfg["redis"]["url"])
    worlds = cfg.get("worlds") or {"main": cfg["redis"]["prefix"]}
    sources, targets = {}, {}
    for name, prefix in worlds.items():
        wcfg = {**cfg, "redis": {**cfg["redis"], "prefix": prefix}}
        sources[name] = LiveSource(wcfg, client)
        targets[name] = QueueTarget(wcfg, client) if cfg.get("compare") else ProcessTarget(wcfg, client)
    if cfg.get("compare"):
        targets = {"all": targets["A"]}   # une seule mise en file : sim.compare l'applique à A et B
    app = create_app(sources, targets, default_world=next(iter(worlds)))
    from vigie.console.chat import attach_standalone
    attach_standalone(app, cfg)
    return app


def main() -> None:
    import uvicorn

    cfg = load_config()
    app = live_app(cfg)
    from vigie.demo_security import announce_access
    announce_access(app)
    uvicorn.run(app, host="127.0.0.1", port=cfg["api"]["port"], log_level="warning")


if __name__ == "__main__":
    main()
