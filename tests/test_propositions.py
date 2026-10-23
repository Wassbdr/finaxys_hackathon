"""Chaque recommandation doit disposer de sa raison observable et de sa preuve."""

import copy
from dataclasses import asdict
import json

import pytest

from bus.message import Keyring, MessageFactory
from journal.writer import JournalWriter
from sim.config import known_senders
from vigie.propositions import Propositions, from_journal, main


@pytest.fixture
def setup(cfg):
    keys = Keyring(known_senders(cfg))
    meta = {'market-feed': {'version': '3.2.0', 'language': 'php', 'php_version': '7.4', 'patchable': False}}
    return Propositions(keys, meta), {s: MessageFactory(s, keys) for s in known_senders(cfg)}, keys


def sources(*names, tick=0, content=True):
    return [{'service': name, 'version': '3.2.0', 'response_hash': 'hash-'+name, 'tick': tick,
             **({'response': {'price': 100}} if content else {})} for name in names]


def emit(setup, sender='trader-1', kind='order_proposal', payload=None, *, tick=0, prov=None, parents=()):
    engine, factories, _ = setup
    message = factories[sender].make(tick, kind, '*', payload or {}, list(parents), prov or [])
    assert engine.observe(message)
    return message


def codes(engine):
    return {p.code for p in engine.proposals()}


def test_single_source_requires_a_decision(setup):
    engine, _, _ = setup
    feed = emit(setup, 'quant', 'price_update', {'assets': {'X': {'price': 100}}}, prov=sources('market-feed'))
    assert 'single_source' not in codes(engine)
    action = emit(setup, parents=[feed['message_id']], prov=sources('market-feed', 'news-wire'))
    assert 'single_source' in codes(engine)
    p = next(p for p in engine.proposals() if p.code == 'single_source')
    assert {action['message_id'], feed['message_id']} <= set(p.evidence)
    # Deux sources de prix explicites lèvent ce motif, sans changer l'action.
    engine.messages[feed['message_id']]['data_provenance'] = sources('market-feed', 'feed-independent')
    assert 'single_source' not in codes(engine)


def test_vulnerable_unpatchable_chain_requires_meta_cve_and_version(setup):
    engine, _, _ = setup
    action = emit(setup, payload={'notional_eur': 120}, prov=sources('market-feed'))
    assert 'vulnerable_chain' not in codes(engine)
    cve = emit(setup, 'secu', 'alert', {'kind': 'cve_published', 'cve': {'affects': {'language': 'php', 'version_lt': '8.1'}}})
    p = next(p for p in engine.proposals() if p.code == 'vulnerable_chain')
    assert '120.00' in p.constat and cve['message_id'] in p.evidence
    # Une exécution de la même chaîne n'est pas un second volume de décision.
    emit(setup, 'executor', 'execution', {'status': 'executed'}, parents=[action['message_id']])
    assert '120.00' in next(p for p in engine.proposals() if p.code == 'vulnerable_chain').constat
    engine.meta['market-feed']['patchable'] = True
    assert 'vulnerable_chain' not in codes(engine)
    engine.meta['market-feed']['patchable'] = False
    engine.meta['market-feed']['version'] = '4.0.0'
    assert 'vulnerable_chain' not in codes(engine)
    engine.meta = {}
    assert 'vulnerable_chain' not in codes(engine)


def test_traceability_missing_and_reconstructed_chain(setup):
    engine, _, _ = setup
    action = emit(setup, parents=['missing'])
    assert 'traceability' in codes(engine)
    assert '0.0%' in next(p for p in engine.proposals() if p.code == 'traceability').constat
    root = emit(setup, 'quant', 'price_update', prov=sources('a', 'b'))
    engine.messages[action['message_id']]['depends_on'] = [root['message_id']]
    assert 'traceability' not in codes(engine)


def test_journal_observed_truncation_only(setup):
    engine, _, _ = setup
    trace = emit(setup, 'trader-1', 'llm_trace', {'prompt_excerpt': 'x'*600+'…', 'prompt_hash': 'hash'})
    assert 'audit_journal' in codes(engine)
    engine.messages[trace['message_id']]['payload']['prompt_excerpt'] = 'x'*600
    assert 'audit_journal' not in codes(engine)
    engine.messages[trace['message_id']]['payload']['prompt_excerpt'] += '…'
    engine.messages[trace['message_id']]['payload']['prompt'] = 'x'*800
    assert 'audit_journal' not in codes(engine)


def test_response_hash_only_and_vigie_decision_absence(setup):
    engine, _, _ = setup
    action = emit(setup, prov=sources('a', 'b', content=False))
    assert 'audit_journal' in codes(engine)
    engine.messages[action['message_id']]['data_provenance'] = sources('a', 'b')
    assert 'audit_journal' not in codes(engine)
    emit(setup, 'vigie', 'heartbeat')
    assert 'audit_journal' in codes(engine)
    emit(setup, 'vigie', 'alert', {'kind': 'gate_decision', 'approved': True})
    assert 'audit_journal' not in codes(engine)


def test_timestamps_cash_and_irregular_heartbeat(setup):
    engine, _, _ = setup
    heartbeat = emit(setup, 'risk', 'heartbeat', {'view': {'cash_eur': 100}}, tick=1)
    assert 'timestamps' in codes(engine)
    engine.messages[heartbeat['message_id']]['payload']['view']['as_of_tick'] = 1
    emit(setup, 'risk', 'heartbeat', tick=2)
    assert 'timestamps' not in codes(engine)
    last = emit(setup, 'risk', 'heartbeat', tick=4)
    assert 'timestamps' in codes(engine)
    engine.messages[last['message_id']]['tick'] = 3
    assert 'timestamps' not in codes(engine)


def test_stale_threshold_is_action_time_not_end_of_run(setup):
    engine, _, _ = setup
    action = emit(setup, tick=6, prov=sources('a', 'b', tick=0))
    assert 'stale_data' in codes(engine)
    engine.messages[action['message_id']]['tick'] = 5
    emit(setup, 'clock', 'tick', tick=100)
    assert 'stale_data' not in codes(engine)


def test_clean_and_human_only_outputs(setup):
    engine, _, keys = setup
    emit(setup, prov=sources('a', 'b'))
    emit(setup, 'risk', 'heartbeat', {'view': {'cash_eur': 100, 'as_of_tick': 0}})
    assert engine.proposals() == []
    assert engine.findings() == []
    assert 'aucune proposition' in engine.report()
    assert keys.verify(engine.alert())
    emit(setup, payload={'notional_eur': 10})
    before = copy.deepcopy(engine.messages)
    proposals = engine.proposals()
    assert len(proposals) <= 8
    assert [p.score for p in proposals] == sorted([p.score for p in proposals], reverse=True)
    for proposal, finding in zip(proposals, engine.findings()):
        assert proposal.constat and proposal.action and proposal.gain and proposal.cout_risque and proposal.decideur
        assert finding.layer == 3 and finding.kind == 'proposition'
        assert finding.evidence == proposal.evidence
        assert all(e in engine.messages for e in finding.evidence)
    alert = engine.alert()
    assert keys.verify(alert) and alert['to'] == 'human' and alert['type'] == 'alert'
    assert alert['payload']['decision_required'] is True
    assert engine.messages == before


def test_signed_bus_and_no_forbidden_keys(setup):
    engine, factories, keys = setup
    forbidden = {'state', 'world:tick', 'tainted', 'altered', 'data_altered', 'svc_modes',
                 'compromised', 'contaminated_agents', 'losses_by_service_eur', 'risk_view_vs_truth', 'trapped_operations'}
    class PublicOnly(dict):
        def __getitem__(self, key):
            assert key not in forbidden
            return super().__getitem__(key)
        def get(self, key, default=None):
            assert key not in forbidden
            return super().get(key, default)
    raw = factories['trader-1'].make(1, 'order_proposal', '*', {'notional_eur': 100}, data_provenance=sources('a', 'b'))
    raw['payload'] = PublicOnly({**raw['payload'], **dict.fromkeys(forbidden, 'interdit')})
    keys.sign(raw)
    assert engine.observe(raw)
    assert not engine.observe(raw)
    bad = copy.deepcopy(raw)
    bad['tick'] = 50
    assert not engine.observe(bad)
    assert engine.proposals() == []
    assert set(vars(engine)) == {'keyring', 'meta', 'max_age', 'factory', 'messages', 'tick', 'journal_check',
                                'max_messages', 'max_bytes', 'observed_bytes', 'history_complete'}


def test_journal_integrity_and_cli(setup, tmp_path):
    engine, _, keys = setup
    message = emit(setup, prov=sources('a', 'b'))
    path = tmp_path/'journal.jsonl'
    writer = JournalWriter(path)
    writer.append(message, True)
    writer.close()
    replay = from_journal(path, keys)
    assert replay.journal_check['ok'] and replay.proposals() == []
    main(['--run', str(tmp_path)])
    assert (tmp_path/'propositions.md').exists()
    alert = json.loads((tmp_path/'propositions.alert.json').read_text())
    assert keys.verify(alert)
    entry = json.loads(path.read_text())
    entry['hash'] = 'broken'
    path.write_text(json.dumps(entry)+'\n')
    replay = from_journal(path, keys)
    assert not replay.journal_check['ok']
    issue = next(p for p in replay.proposals() if p.code == 'audit_journal')
    assert issue.evidence == ['journal:ligne:1']
    path.write_text('{invalid\n')
    assert from_journal(path, keys).journal_check['ok'] is False


def test_runtime_on_demand_and_does_not_execute_actions(cfg, setup):
    from vigie.main import Runtime
    _, factories, keys = setup
    runtime = Runtime(cfg, keys)
    runtime.on_message(factories['clock'].make(1, 'tick', '*', {'tick': 1}))
    action = factories['trader-1'].make(1, 'order_proposal', '*', {'asset': 'ACME', 'side': 'sell', 'notional_eur': 100})
    runtime.on_message(action)
    runtime.on_message(factories['clock'].make(2, 'tick', '*', {'tick': 2}))
    request = factories['human'].make(2, 'human_decision', '*', {'action': 'propositions'})
    response = runtime.on_message(request)
    assert len(response) == 1 and keys.verify(response[0])
    assert response[0]['payload']['kind'] == 'propositions'
    assert all(f.kind == 'proposition' for f in runtime.propositions.findings())
    assert not runtime.decisions
    assert action["message_id"] in runtime.propositions.messages
    assert runtime.propositions.proposals()


def test_shutdown_emits_signed_summary_without_store_reads(cfg, setup):
    from vigie.main import serve
    _, _, keys = setup
    class Subscription:
        def subscribe(self, *channels):
            self.channels = list(channels)
        def get_message(self, **kwargs):
            return {'type': 'subscribe', 'channel': self.channels.pop()}
        def close(self):
            self.closed = True
    class Stop:
        def is_set(self):
            return True
    class BusClient:
        def __init__(self):
            self.subscription = Subscription()
            self.published = []
        def pubsub(self, **kwargs):
            return self.subscription
        def set(self, key, value):
            assert key in {cfg['redis']['prefix']+'agent:vigie:pid', cfg['redis']['prefix']+'vigie:ready'}
        def delete(self, key):
            assert key == cfg['redis']['prefix']+'vigie:ready'
        def publish(self, channel, data):
            assert channel == cfg['redis']['prefix']+'bus'
            self.published.append(json.loads(data))
        def __getattr__(self, key):
            raise AssertionError('Accès hors bus : '+key)
    client = BusClient()
    serve(cfg, client, stop=Stop())
    assert client.subscription.closed
    assert len(client.published) == 1
    alert = client.published[0]
    assert keys.verify(alert) and alert['to'] == 'human'
    assert alert['payload']['kind'] == 'propositions'


def test_empty_provenance_is_not_a_reconstructed_source(setup):
    engine, _, _ = setup
    emit(setup, prov=[{}])
    assert 'traceability' in codes(engine)


def test_live_propositions_do_not_admit_rejected_inputs(cfg, setup):
    from vigie.main import Runtime
    _, factories, keys = setup
    runtime = Runtime(cfg, keys)
    request = factories['human'].make(20, 'human_decision', '*', {'action': 'propositions'})
    assert runtime.on_message(request) == []
    assert request['message_id'] not in runtime.propositions.messages
    assert not runtime.freeze.uncertain


def test_live_propositions_bound_their_additional_history(cfg, setup):
    from vigie.main import Runtime
    _, factories, keys = setup
    runtime = Runtime(cfg, keys)
    engine = runtime.propositions
    engine.max_messages = 2
    engine.max_bytes = 100_000
    for tick in range(10):
        engine.observe(factories['vigie'].make(tick, 'heartbeat', '*', {'agent': 'vigie'}))
    assert len(engine.messages) == 2
    engine.max_messages = 100
    engine.max_bytes = engine.observed_bytes
    assert not engine.observe(factories['vigie'].make(11, 'heartbeat', '*', {'agent': 'vigie'}))
    assert len(engine.messages) == 2
    assert engine.alert()["payload"]["history_complete"] is False
