"""E : moteur Invariant local, traces d'opérations issues du bus signé uniquement."""
from __future__ import annotations

import argparse
from collections import deque
from contextvars import ContextVar
import copy
from dataclasses import asdict
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import time

from bus.gate import Decision
from bus.message import Keyring, MessageFactory, canonical
from sim.config import known_senders, load_config, trader_ids
from vigie.validation import valid_message, MAX_MESSAGE_BYTES

UPSTREAM = '2340fe2d9cd619f73d5b67fa05bf8a08c7cad515'
POLICY = Path(__file__).with_name('invariant_rules')/'bank.ipl'
_ENGINE = ContextVar('invariant_engine_active', default=False)


def _engine_network_guard(event, args):
    if _ENGINE.get() and event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.bind'}:
        raise RuntimeError('Le moteur Invariant doit fonctionner sans réseau')


sys.addaudithook(_engine_network_guard)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def version(value):
    parts = tuple(int(part) for part in value.split('.'))
    return parts+(0,)*max(0, 3-len(parts))


def tool_call(id_, name, arguments):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': id_, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}]}


class InvariantGuard:
    id = 'vigie'

    def __init__(self, cfg, keyring=None, *, meta=None, deterministic=True):
        # LocalPolicy est explicite : l'alias Policy de l'amont peut être distant.
        from invariant.analyzer import LocalPolicy
        self.engine_version = importlib.metadata.version('invariant-ai')
        if self.engine_version != '0.3.5':
            raise RuntimeError('Installer la version épinglée dans requirements-comparateurs.txt')
        self.policy = LocalPolicy.from_file(str(POLICY))
        self.cfg = copy.deepcopy(cfg)
        self.keys = keyring or Keyring(known_senders(cfg))
        self.factory = MessageFactory('vigie', self.keys, deterministic)
        self.assets = set(cfg['market']['assets'])
        self.traders = set(trader_ids(cfg))
        self.meta = {sid: {k: spec[k] for k in ('version', 'php_version', 'language') if k in spec}
                     for sid, spec in (meta or {}).items()}
        self.cves = []
        self.proposals, self.visas, self.texts = {}, {}, {}
        self.seen, self.used, self.decided = set(), set(), {}
        self.tick, self.last_clock, self.day, self.reserved = -1, None, -1, 0.0
        self.receipt_mismatch = False
        self.decisions, self.incidents, self.traces = [], [], []
        self.pending = []
        self.latencies = deque(maxlen=10000)
        self.max_age = cfg['vigie']['heartbeat_timeout_ticks']
        self.wall_lease = self.max_age*cfg['clock']['tick_seconds']/cfg['clock']['accelerator']

    def valid(self, msg):
        try:
            return (valid_message(msg, self.assets) and self.keys.verify(msg)
                    and msg['message_id'].startswith(msg['from']+':'))
        except (TypeError, ValueError, KeyError):
            return False

    def on_message(self, msg):
        if not self.valid(msg) or msg['from'] == 'vigie' or msg['message_id'] in self.seen:
            return []
        self.seen.add(msg['message_id'])
        p, kind, sender = msg['payload'], msg['type'], msg['from']
        if sender == 'clock' and kind == 'tick':
            if msg['tick'] <= self.tick:
                return []
            self.tick, self.last_clock = msg['tick'], time.monotonic()
            day = self.tick//self.cfg['clock']['ticks_per_day']
            if day != self.day:
                self.day, self.reserved = day, 0.0
            return [self.factory.make(self.tick, 'heartbeat', '*', {'agent': 'vigie', 'role': 'vigie', 'comparator': 'Invariant Guardrails'})]
        if sender == 'secu' and kind == 'alert' and p.get('kind') == 'cve_published':
            affects = p.get('cve', {}).get('affects', {})
            if isinstance(affects, dict) and 'language' in affects and 'version_lt' in affects:
                self.cves.append({'language': affects['language'], 'version_lt': affects['version_lt'], 'message_id': msg['message_id']})
        if (sender in self.traders and kind == 'order_proposal') or (sender == 'payment' and kind == 'payment_proposal'):
            fields = ('proposal_id', 'trader', 'asset', 'side', 'notional_eur', 'payment_id', 'beneficiary_id', 'iban', 'amount_eur')
            self.proposals[msg['message_id']] = {'tick': msg['tick'], 'from': sender, 'type': kind,
                'payload': {k: p[k] for k in fields if k in p}}
        if sender == 'compliance' and kind == 'compliance_decision' and p.get('approved') is True:
            proposal = p.get('proposal', {})
            if isinstance(proposal, dict) and proposal.get('message_id') in self.proposals:
                self.visas[proposal['message_id']] = {'tick': msg['tick'], 'message_id': msg['message_id'],
                                                      'proposal': copy.deepcopy(proposal)}
        if kind == 'llm_trace':
            external = p.get('external', [])
            if isinstance(external, list):
                for item in external[:8]:
                    if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                        continue
                    matches = [e for e in msg.get('data_provenance', []) if e.get('service') == item.get('source') and e.get('response_hash')]
                    if len(matches) == 1:
                        e = matches[0]
                        self.texts[(e['service'], e['response_hash'])] = (item['text'][:8192], msg['message_id'])
        if sender == 'executor' and kind == 'execution' and p.get('kind') == 'payment' and p.get('status') == 'executed':
            expected = {b['id']: b['iban'] for b in self.cfg['beneficiaries']}.get(p.get('beneficiary_id'))
            if expected is not None and p.get('iban_credited') != expected:
                self.receipt_mismatch = True
        return []

    def vulnerable(self):
        out = {}
        for sid, spec in self.meta.items():
            for cve in self.cves:
                try:
                    if spec['language'] == cve['language'] and version(spec['php_version']) < version(cve['version_lt']):
                        out[sid] = spec['version']
                except (KeyError, ValueError, AttributeError):
                    continue
        return out

    @staticmethod
    def identity(proposal):
        p = proposal['payload']
        fields = ('payment_id', 'beneficiary_id', 'iban', 'amount_eur') if proposal['type'] == 'payment_proposal' else ('proposal_id', 'trader', 'asset', 'side', 'notional_eur')
        return proposal['type'], proposal['from'], tuple(p.get(k) for k in fields)

    def operation_trace(self, msg, original):
        trace, evidence = [], [msg['message_id']]
        # La provenance directement portée par la décision est déjà un héritage
        # public du producteur. Aucun graphe de contagion n'est reconstruit ici.
        seen = set()
        for entry in msg.get('data_provenance', []):
            service, hash_ = entry.get('service'), entry.get('response_hash')
            key = service, hash_
            if key in seen or not service:
                continue
            seen.add(key)
            id_ = f'source-{len(seen)}'
            text, message_id = self.texts.get(key, ('', None))
            data = {'service': service, 'version': entry.get('version', ''), 'response_hash': hash_, 'text': text}
            trace.extend([tool_call(id_, 'service_data', {'service': service}),
                          {'role': 'tool', 'tool_call_id': id_, 'content': canonical(data)}])
            if message_id:
                evidence.append(message_id)
        p, q = msg['payload'], original['payload']
        name = 'execute_payment' if p['kind'] == 'payment' else 'execute_order'
        arguments = {'amount': p['notional_eur'], 'requested': q.get('amount_eur', q.get('notional_eur')),
                     'beneficiary': q.get('beneficiary_id', ''), 'iban': q.get('iban', ''),
                     'pnl_day': p['risk_view']['pnl_day_eur']}
        trace.append(tool_call(msg['message_id'], name, arguments))
        return trace, list(dict.fromkeys(evidence))

    def check(self, msg):
        if not self.valid(msg):
            return ['INVALID_SIGNATURE_OR_MESSAGE'], [], None
        if msg['from'] != 'risk' or msg['to'] != 'executor' or msg['type'] != 'risk_decision':
            return ['AUTHORIZATION'], [], None
        p = msg['payload']
        if self.last_clock is None or time.monotonic()-self.last_clock >= self.wall_lease or not 0 <= self.tick-msg['tick'] <= self.max_age:
            return ['STALE'], [], None
        if p.get('approved') is not True or p.get('kind') not in {'order', 'payment'}:
            return ['NOT_APPROVED'], [], None
        proposal = p.get('proposal', {})
        original = self.proposals.get(proposal.get('message_id'))
        visa = self.visas.get(proposal.get('message_id'))
        if original is None or visa is None:
            return ['MISSING_SIGNED_VISA'], [], None
        if not 0 <= self.tick-original['tick'] <= self.max_age or not 0 <= self.tick-visa['tick'] <= self.max_age:
            return ['STALE_VISA'], [], None
        if self.identity(proposal) != self.identity(original) or self.identity(visa['proposal']) != self.identity(original):
            return ['IDENTITY_MISMATCH'], [], None
        if proposal['message_id'] in self.used:
            return ['REPLAY'], [], None
        amount, requested = p.get('notional_eur'), original['payload'].get('amount_eur', original['payload'].get('notional_eur'))
        if not number(amount) or not number(requested) or not 0 < amount <= requested:
            return ['AMOUNT'], [], None
        if not number(p.get('risk_view', {}).get('pnl_day_eur')):
            return ['MISSING_RISK_VIEW'], [], None
        if (p['kind'] == 'payment') != (original['type'] == 'payment_proposal'):
            return ['ACTION_TYPE'], [], None
        trace, evidence = self.operation_trace(msg, original)
        vulnerable = self.vulnerable()
        parameters = {'vulnerable': list(vulnerable), 'versions': vulnerable,
            'order_cap': self.cfg['agents']['trader']['max_order_eur'],
            'transfer_cap': self.cfg['agents']['payment']['max_transfer_eur'],
            'day_cap': self.cfg['agents']['payment']['max_daily_eur'],
            'loss_cap': self.cfg['agents']['risk']['max_daily_loss_eur'],
            'beneficiaries': {b['id']: b['iban'] for b in self.cfg['beneficiaries']},
            'reserved': self.reserved, 'receipt_mismatch': self.receipt_mismatch}
        token = _ENGINE.set(True)
        try:
            result = self.policy.analyze(trace, **parameters)
        finally:
            _ENGINE.reset(token)
        rules = sorted({str(error.args[0]) for error in result.errors})
        if 'VULNERABLE_FLOW' in rules:
            evidence.extend(cve['message_id'] for cve in self.cves)
        self.traces.append({'message_id': msg['message_id'], 'trace': trace, 'parameters': parameters, 'rules': rules})
        if not rules:
            self.used.add(proposal['message_id'])
            if p['kind'] == 'payment':
                self.reserved += amount
        return rules, list(dict.fromkeys(evidence)), trace

    def request_approval(self, msg):
        if isinstance(msg, dict) and msg.get('message_id') in self.decided and self.valid(msg):
            signature, previous = self.decided[msg['message_id']]
            if signature == msg['signature']:
                return previous
            return Decision(False, 'IDENTITY_REUSE', 'vigie-invariant')
        start = time.perf_counter()
        try:
            rules, evidence, _ = self.check(msg)
        except Exception as exc:
            rules, evidence = ['ENGINE_OR_INPUT_ERROR'], []
            self.incidents.append({'tick': self.tick, 'error': type(exc).__name__, 'detail': str(exc)[:300]})
        approved = not rules
        reason = canonical({'kind': 'invariant_policy', 'rules': rules, 'evidence': evidence})
        decision = Decision(approved, reason, 'vigie-invariant')
        record = {'tick': self.tick, 'message_id': msg.get('message_id') if isinstance(msg, dict) else None,
                  'approved': approved, 'rules': rules, 'evidence': evidence, 'latency_ms': (time.perf_counter()-start)*1000}
        self.decisions.append(record)
        self.latencies.append(record['latency_ms'])
        if self.valid(msg):
            self.decided[msg['message_id']] = (msg['signature'], decision)
        if not approved:
            self.pending.append(self.factory.make(max(0, self.tick), 'alert', 'human', {'kind': 'invariant_control', **record}))
        return decision

    def flush(self):
        out, self.pending = self.pending, []
        return out


def serve(cfg, client, *, meta=None, stop=None):
    prefix = cfg['redis']['prefix']
    runtime = InvariantGuard(cfg, meta=meta, deterministic=False)
    sub = client.pubsub(ignore_subscribe_messages=False)
    channels = {prefix+'bus', prefix+'vigie.approval_requests'}
    sub.subscribe(*sorted(channels))
    subscribed = set()
    try:
        deadline = time.monotonic()+5
        while subscribed != channels:
            raw = sub.get_message(ignore_subscribe_messages=False, timeout=.1)
            if raw and raw['type'] == 'subscribe':
                subscribed.add(raw['channel'])
            if time.monotonic() > deadline:
                raise TimeoutError('Souscriptions Invariant non confirmées')
        client.set(prefix+'agent:vigie:pid', os.getpid())
        client.set(prefix+'invariant:ready', '1')
        while stop is None or not stop.is_set():
            raw = sub.get_message(ignore_subscribe_messages=True, timeout=.1)
            if not raw:
                continue
            try:
                if not isinstance(raw.get('data'), (str, bytes)) or len(raw['data']) > MAX_MESSAGE_BYTES:
                    continue
                data = json.loads(raw['data'])
                if raw['channel'] == prefix+'bus':
                    out = runtime.on_message(data)
                else:
                    msg = data['message']
                    out = runtime.on_message(msg)
                    decision = runtime.request_approval(msg)
                    client.publish(prefix+'vigie.approval_responses', canonical({'request_id': data['request_id'], **asdict(decision)}))
                for message in [*out, *runtime.flush()]:
                    client.publish(prefix+'bus', canonical(message))
            except (KeyError, TypeError, ValueError):
                continue
    finally:
        sub.close()
        client.delete(prefix+'invariant:ready')
    return runtime



def launch_entries(cfg, directory):
    """Prépare une banque E autonome, sans modifier les lanceurs de la démo."""
    import shlex
    from sim.launch import parse_procfile, procfile
    cfg = copy.deepcopy(cfg)
    cfg.pop('_path', None)
    cfg['vigie'].update(enabled=True, stub=False)
    cfg['gate'].update(impl='bus', default_on_timeout='reject')
    cfg['chaos']['announce_compromised'] = False
    cfg['llm'].update(enabled=False, cache=False)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    config = directory/'config.json'
    config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2)+'\n')
    entries = []
    for name, command in parse_procfile(procfile(cfg, True, False)):
        command = (shlex.quote(sys.executable)+' -m vigie.invariant_guard' if name == 'vigie' else
                   shlex.quote(sys.executable)+' '+command.partition(' ')[2])
        entries.append((name, 'env HORIZON_CONFIG='+shlex.quote(str(config.resolve()))+' HORIZON_NO_LLM=1 '+command))
    (directory/'Procfile').write_text('\n'.join(name+': '+command for name, command in entries)+'\n')
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true', help='lance une banque E autonome avec gate.impl=bus')
    parser.add_argument('--config', help='configuration JSON publique du processus')
    parser.add_argument('--meta', help='copies JSON des réponses /meta, indexées par service')
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text()) if args.config else load_config()
    if args.launch:
        from sim.launch import supervise
        directory = Path(cfg['runs_dir'])/('invariant-launch-'+str(time.time_ns()))
        entries = launch_entries(cfg, directory)
        print(f'Configuration et Procfile E : {directory}', flush=True)
        raise SystemExit(supervise(entries))
    if not cfg['vigie']['enabled'] or cfg['gate']['impl'] != 'bus':
        parser.error('E exige vigie.enabled=true et gate.impl=bus')
    if args.meta:
        meta = json.loads(Path(args.meta).read_text())
    else:
        import httpx
        meta = {}
        with httpx.Client(timeout=2) as http:
            for sid, spec in cfg['services'].items():
                response = http.get(f"http://127.0.0.1:{spec['port']+cfg.get('port_offset',0)}/meta")
                response.raise_for_status()
                record = response.json()
                if record.get('service_id') == sid:
                    meta[sid] = record
    from bus.redis_bus import connect
    import signal
    import threading
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stop.set())
    serve(cfg, connect(cfg['redis']['url']), meta=meta, stop=stop)


if __name__ == '__main__':
    main()
