"""Le monde : horloge, marché, ledger, exécuteur et observateur. Seul détenteur des vrais prix.

    python -m sim.world [--no-vigie] [--ticks N]
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import threading
import time
from pathlib import Path

from agents.base import Context
from bus.message import MessageFactory, canonical
from sim.clock import Clock
from sim.config import agent_ids, declared_dependencies, known_senders, load_config, role_of, trader_ids
from sim.executor import Executor
from sim.ledger import Ledger
from sim.market import Market
from sim.observer import Observer
from sim.params import Params

STORE_KEYS = ["world:*", "svc:*", "svc_modes", "tainted", "altered", "params", "cve_published", "agent:*", "state", "graph", "run_dir"]

POSITIVE_NEWS = ["{a} signe un contrat pluriannuel avec un industriel européen", "{a} relève ses objectifs annuels",
                 "{a} : bénéfice trimestriel supérieur aux attentes"]
NEGATIVE_NEWS = ["{a} : avertissement sur résultats", "{a} visé par une enquête de l'autorité de la concurrence",
                 "{a} : recul des commandes au trimestre"]


def load_cve_feed(cfg: dict) -> list[dict]:
    path = Path(cfg["cve_feed"])
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return json.loads(path.read_text(encoding="utf-8"))["cves"]


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


class World:
    def __init__(self, cfg: dict, store, keyring, gate, exec_services, deterministic: bool = True, run_dir: Path | None = None, public_publish=None):
        self.cfg = cfg
        self.public_publish = public_publish
        self.store = store
        self.prefix = cfg["redis"]["prefix"]
        self.keyring = keyring
        self.clock = Clock.from_config(cfg)
        self.tick = 0
        self.noise_rng = random.Random(f"{cfg['seed']}-noise")
        self.news_rng = random.Random(f"{cfg['seed']}-news")
        self.market = Market(cfg, self.noise_rng)
        self.ledger = Ledger(cfg, self.market.prices, trader_ids(cfg))
        self.market.listener = self.ledger.on_price_move
        self.clock_factory = MessageFactory("clock", keyring, deterministic)
        self.world_factory = MessageFactory("world", keyring, deterministic)
        self.observer = Observer(cfg, store, self.prefix)
        self.params = Params(store, self.prefix, cfg)
        ctx = Context(services=exec_services, llm=None, params=self.params, keyring=keyring,
                      vigie_enabled=cfg["vigie"]["enabled"], deterministic=deterministic)
        self.executor = Executor("executor", cfg, ctx, self, gate)
        self.gate = gate
        self.news: list[dict] = []
        self.cves = load_cve_feed(cfg)
        self.run_dir = run_dir
        self._states_fh = None
        self.reset_store()
        if run_dir:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._states_fh = open(run_dir / "states.jsonl", "a", encoding="utf-8")
            self.store.set(f"{self.prefix}run_dir", str(run_dir))
        self.publish_world()

    # --- store partagé avec les services ---------------------------------------------------
    def reset_store(self) -> None:
        for pattern in STORE_KEYS:
            for key in list(self.store.scan_iter(match=f"{self.prefix}{pattern}")):
                self.store.delete(key)

    def k(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def publish_ledger(self) -> None:
        self.store.set(self.k("world:ledger"), canonical({
            "cash_eur": round(self.ledger.cash, 2),
            "equity_eur": round(self.ledger.equity(), 2),
            "positions_qty": {a: round(q, 6) for a, q in ((a, self.ledger.bank_position(a)) for a in self.market.assets)},
            "pnl_day_eur": round(self.ledger.pnl_day(), 2),
        }))
        self.store.set(self.k("world:prices"), canonical(self.market.prices))

    def publish_world(self) -> None:
        self.store.set(self.k("world:tick"), json.dumps(self.tick))
        self.store.set(self.k("world:news"), canonical(self.news))
        self.publish_ledger()

    # --- déroulé d'un tick ----------------------------------------------------------------
    def generate_news(self) -> None:
        n = self.cfg["market"]["news"]
        if self.news_rng.random() >= n["probability_per_tick"]:
            return
        asset = self.news_rng.choice(self.market.assets)
        positive = self.news_rng.random() < 0.5
        template = self.news_rng.choice(POSITIVE_NEWS if positive else NEGATIVE_NEWS)
        magnitude = self.news_rng.uniform(*n["shock_pct"])
        confidential = self.news_rng.random() < n.get("confidential_probability", 0.0)
        news_id = f"N{self.tick:05d}"
        headline = template.format(a=asset)
        self.news.append({"id": news_id, "tick": self.tick, "asset": asset, "headline": headline,
                          "body": f"{headline}. Dépêche Horizon Wire.", "source": "Horizon Wire", "confidential": confidential})
        self.market.shock(asset, magnitude if positive else -magnitude, f"news:{news_id}")

    def begin_tick(self, tick: int) -> list[dict]:
        self.tick = tick
        self.market.noise_step()
        self.market.decay_impacts()
        self.generate_news()
        self.publish_world()
        return [self.clock_factory.make(tick, "tick", "*", {"tick": tick, "day": self.clock.day_of(tick)})]

    def on_message(self, msg: dict, valid: bool) -> list[dict]:
        out = [self.world_factory.make(self.tick, "alert", "*", alert) for alert in self.observer.observe(msg, valid)]
        if valid:
            p = msg["payload"]
            if msg["type"] == "alert" and msg["from"] == "inject" and p.get("kind") == "market_event":
                self.market_event(p["asset"], float(p["pct"]), p["headline"])
            out += self.executor.handle(msg)
        return out

    def market_event(self, asset: str, pct: float, headline: str) -> None:
        """Vraie news injectée : elle bouge le vrai prix et paraît sur news-wire."""
        news_id = f"E{self.tick:05d}-{len(self.news)}"
        self.news.append({"id": news_id, "tick": self.tick, "asset": asset, "headline": headline,
                          "body": f"{headline}. Dépêche Horizon Wire.", "source": "Horizon Wire", "confidential": False})
        self.market.shock(asset, pct, f"news:{news_id}")
        self.publish_world()

    def end_tick(self, tick: int) -> dict:
        self.ledger.end_tick(tick)
        self.publish_ledger()
        if self.cfg['vigie'].get('public_network_calls') and self.public_publish:
            # L'observabilité des appels devient un événement public explicite.
            # Aucun prix réel, mode, marquage ni nœud du graphe oracle n'en sort.
            edges = []
            agents = {*agent_ids(self.cfg), 'executor'}
            for sid in self.cfg['services']:
                for caller, count in sorted(self.store.hgetall(self.k(f'svc:{sid}:calls')).items()):
                    if caller in agents:
                        edges.append({'source': caller, 'target': sid, 'kind': 'data',
                                      'transitive': False, 'calls': int(count)})
            self.public_publish(self.world_factory.make(tick, 'heartbeat', '*', {
                'agent': 'world', 'role': 'world', 'kind': 'network_calls', 'edges': edges}))
        state, graph = self.state(), self.graph()
        self.store.set(self.k("state"), canonical(state))
        self.store.set(self.k("graph"), canonical(graph))
        if self._states_fh:
            self._states_fh.write(canonical({"tick": tick, "state": state, "graph": graph}) + "\n")
            self._states_fh.flush()
        return state

    # --- vues exposées par l'API ------------------------------------------------------------
    def service_modes(self) -> dict[str, dict]:
        out = {}
        for sid, spec in self.cfg["services"].items():
            mode = self.store.get(self.k(f"svc:{sid}:mode")) or "normal"
            since = int(self.store.get(self.k(f"svc:{sid}:mode_since")) or 0)
            out[sid] = {"mode": mode, "since_tick": since, "php_version": spec["php_version"],
                        "patchable": spec["patchable"], "criticite": spec["criticite"], "owner_team": spec["owner_team"],
                        "cve": self.cve_status(spec)}
        return out

    def cve_status(self, spec: dict) -> list[dict]:
        published = self.store.smembers(self.k("cve_published"))
        out = []
        for cve in self.cves:
            if cve["affects"]["language"] != "php":
                continue
            affected = version_tuple(spec["php_version"]) < version_tuple(cve["affects"]["version_lt"])
            if cve["id"] not in published:
                status = "non_publiee"
            elif not affected:
                status = "non_concerne"
            else:
                status = "vulnerable_patchable" if spec["patchable"] else "vulnerable_non_patchable"
            out.append({"id": cve["id"], "name": cve["name"], "status": status})
        return out

    def agents_view(self) -> dict[str, dict]:
        out = {}
        for agent in [*agent_ids(self.cfg), "executor"]:
            hb = self.observer.heartbeats.get(agent)
            alive = hb is not None and self.tick - hb["tick"] <= 3
            out[agent] = {
                "role": role_of(agent),
                "alive": alive,
                "last_heartbeat_tick": hb["tick"] if hb else None,
                "light": hb.get("light") if hb else None,
                "paused": hb.get("paused") if hb else None,
                "vigie_lost": hb.get("vigie_lost") if hb else None,
                "contaminated": agent in self.observer.contaminated,
                "status": {k: v for k, v in (hb or {}).items() if k not in ("tick", "agent", "role", "light", "paused", "vigie_lost", "depends_on", "bus_light")},
            }
        return out

    def limits(self, snap: dict) -> dict:
        a, t, p = self.cfg["agents"]["risk"], self.cfg["agents"]["trader"], self.cfg["agents"]["payment"]

        def pct(x, limit):
            return round(100 * x / limit, 1) if limit else 0.0

        traders = {}
        for book in self.ledger.positions:
            pos = self.ledger.positions_eur(book)
            traders[book] = {
                "max_position_pct": pct(max(abs(v) for v in pos.values()), t["max_position_eur"]),
                "daily_loss_pct": pct(max(0.0, -self.ledger.book_pnl_day(book)), t["max_daily_loss_eur"]),
            }
        cash = max(self.ledger.cash, 1.0)
        return {
            "bank": {
                "exposure_pct": pct(snap["exposure"]["gross_eur"], a["max_exposure_eur"]),
                "var_pct": pct(snap["var_99_1d_eur"], a["max_var_eur"]),
                "daily_loss_pct": pct(max(0.0, -snap["pnl"]["day_eur"]), a["max_daily_loss_eur"]),
                "daily_loss_incl_fraud_pct": pct(max(0.0, -snap["pnl"]["day_eur"] + self.ledger.fraud_day(self.tick)), a["max_daily_loss_eur"]),
                "liquidity_floor_pct": pct(a["min_cash_eur"], cash),
            },
            "traders": traders,
            "payments": {"daily_pct": pct(self.ledger.payments_legit_day + self.ledger.fraud_day(self.tick), p["max_daily_eur"])},
        }

    def state(self) -> dict:
        snap = self.ledger.snapshot(self.market.liquidation_cost(self.ledger.positions_eur()))
        obs = self.observer
        return {
            "world": self.prefix.strip(":") or "main",
            "tick": self.tick,
            "day": self.clock.day_of(self.tick),
            "seed": self.cfg["seed"],
            "prices": {a: round(p, 4) for a, p in self.market.prices.items()},
            **snap,
            "limits": self.limits(snap),
            "losses": self.ledger.attribution(),
            "contaminated_agents": [{"agent": a, **info} for a, info in sorted(obs.contaminated.items())],
            "trapped_operations": obs.trapped,
            "services": self.service_modes(),
            "agents": self.agents_view(),
            "light": obs.light,
            "risk_view_vs_truth": {
                "risk_sees": obs.heartbeats.get("risk", {}).get("view", {}),
                "truth": {"var_eur": snap["var_99_1d_eur"], "gross_eur": snap["exposure"]["gross_eur"],
                          "pnl_day_eur": snap["pnl"]["day_eur"], "cash_eur": snap["cash_eur"]},
            },
            "params": self.params.all(),
            "vigie": {"enabled": self.cfg["vigie"]["enabled"], "gate": type(self.gate).__name__,
                      "heartbeat_tick": obs.heartbeats.get("vigie", {}).get("tick")},
            "cve_published": sorted(self.store.smembers(self.k("cve_published"))),
            "alerts": list(obs.alerts)[-20:],
            "human_decisions": obs.human_decisions[-20:],
            "invalid_messages": list(obs.invalid)[-20:],
            "llm_calls": obs.llm_calls,
            "decisions": dict(obs.decisions),
        }

    def graph(self) -> dict:
        declared = declared_dependencies(self.cfg)
        agents = self.agents_view()
        nodes = [{"id": a, "kind": "agent", "role": v["role"], "depends_on": declared.get(a, []), "alive": v["alive"],
                  "light": v["light"], "contaminated": v["contaminated"]} for a, v in agents.items()]
        services = self.service_modes()
        nodes += [{"id": sid, "kind": "service", "language": "php", **info} for sid, info in services.items()]
        edges, direct = [], set()
        for sid in self.cfg["services"]:
            calls = self.store.hgetall(self.k(f"svc:{sid}:calls"))
            for caller, count in sorted(calls.items()):
                if caller not in agents:
                    continue
                direct.add((caller, sid))
                is_declared = sid in declared.get(caller, [])
                edges.append({"source": caller, "target": sid, "kind": "data", "declared": is_declared, "observed": True,
                              "hidden": not is_declared, "transitive": False, "calls": int(count)})
        for agent, deps in declared.items():
            for sid in deps:
                if (agent, sid) not in direct:
                    edges.append({"source": agent, "target": sid, "kind": "data", "declared": True, "observed": False,
                                  "hidden": False, "transitive": False, "calls": 0})
        for (agent, sid), count in sorted(self.observer.transitive.items()):
            if (agent, sid) in direct or agent not in agents:
                continue
            edges.append({"source": agent, "target": sid, "kind": "data", "declared": sid in declared.get(agent, []),
                          "observed": True, "hidden": False, "transitive": True, "messages": count})
        for (src, dst, kind), count in sorted(self.observer.flows.items(), key=lambda kv: str(kv[0])):
            if src in agents and dst in [*agents, "traders", "*", "human"]:
                edges.append({"source": src, "target": dst, "kind": "message", "type": kind, "count": count})
        return {"tick": self.tick, "nodes": nodes, "edges": edges}

    def close(self) -> None:
        if self._states_fh:
            self._states_fh.close()


# --- processus ------------------------------------------------------------------------------
def prepare_run_dir(cfg: dict) -> Path:
    from journal.writer import run_dir

    path = run_dir(cfg)
    if path.exists() and any(path.iterdir()):
        archive = path.with_name(f"{path.name}-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.move(str(path), str(archive))
    path.mkdir(parents=True, exist_ok=True)
    return path


def main(argv=None) -> None:
    from bus.gate import make_gate
    from bus.message import Keyring
    from bus.redis_bus import RedisBus, connect
    from services.client import HttpServiceClient

    parser = argparse.ArgumentParser(prog="sim.world")
    parser.add_argument("--no-vigie", action="store_true")
    parser.add_argument("--ticks", type=int, default=None)
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.no_vigie:
        cfg["vigie"]["enabled"] = False
        cfg["gate"]["impl"] = "passthrough"
    client = connect(cfg["redis"]["url"])
    bus = RedisBus(client, cfg["redis"]["prefix"])
    keyring = Keyring(known_senders(cfg))
    gate = make_gate(cfg, client)
    world = World(cfg, client, keyring, gate, HttpServiceClient(cfg, "executor"), deterministic=False, run_dir=prepare_run_dir(cfg), public_publish=bus.publish)
    lock = threading.Lock()

    def listen() -> None:
        for msg in bus.listen(timeout=0.5):
            if msg is None:
                continue
            with lock:
                out = world.on_message(msg, keyring.verify(msg))
            for m in out:
                bus.publish(m)

    threading.Thread(target=listen, daemon=True).start()
    print(f"[world] {cfg['bank']['name']} : tick toutes les {world.clock.period:.2f} s, graine {cfg['seed']}", flush=True)

    def on_tick(tick: int) -> None:
        with lock:
            if tick > 0:
                world.end_tick(tick - 1)
            out = world.begin_tick(tick)
        for m in out:
            bus.publish(m)

    try:
        world.clock.run(on_tick, max_ticks=args.ticks)
    except KeyboardInterrupt:
        pass
    finally:
        world.close()


if __name__ == "__main__":
    main()
