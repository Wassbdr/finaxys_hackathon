"""Socle des services « PHP » simulés.

Aucun code vulnérable ici : « compromis » est un mode du mock qui altère les DONNÉES renvoyées.
Le mode vit dans le store (Redis) pour que inject, l'API et tous les processus le partagent.
Chaque réponse servie en mode compromised ou stale est marquée « tainted » dans le store :
c'est la vérité terrain qui permet de tracer la contagion sans que les agents la connaissent.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from bus.message import canonical, provenance_entry, short_hash

MODES = ("normal", "compromised", "stale", "down", "slow")


class ServiceError(Exception):
    def __init__(self, kind: str, status: int = 503, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.status = status


class ServiceCore:
    service_id = ""
    # (méthode, motif de chemin) -> nom de la méthode ; « * » remplace un segment
    routes: dict[tuple[str, str], str] = {}

    def __init__(self, cfg: dict, store, prefix: str = "", publish: Callable[[str, dict], None] | None = None):
        self.cfg = cfg
        self.store = store
        self.prefix = prefix
        self.publish = publish
        self.spec = cfg["services"][self.service_id]
        self.chaos = cfg["chaos"]
        self._stale_cache: dict[str, Any] = {}

    # --- état partagé ---------------------------------------------------------------------
    def key(self, name: str) -> str:
        return f"{self.prefix}svc:{self.service_id}:{name}"

    def world(self, name: str, default=None):
        raw = self.store.get(f"{self.prefix}world:{name}")
        return default if raw is None else json.loads(raw)

    def tick(self) -> int:
        return int(self.world("tick", 0))

    def mode(self) -> str:
        return self.store.get(self.key("mode")) or "normal"

    def mode_since(self) -> int:
        return int(self.store.get(self.key("mode_since")) or 0)

    def meta(self) -> dict:
        return {
            "service_id": self.service_id,
            "language": "php",
            "php_version": self.spec["php_version"],
            "owner_team": self.spec["owner_team"],
            "patchable": self.spec["patchable"],
            "criticite": self.spec["criticite"],
            "version": self.spec["version"],
        }

    def health(self) -> dict:
        mode = self.mode()
        return {"service_id": self.service_id, "status": "down" if mode == "down" else "up", "tick": self.tick()}

    def set_mode(self, mode: str, by: str = "inject") -> dict:
        if mode not in MODES:
            raise ServiceError("bad_request", 400, f"mode inconnu {mode!r}")
        previous = self.mode()
        tick = self.tick()
        self.store.set(self.key("mode"), mode)
        self.store.set(self.key("mode_since"), tick)
        self.store.hset(f"{self.prefix}svc_modes", self.service_id, mode)
        status = {"service": self.service_id, "mode": mode, "previous": previous, "since_tick": tick, "by": by, "meta": self.meta()}
        shown = status
        if not self.chaos.get("announce_compromised", True) and "compromised" in (mode, previous):
            # An attacker does not announce itself: on the bus a compromised service stays normal.
            shown = {**status, "mode": mode.replace("compromised", "normal"), "previous": previous.replace("compromised", "normal")}
            if shown["mode"] == shown["previous"]:
                shown = None
        if self.publish and shown:
            self.publish("service_status", shown)
        return status

    # --- traitement d'une requête ---------------------------------------------------------
    def _route(self, method: str, path: str) -> tuple[Callable, list[str]]:
        parts = [p for p in path.strip("/").split("/") if p]
        for (m, pattern), name in self.routes.items():
            pat = [p for p in pattern.strip("/").split("/") if p]
            if m == method and len(pat) == len(parts) and all(a == "*" or a == b for a, b in zip(pat, parts)):
                return getattr(self, name), [b for a, b in zip(pat, parts) if a == "*"]
        raise ServiceError("not_found", 404, f"{method} {path}")

    def handle(self, method: str, path: str, params: dict | None = None, body: dict | None = None, caller: str = "?") -> tuple[dict, int]:
        """Retourne ({data, provenance}, latence_ms)."""
        params, body = dict(params or {}), dict(body or {})
        self.store.sadd(self.key("callers"), caller)
        self.store.hincrby(self.key("calls"), caller, 1)
        mode = self.mode()
        if mode == "down":
            raise ServiceError("down", 503, f"{self.service_id} indisponible")
        fn, args = self._route(method, path)
        tick = self.tick()
        cache_key = canonical([method, path, params])
        normal = fn(*args, params=params, body=body, compromised=False)
        if mode == "stale" and method == "GET" and cache_key in self._stale_cache:
            data = self._stale_cache[cache_key]
        elif mode == "compromised":
            data = fn(*args, params=params, body=body, compromised=True)
        else:
            data = normal
            if method == "GET" and mode != "stale":
                self._stale_cache[cache_key] = normal
        response_hash = short_hash({"service": self.service_id, "tick": tick, "caller": caller, "data": data})
        if mode in ("compromised", "stale"):
            self.store.sadd(f"{self.prefix}tainted", response_hash)
            if canonical(data) != canonical(normal):
                self.store.sadd(f"{self.prefix}altered", response_hash)
        prov = provenance_entry(self.service_id, self.spec["version"], response_hash, tick)
        latency = self.chaos["slow_latency_ms"] if mode == "slow" else 0
        return {"data": data, "provenance": prov}, latency

    def since_compromise(self) -> int:
        return self.tick() - self.mode_since()
