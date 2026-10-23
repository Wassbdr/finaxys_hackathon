"""Phase 0 : bus signé, heartbeat, portique ciblé ; les couches restent injectables."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

from bus.gate import Decision, Gate
from bus.message import Keyring, MessageFactory, canonical
from sim.config import known_senders, load_config
from vigie.core import VigieState
from vigie.contagion import Contagion
from vigie.analytics import Analytics


class Runtime(Gate):
    def __init__(self, cfg, keyring, *, meta=None, layers=(), deterministic=True):
        self.keyring = keyring
        self.state = VigieState(meta=meta or {})
        self.contagion = Contagion(cfg, self.state)
        self.layers = [self.contagion, *layers]
        # L'adaptateur livré conserve ses calculs ; seules les versions publiques
        # effectivement disponibles lui sont transmises pour son estimation R0.
        self.analytics = Analytics({**cfg, "services": self.state.meta}, keyring)
        self.factory = MessageFactory("vigie", keyring, deterministic)
        self.findings = []
        self.decisions = []

    def on_message(self, msg):
        if not isinstance(msg, dict) or msg.get("from") == "vigie" or not self.state.observe(msg, self.keyring):
            return []
        findings = []
        self.analytics.observe(msg)
        # L'échantillonnage à l'ouverture du tick reste celui du module livré.
        if msg["from"] == "clock" and msg["type"] == "tick":
            findings.extend(self.analytics.herding.findings(msg["tick"]))
        for layer in self.layers:
            findings.extend(layer.on_message(self.state.messages[msg["message_id"]]))
        self.findings.extend(findings)
        out = [self.factory.make(self.state.tick, "alert", "human", {
            "kind": "layer3_finding", "finding": asdict(f)}) for f in findings]
        if msg["from"] == "clock" and msg["type"] == "tick":
            out.append(self.factory.make(msg["tick"], "heartbeat", "*", {
                "agent": "vigie", "role": "vigie", "stub": False}))
        return out

    def request_approval(self, msg):
        # La politique de feux, seuils et scores appartient au Dev 1. En phase 0,
        # seule une opération marquée est refusée, avec sa chaîne de preuves.
        # L'authentification ci-dessous n'est pas une nouvelle règle métier.
        try:
            valid = self.keyring.verify(msg)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            return Decision(False, "INVALID_SIGNATURE", "vigie-layer3")
        self.on_message(msg)
        marked, evidence = self.contagion.quarantine(msg)
        self.decisions.append({"tick": self.state.tick, "message_id": msg["message_id"],
                               "approved": not marked, "evidence": evidence})
        reason = canonical({"kind": "marked_operation", "evidence": evidence}) if marked else "unmarked_operation"
        return Decision(not marked, reason, "vigie-layer3")


def serve(cfg, client, *, stop=None, meta=None, layers=()):
    prefix = cfg["redis"]["prefix"]
    runtime = Runtime(cfg, Keyring(known_senders(cfg)), meta=meta, layers=layers, deterministic=False)
    sub = client.pubsub(ignore_subscribe_messages=False)
    channels = {prefix + "bus", prefix + "vigie.approval_requests"}
    sub.subscribe(*sorted(channels))
    try:
        deadline = time.monotonic() + 5
        subscribed = set()
        while subscribed != channels:
            raw = sub.get_message(ignore_subscribe_messages=False, timeout=0.1)
            if raw and raw["type"] == "subscribe":
                subscribed.add(raw["channel"])
            if time.monotonic() > deadline:
                raise TimeoutError("Souscriptions Vigie non confirmées")
        client.set(prefix + "agent:vigie:pid", os.getpid())
        client.set(prefix + "vigie:ready", "1")
        while stop is None or not stop.is_set():
            raw = sub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if not raw:
                continue
            try:
                data = json.loads(raw["data"])
                if raw["channel"] == prefix + "bus":
                    out = runtime.on_message(data)
                else:
                    msg = data["message"]
                    out = runtime.on_message(msg)
                    decision = runtime.request_approval(msg)
                    client.publish(prefix + "vigie.approval_responses", canonical({
                        "request_id": data["request_id"], **asdict(decision)}))
                for message in out:
                    client.publish(prefix + "bus", canonical(message))
            except (KeyError, TypeError, ValueError, AttributeError):
                # Une enveloppe invalide ne doit pas arrêter l'observation du bus.
                continue
    finally:
        sub.close()
        client.delete(prefix + "vigie:ready")
    return runtime


def couche3_config(cfg):
    import copy
    cfg = copy.deepcopy(cfg)
    cfg["chaos"]["announce_compromised"] = False
    cfg["vigie"].update(enabled=True, stub=False)
    cfg["gate"]["impl"] = "bus"
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "launch"], nargs="?", default="serve")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.command == "launch":
        import yaml
        from sim.launch import procfile, parse_procfile, supervise
        cfg = couche3_config(cfg)
        if args.no_llm:
            cfg["llm"]["enabled"] = False
        path = Path(cfg["runs_dir"]) / "couche3-config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        os.environ["HORIZON_CONFIG"] = str(path.resolve())
        text = procfile(cfg, args.no_llm, False)
        Path("Procfile.generated").write_text(text, encoding="utf-8")
        raise SystemExit(supervise(parse_procfile(text)))
    from bus.redis_bus import connect
    import httpx
    meta = {}
    # Seul /meta est interrogé : aucun endpoint de chaos ni état du monde.
    with httpx.Client(timeout=2.0) as http:
        for sid, spec in cfg["services"].items():
            url = f"http://127.0.0.1:{spec['port']}/meta"
            try:
                response = http.get(url)
                response.raise_for_status()
                record = response.json()
                if record.get("service_id") == sid:
                    meta[sid] = record
            except (httpx.HTTPError, ValueError):
                print(f"[vigie] /meta indisponible : {sid} ; suspicion CVE impossible pour ce service", flush=True)
    serve(cfg, connect(cfg["redis"]["url"]), meta=meta)


if __name__ == "__main__":
    main()
