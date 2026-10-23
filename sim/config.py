"""Chargement de config.yaml et .env."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
_cache: dict[str, Any] | None = None


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    """Retourne la config (copie) ; HORIZON_CONFIG peut pointer vers un autre fichier."""
    global _cache
    load_env()
    path = Path(path or os.environ.get("HORIZON_CONFIG") or ROOT / "config.yaml")
    if _cache is None or _cache.get("_path") != str(path):
        with open(path, encoding="utf-8") as fh:
            _cache = yaml.safe_load(fh)
        _cache["_path"] = str(path)
    cfg = copy.deepcopy(_cache)
    prefix = os.environ.get("HORIZON_PREFIX")
    if prefix is not None:
        cfg["redis"]["prefix"] = prefix
    if os.environ.get("REDIS_URL"):
        cfg["redis"]["url"] = os.environ["REDIS_URL"]
    if os.environ.get("HORIZON_NO_LLM") == "1":
        cfg["llm"]["enabled"] = False
    if os.environ.get("HORIZON_NO_VIGIE") == "1":
        cfg["vigie"]["enabled"] = False
        cfg["gate"]["impl"] = "passthrough"
    if os.environ.get("HORIZON_COMPARE") == "1":
        cfg["compare"] = True
        cfg["worlds"] = {"A": "A:", "B": "B:"}
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def trader_ids(cfg: dict) -> list[str]:
    return [f"trader-{i + 1}" for i in range(cfg["agents"]["trader"]["count"])]


def trader_profile(cfg: dict, trader_id: str) -> str:
    profiles = cfg["agents"]["trader"]["profiles"]
    index = int(trader_id.rsplit("-", 1)[1]) - 1
    return profiles[index % len(profiles)]


def agent_ids(cfg: dict) -> list[str]:
    """Agents IA (un processus chacun)."""
    return ["quant", *trader_ids(cfg), "compliance", "risk", "payment"]


def known_senders(cfg: dict) -> list[str]:
    """Tous les émetteurs légitimes du bus, donc tous ceux qui ont une clé HMAC."""
    services = [f"svc:{s}" for s in cfg["services"]]
    return [*agent_ids(cfg), "clock", "world", "executor", "vigie", "human", "inject", "secu", *services]


def declared_dependencies(cfg: dict) -> dict[str, list[str]]:
    deps = {}
    for agent in agent_ids(cfg):
        role = "trader" if agent.startswith("trader-") else agent
        deps[agent] = list(cfg["agents"][role]["depends_on"])
    deps["executor"] = list(cfg["agents"]["executor"]["depends_on"])
    return deps


def role_of(agent_id: str) -> str:
    return "trader" if agent_id.startswith("trader-") else agent_id
