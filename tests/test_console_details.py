"""Les preuves consultables ne deviennent pas des ordres ou une vérité terrain."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


def client():
    class Source:
        kind = 'replay'
    return TestClient(create_app({'main': Source()}))


def test_details_routes_and_no_arbitrary_file_read():
    c = client()
    for path in ['/console/details.js', '/console/details.css', '/console/details/data/manifest.json']:
        assert c.get(path).status_code == 200
    assert c.get('/console/details/data/no-such-file').status_code == 404
    assert c.post('/console/details/provenance').status_code == 405
    # Détails est un bloc de la vue Gouvernance, et l'ancienne adresse y mène
    console = c.get('/console').text
    assert console.count('id="details"') == 1 and console.count('data-tab="gouvernance"') == 1
    assert c.get('/dashboard').text == console


def test_archive_verification_rejects_tampering(tmp_path):
    from vigie.console.details_api import archive_report, DATA
    original = DATA / 'journal.jsonl'
    report = archive_report(original)
    assert report['chain_valid'] and report['entries'] > 0
    assert report['signature_scope'] == 'recorded'
    lines = original.read_text().splitlines()
    entry = json.loads(lines[0])
    entry['message']['tick'] += 1
    lines[0] = json.dumps(entry)
    changed = tmp_path/'journal.jsonl'
    changed.write_text('\n'.join(lines)+'\n')
    assert not archive_report(changed)['chain_valid']
    changed.write_text('')
    assert not archive_report(changed)['chain_valid']


def test_provenance_follows_parents_evidence_and_reports_missing():
    from vigie.console.details_api import trace
    messages = {
        'result': {'message_id': 'result', 'from': 'executor', 'type': 'execution', 'tick': 3,
                   'depends_on': ['risk'], 'data_provenance': [],
                   'payload': {'gate': {'reason': json.dumps({'evidence': ['proof', 'absent']})}}},
        'risk': {'message_id': 'risk', 'from': 'risk', 'type': 'risk_decision', 'tick': 2,
                 'depends_on': ['result'], 'payload': {}, 'data_provenance': []},
        'proof': {'message_id': 'proof', 'from': 'vigie', 'type': 'alert', 'tick': 1,
                  'depends_on': [], 'payload': {}, 'data_provenance': []},
    }
    result = trace(messages, 'result')
    assert {m['message_id'] for m in result['messages']} == {'result', 'risk', 'proof'}
    assert result['missing'] == ['absent']
    assert not result['complete']
    assert not trace(messages, 'unknown')['complete']
    assert trace(messages, 'result', limit=1)['truncated']


def test_archive_list_and_message_lookup_are_consistent():
    c = client()
    report = c.get('/console/details/journal').json()
    assert report['chain_valid']
    assert report['decisions']
    mid = report['decisions'][0]['message_id']
    found = c.get('/console/details/provenance', params={'message_id': mid, 'mode': 'archive'}).json()
    assert found['messages'][0]['message_id'] == mid
    assert found['mode'] == 'archive'
    assert c.get('/console/details/provenance', params={'mode': 'live'}).status_code == 409


def test_live_projection_uses_only_existing_verified_knowledge():
    from vigie.krys import Knowledge
    c = client()
    book = Knowledge(lambda m: False)
    book.complete = False
    c.app.state.details_knowledge = lambda: book
    result = c.get('/console/details/provenance', params={'mode': 'live', 'message_id': 'missing'}).json()
    assert not result['complete']
    assert result['mode'] == 'live'


def test_frozen_measurements_match_manifest():
    import hashlib
    from vigie.console.details_api import DATA
    manifest = json.loads((DATA/'manifest.json').read_text())
    for name, ref in manifest['files'].items():
        assert hashlib.sha256((DATA/name).read_bytes()).hexdigest() == ref['sha256']


def test_details_dom_escapes_journal_fields_and_never_posts():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node absent : essai DOM non joué')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root/'tests/details_dom.cjs'), str(root/'vigie/console/details.js')],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_live_provider_is_wired_and_keeps_only_verified_public_messages(cfg):
    from bus.message import Keyring, MessageFactory
    from sim.config import known_senders
    from vigie.krys import Knowledge
    from vigie.krys_api import attach_krys
    from vigie.krys_mistral import Rephraser
    keys = Keyring(known_senders(cfg))
    book = Knowledge(keys.verify)
    message = MessageFactory('executor', keys).make(3, 'execution', '*',
                {'status': 'refused', 'oracle': 'must-not-be-shown'})
    assert book.accept(message)
    forged = {**message, 'message_id': 'forged', 'signature': 'invalid'}
    assert not book.accept(forged)
    c = client()
    attach_krys(c.app, lambda: book, rephraser=Rephraser())
    reply = c.get('/console/details/provenance', params={'message_id': message['message_id'], 'mode': 'live'})
    assert reply.status_code == 200
    assert reply.json()['complete']
    assert reply.json()['messages'][0]['payload'] == {'status': 'refused'}
    assert 'forged' not in reply.text
