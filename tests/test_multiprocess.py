"""Régressions motivées par le traitement séquentiel et les vues retardées."""
from tests.test_couche3 import setup, accept


def test_ledger_compares_snapshot_tick_not_heartbeat_or_arrival_tick(setup):
    initial = setup[3].cash
    for tick in range(3): accept(setup, 'clock', 'tick', {'tick':tick}, tick=tick)
    accept(setup, 'executor', 'execution', {'status':'executed','kind':'payment',
           'exec_id':'later','amount_eur':10000}, tick=3)
    for tick in range(3,11): accept(setup, 'clock', 'tick', {'tick':tick}, tick=tick)
    _, findings = accept(setup, 'risk', 'heartbeat',
        {'view':{'cash_eur':initial,'as_of_tick':1}}, tick=10)
    assert not findings, 'Une vue honnête ancienne ne prouve pas une altération'
    assert not setup[3].suspects


def test_ledger_waits_for_execution_published_after_http_snapshot(setup):
    initial = setup[3].cash
    for tick in range(3): accept(setup, 'clock','tick',{'tick':tick},tick=tick)
    _, findings=accept(setup,'risk','heartbeat',
        {'view':{'cash_eur':initial-10000,'as_of_tick':2}},tick=2)
    assert not findings, 'La lecture HTTP peut précéder la publication de son exécution'
    accept(setup,'executor','execution',{'status':'executed','kind':'payment',
           'exec_id':'in-flight','amount_eur':10000},tick=2)
    for tick in range(3,6): accept(setup,'clock','tick',{'tick':tick},tick=tick)
    assert not setup[3].suspects


def test_deferred_ledger_still_detects_corruption_on_its_own_tick(setup):
    initial=setup[3].cash
    accept(setup,'clock','tick',{'tick':2},tick=2)
    hb,_=accept(setup,'risk','heartbeat',
        {'view':{'cash_eur':initial*1.15,'as_of_tick':2}},tick=2)
    for tick in range(3,6): accept(setup,'clock','tick',{'tick':tick},tick=tick)
    finding=setup[3].suspects['core-ledger']
    assert finding.kind=='ledger_mismatch'
    assert hb['message_id'] in finding.evidence


def test_legacy_delayed_heartbeat_uses_its_declared_tick(setup):
    initial=setup[3].cash
    for tick in range(3): accept(setup,'clock','tick',{'tick':tick},tick=tick)
    accept(setup,'executor','execution',{'status':'executed','kind':'payment',
           'exec_id':'after-heartbeat','amount_eur':10000},tick=3)
    for tick in range(3,11): accept(setup,'clock','tick',{'tick':tick},tick=tick)
    _,findings=accept(setup,'risk','heartbeat',{'view':{'cash_eur':initial}},tick=1)
    assert not findings


def test_risk_heartbeat_declares_ledger_snapshot_tick(cfg):
    from sim.lockstep import Lockstep
    from tests.helpers import price_update, tick_msg, set_world_tick
    ls=Lockstep(cfg)
    try:
        risk=ls.agents['risk']
        risk.handle(price_update(ls,{'ACME':100,'BOREAL':100,'CITRA':100}))
        set_world_tick(ls,7)
        heartbeats=[m for m in risk.handle(tick_msg(ls,7)) if m['type']=='heartbeat']
        assert heartbeats[0]['payload']['view']['as_of_tick']==7
        risk.tick=12
        assert risk.heartbeat_payload()['view']['as_of_tick']==7
    finally:
        ls.close()


def test_pending_balance_respects_lost_execution_coverage(setup):
    initial=setup[3].cash
    accept(setup,'clock','tick',{'tick':0},tick=0)
    accept(setup,'risk','heartbeat',{'view':{'cash_eur':initial-10000,'as_of_tick':0}},tick=0)
    setup[3].cash_complete=False
    accept(setup,'clock','tick',{'tick':2},tick=2)
    assert not setup[3].suspects


def test_pending_ledger_overflow_is_targeted(cfg):
    from bus.message import Keyring,MessageFactory
    from sim.config import known_senders
    from vigie.main import Runtime
    keys=Keyring(known_senders(cfg));rt=Runtime(cfg,keys)
    clock=MessageFactory('clock',keys);risk=MessageFactory('risk',keys)
    rt.on_message(clock.make(0,'tick','*',{'tick':0}))
    for _ in range(128):
        rt.on_message(risk.make(0,'heartbeat','*',{'view':{'cash_eur':rt.contagion.cash,'as_of_tick':0}}))
    rt.on_message(clock.make(1,'tick','*',{'tick':1}))
    rt.on_message(risk.make(1,'heartbeat','*',{'view':{'cash_eur':rt.contagion.cash,'as_of_tick':1}}))
    assert not rt.freeze.uncertain
    assert rt.request_approval(risk.make(1,'risk_decision','executor',{'kind':'payment'})).approved


def test_invalid_view_tick_is_rejected_without_global_refusal(cfg):
    from bus.message import Keyring,MessageFactory
    from sim.config import known_senders
    from vigie.main import Runtime
    keys=Keyring(known_senders(cfg));rt=Runtime(cfg,keys)
    clock=MessageFactory('clock',keys);risk=MessageFactory('risk',keys)
    rt.on_message(clock.make(0,'tick','*',{'tick':0}))
    for value in ('wrong',10000):
        rt.on_message(risk.make(0,'heartbeat','*',{'view':{'cash_eur':1,'as_of_tick':value}}))
    assert not rt.freeze.uncertain
    assert rt.request_approval(risk.make(0,'risk_decision','executor',{'kind':'payment'})).approved
