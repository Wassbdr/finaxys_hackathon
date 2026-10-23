"""Paramètres modifiables à chaud (herding, guardrails, liste restreinte) : store `{prefix}params`."""

from __future__ import annotations

import json

DEFAULTS = {
    "herding": ("agents", "trader", "herding"),
    "guardrails": ("llm", "guardrails"),
    "restricted_list": ("agents", "compliance", "restricted_list"),
}


class Params:
    def __init__(self, store, prefix: str, cfg: dict):
        self.store = store
        self.key = f"{prefix}params"
        self.cfg = cfg

    def get(self, name: str):
        raw = self.store.hget(self.key, name)
        if raw is not None:
            return json.loads(raw)
        node = self.cfg
        for part in DEFAULTS[name]:
            node = node[part]
        return node

    def set(self, name: str, value) -> None:
        if name not in DEFAULTS:
            raise KeyError(f"paramètre inconnu : {name}")
        self.store.hset(self.key, name, json.dumps(value))

    def all(self) -> dict:
        return {name: self.get(name) for name in DEFAULTS}
