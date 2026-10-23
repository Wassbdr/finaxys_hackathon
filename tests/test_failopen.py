"""Refus par opération : cache immuable et provenance réellement analysée."""
import copy
import pytest
from bus.message import Keyring, MessageFactory
from sim.config import known_senders, load_config
from vigie.main import Runtime


@pytest.fixture
def bank():
    cfg=load_config(overrides={'llm':{'enabled':False,'cache':False}})
    keys=Keyring(known_senders(cfg));rt=Runtime(cfg,keys)
    f={s:MessageFactory(s,keys) for s in known_senders(cfg)}
    rt.on_message(f['clock'].make(0,'tick','*',{'tick':0}))
    return rt,f,keys


def payment(bank,provenance=(),parents=()):
    rt,f,_=bank
    return f['risk'].make(rt.freeze.clock,'risk_decision','executor',{'kind':'payment'},
                         depends_on=list(parents),data_provenance=list(provenance))


def entry(hash_='rejected',service='news-wire',tick=0):
    return {'service':service,'version':'1','response_hash':hash_,'tick':tick}


def test_signed_id_collision_cannot_reuse_clean_cached_analysis(bank):
    rt,f,keys=bank
    safe=payment(bank)
    assert rt.request_approval(safe).approved
    rt.state.meta['market-feed']={'language':'php','php_version':'7.4'}
    rt.on_message(f['quant'].make(0,'price_update','*',{'assets':{'ACME':{'price':100}}},
        data_provenance=[entry(service='market-feed')]))
    rt.on_message(f['secu'].make(0,'alert','*',{'kind':'cve_published','cve':{
        'affects':{'language':'php','version_lt':'8.1'}}}))
    assert 'market-feed' in rt.contagion.suspects
    changed=copy.deepcopy(safe)
    changed['data_provenance']=[entry(service='market-feed')]
    keys.sign(changed)
    assert not rt.request_approval(changed).approved, 'Une autre version a réutilisé le cache sain'
    assert rt.request_approval(safe).approved
    assert not rt.freeze.uncertain


def test_rejected_root_cannot_be_laundered_by_response_hash(bank):
    rt,f,_=bank
    provenance=[entry()]
    bad=f['trader-1'].make(0,'order_proposal','*',{'asset':[], 'side':'sell'},data_provenance=provenance)
    rt.on_message(bad)
    assert bad['message_id'] not in rt.state.messages
    operation=payment(bank,provenance)
    assert not rt.request_approval(operation).approved, 'Le lien par hash vers une entrée rejetée a été ignoré'
    assert rt.request_approval(payment(bank)).approved and not rt.freeze.uncertain


def test_operation_cannot_be_its_own_only_provenance_witness(bank):
    rt,_,_=bank
    assert not rt.request_approval(payment(bank,[entry(service='core-ledger')])).approved
    assert rt.request_approval(payment(bank)).approved and not rt.freeze.uncertain


def public_root(bank,provenance=None,sender='trader-1'):
    rt,f,_=bank
    msg=f[sender].make(rt.freeze.clock,'llm_trace','*',{'response':{'justification':'lecture publique'}},
                       data_provenance=list(provenance or [entry()]))
    rt.on_message(msg)
    return msg


def test_admitted_provenance_is_usable_without_explicit_parent(bank):
    rt,_,_=bank
    public_root(bank)
    assert rt.request_approval(payment(bank,[entry()])).approved


def test_budget_rejected_provenance_is_not_recovered_by_ignoring_its_root(bank):
    rt,_,_=bank
    rt.admission.messages['trader-1']=rt.admission.message_limit
    root=public_root(bank)
    assert root['message_id'] not in rt.state.messages
    assert not rt.request_approval(payment(bank,[entry()])).approved
    assert rt.request_approval(payment(bank)).approved and not rt.freeze.uncertain


def test_incomplete_operation_stays_rejected_after_window_expires(bank):
    rt,f,_=bank
    public_root(bank)
    for _ in range(rt.admission.trace_limit): public_root(bank,provenance=[entry('other')])
    assert rt.admission.gaps['trader-1']==30
    blocked=payment(bank,[entry()])
    assert not rt.request_approval(blocked).approved
    rt.on_message(f['clock'].make(31,'tick','*',{'tick':31}))
    assert not rt.request_approval(blocked).approved, 'La fin de fenêtre a blanchi une opération non analysée'
    assert rt.request_approval(payment(bank,[entry()])).approved
    assert not rt.freeze.uncertain


@pytest.mark.parametrize('provenance',[
    {'service':'news-wire'},entry(service='unknown'),{**entry(),'response_hash':''},
    {**entry(),'tick':None},{**entry(),'version':''},
])
def test_unknown_provenance_refuses_only_its_operation(bank,provenance):
    rt,_,_=bank
    assert not rt.request_approval(payment(bank,[provenance])).approved
    assert rt.request_approval(payment(bank)).approved and not rt.freeze.uncertain


@pytest.mark.parametrize('changed', [{'version':'different'},{'tick':1},{'service':'market-feed'}])
def test_response_hash_does_not_certify_another_provenance_tuple(bank,changed):
    rt,_,_=bank
    public_root(bank)
    assert not rt.request_approval(payment(bank,[{**entry(),**changed}])).approved
    assert rt.request_approval(payment(bank,[entry()])).approved


def test_unverified_copy_cannot_poison_an_admitted_independent_proof(bank):
    rt,f,_=bank
    root=public_root(bank)
    for i in range(200):
        forged=copy.deepcopy(root);forged['message_id']=f'fake:{i}';forged['signature']='bad'
        rt.on_message(forged)
    assert rt.request_approval(payment(bank,[entry()],parents=[root['message_id']])).approved
    assert not rt.freeze.uncertain


def test_cached_proposal_does_not_authorize_changed_nested_financial_data(bank):
    rt,f,_=bank
    root=f['trader-1'].make(0,'order_proposal','*',{'asset':'ACME','side':'buy','notional_eur':100})
    rt.on_message(root)
    short={k:copy.deepcopy(root[k]) for k in ('message_id','from','type','payload')}
    changed=copy.deepcopy(short);changed['payload']['notional_eur']=100000
    bad=f['risk'].make(0,'risk_decision','executor',{'kind':'order','proposal':changed})
    assert not rt.request_approval(bad).approved
    good=f['risk'].make(0,'risk_decision','executor',{'kind':'order','proposal':short})
    assert rt.request_approval(good).approved and not rt.freeze.uncertain


def test_metadata_outside_signed_fields_is_not_a_cache_collision(bank):
    rt,_,_=bank
    op=payment(bank)
    assert rt.request_approval(op).approved
    copy_=copy.deepcopy(op);copy_['data_altered']=True
    assert rt.request_approval(copy_).approved


def test_proof_budget_exhaustion_refuses_the_operation_only(bank):
    rt,f,_=bank
    parent=None
    for i in range(130):
        if i%15==0:
            tick=i//15+1
            rt.on_message(f['clock'].make(tick,'tick','*',{'tick':tick}))
        sender=('trader-1','trader-2','trader-3')[i%3]
        msg=f[sender].make(rt.freeze.clock,'heartbeat','*',{},depends_on=[parent] if parent else [])
        rt.on_message(msg);parent=msg['message_id']
    assert parent in rt.admission.unanalysed
    assert rt.admission.last_reason=='proof_budget'
    assert not rt.request_approval(payment(bank,parents=[parent])).approved
    assert rt.request_approval(payment(bank)).approved and not rt.freeze.uncertain


def test_truncated_proposal_cannot_replace_an_existing_root(bank):
    rt,f,_=bank
    root=f['payment'].make(0,'payment_proposal','*',{'amount_eur':100,'beneficiary_id':'BEN-01'})
    rt.on_message(root)
    op=f['risk'].make(0,'risk_decision','executor',{'kind':'payment','proposal':{'message_id':root['message_id']}})
    assert not rt.request_approval(op).approved
    assert rt.request_approval(payment(bank)).approved


def test_risk_publishes_its_read_before_proposing_irreversible_operation(cfg):
    from sim.lockstep import Lockstep
    from sim.config import load_config
    cfg=load_config(overrides={'llm':{'enabled':False,'cache':False},'runs_dir':cfg['runs_dir']})
    ls=Lockstep(cfg,no_llm=True)
    seen=set();count=0
    def observe(msg):
        nonlocal count
        if msg['type']!='risk_decision':
            seen.update(p['response_hash'] for p in msg['data_provenance'] if p['service']=='core-ledger')
        if msg['from']=='risk' and msg['type']=='heartbeat':
            for p in msg['data_provenance']:
                if p['service']=='core-ledger':
                    assert ls.keyring.verify(msg)
                    seen.add(p['response_hash']);count+=1
        if msg['from']=='risk' and msg['type']=='risk_decision' and msg['payload'].get('approved'):
            for p in msg['data_provenance']:
                if p['service']=='core-ledger': assert p['response_hash'] in seen
    ls.bus.tap(observe)
    try:
        ls.run(12)
        assert count>0
    finally: ls.close()


def test_same_producer_cannot_rename_a_rejected_provenance_as_a_new_root(bank):
    rt,f,_=bank
    bad=f['trader-1'].make(0,'llm_trace','*',{'response':{'justification':[]}},data_provenance=[entry()])
    rt.on_message(bad)
    relay=public_root(bank)
    assert not rt.request_approval(payment(bank,[entry()])).approved, 'Une nouvelle racine a blanchi le hash rejeté'
    assert relay['message_id'] in rt.admission.unanalysed
    public_root(bank,[entry('independent')],sender='trader-2')
    assert rt.request_approval(payment(bank,[entry('independent')])).approved
    assert not rt.freeze.uncertain


def test_rejected_provenance_memory_is_bounded_by_authenticated_source(bank):
    rt,f,_=bank
    for i in range(rt.admission.message_limit+10):
        rt.on_message(f['trader-1'].make(0,'llm_trace','*',{'response':{'justification':[]}},
            data_provenance=[entry(f'bad-{i}')]))
    assert len(rt.admission.rejected_provenance['trader-1'])<=rt.admission.message_limit
    assert rt.admission.opaque_producers=={'trader-1'}
    public_root(bank,[entry('fresh-unanalysable')])
    assert not rt.request_approval(payment(bank,[entry('fresh-unanalysable')])).approved
    public_root(bank,[entry('other-source')],sender='trader-2')
    assert rt.request_approval(payment(bank,[entry('other-source')])).approved
    assert rt.request_approval(payment(bank)).approved and not rt.freeze.uncertain


def test_later_malformed_claim_does_not_revoke_an_independent_admitted_read(bank):
    rt,f,_=bank
    original=public_root(bank)
    rt.on_message(f['trader-1'].make(0,'llm_trace','*',{'response':{'justification':[]}},data_provenance=[entry()]))
    assert rt.request_approval(payment(bank,[entry()],parents=[original['message_id']])).approved
    assert not rt.freeze.uncertain


@pytest.mark.parametrize('replacement_sender', ['trader-1', 'trader-2'])
def test_rejected_identity_cannot_be_reintroduced_as_an_analysed_parent(bank, replacement_sender):
    rt, f, keys = bank
    rejected = f['trader-1'].make(0, 'order_proposal', '*',
                                {'asset': [], 'side': 'sell', 'notional_eur': 100})
    rt.on_message(rejected)
    assert rejected['message_id'] not in rt.state.messages
    replacement = copy.deepcopy(rejected)
    replacement['from'] = replacement_sender
    replacement['payload']['asset'] = 'ACME'
    keys.sign(replacement)
    rt.on_message(replacement)
    operation = payment(bank, parents=[rejected['message_id']])
    decision = rt.request_approval(operation)
    assert not decision.approved, 'La réintroduction du même identifiant a blanchi une entrée rejetée'
    assert rejected['message_id'] in decision.reason
    assert rt.request_approval(payment(bank)).approved
    assert not rt.freeze.uncertain


def test_unverified_identity_does_not_poison_a_future_admitted_parent(bank):
    rt, f, _ = bank
    root = f['trader-1'].make(0, 'order_proposal', '*',
                            {'asset': 'ACME', 'side': 'sell', 'notional_eur': 100})
    forged = copy.deepcopy(root)
    forged['signature'] = 'bad'
    forged['payload']['asset'] = []
    rt.on_message(forged)
    rt.on_message(root)
    assert rt.request_approval(payment(bank, parents=[root['message_id']])).approved
    assert not rt.freeze.uncertain


def test_later_rejected_identity_does_not_revoke_an_admitted_parent(bank):
    rt, f, keys = bank
    root = f['trader-1'].make(0, 'order_proposal', '*',
                            {'asset': 'ACME', 'side': 'sell', 'notional_eur': 100})
    rt.on_message(root)
    changed = copy.deepcopy(root)
    changed['payload']['asset'] = []
    keys.sign(changed)
    rt.on_message(changed)
    assert rt.request_approval(payment(bank, parents=[root['message_id']])).approved
    assert not rt.freeze.uncertain


def test_rejected_identity_budget_stays_targeted_and_keeps_old_proofs(bank):
    rt, f, _ = bank
    original = public_root(bank)
    limit = rt.admission.message_limit
    rt.admission.message_limit = 2
    for _ in range(5):
        rt.on_message(f['trader-1'].make(0, 'order_proposal', '*', {'asset': [], 'side': 'sell'}))
    rt.admission.message_limit = limit
    fresh = f['trader-1'].make(0, 'order_proposal', '*',
                             {'asset': 'ACME', 'side': 'sell', 'notional_eur': 100})
    rt.on_message(fresh)
    decision = rt.request_approval(payment(bank, parents=[fresh['message_id']]))
    assert not decision.approved
    assert len(rt.admission.rejected_identities['trader-1']) == 2
    assert rt.admission.opaque_identities == {'trader-1'}
    assert fresh['message_id'] in decision.reason
    assert rt.request_approval(payment(bank, parents=[original['message_id']])).approved
    assert rt.request_approval(payment(bank)).approved
    assert not rt.freeze.uncertain


@pytest.mark.parametrize('reason', ['source_memory', 'future_input'])
def test_rejected_identity_survives_quota_recovery_and_clock_advance(bank, reason):
    rt, f, _ = bank
    root = f['trader-1'].make(1 if reason == 'future_input' else 0, 'order_proposal', '*',
                            {'asset': 'ACME', 'side': 'sell', 'notional_eur': 100})
    if reason == 'source_memory':
        rt.admission.messages['trader-1'] = rt.admission.message_limit
    else:
        root['tick'] = 2
        bank[2].sign(root)
    rt.on_message(root)
    assert root['message_id'] not in rt.state.messages
    assert rt.admission.rejected['signed:trader-1'][reason] == 1
    rt.admission.messages['trader-1'] = 0
    rt.on_message(f['clock'].make(31, 'tick', '*', {'tick': 31}))
    rt.on_message(root)
    decision = rt.request_approval(payment(bank, parents=[root['message_id']]))
    assert not decision.approved, 'La récupération du quota ou de l’horloge a effacé le rejet'
    assert root['message_id'] in decision.reason
    assert rt.request_approval(payment(bank)).approved
    assert not rt.freeze.uncertain


def test_overflowed_rejected_identity_cannot_be_rebound_by_another_sender(bank):
    rt, f, keys = bank
    limit = rt.admission.message_limit
    rt.admission.message_limit = 1
    for _ in range(2):
        bad = f['trader-1'].make(0, 'order_proposal', '*', {'asset': [], 'side': 'sell'})
        rt.on_message(bad)
    rt.admission.message_limit = limit
    replacement = copy.deepcopy(bad)
    replacement['from'] = 'trader-2'
    replacement['payload']['asset'] = 'ACME'
    keys.sign(replacement)
    rt.on_message(replacement)
    assert not rt.request_approval(payment(bank, parents=[bad['message_id']])).approved
    independent = f['trader-2'].make(0, 'order_proposal', '*', {'asset': 'ACME', 'side': 'sell'})
    rt.on_message(independent)
    assert rt.request_approval(payment(bank, parents=[independent['message_id']])).approved
    assert not rt.freeze.uncertain


def test_signed_sender_cannot_reserve_another_senders_future_identity(bank):
    rt, f, keys = bank
    root = f['trader-2'].make(0, 'order_proposal', '*', {'asset': 'ACME', 'side': 'sell'})
    impostor = copy.deepcopy(root)
    impostor['from'] = 'trader-1'
    keys.sign(impostor)
    rt.on_message(impostor)
    assert root['message_id'] not in rt.state.messages
    rt.on_message(root)
    assert rt.request_approval(payment(bank, parents=[root['message_id']])).approved
    assert not rt.freeze.uncertain
