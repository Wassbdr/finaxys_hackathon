"""Contre-épreuves des indicateurs, sans accès réseau ni vérité du simulateur."""

import copy
import json

import pytest

from bus.message import Keyring, MessageFactory
from sim.config import agent_ids, known_senders
from vigie.analytics import Analytics, ContagionR0, run
from vigie.herding import Finding, Herding, effective_agents, text_similarity


def test_effective_agents():
    assert effective_agents([[1, -1, 1, -1], [1, 1, -1, -1]]) == pytest.approx(2)
    assert effective_agents([[1, -1], [1, -1], [-1, 1]]) == pytest.approx(1)
    assert effective_agents([[0, 0], [1, 1]]) is None


def test_justifications():
    assert text_similarity([('a', 'vendre actif risque'), ('b', 'vendre actif risque')]) == pytest.approx(1)
    assert text_similarity([('a', 'vendre actif'), ('b', 'acheter obligation')]) == 0
    assert text_similarity([('a', 'même auteur'), ('a', 'même auteur')]) is None


def test_reference_and_window():
    h = Herding(['a', 'b'], window=5, clean_reference=.5)
    for author in ['a', 'b']:
        h.observe({'message_id': author+'1', 'tick': 1, 'from': author, 'type': 'order_proposal', 'payload': {'asset': 'X', 'side': 'sell'}})
    assert h.snapshot(1)['herding_excess'] == .5
    assert h.snapshot(6)['herding_index'] is None


def test_reflexive_loop():
    h = Herding(['a'])
    def price(t, value):
        return {'tick': t, 'from': 'quant', 'message_id': f'q{t}', 'type': 'price_update', 'payload': {'assets': {'X': {'price': value}}}}
    h.observe(price(0, 100))
    sale = {'message_id': 'sale1', 'tick': 1, 'type': 'execution', 'payload': {'kind': 'order', 'status': 'executed', 'asset': 'X', 'qty': -1}}
    h.observe(sale)
    h.observe(price(2, 90))
    order = {'message_id': 'order2', 'tick': 2, 'from': 'a', 'type': 'order_proposal', 'payload': {'asset': 'X', 'side': 'sell'}, 'depends_on': ['q2']}
    h.observe(order)
    assert h.snapshot(2)['reflexive_loops'] == 1
    loop = next(f for f in h.findings(2) if f.kind == 'reflexive_loops')
    assert set(loop.evidence) == {'q0', 'sale1', 'q2', 'order2'}
    order['depends_on'] = []
    assert h.snapshot(2)['reflexive_loops'] == 0


def test_pdg_contagion(cfg):
    keys = Keyring(known_senders(cfg))
    pdg = ContagionR0(cfg['services'], agent_ids(cfg))
    def msg(sender, kind, payload, deps=(), provenance=()):
        return MessageFactory(sender, keys).make(1, kind, '*', payload, list(deps), list(provenance))
    cve = {'affects': {'language': 'php', 'version_lt': '8.1'}}
    pdg.observe(msg('secu', 'alert', {'kind': 'cve_published', 'cve': cve}))
    provenance = [{'service': 'market-feed', 'version': '3.2.0', 'response_hash': 'abc'}]
    root = msg('quant', 'price_update', {}, provenance=provenance)
    pdg.observe(root)
    first = msg('trader-1', 'order_proposal', {}, [root['message_id']], provenance)
    second = msg('trader-2', 'order_proposal', {}, [first['message_id']], provenance)
    pdg.observe(first)
    pdg.observe(second)
    result = pdg.snapshot(1, 1.5)
    assert result['contagion_r0_estimate'] == 2
    assert set(result) == {'effective_agents', 'contagion_r0_estimate', 'suspected_source_messages', 'window_ticks'}
    assert not pdg.vulnerable({**provenance[0], 'version': 'patched'})
    other = ContagionR0(cfg['services'], agent_ids(cfg))
    other.cves = [{'affects': {'language': 'python', 'version_lt': '99'}}]
    assert not other.vulnerable(provenance[0])
    assert pdg.snapshot(301, None)['contagion_r0_estimate'] is None


def test_anomaly_provenance_without_cve(cfg):
    pdg = ContagionR0(cfg['services'], agent_ids(cfg))
    root = {'tick': 1, 'message_id': 'root', 'from': 'quant', 'type': 'price_update', 'payload': {}}
    pdg.observe(root)
    pdg.observe({'tick': 2, 'message_id': 'alert', 'from': 'vigie', 'type': 'alert', 'payload': {'kind': 'data_anomaly', 'message_ids': ['root']}})
    pdg.observe({'tick': 3, 'message_id': 'child', 'from': 'trader-1', 'type': 'heartbeat', 'payload': {}, 'depends_on': ['root']})
    assert pdg.snapshot(3, None)['contagion_r0_estimate'] == 1


def test_bus_only_and_signed(cfg):
    keys = Keyring(known_senders(cfg))
    engine = Analytics(cfg, keys)
    tick = MessageFactory('clock', keys).make(1, 'tick', '*')
    forged = copy.deepcopy(tick)
    forged['tick'] = 2
    assert not engine.observe(forged)
    class BusOnly:
        def listen(self):
            yield tick
            yield tick
        def publish_raw(self, channel, data):
            assert channel == 'vigie:analytics'
            self.result = data
        def __getattr__(self, name):
            raise AssertionError(f'Accès interdit hors bus : {name}')
    bus = BusOnly()
    run(bus, engine)
    assert bus.result['tick'] == 1
    assert bus.result['contagion']['contagion_r0_estimate'] is None
    # Le consommateur n'a aucun store, client HTTP ou objet de simulation.
    assert set(vars(engine)) == {'keyring', 'herding', 'contagion', 'tick', 'seen'}


def measure_scenario(cfg, herding):
    from sim.lockstep import Lockstep
    from sim.inject import load_scenario, schedule_scenario
    scenario = load_scenario('scenarios/rumeur_mimetisme.yaml')
    scenario['params']['herding'] = herding
    ls = Lockstep(cfg, no_llm=True, no_vigie=True)
    engine = Analytics(cfg, ls.keyring)
    ls.bus.tap(engine.observe)
    ticks = schedule_scenario(ls, scenario)
    snapshots = []
    try:
        for _ in range(ticks):
            ls.step()
            snapshots.append(engine.snapshot())
    finally:
        ls.close()
    usable = [s['herding'] for s in snapshots if s['tick'] >= 30 and s['herding']['effective_agents'] is not None and s['herding']['herding_index'] is not None]
    return {key: sum(s[key] for s in usable)/len(usable) for key in ['herding_index', 'effective_agents']}


def test_rumour_acceptance(cfg):
    clean = measure_scenario(cfg, 0)
    herd = measure_scenario(cfg, .8)
    print(json.dumps({'herding_0': clean, 'herding_08': herd}))
    assert herd['herding_index'] > clean['herding_index'] + .05
    # Une corrélation négative est aussi une dépendance : un profil value
    # opposé au momentum peut réduire le rang du contrôle sans mimétisme.
    assert abs(herd['effective_agents'] - clean['effective_agents']) > .15


def test_no_forbidden_fields_read(cfg):
    forbidden = {'state', 'world:tick', 'tainted', 'altered', 'data_altered',
                 'svc_modes', 'contaminated_agents', 'losses_by_service_eur',
                 'risk_view_vs_truth', 'trapped_operations', 'compromised'}
    class PublicOnly(dict):
        def __getitem__(self, key):
            assert key not in forbidden, f'Lecture interdite : {key}'
            return super().__getitem__(key)
        def get(self, key, default=None):
            assert key not in forbidden, f'Lecture interdite : {key}'
            return super().get(key, default)
    keys = Keyring(known_senders(cfg))
    engine = Analytics(cfg, keys)
    factory = MessageFactory('trader-1', keys)
    for kind, payload in [
        ('order_proposal', {'asset': 'X', 'side': 'sell', 'notional_eur': 100}),
        ('llm_trace', {'response': {'justification': 'risque de baisse'}}),
        ('alert', {'kind': 'data_anomaly', 'message_ids': []}),
    ]:
        message = factory.make(1, kind, '*', payload)
        # Les champs cachés existent mais toute tentative de lecture échoue.
        message['payload'] = PublicOnly({**payload, **dict.fromkeys(forbidden, 'interdit')})
        keys.sign(message)
        assert engine.observe(message)
    assert engine.snapshot()['herding']['decision_samples'] == 1


def test_finding_contract_and_evidence():
    from dataclasses import asdict, fields
    assert [field.name for field in fields(Finding)] == [
        'tick', 'layer', 'kind', 'target', 'severity', 'evidence', 'score']
    h = Herding(['a', 'b'], clean_reference=.5)
    assert h.findings(0) == []
    messages = []
    for tick, side in [(1, 'buy'), (2, 'sell')]:
        for author in ['a', 'b']:
            messages.append({'message_id': f'{author}{tick}', 'tick': tick, 'from': author,
                             'type': 'order_proposal', 'payload': {'asset': 'X', 'side': side}})
    for author in ['a', 'b']:
        messages.append({'message_id': f'{author}-trace', 'tick': 2, 'from': author,
                         'type': 'llm_trace', 'payload': {'response': {'justification': 'risque commun'}}})
    for message in messages:
        h.observe(message)
    findings = h.findings(2)
    # Les ordres opposés se compensent : l'indice directionnel est inconnu,
    # mais la corrélation et les justifications gardent leurs preuves propres.
    assert {f.kind for f in findings} == {'effective_agents', 'llm_similarity'}
    for finding in findings:
        assert finding.tick == 2 and finding.layer == 3
        assert finding.target == 'traders' and finding.severity == 'info'
        assert finding.score == pytest.approx(1)
        expected = {m['message_id'] for m in messages if
                    m['type'] == ('llm_trace' if finding.kind == 'llm_similarity' else 'order_proposal')}
        assert set(finding.evidence) == expected
        json.dumps(asdict(finding))
    assert h.findings(32) == []
    h = Herding(['a', 'b'], clean_reference=.5)
    for message in messages[:2]:
        h.observe(message)
    scores = {f.kind: f.score for f in h.findings(1)}
    assert scores['herding_index'] == 1 and scores['herding_excess'] == .5
