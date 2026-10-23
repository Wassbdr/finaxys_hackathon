"""Clients des services, côté agents : HTTP en mode processus, appel direct en lockstep."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from services.base import ServiceError


@dataclass
class ServiceResult:
    data: dict
    provenance: dict


class ServiceUnavailable(Exception):
    def __init__(self, service: str, kind: str):
        super().__init__(f"{service} : {kind}")
        self.service = service
        self.kind = kind


class LocalServiceClient:
    """Appelle les cœurs en mémoire ; le mode slow devient un dépassement de délai sans attendre."""

    def __init__(self, cores: dict, caller: str, timeout_ms: int):
        self.cores = cores
        self.caller = caller
        self.timeout_ms = timeout_ms

    def call(self, service: str, method: str, path: str, params: dict | None = None, body: dict | None = None) -> ServiceResult:
        core = self.cores[service]
        try:
            response, latency = core.handle(method, path, params, body, self.caller)
        except ServiceError as err:
            raise ServiceUnavailable(service, err.kind) from err
        if latency > self.timeout_ms:
            raise ServiceUnavailable(service, "timeout")
        return ServiceResult(response["data"], response["provenance"])

    def get(self, service, path, params=None):
        return self.call(service, "GET", path, params=params)

    def post(self, service, path, body):
        return self.call(service, "POST", path, body=body)


class HttpServiceClient:
    def __init__(self, cfg: dict, caller: str, host: str = "127.0.0.1"):
        self.urls = {sid: f"http://{host}:{spec['port']}" for sid, spec in cfg["services"].items()}
        self.caller = caller
        self.http = httpx.Client(timeout=cfg["chaos"]["client_timeout_ms"] / 1000, headers={"X-Agent-Id": caller})

    def call(self, service, method, path, params=None, body=None) -> ServiceResult:
        try:
            resp = self.http.request(method, self.urls[service] + path, params=params, json=body)
        except httpx.TimeoutException as err:
            raise ServiceUnavailable(service, "timeout") from err
        except httpx.TransportError as err:
            raise ServiceUnavailable(service, "unreachable") from err
        if resp.status_code == 503:
            raise ServiceUnavailable(service, "down")
        if resp.status_code >= 400:
            raise ServiceUnavailable(service, f"http_{resp.status_code}")
        payload = resp.json()
        return ServiceResult(payload["data"], payload["provenance"])

    def get(self, service, path, params=None):
        return self.call(service, "GET", path, params=params)

    def post(self, service, path, body):
        return self.call(service, "POST", path, body=body)
