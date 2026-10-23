"""Injection de failles : CLI, appelée aussi par POST /inject et par les scénarios YAML.

    python -m sim.inject publish-cve
    python -m sim.inject compromise market-feed | stale <svc> | down <svc> | slow <svc> | restore <svc>
    python -m sim.inject fake-agent [--as risk|trader-9]
    python -m sim.inject kill trader-2 | kill-vigie
    python -m sim.inject set herding 0.8 | set guardrails on|off | set restricted CITRA,ACME
    python -m sim.inject light ORANGE | human resume | human unblock BEN-07 | human set_threshold price_drift_z 5
    python -m sim.inject news BOREAL -6 "BOREAL : avertissement sur résultats"
    python -m sim.inject invoice BEN-07 900000 | change-iban BEN-02 "FR76 ..."
    python -m sim.inject scenario scenarios/php2shell_day.yaml

Aucun exploit ici : « compromis » ne fait que basculer le mode de données d'un service simulé.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import signal
import sys
import time
from pathlib import Path

import yaml

from bus.message import MessageFactory
from sim.clock import Clock, parse_at

SERVICE_MODES = {"compromise": "compromised", "stale": "stale", "down": "down", "slow": "slow", "restore": "normal"}


# --- cibles : lockstep (en mémoire) ou processus (Redis + HTTP) -----------------------------
class LockstepTarget:
    def __init__(self, ls):
        self.ls = ls
        self.cfg = ls.cfg
        self.store = ls.store
        self.prefix = ls.prefix
        self.keyring = ls.keyring
        self.factory = ls.inject_factory

    def tick(self) -> int:
        return self.ls.tick

    def factory_for(self, sender: str, keyed: bool = True) -> MessageFactory:
        """Fabriques déterministes : une partie lockstep reste rejouable à l'identique."""
        cache = self.ls.__dict__.setdefault("_inject_factories", {})
        if (sender, keyed) not in cache:
            cache[(sender, keyed)] = MessageFactory(sender, self.keyring if keyed else None)
        return cache[(sender, keyed)]

    def forged_key(self) -> bytes:
        return hashlib.sha256(f"cle-inconnue-{self.ls.tick}".encode()).digest()

    def set_service_mode(self, sid: str, mode: str) -> dict:
        return self.ls.cores[sid].set_mode(mode, by="inject")

    def publish(self, msg: dict) -> None:
        self.ls.publish(msg)

    def set_param(self, name: str, value) -> None:
        self.ls.params.set(name, value)

    def kill(self, agent_id: str) -> str:
        self.ls.killed.add(agent_id)
        return f"{agent_id} débranché"

    def change_iban(self, ben: str, iban: str) -> None:
        self.ls.cores["payment-gateway"].change_iban(ben, iban)


class ProcessTarget:
    def __init__(self, cfg: dict, client=None):
        from bus.message import Keyring
        from bus.redis_bus import RedisBus, connect
        from sim.config import known_senders

        self.cfg = cfg
        self.prefix = cfg["redis"]["prefix"]
        self.store = client or connect(cfg["redis"]["url"], wait_s=3)
        self.bus = RedisBus(self.store, self.prefix)
        self.keyring = Keyring(known_senders(cfg))
        self.factory = MessageFactory("inject", self.keyring, deterministic=False)

    def tick(self) -> int:
        return int(self.store.get(f"{self.prefix}world:tick") or 0)

    def factory_for(self, sender: str, keyed: bool = True) -> MessageFactory:
        if sender == "inject" and keyed:
            return self.factory
        return MessageFactory(sender, self.keyring if keyed else None, deterministic=False)

    def forged_key(self) -> bytes:
        return secrets.token_bytes(32)

    def set_service_mode(self, sid: str, mode: str) -> dict:
        import httpx

        port = self.cfg["services"][sid]["port"] + int(self.cfg.get("port_offset", 0))
        resp = httpx.post(f"http://127.0.0.1:{port}/chaos", json={"mode": mode, "by": "inject"}, timeout=3)
        resp.raise_for_status()
        return resp.json()

    def publish(self, msg: dict) -> None:
        self.bus.publish(msg)

    def set_param(self, name: str, value) -> None:
        from sim.params import Params

        Params(self.store, self.prefix, self.cfg).set(name, value)

    def kill(self, agent_id: str) -> str:
        pid = self.store.get(f"{self.prefix}agent:{agent_id}:pid")
        if not pid:
            return f"{agent_id} : PID inconnu"
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            return f"{agent_id} déjà arrêté"
        return f"{agent_id} (pid {pid}) débranché"

    def change_iban(self, ben: str, iban: str) -> None:
        tick = self.tick()
        self.store.hset(f"{self.prefix}svc:payment-gateway:iban_changes", ben, json.dumps({"iban": iban, "tick": tick}))


class QueueTarget:
    """Mode comparaison : la commande est mise en file ; sim.compare l'applique aux deux mondes au même tick."""

    def __init__(self, cfg: dict, client=None):
        from bus.redis_bus import connect

        self.cfg = cfg
        self.store = client or connect(cfg["redis"]["url"], wait_s=3)
        self.key = "compare:inject_queue"

    def tick(self) -> int:
        return int(self.store.get(f"{self.cfg['redis']['prefix']}world:tick") or 0)

    def submit(self, args: list[str]) -> str:
        self.store.rpush(self.key, json.dumps(args))
        return f"en file pour A et B au prochain tick : {' '.join(args)}"


# --- commandes ------------------------------------------------------------------------------
def _msg(target, type_: str, to: str, payload: dict, sender: str = "inject") -> dict:
    return target.factory_for(sender).make(target.tick(), type_, to, payload)


def run_command(target, args: list[str]) -> str:
    """Exécute une commande d'injection ; renvoie un compte rendu lisible."""
    if not args:
        raise ValueError("commande vide")
    if isinstance(target, QueueTarget):
        return target.submit(args)
    cmd, rest = args[0], args[1:]
    services = list(target.cfg["services"])

    if cmd in SERVICE_MODES:
        if not rest or rest[0] not in services:
            raise ValueError(f"{cmd} <service> ; services : {', '.join(services)}")
        target.set_service_mode(rest[0], SERVICE_MODES[cmd])
        return f"{rest[0]} -> {SERVICE_MODES[cmd]}"

    if cmd == "publish-cve":
        from sim.world import load_cve_feed, version_tuple

        cves = load_cve_feed(target.cfg)
        cve = next((c for c in cves if not rest or c["id"] == rest[0] or c["name"] == rest[0]), cves[0])
        target.store.sadd(f"{target.prefix}cve_published", cve["id"])
        affected = [sid for sid, spec in target.cfg["services"].items()
                    if version_tuple(spec["php_version"]) < version_tuple(cve["affects"]["version_lt"])]
        target.publish(_msg(target, "alert", "*", {
            "kind": "cve_published", "severity": "critical", "cve": cve, "affected_services": affected,
            "unpatchable": [s for s in affected if not target.cfg["services"][s]["patchable"]],
            "text": f"{cve['name']} ({cve['id']}) publiée : {', '.join(affected)} concernés.",
        }, sender="secu"))
        return f"{cve['id']} publiée ; concernés : {', '.join(affected)}"

    if cmd == "fake-agent":
        claimed = rest[rest.index("--as") + 1] if "--as" in rest else "risk"
        if claimed == "risk":
            proposal = {"message_id": "trader-1:forged", "from": "trader-1", "type": "order_proposal",
                        "payload": {"proposal_id": "forged", "trader": "trader-1", "asset": "CITRA", "side": "buy",
                                    "notional_eur": 20_000_000, "justification": "ordre urgent"}}
            fake = target.factory_for("risk", keyed=False).make(target.tick(), "risk_decision", "executor", {
                "approved": True, "kind": "order", "notional_eur": 20_000_000, "requested_eur": 20_000_000,
                "reasons": [], "proposal": proposal})
        else:
            fake = target.factory_for(claimed, keyed=False).make(target.tick(), "order_proposal", "compliance", {
                "proposal_id": f"{claimed}-fake", "trader": claimed, "asset": "BOREAL", "side": "sell",
                "notional_eur": 5_000_000, "news_refs": [], "justification": "ordre d'un agent inconnu"})
        target.keyring.sign(fake, key=target.forged_key())
        target.publish(fake)
        return f"faux message {fake['type']} signé par une clé inconnue (émetteur annoncé : {claimed})"

    if cmd == "kill":
        if not rest:
            raise ValueError("kill <agent>")
        return target.kill(rest[0])

    if cmd == "kill-vigie":
        return target.kill("vigie")

    if cmd == "set":
        if len(rest) < 2:
            raise ValueError("set herding <x> | set guardrails on|off | set restricted A,B")
        name, value = rest[0], rest[1]
        if name == "herding":
            x = float(value)
            if not 0 <= x <= 1:
                raise ValueError("herding entre 0 et 1")
            target.set_param("herding", x)
        elif name == "guardrails":
            if value not in ("on", "off"):
                raise ValueError("guardrails on|off")
            target.set_param("guardrails", value)
        elif name == "restricted":
            target.set_param("restricted_list", [v for v in value.split(",") if v and v != "-"])
        else:
            raise ValueError(f"paramètre inconnu : {name}")
        # La Vigie ne lit pas le store des paramètres : l'opérateur publie
        # explicitement le changement sur le même bus signé que ses décisions.
        published_name = 'restricted_list' if name == 'restricted' else name
        published_value = x if name == 'herding' else (
            [v for v in value.split(',') if v and v != '-'] if name == 'restricted' else value)
        target.publish(_msg(target, 'alert', '*', {'kind': 'parameter_changed',
                       'name': published_name, 'value': published_value}))
        return f"{name} = {value}"

    if cmd == "light":
        light = rest[0].upper() if rest else ""
        if light not in ("VERT", "ORANGE", "ROUGE"):
            raise ValueError("light VERT|ORANGE|ROUGE")
        target.publish(_msg(target, "light_change", "*", {"light": light, "reason": " ".join(rest[1:]) or "inject"}))
        return f"feu {light}"

    if cmd == "human":
        if not rest:
            raise ValueError("human resume | human resume_trading | human unblock <cible>")
        payload = {"action": rest[0], "by": "opérateur"}
        if rest[0] == "unblock":
            payload["target"] = rest[1]
        if rest[0] == "set_threshold":
            payload.update(rule=rest[1], value=float(rest[2]))
        target.publish(_msg(target, "human_decision", "*", payload, sender="human"))
        return f"décision humaine : {' '.join(rest)}"

    if cmd == "news":
        if len(rest) < 2:
            raise ValueError('news <actif> <variation %> "titre"')
        asset, pct = rest[0], float(rest[1])
        headline = rest[2] if len(rest) > 2 else f"{asset} : événement de marché"
        target.publish(_msg(target, "alert", "world", {"kind": "market_event", "asset": asset, "pct": pct, "headline": headline,
                                                        "text": f"Événement réel : {headline} ({pct:+.1f} %)."}))
        return f"news réelle {asset} {pct:+.1f} %"

    if cmd == "invoice":
        if len(rest) < 2:
            raise ValueError("invoice <bénéficiaire> <montant>")
        target.publish(_msg(target, "alert", "payment", {"kind": "invoice", "beneficiary_id": rest[0], "amount_eur": float(rest[1]),
                                                          "text": f"Facture à régler : {rest[0]}, {float(rest[1]):,.0f} €."}))
        return f"facture {rest[0]} {rest[1]} €"

    if cmd == "change-iban":
        if len(rest) < 2:
            raise ValueError("change-iban <bénéficiaire> <iban>")
        target.change_iban(rest[0], rest[1])
        return f"IBAN de {rest[0]} modifié"

    if cmd == "note":
        text = " ".join(rest)
        target.publish(_msg(target, "alert", "*", {"kind": "scenario_note", "severity": "info", "text": text}))
        return text

    raise ValueError(f"commande inconnue : {cmd}")


# --- scénarios ------------------------------------------------------------------------------
def load_scenario(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def scenario_steps(scenario: dict, clock: Clock) -> list[tuple[int, list[str]]]:
    steps = []
    for step in scenario.get("steps", []):
        at = parse_at(step.get("at", 0), clock)
        if "note" in step:
            steps.append((at, ["note", step["note"]]))
        if "do" in step:
            steps.append((at, shlex.split(step["do"])))
    return sorted(steps, key=lambda s: s[0])


def initial_commands(scenario: dict) -> list[list[str]]:
    cmds = []
    for name, value in (scenario.get("params") or {}).items():
        cmds.append(["set", "restricted" if name == "restricted_list" else name,
                     ",".join(value) if isinstance(value, list) else str(value)])
    return cmds


def schedule_scenario(ls, scenario: dict, start: int = 0) -> int:
    """Planifie un scénario dans un lockstep à partir du tick `start` ; renvoie sa durée en ticks."""
    clock = Clock.from_config(ls.cfg)
    log = []

    def action(args):
        return lambda l: log.append((l.tick, args, run_command(LockstepTarget(l), args)))

    for cmd in initial_commands(scenario):
        ls.at(start, action(cmd))
    for at, args in scenario_steps(scenario, clock):
        ls.at(start + at, action(args))
    ls.scenario_log = log
    return parse_at(scenario.get("duration", "180s"), clock)


def run_scenario(target, scenario: dict, out=print) -> None:
    """Mode processus : suit l'horloge du monde et déclenche chaque étape à son tick."""
    clock = Clock.from_config(target.cfg)
    start = target.tick()
    out(f"scénario {scenario.get('name')} : départ au tick {start}")
    for cmd in initial_commands(scenario):
        out(f"  t{target.tick()} {' '.join(cmd)} -> {run_command(target, cmd)}")
    for at, args in scenario_steps(scenario, clock):
        while target.tick() < start + at:
            time.sleep(clock.period / 4)
        out(f"  t{target.tick()} {' '.join(args)} -> {run_command(target, args)}")
    end = start + parse_at(scenario.get("duration", "180s"), clock)
    out(f"scénario lancé ; fin prévue au tick {end}")


def main(argv=None) -> None:
    from sim.config import load_config

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cfg = load_config()
    if cfg.get("compare"):
        print(QueueTarget(cfg).submit(argv))
        return
    target = ProcessTarget(cfg)
    if argv[0] == "scenario":
        run_scenario(target, load_scenario(argv[1]))
        return
    try:
        print(run_command(target, argv))
    except ValueError as err:
        print(f"erreur : {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
