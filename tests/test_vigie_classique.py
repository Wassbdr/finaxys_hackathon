"""Contrôles fixes et frontière d'information de la Vigie classique."""
import ast
import copy
import json
from pathlib import Path

import fakeredis
import pytest

from bus.message import MessageFactory
from vigie.classique import Classique, RedisPort, handle


@pytest.fixture
def classic(cfg):
    c = Classique(cfg)
    c.on_message(MessageFactory('clock', c.keys).make(0, 'tick', '*', {'tick': 0}))
    return c


def message(c, sender, kind, payload, to='*', tick=None):
    f = MessageFactory(sender, c.keys)
    f.nonce = str(len(c.seen) + len(c.decisions)) + ':' + kind + ':'
    return f.make(c.tick if tick is None else tick, kind, to, payload)


def approval(c, amount=100000, *, payment=False, iban=None, beneficiary='BEN-01'):
    sender = 'payment' if payment else 'trader-1'
    p = ({'payment_id': 'P'+str(len(c.seen)), 'beneficiary_id': beneficiary,
          'iban': iban if iban is not None else c.ibans['BEN-01'], 'amount_eur': amount}
         if payment else {'proposal_id': 'O'+str(len(c.seen)), 'trader': sender,
                          'asset': 'ACME', 'side': 'buy', 'notional_eur': amount})
    original = message(c, sender, 'payment_proposal' if payment else 'order_proposal', p, 'compliance')
    c.on_message(original)
    proposal = {k: original[k] for k in ('message_id', 'from', 'type', 'payload')}
    visa = message(c, 'compliance', 'compliance_decision', {'approved': True, 'proposal': proposal}, 'risk')
    c.on_message(visa)
    return message(c, 'risk', 'risk_decision', {'approved': True, 'kind': 'payment' if payment else 'order',
                   'proposal': proposal, 'notional_eur': amount, 'risk_view': {'pnl_day_eur': 0}}, 'executor')


def test_legal_actions_and_signed_lease(classic):
    assert classic.request_approval(approval(classic)).approved
    assert classic.request_approval(approval(classic, payment=True)).approved
    outputs = classic.on_message(message(classic, 'clock', 'tick', {'tick': 1}, tick=1))
    assert {'heartbeat', 'light_change'} <= {m['type'] for m in outputs}
    assert all(classic.keys.verify(m) for m in outputs)
    assert next(m for m in outputs if m['type'] == 'light_change')['payload']['expiry_tick'] == 6


@pytest.mark.parametrize('change', ['unsigned', 'unknown', 'tampered', 'wrong_sender'])
def test_authentication(classic, change):
    m = approval(classic)
    if change == 'unsigned': m['signature'] = None
    if change == 'unknown': m['from'] = 'pirate'
    if change == 'tampered': m['payload']['notional_eur'] = 1
    if change == 'wrong_sender':
        m['from'] = 'payment'
        classic.keys.sign(m)
    assert not classic.request_approval(m).approved


def test_order_ceiling(classic):
    assert classic.request_approval(approval(classic, 5000000)).approved
    assert classic.request_approval(approval(classic, 5000001)).reason == 'ORDER_LIMIT'


def test_daily_payment_ceiling(classic):
    for _ in range(10):
        assert classic.request_approval(approval(classic, 1000000, payment=True)).approved
    assert classic.request_approval(approval(classic, 1, payment=True)).reason == 'DAY_LIMIT'
    classic.on_message(message(classic, 'clock', 'tick', {'tick': 300}, tick=300))
    assert classic.request_approval(approval(classic, 1, payment=True)).approved


def test_transfer_ceiling(classic):
    assert classic.request_approval(approval(classic, 1000001, payment=True)).reason == 'TRANSFER_LIMIT'


@pytest.mark.parametrize('kwargs', [{'iban':'inconnu'}, {'beneficiary':'pirate'}])
def test_iban_beneficiary_whitelist(classic, kwargs):
    assert classic.request_approval(approval(classic, payment=True, **kwargs)).reason == 'IBAN_UNKNOWN'


def test_loss_breaker_is_bank_wide_and_human_only(classic):
    classic.on_message(message(classic, 'risk', 'heartbeat', {'view': {'pnl_day_eur': -2000001}}))
    assert classic.red
    assert not classic.request_approval(approval(classic)).approved
    assert not classic.request_approval(approval(classic, payment=True)).approved
    classic.on_message(message(classic, 'payment', 'human_decision', {'action':'resume'}))
    assert classic.red
    m = message(classic, 'human', 'human_decision', {'action':'resume'})
    invalid = copy.deepcopy(m); invalid['signature'] = ''
    classic.on_message(invalid); assert classic.red
    classic.on_message(m); assert not classic.red


def test_receipt_iban_mismatch_stops_bank(classic):
    classic.on_message(message(classic, 'executor', 'execution', {
        'kind':'payment','status':'executed','beneficiary_id':'BEN-01','iban_credited':'inconnu'}))
    assert classic.red
    assert classic.incidents[-1]['reason'] == 'IBAN_RECEIPT'


def test_missing_stale_and_replayed_requests(classic):
    m = approval(classic)
    assert classic.request_approval(m).approved
    assert classic.request_approval(m).reason == 'REPLAY'
    m = approval(classic); m['payload'].pop('risk_view'); classic.keys.sign(m)
    assert classic.request_approval(m).reason == 'MISSING_RISK_VIEW'
    classic.last_clock -= classic.wall_lease+1
    assert classic.request_approval(approval(classic)).reason == 'STALE'


def test_signed_claim_cannot_replace_original(classic):
    m = approval(classic)
    m['payload']['proposal']['payload']['trader'] = 'trader-2'
    classic.keys.sign(m)
    assert classic.request_approval(m).reason == 'IDENTITY_MISMATCH'


def test_no_cumulative_trading_or_targeted_quarantine(classic):
    for _ in range(3):
        assert classic.request_approval(approval(classic, 4900000)).approved
    assert not classic.red


def test_untrusted_hints_do_not_change_decision(classic):
    # Seule la signature inclut les champs opaques du protocole, sans les interpréter.
    for m in [message(classic,'svc:core-ledger','service_status', {'mode':'compromised'}),
              message(classic,'world','heartbeat', {'tainted':True,'altered':True,'state':{'pnl':-1e12}})]:
        classic.on_message(m)
    assert classic.request_approval(approval(classic)).approved
    assert not classic.incidents


def test_module_imports_only_team_contract_and_stdlib():
    source = Path('vigie/classique.py').read_text()
    tree = ast.parse(source)
    allowed = {'__future__','argparse','copy','json','math','os','time','collections','pathlib',
               'bus.gate','bus.message','bus.redis_bus','sim.config','sim.launch','yaml'}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom): assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(n.name in allowed for n in node.names)
    forbidden = {'state','tainted','altered','svc_modes','compromised'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value,str):
            assert node.value not in forbidden
            assert not node.value.startswith('world:')


class OnlyBus:
    def __init__(self):
        self.fake = fakeredis.FakeRedis(decode_responses=True)
        self.calls = []
    def pubsub(self, **kw):
        sub = self.fake.pubsub(**kw)
        parent = self
        class Subscription:
            def subscribe(self, *channels):
                assert set(channels) == {'T:bus','T:vigie.approval_requests'}
                parent.calls.append(('subscribe',channels))
                return sub.subscribe(*channels)
            def close(self): return sub.close()
        return Subscription()
    def set(self,key,value):
        assert key in {'T:agent:vigie:pid','T:vigie:status'}
        self.calls.append(('set',key)); return self.fake.set(key,value)
    def publish(self,key,value):
        assert key in {'T:bus','T:vigie.approval_responses'}
        self.calls.append(('publish',key)); return self.fake.publish(key,value)
    def __getattr__(self,name):
        raise AssertionError('Redis hors liste blanche : '+name)


def test_redis_whitelist_and_bus_protocol(classic):
    client = OnlyBus(); port = RedisPort(client,'T:'); sub = port.open()
    responses = client.fake.pubsub(ignore_subscribe_messages=True)
    responses.subscribe('T:vigie.approval_responses'); responses.get_message(timeout=0)
    m = approval(classic)
    handle(port,classic,{'channel':'T:vigie.approval_requests','data':json.dumps({'request_id':m['message_id'],'message':m})})
    raw = responses.get_message(timeout=0)
    assert raw and json.loads(raw['data'])['approved'] is True
    m = approval(classic); m['signature'] = None
    handle(port,classic,{'channel':'T:vigie.approval_requests','data':json.dumps({'request_id':m['message_id'],'message':m})})
    assert json.loads(responses.get_message(timeout=0)['data'])['approved'] is False
    for bad in ['null','[]','{', '"texte"']:
        handle(port,classic,{'channel':'T:bus','data':bad})
    sub.close(); responses.close()


def test_loss_red_is_published_immediately(classic):
    out = classic.on_message(message(classic,'risk','heartbeat',{'view':{'pnl_day_eur':-2000001}}))
    assert any(m['type']=='light_change' and m['payload']['light']=='ROUGE' for m in out)
    assert any(m['type']=='alert' and m['to']=='human' for m in out)


def test_no_vigie_after_five_ticks_keeps_team_watchdog(cfg):
    from sim.lockstep import Lockstep
    ls = Lockstep(cfg,no_llm=True)
    c = Classique(cfg,ls.keyring)
    ls.vigie = c
    ls.gate = ls.world.gate = ls.world.executor.gate = c
    ls.run(3)
    ls.killed.add('vigie')
    ls.run(7)
    assert all(a.light=='ORANGE' for a in ls.agents.values())
    ls.close()


def test_busgate_redis_response_without_network(classic):
    import threading
    from bus.gate import BusGate
    client = fakeredis.FakeRedis(decode_responses=True)
    port = RedisPort(client,'T:'); sub = port.open()
    sub.get_message(timeout=0)
    sub.get_message(timeout=0)
    gate = BusGate(client,'T:',timeout_s=.5,default_on_timeout='reject')
    result = []
    m = approval(classic)
    thread = threading.Thread(target=lambda: result.append(gate.request_approval(m)))
    thread.start()
    raw = sub.get_message(timeout=.4)
    assert raw is not None
    handle(port,classic,raw)
    thread.join(timeout=1)
    assert result and result[0].approved
    assert gate.request_approval(approval(classic)).approved is False
    sub.close()
