"""Outils partagés des tests : un monde lockstep et des messages fabriqués à la main."""

import json

from bus.message import MessageFactory
from sim.lockstep import Lockstep

PROV_FEED = {"service": "market-feed", "version": "3.2.0", "response_hash": "feedhash", "tick": 0}


def make_world(cfg, **kwargs) -> Lockstep:
    return Lockstep(cfg, **kwargs)


def send(ls: Lockstep, sender: str, type_: str, to: str, payload: dict, provenance=None, tick=None) -> dict:
    return MessageFactory(sender, ls.keyring).make(ls.tick if tick is None else tick, type_, to, payload,
                                                   data_provenance=provenance or [])


def price_update(ls, prices: dict, confidence=1.0, fair=None, vol=0.003, tick=None, provenance=None) -> dict:
    assets = {a: {"price": p, "fair": (fair or {}).get(a, p), "vol": vol, "confidence": confidence} for a, p in prices.items()}
    return send(ls, "quant", "price_update", "*", {"as_of_tick": ls.tick, "age_ticks": 0, "assets": assets},
                provenance=provenance or [PROV_FEED], tick=tick)


def tick_msg(ls, tick: int) -> dict:
    return send(ls, "clock", "tick", "*", {"tick": tick, "day": 0}, tick=tick)


def set_ledger(ls, cash=60e6, positions_qty=None, pnl_day=0.0):
    ls.store.set("world:ledger", json.dumps({"cash_eur": cash, "equity_eur": cash, "positions_qty": positions_qty or {},
                                              "pnl_day_eur": pnl_day}))


def set_world_tick(ls, tick: int):
    ls.store.set("world:tick", json.dumps(tick))


def of_type(msgs, type_):
    return [m for m in msgs if m["type"] == type_]
