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
from vigie.admission import Admission


class Runtime(Gate):
    def __init__(self, cfg, keyring, *, meta=None, layers=(), deterministic=True, checkpoint=None, fresh_checkpoint=False):
        self.keyring = keyring
        self.state = VigieState(meta=meta or {})
        self.contagion = Contagion(cfg, self.state)
        self.assets = set(cfg['market']['assets'])
        self.admitted_bytes = 0
        self.admission = Admission(cfg)
        self.admission_clock = None
        self.reported_internal = set()
        self.approval_out = []
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

    def authenticate(self, msg):
        sender=msg.get('from') if isinstance(msg,dict) else None
        size=cost(msg)
        if size is None or not isinstance(msg,dict):
            self.admission.reject(sender,'malformed')
            return None
        sender=msg.get('from')
        try:
            signed=self.keyring.verify(msg)
        except Exception:
            signed=False
        if not signed:
            self.admission.reject(sender,'signature')
            return None
        if not valid_message(msg,self.assets):
            self.admission.reject_provenance(msg,self.freeze.logical_tick())
            self.admission.reject(sender,'malformed',True)
            return None
        # La taille JSON sérialisée borne aussi le futur checkpoint, y compris
        # les caractères de contrôle qui grossissent à l'échappement.
        size=max(size,len(json.dumps(msg,ensure_ascii=False).encode()))
        if size>MAX_MESSAGE_BYTES:
            self.admission.reject_provenance(msg,self.freeze.logical_tick())
            self.admission.reject(sender,'malformed',True)
            return None
        return size

    def diagnostics(self):
        tick=self.freeze.logical_tick()
        out=[]
        for bucket in sorted(self.admission.pending):
            if self.admission.last_notice.get(bucket)==tick: continue
            self.admission.last_notice[bucket]=tick
            self.admission.pending.discard(bucket)
            out.append(self.factory.make(tick,'alert','human',{
                'kind':'vigie_input_rejected', 'source':bucket,
                'counts':dict(self.admission.rejected[bucket]), 'scope':'source',
                'incomplete_until_tick':self.admission.gaps.get(bucket.split(':',1)[1]),
                'unavailable_services':sorted(self.admission.unavailable_services)}))
        internal=self.freeze.uncertain-self.reported_internal
        if internal:
            self.reported_internal.update(internal)
            out.append(self.factory.make(tick,'alert','human',{
                'kind':'vigie_degraded','scope':'global','reasons':sorted(internal)}))
        return out

    def reject_input(self,msg,reason,*,gap=False):
        self.admission.reject(msg['from'],reason,True)
        self.admission.reject_provenance(msg,self.freeze.logical_tick())
        if gap:
            self.admission.gap(msg['from'],self.freeze.logical_tick(),self.freeze.window)
            if msg['from']=='executor' and msg['type']=='execution' and msg['payload'].get('status')=='executed':
                # Une somme partielle n'est pas une preuve de compromission.
                self.contagion.cash_complete=False
                self.admission.unavailable_services.add('core-ledger')
        return []

    def on_message(self, msg):
        size=self.authenticate(msg)
        if size is None: return []
        if msg['from']=='vigie': return []
        if msg['message_id'] in self.state.messages:
            if not self.admission.same_message(msg):
                return self.reject_input(msg,'identity_conflict')
            return []
        clock=self.freeze.clock if self.freeze.clock is not None else 0
        is_clock=msg['from']=='clock' and msg['type']=='tick'
        if self.admission_clock!=self.freeze.clock:
            # Deux copies internes divergent : ce n'est pas une entrée rejetée.
            self.freeze.degrade('clock_regression')
        if is_clock and self.freeze.clock is not None and (msg['tick']<=clock or msg['tick']>clock+64):
            return self.reject_input(msg,'clock_input')
        if not is_clock and (self.freeze.clock is None and msg['tick']>0 or self.freeze.clock is not None and msg['tick']>clock+1):
            return self.reject_input(msg,'future_input')
        view=msg['payload'].get('view',{}) if msg['type']=='heartbeat' else {}
        if (msg['from']=='risk' and self.contagion.cash_complete and view.get('cash_eur') is not None
                and 'as_of_tick' in view and self.contagion.ledger_clock<view['as_of_tick']+2
                and len(self.contagion.pending_balances)>=self.contagion.pending_balances.maxlen):
            # Une file de rapprochement pleine refuse cette vue, sans panne globale.
            return self.reject_input(msg,'ledger_window_full')
        reason=self.admission.check(msg,size,clock,self.freeze)
        if len(self.state.messages)>=20_000 or self.admitted_bytes+size>16*1024*1024:
            reason='storage_full'
        if reason:
            self.reject_input(msg,reason,gap=msg['type'] in {'order_proposal','price_update','execution','llm_trace'})
            if is_clock:
                # La mémoire pleine n'empêche pas de suivre l'horloge publique,
                # de conserver les baux, ni d'avertir l'humain.
                self.freeze.on_message(msg)
                self.admission_clock=self.freeze.clock
                self.state.tick=max(self.state.tick,msg['tick'])
                self.persist_freeze()
                return self.diagnostics()+[self.factory.make(msg['tick'],'heartbeat','*',{
                    'agent':'vigie','role':'vigie','stub':False})]
            return []
        if not self.state.observe(msg,self.keyring):
            return self.reject_input(msg,'projection')
        self.admitted_bytes+=size
        self.admission.commit(msg,size,clock)
        if not self.admission.record(msg,self.state,clock):
            self.reject_input(msg,'unanalysed_input')
            return self.diagnostics()
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
        if is_clock: self.admission_clock=self.freeze.clock
        self.findings.extend(findings)
        self.persist_freeze()
        out = []
        if len(findings)>32:
            self.admission.reject(msg['from'],'finding_limit',True)
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
        return out+self.diagnostics()

    def request_approval(self, msg):
        # Le gel est une règle PROVISOIRE de couche 3, remplaçable par le Dev 1.
        # Les feux, scores et autres politiques restent de son périmètre.
        # L'authentification ci-dessous n'est pas une nouvelle règle métier.
        self.approval_out=[]
        if self.authenticate(msg) is None:
            return Decision(False,'INVALID_MESSAGE_OR_SIGNATURE','vigie-layer3')
        if msg['from']!='risk' or msg['type']!='risk_decision':
            self.reject_input(msg,'request_authority')
            return Decision(False,'INVALID_REQUEST_AUTHORITY','vigie-layer3')
        if (self.freeze.clock is not None and msg['tick']>self.freeze.clock):
            self.reject_input(msg,'future_request')
            return Decision(False,'UNANALYSED_OPERATION','vigie-layer3')
        if msg['message_id'] in self.state.messages and not self.admission.same_message(msg):
            self.reject_input(msg,'identity_conflict')
            return Decision(False,canonical({'kind':'identity_conflict','evidence':[msg['message_id']]}),'vigie-layer3')
        self.approval_out=self.on_message(msg)
        if (msg['message_id'] not in self.state.messages or
                self.admission.affected(msg,self.state,self.freeze.logical_tick())):
            evidence=list(dict.fromkeys([msg['message_id'],*self.admission.last_evidence]))[:64]
            self.decisions.append({'tick':self.state.tick,'message_id':msg['message_id'],
                'approved':False,'evidence':evidence, 'herding_frozen':False,'freeze_guard':False,
                'unanalysed':True})
            return Decision(False,canonical({'kind':'unanalysed_operation','reason':self.admission.last_reason,
                'evidence':evidence}),'vigie-layer3')
        if self.checkpoint is not None and self.freeze.clock is None:
            self.reject_input(msg,'no_clock')
            return Decision(False,'UNANALYSED_OPERATION','vigie-layer3')
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
                    runtime.admission.reject(None,'wire_size')
                    continue
                data = json.loads(raw['data'])
                if raw["channel"] == prefix + "bus":
                    out = runtime.on_message(data)
                else:
                    msg = data["message"]
                    decision = runtime.request_approval(msg)
                    out = runtime.approval_out
                    client.publish(prefix + "vigie.approval_responses", canonical({
                        "request_id": data["request_id"], **asdict(decision)}))
                for message in out+runtime.diagnostics():
                    client.publish(prefix + "bus", canonical(message))
            except Exception:
                runtime.admission.reject(None,'wire_envelope')
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
