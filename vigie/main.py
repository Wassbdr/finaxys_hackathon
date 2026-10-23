"""Phase 0 : bus signé, heartbeat, portique ciblé ; les couches restent injectables."""
import argparse
from collections import deque
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

from bus.gate import Decision, Gate
from bus.message import Keyring, MessageFactory, canonical
from sim.config import known_senders, load_config, trader_ids
from vigie.core import VigieState
from vigie.contagion import Contagion
from vigie.analytics import Analytics
from vigie.freeze import Freeze, load_profile
from vigie.validation import valid_message, cost, MAX_MESSAGE_BYTES


class Runtime(Gate):
    def __init__(self, cfg, keyring, *, meta=None, layers=(), deterministic=True, checkpoint=None, fresh_checkpoint=False):
        self.keyring = keyring
        self.state = VigieState(meta=meta or {})
        self.contagion = Contagion(cfg, self.state)
        self.assets = set(cfg['market']['assets'])
        self.admitted_bytes = 0
        self.trace_ticks = deque(maxlen=64)
        self.burst = {}
        self.checkpoint = checkpoint
        self.checkpoint_writable = checkpoint is None
        required = 'freeze_profile' not in cfg['vigie'] or cfg['vigie'].get('freeze_profile') is not None
        try:
            profile = cfg['vigie']['freeze_profile'] if 'freeze_profile' in cfg['vigie'] else load_profile()
            self.freeze = Freeze(self.state, trader_ids(cfg), profile, assets=self.assets, required=required)
        except Exception:
            self.freeze = Freeze(self.state, trader_ids(cfg), None, assets=self.assets, required=True)
        if checkpoint is not None:
            try:
                if fresh_checkpoint:
                    if checkpoint.path.exists(): raise ValueError('État existant : aucun écrasement')
                    checkpoint.bootstrap(self.freeze.snapshot(), self.freeze.profile)
                else:
                    if not self.freeze.restore(checkpoint.load(self.freeze.profile)):
                        raise ValueError('Checkpoint incohérent')
                    # Un redémarrage ne prouve pas que le bus a été reçu sans trou.
                    self.freeze.degrade('checkpoint_unavailable')
                self.checkpoint_writable = True
            except Exception:
                self.freeze.degrade('checkpoint_unavailable')
        self.layers = [self.contagion, self.freeze, *layers]
        # L'adaptateur livré conserve ses calculs ; seules les versions publiques
        # effectivement disponibles lui sont transmises pour son estimation R0.
        self.analytics = Analytics({**cfg, "services": self.state.meta}, keyring)
        self.factory = MessageFactory("vigie", keyring, deterministic)
        self.findings = deque(maxlen=4096)
        self.decisions = deque(maxlen=4096)

    def persist_freeze(self):
        if self.checkpoint is None:
            return True
        if not self.checkpoint_writable:
            self.freeze.degrade('checkpoint_unavailable')
            return False
        try:
            self.checkpoint.save(self.freeze.snapshot(), self.freeze.profile)
            return True
        except Exception:
            self.freeze.degrade('checkpoint_write')
            return False

    def on_message(self, msg):
        if not isinstance(msg, dict): return []
        if msg.get('from') == 'vigie': return []
        if not valid_message(msg, self.assets):
            # Un paquet non authentifiable ne peut pas geler les autres acteurs.
            # L'opération correspondante sera refusée au portique, jamais admise.
            try:
                if cost(msg) is not None and self.keyring.verify(msg):
                    self.freeze.degrade('invalid_message')
                    self.persist_freeze()
            except Exception:
                pass
            return []
        if (self.freeze.clock is not None and msg['from'] != 'clock'
                and msg['tick'] > self.freeze.clock + 1):
            if self.keyring.verify(msg): self.freeze.degrade('clock_regression')
            self.persist_freeze()
            return []
        clock = self.freeze.logical_tick()
        self.trace_ticks = deque((t for t in self.trace_ticks if t > clock-30), maxlen=64)
        self.burst = {key:value for key,value in self.burst.items() if key[0]>=clock-1}
        key = (clock,msg['from'])
        if (self.burst.get(key,0)>=128 or msg['type']=='llm_trace' and len(self.trace_ticks)>=64):
            if self.keyring.verify(msg): self.freeze.degrade('capacity')
            return []
        if ('capacity' in self.freeze.uncertain or len(self.state.messages) >= 20_000
                or self.admitted_bytes + cost(msg) > 16*1024*1024):
            self.freeze.degrade('capacity')
            # Continuer à voir l'horloge sans grossir les autres historiques.
            if msg['type'] == 'tick' and msg['from'] == 'clock' and self.keyring.verify(msg):
                self.freeze.on_message(msg)
                out = [self.factory.make(msg['tick'], 'heartbeat', '*', {'agent':'vigie','role':'vigie','stub':False})]
            else:
                out = []
            self.persist_freeze()
            return out
        if not self.state.observe(msg, self.keyring): return []
        self.admitted_bytes += cost(msg)
        self.burst[key] = self.burst.get(key,0)+1
        if msg['type']=='llm_trace': self.trace_ticks.append(clock)
        findings = []
        try:
            self.analytics.observe(msg)
        except Exception:
            self.freeze.degrade('analysis_error')
        # L'échantillonnage à l'ouverture du tick reste celui du module livré.
        if msg["from"] == "clock" and msg["type"] == "tick":
            try:
                findings.extend(self.analytics.herding.findings(msg['tick']))
            except Exception:
                self.freeze.degrade('analysis_error')
        for layer in self.layers:
            try:
                findings.extend(layer.on_message(self.state.messages[msg['message_id']]))
            except Exception:
                self.freeze.degrade('analysis_error')
        self.findings.extend(findings)
        self.persist_freeze()
        out = []
        if len(findings)>32: self.freeze.degrade('capacity')
        for finding in findings[:32]:
            try:
                out.append(self.factory.make(self.state.tick, 'alert', 'human', {
                    'kind':'layer3_finding', 'finding':asdict(finding)}))
            except Exception:
                self.freeze.degrade('analysis_error')
        self.persist_freeze()
        if msg["from"] == "clock" and msg["type"] == "tick":
            out.append(self.factory.make(msg["tick"], "heartbeat", "*", {
                "agent": "vigie", "role": "vigie", "stub": False}))
        return out

    def request_approval(self, msg):
        # Le gel est une règle PROVISOIRE de couche 3, remplaçable par le Dev 1.
        # Les feux, scores et autres politiques restent de son périmètre.
        # L'authentification ci-dessous n'est pas une nouvelle règle métier.
        try:
            valid = valid_message(msg, self.assets) and self.keyring.verify(msg)
        except Exception:
            valid = False
        if not valid:
            return Decision(False, 'INVALID_MESSAGE_OR_SIGNATURE', 'vigie-layer3')
        self.on_message(msg)
        if (self.freeze.clock is not None and msg['tick'] > self.freeze.clock
                or self.checkpoint is not None and self.freeze.clock is None):
            self.freeze.degrade('clock_regression')
        try:
            marked, evidence = self.contagion.quarantine(msg)
        except Exception:
            self.freeze.degrade('analysis_error')
            marked, evidence = True, []
        frozen, freeze_evidence = self.freeze.refusal(msg)
        evidence = list(dict.fromkeys([*evidence, *freeze_evidence]))
        persisted = self.persist_freeze()
        guard_failed = bool(self.freeze.uncertain) or (frozen and self.freeze.last_refusal!='herding_freeze')
        refused = marked or frozen or not persisted or guard_failed
        self.decisions.append({"tick": self.state.tick, "message_id": msg["message_id"],
                               "approved": not refused, "evidence": evidence, "herding_frozen": frozen and not guard_failed, "freeze_guard": guard_failed or not persisted})
        reason = canonical({"kind": ("freeze_state_unavailable" if guard_failed or not persisted else self.freeze.last_refusal) if frozen or guard_failed or not persisted else "marked_operation", "evidence": evidence}) if refused else "unmarked_operation"
        return Decision(not refused, reason, "vigie-layer3")


def serve(cfg, client, *, stop=None, meta=None, layers=(), checkpoint=None, fresh_checkpoint=False):
    prefix = cfg["redis"]["prefix"]
    if checkpoint is None:
        from vigie.freeze_store import FileCheckpoint
        import hashlib
        namespace=hashlib.sha256(prefix.encode()).hexdigest()[:16]
        checkpoint=FileCheckpoint(Path(cfg['runs_dir'])/('freeze-state-'+namespace+'.json'))
    runtime = Runtime(cfg, Keyring(known_senders(cfg)), meta=meta, layers=layers, deterministic=False, checkpoint=checkpoint, fresh_checkpoint=fresh_checkpoint)
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
                if not isinstance(raw.get('data'), (str, bytes)) or len(raw['data']) > MAX_MESSAGE_BYTES:
                    # Sans signature vérifiable, pas de mutation de l'état.
                    # Une demande trop grande expire en refus côté BusGate.
                    continue
                data = json.loads(raw['data'])
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
            except Exception:
                # Les fautes authentifiées sont retenues dans Runtime. Une
                # enveloppe externe illisible ne doit pas empoisonner son état.
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
    cfg['gate']['default_on_timeout'] = 'reject'
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "launch"], nargs="?", default="serve")
    parser.add_argument('--no-llm', action='store_true')
    parser.add_argument('--freeze-state', help='checkpoint local public du gel')
    parser.add_argument('--fresh-freeze-state', action='store_true', help='amorçage EXPLICITE, uniquement sur fichier absent')
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
        from shlex import quote
        flags = (' --freeze-state ' + quote(args.freeze_state)) if args.freeze_state else ''
        if args.fresh_freeze_state: flags += ' --fresh-freeze-state'
        text = '\n'.join(line+flags if line.startswith('vigie:') else line for line in text.splitlines())+'\n'
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
    from vigie.freeze_store import FileCheckpoint
    import hashlib
    namespace = hashlib.sha256(cfg['redis']['prefix'].encode()).hexdigest()[:16]
    path = args.freeze_state or str(Path(cfg['runs_dir']) / ('freeze-state-' + namespace + '.json'))
    serve(cfg, connect(cfg['redis']['url']), meta=meta, checkpoint=FileCheckpoint(path),
          fresh_checkpoint=args.fresh_freeze_state)


if __name__ == "__main__":
    main()
