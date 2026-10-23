"""Contre-épreuves de contournement, saturation et dérive du gel."""
import pytest
from vigie.core import VigieState
from vigie.freeze import Freeze
from tests.test_freeze import profile


def frame(tick=0, asset="ACME", side="sell", aligned=None):
    return {"tick": tick, "asset": asset, "side": side, "aligned": aligned or ["a", "b", "c"],
        "index": 1.0, "loop_evidence": ["sale", "drop", "dependent-sale"], "similarity": None,
        "trace_evidence": [], "unique_evidence": [], "order_evidence": ["p1", "p2", "p3"]}


def manager(**changes):
    return Freeze(VigieState(), ["a", "b", "c", "d"], profile(**changes))


def test_evasion_short_gaps_do_not_erase_accumulated_danger():
    m=manager(persistence_ticks=20)
    for tick in range(23):
        m.update([] if tick in {9, 18} else [frame(tick)], tick)
    assert "ACME" in m.active


def test_evasion_rotating_member_is_added_to_the_targeted_lease():
    m=manager()
    m.update([frame()], 0)
    m.update([frame(1, aligned=["b", "c", "d"])], 1)
    assert set(m.active["ACME"]["traders"]) == {"a", "b", "c", "d"}


def test_dos_malformed_message_never_raises():
    m=manager()
    for msg in (None, [], {}, {"type": "order_proposal", "payload": None},
                {"type": "order_proposal", "from": "a", "tick": 0, "message_id": "x", "payload": {"asset": []}}):
        m.on_message(msg)


def test_dos_events_and_reference_memory_have_fixed_limits():
    m=manager()
    for i in range(6000):
        m.observe({"message_id": str(i), "type": "order_proposal", "from": "a", "tick": 0,
          "payload": {"asset": "ACME", "side": "sell", "news_refs": [{"id": str(i), "source": "wire"}]}})
    assert len(m.events) <= 2048 and len(m.first_refs) <= 4096
    assert m.uncertain


def request(m):
    m.state.messages['p']={"message_id": "p", "from": "a", "type": "order_proposal", "payload": {"asset": "ACME", "side": "sell"}}
    return {"message_id": "r", "payload": {"kind": "order", "proposal": {"message_id": "p"}}}


def test_fail_closed_missing_reference():
    m=manager()
    m.profile["reference"].pop("ACME")
    assert m.refusal(request(m))[0]


def test_fail_closed_lost_lease_cannot_pass():
    m=manager()
    m.update([frame()],0)
    m.active.clear()
    assert m.refusal(request(m))[0]


def test_fail_closed_backwards_clock_retains_lease():
    m=manager()
    m.update([frame()], 10)
    m.update([], 9)
    assert m.uncertain and "ACME" in m.active


def test_fail_closed_checkpoint_restart_round_trip_and_corruption(tmp_path):
    from vigie.freeze_store import FileCheckpoint
    from vigie.main import Runtime
    from sim.config import load_config, known_senders
    from bus.message import Keyring
    cfg=load_config(overrides={"vigie": {"freeze_profile": profile()}})
    keys=Keyring(known_senders(cfg))
    store=FileCheckpoint(tmp_path/'freeze.json')
    rt=Runtime(cfg,keys,checkpoint=store,fresh_checkpoint=True)
    rt.freeze.update([frame(aligned=["trader-1","trader-2","trader-3"])],0)
    rt.persist_freeze()
    recovered=Runtime(cfg,keys,checkpoint=store)
    assert recovered.freeze.active == rt.freeze.active
    (tmp_path/'freeze.json').unlink()
    lost=Runtime(cfg,keys,checkpoint=store)
    assert lost.freeze.uncertain


def test_evasion_switching_assets_and_sides_keeps_shared_source_history():
    m=manager(persistence_ticks=20)
    for tick in range(24):
        f=frame(tick, asset='ACME' if tick%2 else 'BOREAL', side='sell' if tick%3 else 'buy')
        f['cause_keys']=['news:one-shared-story']
        m.update([f],tick)
    assert set(m.active)=={'ACME','BOREAL'}


def test_evasion_split_orders_and_opposite_dust_do_not_cancel_gross_herd():
    m=manager(persistence_ticks=1,min_aligned=3)
    for actor in ('a','b','c'):
        for n,(side,amount) in enumerate([('sell',1000),('buy',.01)]):
            m.observe({'message_id': actor+str(n), 'type':'order_proposal','from':actor,'tick':0,
              'payload':{'asset':'ACME','side':side,'notional_eur':amount,'news_refs':[{'id':'story','source':actor}]}})
    m.update(m.features(0),0)
    assert 'ACME' in m.active and set(m.active['ACME'].get('sides',[m.active['ACME']['side']]))=={'buy','sell'}


def test_dos_runtime_handles_signed_wrong_types_and_unknown_asset():
    from tests.test_freeze import setup as _fixture
    from vigie.main import Runtime
    from sim.config import load_config, known_senders
    from bus.message import Keyring, MessageFactory
    cfg=load_config(); keys=Keyring(known_senders(cfg)); rt=Runtime(cfg,keys)
    f=MessageFactory('trader-1',keys)
    payloads=[{'asset':[], 'side':'sell'}, {'asset':'UNKNOWN','side':'sell'},
              {'asset':'ACME','side':{}}, {'asset':'ACME','side':'sell','news_refs':None}]
    for p in payloads:
        rt.on_message(f.make(0,'order_proposal','*',p))
    assert rt.freeze.uncertain


def test_dos_cyclic_huge_integer_and_surrogate_inputs_are_rejected():
    from vigie.validation import cost
    cycle=[]; cycle.append(cycle)
    for data in (cycle, {'n':10**1000}, {'s':'\ud800'}, {'big':'x'*100000}):
        assert cost(data) is None


def test_dos_unsigned_malformed_input_cannot_poison_the_bank():
    from vigie.main import Runtime
    from sim.config import load_config, known_senders
    from bus.message import Keyring
    cfg=load_config(); rt=Runtime(cfg,Keyring(known_senders(cfg)))
    for msg in (None, [], {}, {'payload':None}, {'payload':{'n':10**1000}}): rt.on_message(msg)
    assert not rt.freeze.uncertain


def test_dos_runtime_memory_admission_closes_before_growth():
    from vigie.main import Runtime
    from sim.config import load_config, known_senders
    from bus.message import Keyring, MessageFactory
    cfg=load_config(); keys=Keyring(known_senders(cfg)); rt=Runtime(cfg,keys)
    rt.admitted_bytes=16*1024*1024
    rt.on_message(MessageFactory('trader-1',keys).make(0,'order_proposal','*',{'asset':'ACME','side':'sell'}))
    assert len(rt.state.messages)==0 and 'capacity' in rt.freeze.uncertain


def test_fail_closed_write_failure_refuses_authorization(tmp_path):
    from vigie.main import Runtime
    from sim.config import load_config, known_senders
    from bus.message import Keyring, MessageFactory
    class BrokenStore:
        path=tmp_path/'missing'
        def save(self,*args): raise OSError('disque indisponible')
        def load(self,*args): raise OSError('état indisponible')
    cfg=load_config(); keys=Keyring(known_senders(cfg)); rt=Runtime(cfg,keys,checkpoint=BrokenStore())
    msg=MessageFactory('risk',keys).make(0,'risk_decision','executor',{'kind':'payment'})
    assert not rt.request_approval(msg).approved
    assert rt.freeze.uncertain


def test_fail_closed_clock_comes_only_from_signed_clock_messages():
    m=manager()
    m.on_message({'message_id':'clock0','type':'tick','from':'clock','tick':0,'payload':{'tick':0}})
    m.update([frame()],0)
    m.state.tick=10000
    m.on_message({'message_id':'actor','type':'order_proposal','from':'a','tick':10000,
                  'payload':{'asset':'ACME','side':'sell'}})
    assert m.clock==0 and m.refusal(request(m))[0]
    m.on_message({'message_id':'clockbad','type':'tick','from':'clock','tick':0,'payload':{'tick':0}})
    assert m.active


def test_fail_closed_corrupted_lease_deadline_is_not_forgotten():
    m=manager(); m.update([frame()],0)
    m.active['ACME']['until_tick']='broken'
    m.on_message({'message_id':'c','type':'tick','from':'clock','tick':20,'payload':{'tick':20}})
    assert m.uncertain and m.refusal(request(m))[0]


def test_fail_closed_missing_and_invalid_profile_file(monkeypatch):
    import vigie.main as main
    from sim.config import load_config, known_senders
    from bus.message import Keyring, MessageFactory
    cfg=load_config(); keys=Keyring(known_senders(cfg))
    monkeypatch.setattr(main,'load_profile',lambda:None)
    rt=main.Runtime(cfg,keys)
    assert rt.freeze.uncertain
    assert not rt.request_approval(MessageFactory('risk',keys).make(0,'risk_decision','executor',{'kind':'payment'})).approved


def test_dos_trace_volume_is_bounded_before_quadratic_similarity():
    from vigie.main import Runtime
    from sim.config import load_config, known_senders
    from bus.message import Keyring, MessageFactory
    cfg=load_config(); keys=Keyring(known_senders(cfg)); rt=Runtime(cfg,keys)
    factory=MessageFactory('trader-1',keys)
    for _ in range(300):
        rt.on_message(factory.make(0,'llm_trace','*',{'response':{'justification':'texte'}}))
    assert len(rt.analytics.herding.events)<=64
    assert 'capacity' in rt.freeze.uncertain


def test_fail_closed_fresh_flag_never_overwrites_an_existing_checkpoint(tmp_path):
    from vigie.freeze_store import FileCheckpoint
    from vigie.main import Runtime
    from sim.config import load_config, known_senders
    from bus.message import Keyring
    cfg=load_config(); keys=Keyring(known_senders(cfg)); store=FileCheckpoint(tmp_path/'state.json')
    original=Runtime(cfg,keys,checkpoint=store,fresh_checkpoint=True)
    original.persist_freeze(); before=store.path.read_bytes()
    attempted=Runtime(cfg,keys,checkpoint=store,fresh_checkpoint=True)
    assert attempted.freeze.uncertain and not attempted.persist_freeze()
    assert store.path.read_bytes()==before
