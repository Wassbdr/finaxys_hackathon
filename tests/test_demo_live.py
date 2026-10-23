"""Démo : synchronisation des mondes, preuve publique et enregistrement local."""
import json
from pathlib import Path

import fakeredis
import pytest

from sim.config import load_config
from sim.inject import LockstepTarget, run_command
from vigie.demo_live import DemoRunner, load_day, read_frames, saved_recording, create_demo_app, Display
from vigie.demo_controller import Bridge


@pytest.mark.parametrize('scenario_path',['vigie/scenarios/jour_noir.yaml','vigie/scenarios/derive_marche.yaml',
    'vigie/scenarios/krach_reel.yaml','vigie/scenarios/krach_rumeur.yaml'])
def test_scenario_uses_only_existing_team_commands(cfg,scenario_path):
    from sim.lockstep import Lockstep
    scenario = load_day(scenario_path,cfg)
    ls=Lockstep(cfg,no_llm=True,no_vigie=True)
    target=LockstepTarget(ls)
    for tick,args in scenario['commands']:
        assert args[0] != 'corrupt-order'
        run_command(target,args)
    ls.close()


def test_three_worlds_same_seed_and_exact_injections(tmp_path,cfg):
    day={'name':'preuve','duration':'5s','steps':[{'at':'2s','do':'compromise core-ledger'}]}
    runner=DemoRunner(cfg,day,tmp_path/'run')
    try:
        frames=[runner.step() for _ in range(5)]
        assert [f['tick'] for f in frames] == list(range(5))
        assert {ls.cfg['seed'] for ls in runner.worlds.values()} == {cfg['seed']}
        assert [ls.scenario_log for ls in runner.worlds.values()][0] == [ls.scenario_log for ls in runner.worlds.values()][1]
        assert runner.worlds['B'].scenario_log == runner.worlds['C'].scenario_log
        assert {ls.prefix for ls in runner.worlds.values()} == {'A:','B:','C:'}
        assert all(ls.tick==5 for ls in runner.worlds.values())
        assert all(not ls.cfg['llm']['enabled'] for ls in runner.worlds.values())
        assert all(not ls.cfg['chaos']['announce_compromised'] for ls in runner.worlds.values())
        finding=runner.controllers['C'].contagion.suspects['core-ledger']
        # Le rapprochement attend deux ticks pour les exécutions encore en transit.
        assert finding.tick==4
        heartbeat=runner.controllers['C'].state.messages[finding.evidence[0]]
        assert heartbeat['tick']==2 and heartbeat['payload']['view']['as_of_tick']==2
        assert not runner.controllers['B'].red
        assert set(frames[-1]['banks']) == {'A','B','C'}
    finally: runner.close()


def test_api_tick_contract_and_existing_world_routes(tmp_path,cfg):
    from fastapi.testclient import TestClient
    from api.sources import LiveSource
    runner=DemoRunner(cfg,{'duration':'2s','steps':[]},tmp_path/'run')
    display=Display();display.directory=runner.directory
    try:
        frame=runner.step();display.update(frame)
        sources={n:LiveSource(ls.cfg,ls.store) for n,ls in runner.worlds.items()}
        with TestClient(create_demo_app(display,sources)) as client:
            payload=client.get('/demo').json()
            assert payload['schema_version']==1 and payload['tick']==0
            assert set(payload['banks'])=={'A','B','C'}
            for bank in payload['banks'].values():
                assert {'pnl_eur','fraud_eur','light','blocked','executed','decisions','findings'}<=bank.keys()
                assert bank['light'] in {'VERT','ORANGE','ROUGE'}
            assert payload['events']==[]
            for n in 'ABC':
                assert client.get('/state?world='+n).json()['tick']==0
                assert 'nodes' in client.get('/graph?world='+n).json()
            with client.websocket_connect('/demo/stream?since_tick=-1') as ws:
                packet=ws.receive_json()
                assert packet==frame
            assert client.get('/').status_code==404
    finally:runner.close()


def test_recording_is_independent_and_replay_tolerates_last_partial_line(tmp_path,cfg):
    path=tmp_path/'original'
    runner=DemoRunner(cfg,{'duration':'2s','steps':[]},path)
    try:
        runner.step(); runner.step()
        recorded=saved_recording(path,tmp_path/'recordings')
        assert len(read_frames(recorded/'frames.jsonl'))==2
        with (recorded/'frames.jsonl').open('a') as f: f.write('{"tick":')
        assert len(read_frames(recorded/'frames.jsonl'))==2
        assert (recorded/'A/journal.jsonl').is_file()
    finally:runner.close()


class OnlyPublicBus:
    def __init__(self): self.fake=fakeredis.FakeRedis(decode_responses=True); self.calls=[]
    def publish(self,key,value):
        assert key in {'B:bus','B:vigie.approval_responses','B:demo:ack',
                       'C:bus','C:vigie.approval_responses','C:demo:ack'}
        self.calls.append(('publish',key));return self.fake.publish(key,value)
    def set(self,key,value):
        assert key in {'B:agent:vigie:pid','B:demo:ready','C:agent:vigie:pid','C:demo:ready'}
        self.calls.append(('set',key));return self.fake.set(key,value)
    def __getattr__(self,name): raise AssertionError('Accès Redis interdit : '+name)


@pytest.mark.parametrize('variant',['B','C'])
def test_controller_bridge_reads_no_truth_key(cfg,variant):
    from bus.message import Keyring,MessageFactory
    from sim.config import known_senders
    cfg['redis']['prefix']=variant+':'
    keys=Keyring(known_senders(cfg)); client=OnlyPublicBus()
    bridge=Bridge(cfg,client,variant,meta={})
    f=MessageFactory('clock',keys)
    bridge.handle({'channel':variant+':bus','data':json.dumps(f.make(0,'tick','*',{'tick':0}))})
    f=MessageFactory('world',keys)
    bridge.handle({'channel':variant+':bus','data':json.dumps(f.make(1,'service_status','*',{
        'mode':'compromised','state':{'loss':1e12},'tainted':True,'altered':True}))})
    assert all(call[0] in {'publish','set'} for call in client.calls)
    assert not (bridge.controller.red if variant=='B' else bridge.controller.contagion.suspects)


def test_classical_controls_are_unmodified():
    import hashlib
    # Le concurrent intégré est exactement le module déjà livré, sans seuil abaissé.
    assert hashlib.sha256(__import__('inspect').getsource(__import__('vigie.classique',fromlist=['Classique']).Classique).encode()).hexdigest() == 'c77a9c2b12f3cff2ac1b1e2efd44557210b6c819b3647ca1053cb02022e62bb6'


def test_format_example_matches_real_first_tick(tmp_path,cfg):
    example=json.loads(Path('vigie/demo_api_example.json').read_text())
    runner=DemoRunner(cfg,load_day('vigie/scenarios/jour_noir.yaml',cfg),tmp_path/'example')
    try:
        frame=runner.step()
        assert example==frame
        assert set(frame)=={'schema_version','tick','seed','banks','events','complete','replay'}
        for bank in frame['banks'].values():
            assert set(bank)=={'label','pnl_eur','loss_eur','fraud_eur','light','executed','blocked','gate_blocked',
                'executed_after_detection','last_execution_tick','contaminated_executions','detection_tick',
                'first_block_tick','working_agents','paused_agents','findings','decisions','last_decisions'}
            assert type(bank['executed']) is int and type(bank['blocked']) is int
            assert isinstance(bank['pnl_eur'],(int,float))
            assert type(bank['findings']) is list and type(bank['decisions']) is list
    finally:runner.close()


def test_gate_decisions_have_reason_and_verifiable_message_id(tmp_path,cfg):
    runner=DemoRunner(cfg,{'duration':'6s','steps':[{'at':2,'do':'compromise core-ledger'}]},tmp_path/'decisions')
    try:
        frames=[runner.step() for _ in range(6)]
        decisions=[d for f in frames for b in f['banks'].values() for d in b['decisions']]
        assert decisions and any(not d['approved'] for d in decisions)
        for decision in decisions:
            assert set(decision)=={'tick','message_id','approved','reason','evidence'}
            assert type(decision['approved']) is bool
            assert decision['reason'] and 1<=len(decision['evidence'])<=3
            assert decision['message_id'].startswith('risk:')
            assert all(isinstance(mid,str) and ':' in mid for mid in decision['evidence'])
    finally:runner.close()


def test_classic_launcher_removes_other_vigie(tmp_path,cfg,monkeypatch):
    from vigie.classique import launch
    from sim import launch as team_launch
    captured=[]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HORIZON_CONFIG','unused')
    monkeypatch.setenv('HORIZON_NO_LLM','1')
    monkeypatch.setattr(team_launch,'supervise',lambda entries:captured.extend(entries))
    launch(cfg)
    processes=[cmd for name,cmd in captured if name.startswith('vigie')]
    assert len(processes)==1
    assert '-m vigie.classique' in processes[0]


def test_replay_ignores_unfinished_world_tick_and_last_partial_utf8(tmp_path,cfg):
    from vigie.demo_live import SafeReplaySource
    runner=DemoRunner(cfg,{'duration':'3s','steps':[]},tmp_path/'crash')
    try:
        runner.step();runner.step()
        runner.worlds['A'].step()
    finally:runner.close()
    path=runner.directory/'A'
    with (path/'states.jsonl').open('ab') as file:file.write(b'{"tick":\xc3')
    with (path/'journal.jsonl').open('ab') as file:file.write(b'{"tick":')
    source=SafeReplaySource(path,last_tick=1,speed=1)
    assert [f['tick'] for f in source.frames]==[0,1]
    assert max(source.messages)<=1


def test_market_drift_scenario_attacks_only_market_feed(cfg):
    day=load_day('vigie/scenarios/derive_marche.yaml',cfg)
    commands=day['commands']
    attacks=[(tick,args) for tick,args in commands if args[0]!='note']
    assert attacks==[(40,['compromise','market-feed'])]
    assert day['duration']=='180s'


@pytest.mark.parametrize('seed',[42,43])
def test_drift_confines_orders_and_preserves_healthy_payments(tmp_path,cfg,seed):
    from vigie.demo_metrics import continuity
    cfg['seed']=seed
    runner=DemoRunner(cfg,load_day('vigie/scenarios/derive_marche.yaml',cfg),tmp_path/str(seed))
    try:
        for _ in range(runner.duration):frame=runner.step()
        gate=runner.controllers['C']
        assert set(gate.contagion.suspects)=={'market-feed'}
        assert gate.contagion.suspects['market-feed'].kind=='monotone_drift'
        assert not gate.contagion.cve_ticks
        cutoff=frame['banks']['C']['detection_tick']
        assert cutoff is not None and cutoff>=40
        after=continuity(runner.messages['C'],cutoff)['after_anchor']
        assert after['contaminated_orders']==0
        assert after['healthy_payments']>0 and after['contaminated_payments']==0
        assert after['gate_refused_orders']>0 and after['gate_refused_payments']==0
        assert frame['banks']['B']['detection_tick'] is None
        assert frame['banks']['A']['pnl_eur']==frame['banks']['B']['pnl_eur']
        assert runner.worlds['A'].scenario_log==runner.worlds['B'].scenario_log==runner.worlds['C'].scenario_log
    finally:runner.close()


def test_argus_krys_display_names_preserve_bus_contract(tmp_path, cfg):
    expected = {'A': 'Sans Krys', 'B': 'Contrôles classiques', 'C': 'Krys'}
    runner = DemoRunner(cfg, {'duration': '1s', 'steps': []}, tmp_path/'names')
    try:
        frame = runner.step()
        assert {name: bank['label'] for name, bank in frame['banks'].items()} == expected
        example = json.loads(Path('vigie/demo_api_example.json').read_text())
        assert {name: bank['label'] for name, bank in example['banks'].items()} == expected
        page = Path('vigie/demo_page.html').read_text()
        assert '<title>Argus — trois banques, une journée</title>' in page
        assert '<h1>Argus — trois banques, une journée</h1>' in page
        assert all(label in page for label in expected.values())
        assert '<title>KRYS · ARGUS</title>' in Path('vigie/dashboard.html').read_text()
        assert 'Krys, couche 3' in Path('vigie/DEMO_API.md').read_text()
        assert 'Krys, couche 3' in Path('vigie/ANNEXE_INVARIANT.md').read_text()
        # Le nom commercial ne change ni l'identité authentifiée ni les preuves.
        messages = runner.messages['C']
        assert any(m['from'] == 'vigie' for m in runner.messages['C'])
        assert all(runner.worlds['C'].keyring.verify(m) for m in messages)
    finally:
        runner.close()
