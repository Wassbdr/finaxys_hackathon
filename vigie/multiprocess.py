"""Recette réelle Redis/HTTP/processus, sans API LLM (outil de mesure seulement).

python -m vigie.multiprocess --redis-server /chemin/redis-server --out /dossier/neuf
Le détecteur ne reçoit que le bus et /meta ; seul ce harnais note l'état final.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys
import time

from agents.llm import RuleBackend


class SleepingBackend(RuleBackend):
    model = 'rules-latency-fixture'

    def __init__(self, seed, agent):
        self.rng = random.Random(f'{seed}:{agent}:latency')

    def complete(self, system, user, task, context):
        time.sleep(self.rng.uniform(1, 6))
        return super().complete(system, user, task, context)


def worker(role):
    if role == 'world':
        from sim.clock import Clock
        from sim.world import main, World
        close = World.close
        def final_close(self):
            self.end_tick(self.tick)
            close(self)
        World.close = final_close
        from sim.config import load_config
        from bus.redis_bus import connect
        original = Clock.run
        def wait_start(self, callback, **kwargs):
            client = connect(load_config()['redis']['url'])
            end = time.monotonic() + 45
            while not client.get('recipe:go'):
                if time.monotonic() > end:
                    raise TimeoutError('Départ de recette absent')
                time.sleep(.05)
            return original(self, callback, **kwargs)
        Clock.run = wait_start
        main(['--ticks', os.environ['RECIPE_TICKS']])
        return
    from agents import runtime
    from agents.compliance import Compliance
    from agents.quant import Quant
    from agents.trader import Trader
    from agents.risk import Risk
    from agents.payment import Payment
    original = runtime.build
    def build(cls, agent_id, cfg, client):
        # Force les règles avant toute construction : aucun import de backend API.
        cfg['llm'].update(enabled=False, cache=False)
        agent, keys = original(cls, agent_id, cfg, client)
        if role in ('quant', 'compliance') or role.startswith('trader-'):
            agent.ctx.llm.backend = SleepingBackend(cfg['seed'], agent_id)
            if role == 'quant':
                on_tick = agent.on_tick
                def delayed_tick():
                    # Quant n'utilise pas de LLM métier : attente synthétique déclarée.
                    agent.ask_llm('justify', 'Recette de latence Quant.',
                                  {'rationale': 'Estimation quantitative.'}, [], [])
                    on_tick()
                agent.on_tick = delayed_tick
        return agent, keys
    runtime.build = build
    cls = Trader if role.startswith('trader-') else {
        'quant': Quant, 'compliance': Compliance, 'risk': Risk, 'payment': Payment}[role]
    sys.argv = [sys.argv[0]]
    runtime.main(cls, role)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def run_case(name, root, redis_server, ticks=None):
    import httpx
    import redis
    import yaml
    from bus.message import Keyring
    from journal.writer import JournalWriter
    from sim.clock import Clock
    from sim.config import known_senders, load_config, agent_ids
    from sim.inject import ProcessTarget, initial_commands, load_scenario, run_command, scenario_steps
    from vigie.main import couche3_config
    folder = root / name
    folder.mkdir(parents=True, exist_ok=False)
    cfg = couche3_config(load_config())
    cfg['seed'] = 42
    cfg['redis'] = {'url': f'redis://127.0.0.1:{free_port()}/0', 'prefix': ''}
    cfg['runs_dir'] = str(folder / 'runs')
    cfg['llm'].update(enabled=False, cache=False)
    for spec in cfg['services'].values():
        spec['port'] = free_port()
    scenario = {'duration': '180s'} if name == 'clean' else load_scenario(f'scenarios/{name}.yaml')
    from sim.clock import parse_at
    count = ticks or parse_at(scenario['duration'], Clock.from_config(cfg))
    path = folder / 'config.yaml'
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True))
    env = {**os.environ, 'HORIZON_CONFIG': str(path), 'RECIPE_TICKS': str(count),
           'PYTHONUNBUFFERED': '1', 'HORIZON_NO_LLM': '1'}
    env.pop('REDIS_URL', None)
    env.pop('HORIZON_PREFIX', None)
    procs, handles = [], []
    client = None
    journal = None
    def spawn(label, args):
        fh = open(folder / f'{label}.log', 'w')
        handles.append(fh)
        p = subprocess.Popen(args, env=env, stdout=fh, stderr=subprocess.STDOUT)
        procs.append((label, p))
        return p
    def wait_until(predicate, seconds=30):
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline:
            try:
                if predicate(): return
            except (redis.RedisError, httpx.HTTPError):
                pass
            if any(p.poll() is not None for _,p in procs):
                raise RuntimeError('Processus arrêté avant le départ')
            time.sleep(.05)
        raise TimeoutError('Disponibilité de la recette')
    try:
        port = cfg['redis']['url'].split(':')[-1].split('/')[0]
        spawn('redis', [redis_server, '--bind', '127.0.0.1', '--port', port,
                        '--save', '', '--appendonly', 'no', '--dir', str(folder)])
        client = redis.Redis.from_url(cfg['redis']['url'], decode_responses=True)
        wait_until(lambda: client.ping())
        bus = client.pubsub(ignore_subscribe_messages=False)
        bus.subscribe('bus', 'vigie.approval_responses')
        for _ in range(2): bus.get_message(timeout=2)
        journal = JournalWriter(folder / 'journal.jsonl')
        keys = Keyring(known_senders(cfg))
        spawn('world', [sys.executable, '-m', 'vigie.multiprocess', '--worker', 'world'])
        wait_until(lambda: client.get('world:tick') is not None)
        for sid in cfg['services']:
            spawn(sid, [sys.executable, '-m', 'services.run', sid])
        for sid,spec in cfg['services'].items():
            wait_until(lambda spec=spec: httpx.get(f"http://127.0.0.1:{spec['port']}/meta").is_success)
        spawn('vigie', [sys.executable, '-m', 'vigie.main', '--freeze-state', str(folder/'freeze.json'),
                        '--fresh-freeze-state', '--no-llm'])
        wait_until(lambda: client.get('vigie:ready'))
        for agent in agent_ids(cfg):
            spawn(agent, [sys.executable, '-m', 'vigie.multiprocess', '--worker', agent])
        # Un abonnement par agent, plus monde, Vigie et collecteur : aucun tick initial perdu.
        wait_until(lambda: client.pubsub_numsub('bus')[0][1] >= len(agent_ids(cfg))+3)
        target = ProcessTarget(cfg, client)
        for cmd in initial_commands(scenario): run_command(target, cmd)
        steps = iter(scenario_steps(scenario, Clock.from_config(cfg)))
        pending = next(steps, None)
        client.set('recipe:go', '1')
        start = time.monotonic()
        responses = []
        world = next(p for label,p in procs if label=='world')
        while time.monotonic()-start < count+40:
            tick = target.tick()
            while pending and tick >= pending[0]:
                run_command(target, pending[1])
                pending = next(steps, None)
            raw = bus.get_message(timeout=.05)
            if raw and raw['type']=='message':
                msg = json.loads(raw['data'])
                if raw['channel']=='bus': journal.append(msg, keys.verify(msg))
                else: responses.append(msg)
            if world.poll() is not None:
                if world.returncode: raise RuntimeError('Échec du monde')
                # État final noté uniquement par le harnais, jamais transmis à Vigie.
                break
            for label,p in procs:
                if p.poll() is not None: raise RuntimeError(f'{label}: sortie {p.returncode}')
        else: raise TimeoutError('Fin du monde absente')
        state = json.loads(client.get('state'))
        (folder/'final-state.json').write_text(json.dumps(state, ensure_ascii=False))
        (folder/'responses.json').write_text(json.dumps(responses, ensure_ascii=False))
        journal.close(); journal = None
        messages = [json.loads(line)['message'] for line in (folder/'journal.jsonl').read_text().splitlines()]
        findings = [m['payload']['finding'] for m in messages
                    if m['from']=='vigie' and m['payload'].get('kind')=='layer3_finding']
        freezes = [p for p in findings if p.get('kind')=='herding_freeze']
        traces = [m for m in messages if m['type']=='llm_trace']
        result = {'case':name, 'seed':42, 'ticks':count, 'wall_s':round(time.monotonic()-start,2),
                  'messages':len(messages), 'service_suspicions':[f for f in findings if f['kind'] in
                      ('ledger_mismatch','published_cve','price_jump','price_frozen','monotone_drift')],
                  'herding_freeze':freezes,
                  'freeze_uncertain':json.loads((folder/'freeze.json').read_text())['body']['state']['uncertain'],
                  'refused_executions':[{'tick':m['tick'], **m['payload']} for m in messages
                      if m['type']=='execution' and m['payload'].get('status')=='refused'],
                  'refused_gate':sum(not p['approved'] for p in responses),
                  'responses':len(responses), 'executions':state['decisions'],
                  'pnl':state['pnl'], 'llm':{}}
        for agent in agent_ids(cfg):
            calls=[m['payload']['latency_ms'] for m in traces if m['from']==agent]
            heartbeats=[m['tick'] for m in messages if m['from']==agent and m['type']=='heartbeat']
            result['llm'][agent]={'calls':len(calls), 'min_ms':min(calls,default=0), 'max_ms':max(calls,default=0),
                'heartbeats':len(heartbeats), 'max_gap_ticks':max((b-a for a,b in zip(heartbeats,heartbeats[1:])),default=0)}
        (folder/'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(json.dumps(result, ensure_ascii=False),flush=True)
        return result
    finally:
        for _,p in reversed(procs):
            if p.poll() is None: p.terminate()
        for _,p in reversed(procs):
            try: p.wait(timeout=8)
            except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=3)
        if journal: journal.close()
        for fh in handles: fh.close()
        if client: client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker')
    parser.add_argument('--redis-server')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--cases', nargs='+', default=['clean','rumeur_mimetisme','php2shell_day'])
    parser.add_argument('--ticks',type=int)
    args=parser.parse_args()
    if args.worker: return worker(args.worker)
    if not args.out or not args.redis_server: parser.error('--out et --redis-server requis')
    args.out.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda name:run_case(name,args.out,args.redis_server,args.ticks),args.cases))
    (args.out/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))


if __name__=='__main__': main()
