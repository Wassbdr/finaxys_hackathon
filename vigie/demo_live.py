"""Trois banques synchronisées : simulation, affichage et notation hors des instances KRYS."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque

import fakeredis
from fastapi import WebSocket

from api.sources import ReplaySource
from bus.gate import BusGate
from bus.message import canonical
from journal.writer import verify_journal
from sim.clock import Clock, parse_at
from sim.config import load_config
from sim.inject import load_scenario, schedule_scenario, scenario_steps
from sim.lockstep import Lockstep
from vigie.classique import Classique
from vigie.main import Runtime

LABELS={'published_cve':'CVE publiée : vulnérabilité suspectée',
        'ledger_mismatch':'Grand livre : trésorerie déclarée incohérente',
        'monotone_drift':'Cours : dérive monotone suspecte',
        'price_jump':'Cours : saut suspect','price_frozen':'Cours figé',
        'herding_freeze':'Gel ciblé de mimétisme'}


def load_day(path,cfg):
    day=load_scenario(path)
    commands=scenario_steps(day,Clock.from_config(cfg))
    # Cette liste borne seulement le lanceur ; l'injecteur de l'équipe exécute chaque commande.
    allowed={'note','publish-cve','compromise','stale','down','slow','restore','fake-agent',
             'kill','kill-vigie','set','light','human','news','invoice','change-iban'}
    for _,args in commands:
        if not args or args[0] not in allowed: raise ValueError('Commande hors contrat injecteur')
    day['commands']=commands
    return day


def read_frames(path):
    frames=[]
    lines=Path(path).read_bytes().splitlines()
    for i,line in enumerate(lines):
        try: frames.append(json.loads(line))
        except ValueError:
            if i!=len(lines)-1: raise
    return frames


def saved_recording(source,recordings=Path('recordings')):
    source=Path(source); recordings=Path(recordings)
    recordings.mkdir(parents=True,exist_ok=True)
    dest=recordings/(source.name+'-'+time.strftime('%H%M%S')+'-'+str(time.time_ns()%1000000))
    shutil.copytree(source,dest)
    return dest


class Remote:
    """Le simulateur attend la consommation du bus avant d'exécuter le prochain message."""
    def __init__(self,client,prefix,ls):
        self.client,self.prefix,self.ls=client,prefix,ls
        self.sub=client.pubsub(ignore_subscribe_messages=True)
        self.sub.subscribe(prefix+'bus',prefix+'demo:ack')
        self.sub.get_message(timeout=0);self.sub.get_message(timeout=0)

    def forward(self,msg):
        if msg['from']=='vigie': return
        self.client.publish(self.prefix+'bus',canonical(msg))
        deadline=time.monotonic()+5
        outputs=[]
        while time.monotonic()<deadline:
            raw=self.sub.get_message(timeout=.05)
            if not raw: continue
            data=json.loads(raw['data'])
            if raw['channel']==self.prefix+'demo:ack' and data.get('message_id')==msg['message_id']:
                for emitted in outputs: self.ls.publish(emitted)
                return
            if raw['channel']==self.prefix+'bus' and data.get('from')=='vigie' and self.ls.keyring.verify(data):
                outputs.append(data)
        raise TimeoutError(f'{self.prefix} KRYS muette : arrêt du direct, aucun accord inventé')

    def close(self): self.sub.close()


class MeteredGate:
    """Mesure la décision obtenue sans modifier ni renforcer le contrôleur."""
    def __init__(self,gate,ls,records):
        self.gate,self.ls,self.records=gate,ls,records

    def request_approval(self,msg):
        decision=self.gate.request_approval(msg)
        try:
            detail=json.loads(decision.reason)
            proof=detail.get('evidence',[]) if isinstance(detail,dict) else []
        except (ValueError,TypeError):proof=[]
        if not proof:proof=[msg['message_id']]
        self.records.append({'tick':self.ls.tick,'message_id':msg['message_id'],
            'approved':decision.approved,'reason':decision.reason,'evidence':proof[:3]})
        return decision


class DemoRunner:
    def __init__(self,cfg,day,directory,*,client=None,remote=False):
        self.cfg=copy.deepcopy(cfg);self.day=day;self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.client=client if client is not None else fakeredis.FakeRedis(decode_responses=True)
        self.worlds={};self.controllers={};self.remotes={};self.processes=[]
        self.decisions={n:[] for n in 'ABC'}
        self.messages={n:[] for n in 'ABC'};self.notes={n:deque(maxlen=6) for n in 'ABC'}
        self.first_detection={n:None for n in 'ABC'};self.first_block={n:None for n in 'ABC'}
        self.remote=remote;self.tick=0;self.closed=False
        self.duration=parse_at(day.get('duration','180s'),Clock.from_config(cfg))
        self.events=scenario_steps(day,Clock.from_config(cfg))
        self.frames=(self.directory/'frames.jsonl').open('w')
        try:
            for name in 'ABC':
                wcfg=copy.deepcopy(cfg)
                wcfg['redis']['prefix']=name+':'
                wcfg['chaos']['announce_compromised']=False
                wcfg['llm'].update(enabled=False,cache=False)
                wcfg['vigie'].update(enabled=name!='A',stub=False)
                # Lockstep construit ses services ; le portique réel est branché juste après.
                wcfg['gate'].update(impl='stub',default_on_timeout='reject')
                ls=Lockstep(wcfg,no_llm=True,no_vigie=name=='A',run_dir=self.directory/name,store=self.client)
                ls.vigie=None;self.worlds[name]=ls
                meta={sid:core.meta() for sid,core in ls.cores.items()}
                if name!='A':
                    if remote:
                        ls.cfg['gate']['impl']='bus'
                        self._start_controller(name,ls,meta)
                        transport=Remote(self.client,name+':',ls);self.remotes[name]=transport
                        ls.bus.tap(transport.forward)
                        gate=BusGate(self.client,name+':',wcfg['gate']['timeout_s'],'reject')
                    else:
                        gate=Classique(wcfg,ls.keyring) if name=='B' else Runtime(wcfg,ls.keyring,meta=meta)
                        self.controllers[name]=gate
                        ls.bus.tap(self._observe(name,ls,gate))
                    ls.gate=ls.world.gate=ls.world.executor.gate=MeteredGate(gate,ls,self.decisions[name])
                else:
                    ls.bus.tap(lambda msg:self.client.publish('A:bus',canonical(msg)))
                ls.bus.tap(lambda msg,name=name:self._record_message(name,msg))
                schedule_scenario(ls,day)
            (self.directory/'manifest.json').write_text(json.dumps({
                'scenario':day,'seed':cfg['seed'],'variants':{'A':'Sans Krys','B':'Contrôles classiques','C':'Krys'},
                'remote_controllers':remote,'clock':cfg['clock']},ensure_ascii=False,indent=2)+'\n')
        except BaseException:
            self.close();raise

    def _start_controller(self,name,ls,meta):
        cpath=self.directory/name/'controller-config.json'
        config=copy.deepcopy(ls.cfg);config.pop('_path',None)
        cpath.write_text(json.dumps(config))
        mpath=self.directory/name/'public-meta.json';mpath.write_text(json.dumps(meta))
        # Un Redis dédié au direct évite d'écraser les préfixes d'une autre démo.
        self.client.delete(name+':demo:ready')
        log=(self.directory/name/'controller.log').open('w')
        try:
            proc=subprocess.Popen([sys.executable,'-m','vigie.demo_controller','--config',str(cpath),
                                   '--meta',str(mpath),'--variant',name],stdout=log,stderr=subprocess.STDOUT)
        finally: log.close()
        self.processes.append(proc)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if proc.poll() is not None: raise RuntimeError(f'Contrôleur {name} arrêté ; voir {name}/controller.log')
            if self.client.get(name+':demo:ready')=='1': return
            time.sleep(.02)
        raise TimeoutError(f'Démarrage du contrôleur {name} dépassé')

    @staticmethod
    def _observe(name,ls,gate):
        def observe(msg):
            if msg['from']!='vigie':
                for out in gate.on_message(msg):ls.publish(out)
        return observe

    def _record_message(self,name,msg):
        self.messages[name].append(msg)
        if msg['from']=='vigie' and msg['type']=='alert':
            p=msg['payload']; finding=p.get('finding')
            if isinstance(finding,dict):
                if finding.get('target')=='traders': return
                kind=finding['kind'];target=finding['target'];proof=finding.get('evidence',[])
                if self.first_detection[name] is None:self.first_detection[name]=msg['tick']
                self.notes[name].append({'tick':msg['tick'],'text':target+' : '+LABELS.get(kind,kind),'proof':proof[:3]})
            elif p.get('kind')=='classique_control':
                if self.first_detection[name] is None:self.first_detection[name]=msg['tick']
                self.notes[name].append({'tick':msg['tick'],'text':p['reason'],'proof':[msg['message_id']]})
        if msg['type']=='execution' and msg['payload'].get('status')=='refused':
            p=msg['payload']
            if p.get('decided_by','').startswith('vigie'):
                if self.first_block[name] is None:self.first_block[name]=msg['tick']
                try:
                    reason=json.loads(p.get('gate',{}).get('reason','{}'))
                except (ValueError,TypeError):reason={}
                proof=reason.get('evidence',[]) if isinstance(reason,dict) else []
                self.notes[name].append({'tick':msg['tick'],'text':'Action bloquée : '+str(reason.get('kind','contrôle du portique')),
                                         'proof':proof[:3]})

    def bank(self,name,state):
        msgs=self.messages[name]
        executed=[m for m in msgs if m['type']=='execution' and m['payload'].get('status')=='executed']
        refused=[m for m in msgs if m['type']=='execution' and m['payload'].get('status')=='refused']
        pnl=state['pnl']['trading_eur']+state['pnl']['payment_fraud_eur']
        first=self.first_detection[name]
        gate_blocked=sum(m['payload'].get('decided_by','').startswith('vigie') for m in refused)
        # Ces mesures sont réservées à l'affichage et à l'arbitre, jamais aux contrôleurs.
        return {'label':{'A':'Sans Krys','B':'Contrôles classiques','C':'Krys'}[name],
                'pnl_eur':pnl,'loss_eur':max(0,-pnl),'fraud_eur':-state['pnl']['payment_fraud_eur'],
                'light':state['light']['light'],'executed':len(executed),'blocked':len(refused),'gate_blocked':gate_blocked,
                'executed_after_detection':sum(m['tick']>first for m in executed) if first is not None else None,
                'last_execution_tick':executed[-1]['tick'] if executed else None,
                'contaminated_executions':len(state['trapped_operations']),
                'detection_tick':first,'first_block_tick':self.first_block[name],
                'working_agents':sum(not a.paused for a in self.worlds[name].agents.values()),
                'paused_agents':sum(a.paused for a in self.worlds[name].agents.values()),
                'findings':list(self.notes[name]),
                'decisions':[d for d in self.decisions[name] if d['tick']==state['tick']],
                'last_decisions':self.decisions[name][-3:]}

    def step(self):
        frame={'schema_version':1,'tick':self.tick,'seed':self.cfg['seed'],'banks':{},'events':[
            ' '.join(args) for t,args in self.events if t==self.tick],'complete':False,'replay':False}
        for name,ls in self.worlds.items():
            if ls.tick!=self.tick:raise RuntimeError('Désynchronisation des mondes')
            frame['banks'][name]=self.bank(name,ls.step())
        self.tick+=1;frame['complete']=self.tick>=self.duration
        self.frames.write(canonical(frame)+'\n');self.frames.flush()
        return frame

    def close(self):
        if self.closed:return
        self.closed=True
        for ls in self.worlds.values():ls.close()
        for transport in self.remotes.values():transport.close()
        for proc in self.processes:
            if proc.poll() is None:proc.terminate()
        for proc in self.processes:
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
        self.frames.close()


class Display:
    def __init__(self):
        self.lock=threading.Lock();self.frame={'schema_version':1,'tick':-1,'banks':{},'events':['Démarrage des trois banques…'],'complete':False,'replay':False}
        self.history=[];self.packets=[];self.directory=None;self.error=None;self.running=True

    def update(self,frame):
        with self.lock:
            self.frame=frame
            self.packets.append(copy.deepcopy(frame))
            self.history.append({'tick':frame['tick'],**{n:b['pnl_eur'] for n,b in frame['banks'].items()}})

    def snapshot(self):
        with self.lock:
            return {**self.frame,'history':list(self.history),'error':self.error,'running':self.running}


def create_demo_app(display,sources,*,allowed_origins=None):
    import asyncio
    from api.app import create_app
    from fastapi import WebSocket, WebSocketDisconnect, HTTPException
    app=create_app(sources,default_world='A')
    from vigie.demo_security import protect_mutations
    protect_mutations(app,allowed_origins)

    @app.get('/demo')
    def demo():
        return display.snapshot()

    @app.websocket('/demo/stream')
    async def ticks(ws:WebSocket,since_tick:int=-1):
        await ws.accept()
        cursor=since_tick
        try:
            while True:
                with display.lock:
                    batch=[f for f in display.packets if f['tick']>cursor]
                    error=display.error
                for frame in batch:
                    await ws.send_json(frame)
                    cursor=frame['tick']
                if error:
                    await ws.send_json({'schema_version':1,'error':error,'tick':cursor})
                    return
                try:
                    event=await asyncio.wait_for(ws.receive(),timeout=.05)
                    if event['type']=='websocket.disconnect':return
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect,RuntimeError):
            return

    @app.post('/demo/record')
    def record():
        if display.directory is None:
            raise HTTPException(409,'Aucun journal à enregistrer')
        with display.lock:
            path=saved_recording(display.directory)
        return {'recording':path.as_posix(),'replay':'make demo-replay RECORDING='+path.as_posix()}
    return app


def broker_start(directory):
    # Le port est local et dédié ; aucun Redis existant n'est arrêté ou vidé.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    url=f'redis://127.0.0.1:{port}/0'
    binary=shutil.which('redis-server')
    log=(directory/'redis.log').open('w')
    command=([binary,'--port',str(port),'--bind','127.0.0.1','--save','','--appendonly','no'] if binary else
             [sys.executable,'-m','vigie.demo_live','broker','--redis-port',str(port)])
    try:proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
    finally:log.close()
    from bus.redis_bus import connect
    try:client=connect(url,wait_s=10)
    except BaseException:
        proc.terminate();proc.wait(timeout=3);raise
    return proc,client,url


def measure(args):
    all_results=[]
    for seed in (42,43):
        cfg=load_config(overrides={'seed':seed})
        day=load_day(args.scenario,cfg)
        directory=Path(args.out)/str(seed)
        runner=DemoRunner(cfg,day,directory)
        try:
            for _ in range(runner.duration):frame=runner.step()
            from vigie.demo_metrics import continuity
            anchor=frame['banks']['C']['detection_tick']
            result={'seed':seed,'tick':frame['tick'],'banks':frame['banks'],'scenario':args.scenario,
                    'injections':runner.worlds['A'].scenario_log,'run_dir':directory.as_posix(),
                    'continuity':{name:continuity(runner.messages[name],anchor) for name in 'ABC'}}
            from vigie.crisis_metrics import CRISES, evaluate_crisis
            if day.get('name') in CRISES:
                result['crisis']=evaluate_crisis(runner,frame,cfg,day,directory)
            all_results.append(result)
        finally:runner.close()
        for name in 'ABC':
            ok,_,_=verify_journal(directory/name/'journal.jsonl')
            if not ok:raise RuntimeError('Journal altéré')
        print(canonical(result),flush=True)
    Path(args.out,'metrics.json').write_text(json.dumps(all_results,indent=2,ensure_ascii=False)+'\n')
    return all_results


def serve_demo(args):
    import uvicorn
    from api.sources import LiveSource
    from vigie.demo_security import configured_origins, announce_access
    origins=configured_origins(args.allowed_origin,args.port)
    display=Display();stop=threading.Event()
    directory=Path(args.out);directory.mkdir(parents=True,exist_ok=False)
    display.directory=directory
    cfg=load_config(overrides={'seed':args.seed,'clock':{'accelerator':args.accelerator}})
    day=load_day(args.scenario,cfg)
    broker,client,url=broker_start(directory)
    cfg['redis']['url']=url
    sources={name:LiveSource({**cfg,'redis':{**cfg['redis'],'prefix':name+':'}},client) for name in 'ABC'}
    app=create_demo_app(display,sources,allowed_origins=origins)
    announce_access(app)
    def simulation():
        runner=None
        try:
            runner=DemoRunner(cfg,day,directory,client=client,remote=True)
            Path('runs').mkdir(exist_ok=True)
            Path('runs/demo-live-latest.json').write_text(canonical({'directory':directory.as_posix()}))
            period=Clock.from_config(cfg).period
            origin=time.monotonic()
            for t in range(runner.duration):
                if stop.is_set():break
                with display.lock:frame=runner.step()
                display.update(frame)
                delay=origin+(t+1)*period-time.monotonic()
                if delay>0:stop.wait(delay)
        except BaseException as error:
            display.error=f'{type(error).__name__}: {error}'
            print(display.error,flush=True)
        finally:
            if runner:runner.close()
            display.running=False
    worker=threading.Thread(target=simulation,name='demo-simulation')
    worker.start()
    print(f'API démo : http://127.0.0.1:{args.port}/demo — Ctrl+C pour arrêter ; traces {directory}',flush=True)
    try:
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=3)
    finally:
        stop.set();worker.join(timeout=20)
        client.close()
        if broker.poll() is None:
            broker.terminate()
            try:broker.wait(timeout=3)
            except subprocess.TimeoutExpired:broker.kill();broker.wait(timeout=3)
    return 1 if display.error else 0


class SafeReplaySource(ReplaySource):
    """Un crash peut laisser un monde au tick suivant : seul le dernier tick triple compte."""
    def __init__(self,path,last_tick,*,speed):
        self.run=Path(path)
        self.frames=[f for f in read_frames(self.run/'states.jsonl') if f['tick']<=last_tick]
        if not self.frames:raise ValueError('Aucun état complet enregistré')
        self.messages={};self.traces=[]
        for entry in read_frames(self.run/'journal.jsonl'):
            msg=entry['message']
            if msg['tick']>last_tick:continue
            self.messages.setdefault(msg['tick'],[]).append(msg)
            if msg['type']=='llm_trace' and entry['signature_valid']:
                self.traces.append({'tick':msg['tick'],'message_id':msg['message_id'],**msg['payload']})
        self.period=1/speed;self.loop=False;self.started=time.monotonic()


def replay(args):
    import uvicorn
    from api.sources import ReplaySource
    from vigie.demo_security import configured_origins, announce_access
    origins=configured_origins(args.allowed_origin,args.port)
    path=Path(args.recording) if args.recording else Path(json.loads(Path('runs/demo-live-latest.json').read_text())['directory'])
    frames=read_frames(path/'frames.jsonl')
    if not frames:raise ValueError('Aucun tick enregistré')
    display=Display();display.directory=path
    stop=threading.Event()
    sources={n:SafeReplaySource(path/n,frames[-1]['tick'],speed=args.accelerator) for n in 'ABC'}
    started=time.monotonic()
    for source in sources.values():source.started=started
    def playback():
        for index,frame in enumerate(frames):
            display.update({**frame,'replay':True})
            delay=started+(index+1)/args.accelerator-time.monotonic()
            if delay>0 and stop.wait(delay):break
        display.running=False
    app=create_demo_app(display,sources,allowed_origins=origins)
    announce_access(app)
    worker=threading.Thread(target=playback,name='demo-replay');worker.start()
    print(f'API replay : http://127.0.0.1:{args.port}/demo — {path}',flush=True)
    try:uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=3)
    finally:stop.set();worker.join(timeout=3)
    return 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',nargs='?',choices=['live','measure','replay','broker'],default='live')
    p.add_argument('--scenario',default='vigie/scenarios/jour_noir.yaml')
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--out',default=None)
    p.add_argument('--port',type=int,default=8010)
    p.add_argument('--redis-port',type=int)
    p.add_argument('--accelerator',type=float,default=1.2)
    p.add_argument('--recording')
    p.add_argument('--allowed-origin',action='append',help='Origine exacte de la page du Dev 1 ; répétable, sinon VIGIE_DEMO_ORIGINS')
    args=p.parse_args(argv)
    if args.accelerator<=0:raise ValueError('Accélérateur strictement positif')
    if args.command=='broker':
        from fakeredis import TcpFakeServer
        server=TcpFakeServer(('127.0.0.1',args.redis_port),server_type='redis');server.daemon_threads=True
        try:server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:pass
        finally:server.server_close()
        return 0
    args.out=args.out or (time.strftime('runs/vigie-demo-%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex)
    if args.command=='measure':measure(args);return 0
    if args.command=='replay':return replay(args)
    return serve_demo(args)


if __name__=='__main__':raise SystemExit(main())
