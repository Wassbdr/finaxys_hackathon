"""Une panne ou un relais non attesté ne doit produire aucune autorisation inventée."""
import pytest
from vigie.panne import receipt_valid, stream_hash
from bus.message import Keyring,MessageFactory
from sim.config import known_senders


def test_relay_receipt_is_bound_to_prefix_and_reply(cfg):
    keys=Keyring(known_senders(cfg));reply={'out':[],'decision':{'approved':False},'uncertain':[]}
    body={'seq':4,'head':'abc','reply_hash':stream_hash(reply),'replica':'standby','incarnation':'run-1'}
    cert=MessageFactory('vigie',keys).make(1,'heartbeat','*',body)
    assert receipt_valid(keys,cert,reply,4,'abc','standby','run-1')
    assert not receipt_valid(keys,cert,reply,3,'abc','standby','run-1')
    assert not receipt_valid(keys,cert,reply,4,'other','standby','run-1')
    assert not receipt_valid(keys,cert,{**reply,'decision':{'approved':True}},4,'abc','standby','run-1')
    assert not receipt_valid(keys,cert,reply,4,'abc','standby','previous-run')
    cert['signature']='forged'
    assert not receipt_valid(keys,cert,reply,4,'abc','standby','run-1')


def test_real_restart_stays_closed_after_signed_human(tmp_path,cfg):
    from vigie.panne import prepare,Hub
    from sim.lockstep import Lockstep
    from bus.message import MessageFactory
    cfg['gate']['timeout_s']=0.02
    ls=Lockstep(cfg,run_dir=tmp_path/'world');ls.vigie=None
    directory=tmp_path/'controllers';prepare(ls,directory)
    hub=Hub(ls,directory,relay=False);ls.bus.tap(hub.observe)
    try:
        ls.step();old=hub.replicas['primary'].proc.pid
        hub.cut();assert hub.replicas['primary'].proc.poll() is not None
        hub.restart();assert hub.replicas['primary'].proc.pid!=old
        factory=MessageFactory('human',ls.keyring)
        for payload in ({'action':'resume'},{'action':'unfreeze','target':'*'}):
            ls.publish(factory.make(ls.tick,'human_decision','*',payload))
        ls.step()
        assert 'checkpoint_unavailable' in hub.replicas['primary'].start['uncertain']
        response=hub.replicas['primary']
        response.proc.stdin.write(b'{"command":"status"}\n');response.proc.stdin.flush()
        assert response.read(1)['uncertain']
    finally:hub.close();ls.close()


def test_relay_killed_primary_does_not_publish_twice(tmp_path,cfg):
    from vigie.panne import prepare,Hub
    from sim.lockstep import Lockstep
    ls=Lockstep(cfg,run_dir=tmp_path/'world');ls.vigie=None
    directory=tmp_path/'controllers';prepare(ls,directory)
    hub=Hub(ls,directory,relay=True);ls.bus.tap(hub.observe);messages=[];ls.bus.tap(messages.append)
    try:
        ls.step();hub.cut();ls.step()
        assert hub.active=='standby'
        heartbeats=[m for m in messages if m['from']=='vigie' and m['type']=='heartbeat']
        assert [m['tick'] for m in heartbeats]==[0,1]
    finally:hub.close();ls.close()


def test_unattested_standby_cannot_take_over(tmp_path,cfg,monkeypatch):
    from vigie.panne import prepare,Hub
    from sim.lockstep import Lockstep
    ls=Lockstep(cfg,run_dir=tmp_path/'world');ls.vigie=None
    directory=tmp_path/'controllers';prepare(ls,directory)
    hub=Hub(ls,directory,relay=True);ls.bus.tap(hub.observe)
    try:
        ls.step();hub.cut()
        monkeypatch.setattr(hub.replicas['standby'],'receive',lambda _:None)
        ls.step()
        assert hub.active=='primary' and not hub.current_ready
    finally:hub.close();ls.close()


def test_measurement_pairs_business_operations_not_message_sequences():
    from vigie.panne import compare
    from tempfile import TemporaryDirectory
    from pathlib import Path
    baseline={'result_path':'base','operations':[{'key':['trader-1','order',65,'order65'],'tick':65,'message_id':'ref:1',
        'requested_eur':100,'executed':True,'execution_eur':80}], 'availability':[{'tick':t,'ready':True} for t in range(60,80)],'pnl_eur':50}
    with TemporaryDirectory() as folder:
        result={'ticks':80,'plan':{'cut_tick':60,'human_delay_after_restart_ticks':10},'duration_ticks':10,
            'operations':[],'availability':[{'tick':t,'ready':False} for t in range(60,80)],
            'pnl_eur':0,'result_path':str(Path(folder)/'result.json')}
        out=compare(result,baseline)['comparison']
        assert out['not_proposed']==1 and out['not_proposed_eur']==100
        assert out['lost_baseline_executions']==1 and out['lost_baseline_eur']==80
        assert out['delta_pnl_eur']==-50 and out['unavailable_ticks']==20
        assert out['first_execution_after_restart_tick'] is None and out['right_censored']


def test_dead_process_gate_times_out_and_agents_orange_after_five_ticks(tmp_path,cfg):
    from vigie.panne import prepare,Hub
    from sim.lockstep import Lockstep
    cfg['gate']['timeout_s']=0.01
    ls=Lockstep(cfg,run_dir=tmp_path/'world');ls.vigie=None
    directory=tmp_path/'controllers';prepare(ls,directory)
    hub=Hub(ls,directory,relay=False)
    ls.gate=ls.world.gate=ls.world.executor.gate=hub
    ls.bus.tap(hub.observe)
    try:
        ls.step();hub.cut()
        msg=MessageFactory('risk',ls.keyring).make(1,'risk_decision','executor',{})
        decision=hub.request_approval(msg)
        assert not decision.approved and decision.decided_by=='timeout'
        for _ in range(5):ls.step()
        assert all(a.light=='VERT' for a in ls.agents.values())
        ls.step()
        assert all(a.light=='ORANGE' for a in ls.agents.values())
    finally:hub.close();ls.close()


def test_live_relay_preserves_healthy_operations_and_pnl(tmp_path):
    from vigie.panne import measure,compare
    plan={'ticks':16,'cut_tick':6,'human_delay_after_restart_ticks':1,
          'human_actions':[{'action':'resume'},{'action':'unfreeze','target':'*'}]}
    baseline=measure(tmp_path/'baseline',42,plan=plan)
    relay=compare(measure(tmp_path/'relay',42,duration=3,relay=True,plan=plan),baseline)
    assert relay['comparison']['lost_baseline_executions']==0
    assert relay['comparison']['unavailable_ticks']==0
    assert relay['pnl_eur']==baseline['pnl_eur']
    assert relay['operations']==baseline['operations']
    assert any(e['kind']=='failover' for e in relay['events'])
    assert any('checkpoint_unavailable' in e.get('uncertain',[]) for e in relay['events'])
