"""Le rejet d'une entrée hostile ne doit pas devenir un arrêt bancaire."""
import copy
import pytest
from bus.message import Keyring, MessageFactory
from sim.config import load_config, known_senders
from vigie.main import Runtime


@pytest.fixture
def bank():
    cfg=load_config(overrides={'llm':{'enabled':False,'cache':False}})
    keys=Keyring(known_senders(cfg))
    rt=Runtime(cfg,keys)
    factories={sender:MessageFactory(sender,keys) for sender in known_senders(cfg)}
    rt.on_message(factories['clock'].make(0,'tick','*',{'tick':0}))
    return rt,factories


def payment(bank):
    rt,f=bank
    return rt.request_approval(f['risk'].make(rt.freeze.clock or 0,'risk_decision','executor',{'kind':'payment'}))


def test_malformed_signed_flood_does_not_globally_refuse(bank):
    rt,f=bank
    for _ in range(200):
        rt.on_message(f['trader-1'].make(0,'order_proposal','*',{'asset':[], 'side':'sell'}))
    assert payment(bank).approved, 'Le rejet de paquets malformés a arrêté toute la banque'
    assert not rt.freeze.uncertain


def test_unsigned_packet_at_capacity_does_not_latch_global_refusal(bank):
    rt,f=bank
    rt.admitted_bytes=16*1024*1024
    for _ in range(200):
        msg=f['trader-1'].make(0,'heartbeat','*',{})
        msg['signature']='bad'
        rt.on_message(msg)
    assert not rt.freeze.uncertain, 'Un paquet non signé a transformé la saturation en refus global'


def test_one_source_cannot_spend_another_sources_trace_budget(bank):
    rt,f=bank
    for _ in range(300):
        rt.on_message(f['trader-1'].make(0,'llm_trace','*',{'response':{'justification':'texte'}}))
    other=f['trader-2'].make(0,'llm_trace','*',{'response':{'justification':'autre'}})
    rt.on_message(other)
    assert other['message_id'] in rt.state.messages
    assert payment(bank).approved
    assert not rt.freeze.uncertain


def test_bad_future_request_does_not_poison_clock(bank):
    rt,f=bank
    bad=f['risk'].make(10000,'risk_decision','executor',{'kind':'payment'})
    assert not rt.request_approval(bad).approved
    assert payment(bank).approved
    assert rt.freeze.clock==0 and not rt.freeze.uncertain


def test_signed_backwards_clock_is_rejected_without_overwriting_internal_clock(bank):
    rt,f=bank
    rt.on_message(f['clock'].make(3,'tick','*',{'tick':3}))
    rt.on_message(f['clock'].make(2,'tick','*',{'tick':2}))
    assert rt.freeze.clock==3 and payment(bank).approved
    assert not rt.freeze.uncertain


def test_saturation_refuses_only_the_unanalysed_operation(bank):
    rt,f=bank
    rt.admitted_bytes=16*1024*1024
    assert not payment(bank).approved
    assert not rt.freeze.uncertain
    rt.admitted_bytes=0
    assert payment(bank).approved


def test_input_nested_wrong_types_cannot_latch_global_refusal(bank):
    rt,f=bank
    rt.on_message(f['risk'].make(0,'heartbeat','*',{'view':[]}))
    rt.on_message(f['secu'].make(0,'alert','*',{'kind':'cve_published','cve':[]}))
    assert payment(bank).approved
    assert not rt.freeze.uncertain


def test_unverified_claims_never_charge_authenticated_sender(bank):
    rt,f=bank
    for i in range(1500):
        msg=f['trader-1'].make(0,'heartbeat','*',{})
        msg['from']=f'unknown-{i}' if i%2 else 'trader-2'
        msg['signature']='bad'
        rt.on_message(msg)
    legitimate=f['trader-2'].make(0,'heartbeat','*',{})
    rt.on_message(legitimate)
    assert legitimate['message_id'] in rt.state.messages
    assert len(rt.admission.rejected)==2
    assert not rt.admission.messages['trader-1']
    assert not rt.freeze.uncertain and payment(bank).approved


def test_sender_storage_reservation_and_admission_decision(bank):
    rt,f=bank
    for tick in range(20):
        rt.on_message(f['clock'].make(tick,'tick','*',{'tick':tick}))
        for _ in range(100): rt.on_message(f['trader-1'].make(tick,'heartbeat','*',{}))
    assert rt.admission.messages['trader-1']==rt.admission.message_limit
    assert payment(bank).approved and not rt.freeze.uncertain
    own=f['trader-1'].make(19,'risk_decision','executor',{'kind':'payment'})
    assert not rt.request_approval(own).approved
    assert own['message_id'] not in rt.state.messages


def test_dropped_root_only_refuses_operations_depending_on_it(bank):
    rt,f=bank
    rt.admission.messages['trader-1']=rt.admission.message_limit
    proposal=f['trader-1'].make(0,'order_proposal','*',{'asset':'ACME','side':'sell'})
    rt.on_message(proposal)
    linked=f['risk'].make(0,'risk_decision','executor',{'kind':'order','proposal':proposal},
                           depends_on=[proposal['message_id']])
    assert not rt.request_approval(linked).approved
    assert payment(bank).approved and not rt.freeze.uncertain


def test_human_diagnostics_are_signed_bounded_and_distinguish_internal(bank):
    rt,f=bank
    for _ in range(100):
        rt.on_message(f['trader-1'].make(0,'order_proposal','*',{'asset':[], 'side':'sell'}))
    out=rt.on_message(f['clock'].make(1,'tick','*',{'tick':1}))
    alerts=[m for m in out if m['type']=='alert' and m['payload'].get('kind')=='vigie_input_rejected']
    assert len(alerts)==1 and rt.keyring.verify(alerts[0])
    assert alerts[0]['payload']['counts']['malformed']==100
    assert alerts[0]['payload']['source']=='signed:trader-1'
    assert len(rt.diagnostics())==0
    rt.freeze.degrade('checkpoint_write')
    out=rt.diagnostics()
    assert any(m['payload'].get('kind')=='vigie_degraded' for m in out)
    assert not payment(bank).approved


def test_existing_frozen_lease_survives_noise(bank):
    from tests.test_freeze_security import frame
    rt,f=bank
    rt.freeze.profile['persistence_ticks']=1
    rt.freeze.update([frame(aligned=['trader-1','trader-2','trader-3'])],0)
    before=copy.deepcopy(rt.freeze.active)
    for _ in range(300):
        rt.on_message(f['trader-1'].make(0,'order_proposal','*',{'asset':[], 'side':'sell'}))
    assert rt.freeze.active==before
    assert payment(bank).approved
    rt.freeze.active.clear()
    proposal=f['trader-2'].make(0,'order_proposal','*',{'asset':'ACME','side':'sell'})
    rt.on_message(proposal)
    order=f['risk'].make(0,'risk_decision','executor',{'kind':'order','proposal':proposal})
    assert not rt.request_approval(order).approved
    assert 'lease_lost' in rt.freeze.uncertain


@pytest.mark.parametrize('sender,kind,payload',[
    ('human','human_decision',{'action':[]}),
    ('secu','alert',{'kind':'cve_published','cve':{'affects':{'language':[]}}}),
    ('executor','execution',{'status':'executed','kind':'order','qty':-1,'fill_price':100,'asset':[]}),
    ('executor','execution',{'status':'executed','kind':'payment','exec_id':[],'amount_eur':100}),
    ('quant','price_update',{'assets':{'UNKNOWN':{'price':100}}}),
    ('risk','risk_decision',{'kind':'order','proposal':{'message_id':[]}}),
])
def test_malformed_consumer_fields_are_isolated(bank,sender,kind,payload):
    rt,f=bank
    msg=f[sender].make(0,kind,'*',payload)
    rt.on_message(msg)
    assert msg['message_id'] not in rt.state.messages
    assert not rt.freeze.uncertain and payment(bank).approved


def test_malformed_traffic_does_not_write_checkpoint(bank):
    rt,f=bank
    class Unreachable:
        def save(self,*args): raise AssertionError('Entrée rejetée écrite sur disque')
    rt.checkpoint=Unreachable()
    for _ in range(50):
        rt.on_message(f['trader-1'].make(0,'order_proposal','*',{'asset':[], 'side':'sell'}))
    assert not rt.freeze.uncertain


def test_internal_clock_divergence_remains_global(bank):
    rt,f=bank
    rt.freeze.clock=50
    rt.on_message(f['risk'].make(0,'heartbeat','*',{}))
    assert 'clock_regression' in rt.freeze.uncertain
    assert not payment(bank).approved


def test_missing_execution_after_quota_is_not_ledger_compromise(bank):
    rt,f=bank
    initial=rt.contagion.cash
    rt.admission.messages['executor']=rt.admission.message_limit
    rt.on_message(f['executor'].make(0,'execution','*',{'status':'executed','kind':'payment',
        'amount_eur':100,'exec_id':'missing'}))
    rt.on_message(f['risk'].make(0,'heartbeat','*',{'view':{'cash_eur':initial-100}}))
    assert 'core-ledger' not in rt.contagion.suspects, 'Une lacune connue est devenue une fausse compromission'
    linked=f['risk'].make(0,'risk_decision','executor',{'kind':'payment'},
        data_provenance=[{'service':'core-ledger'}])
    assert not rt.request_approval(linked).approved
    assert payment(bank).approved and not rt.freeze.uncertain


def test_authenticated_volume_cannot_overflow_checkpoint(bank,tmp_path):
    from vigie.freeze_store import FileCheckpoint
    rt,f=bank
    store=FileCheckpoint(tmp_path/'freeze.json')
    store.bootstrap(rt.freeze.snapshot(),rt.freeze.profile)
    rt.checkpoint=store
    for sender in ('trader-1','trader-2','trader-3','quant','compliance','payment'):
        for _ in range(15):
            rt.on_message(f[sender].make(0,'alert','*',{'kind':'noise','cve':{'padding':['x'*8000 for _ in range(7)]}}))
    assert not rt.freeze.uncertain, 'Des entrées volumineuses ont provoqué une panne de checkpoint artificielle'
    assert payment(bank).approved
    assert store.path.stat().st_size<4*1024*1024


def test_traffic_before_first_clock_cannot_seed_a_future_internal_clock():
    cfg=load_config(); keys=Keyring(known_senders(cfg));rt=Runtime(cfg,keys)
    trader=MessageFactory('trader-1',keys);clock=MessageFactory('clock',keys)
    bad=trader.make(10000,'order_proposal','*',{'asset':'ACME','side':'sell'})
    rt.on_message(bad)
    rt.on_message(clock.make(0,'tick','*',{'tick':0}))
    assert bad['message_id'] not in rt.state.messages
    assert not rt.freeze.uncertain


def test_only_risk_can_create_portique_operations(bank):
    rt,f=bank
    msg=f['trader-1'].make(0,'risk_decision','executor',{'kind':'payment'})
    assert not rt.request_approval(msg).approved
    assert rt.admission.rejected['signed:trader-1']['request_authority']==1
    assert payment(bank).approved and not rt.freeze.uncertain


def test_large_clock_jump_is_rejected_as_input(bank):
    rt,f=bank
    rt.on_message(f['clock'].make(10000,'tick','*',{'tick':10000}))
    assert rt.freeze.clock==0 and not rt.freeze.uncertain
    assert payment(bank).approved


def test_rpc_rejection_is_counted_once_and_next_request_still_passes(bank,tmp_path):
    import json
    from vigie.main import serve
    from vigie.freeze_store import FileCheckpoint
    rt,f=bank
    cfg=load_config()
    bad=f['trader-1'].make(0,'order_proposal','*',{'asset':[], 'side':'sell'})
    good=f['risk'].make(0,'risk_decision','executor',{'kind':'payment'})
    events=[('vigie.approval_requests',{'request_id':'bad','message':bad}),
            ('bus',f['clock'].make(0,'tick','*',{'tick':0})),
            ('vigie.approval_requests',{'request_id':'good','message':good})]
    class Sub:
        def subscribe(self,*channels): self.channels=list(channels)
        def get_message(self,**kwargs):
            if self.channels: return {'type':'subscribe','channel':self.channels.pop(),'data':1}
            if events:
                channel,data=events.pop(0)
                return {'type':'message','channel':channel,'data':json.dumps(data)}
        def close(self): pass
    class Stop:
        def is_set(self): return not events
    class Client:
        def __init__(self): self.published=[]
        def pubsub(self,**kwargs): return Sub()
        def set(self,*args): pass
        def delete(self,*args): pass
        def publish(self,channel,data): self.published.append(json.loads(data))
    client=Client()
    live=serve(cfg,client,stop=Stop(),checkpoint=FileCheckpoint(tmp_path/'fresh'),fresh_checkpoint=True)
    assert live.admission.rejected['signed:trader-1']['malformed']==1
    response=next(m for m in client.published if m.get('request_id')=='good')
    assert response['approved'] and not live.freeze.uncertain
