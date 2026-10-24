"""La présentation réglementaire ne transforme pas une preuve technique en agrément."""
import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_conformite_assets_and_proofs_are_read_only_and_whitelisted(cfg, tmp_path):
    from vigie.demo_live import Display, create_demo_app
    app = create_demo_app(Display(), {}, allowed_origins=['http://127.0.0.1:8010'])
    with TestClient(app) as client:
        for name in ('conformite.js', 'conformite.css', 'data/conformite.json', 'data/conformite-fixed-comparison.json'):
            assert client.get('/console/conformite/' + name).status_code == 200
        assert client.get('/console/conformite/preuves/journal').status_code == 200
        assert client.get('/console/conformite/preuves/no-such-proof').status_code == 404
        assert client.get('/console/conformite/data/config.yaml').status_code == 404
        assert client.get('/console/conformite/%2e%2e/config.yaml').status_code == 404
        assert client.post('/demo/krys/ask', json={'question': 'Brouillon DORA'}).status_code == 403
        headers = {'Origin': 'http://127.0.0.1:8010'}
        result = client.post('/demo/krys/ask', headers=headers, json={'question': 'Brouillon DORA'})
        assert result.status_code == 200
        assert result.json()['outcome'] == 'no_source'


def test_conformite_matrix_has_scope_limits_baseline_and_exact_articles():
    data = json.loads((ROOT/'vigie/console/data/conformite.json').read_text())
    assert data['measurement']['commit'] == 'cf4973abf1e3706be5ef99bf41c450bc9315b136'
    assert data['measurement']['delivery_commit'].startswith('48a980a')
    assert '2027' in data['calendar'] and 'non confirmé au JO' in data['calendar']
    by_id = {item['id']: item for item in data['items']}
    for item in by_id.values():
        assert item['status'] in {'couvert', 'partiel', 'hors-perimetre'}
        assert all(item[k] for k in ('limite', 'classiques', 'preuve', 'article', 'fait'))
        assert item['source'].startswith('https://eur-lex.europa.eu/') or item['id'] == 'assurance'
    assert '17' in by_id['dora-journal']['article']
    assert '10' in by_id['dora-detection']['article']
    assert '19' in by_id['dora-notification']['article']
    assert by_id['dora-notification']['status'] == 'partiel'
    for article in ('12', '14', '26'):
        assert article in by_id['ai-'+article]['article']
    assert by_id['ai-26']['status'] == 'partiel'
    assert by_id['assurance']['status'] == 'partiel'
    assert by_id['rts-coupe']['status'] == 'partiel'
    assert any(item['status'] == 'hors-perimetre' for item in by_id.values())


def test_conformite_dom_xss_badges_draft_and_minimal_connections():
    result = subprocess.run(['node', str(ROOT/'tests/console_conformite_dom.cjs'),
                             str(ROOT/'vigie/console/conformite.js')], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    # Édouard a retiré l'onglet Conformité et ses badges au survol (24/10) : le module reste disponible pour la page
    # Régulation de l'équipe, mais aucune page de la console ni de la démo ne le charge plus.
    page = (ROOT/'vigie/dashboard.html').read_text()
    assert 'data-tab="conformite"' not in page and 'id="conformite"' not in page
    assert '/console/conformite/conformite.js' not in page
    demo = (ROOT/'vigie/demo_page.html').read_text()
    assert 'id="conformite-resume"' not in demo and '/console/conformite/conformite.js' not in demo


def test_dora_button_question_reuses_signed_chat_draft_without_human_command():
    from bus.message import Keyring, MessageFactory
    from vigie.krys import Knowledge
    from vigie.demo_live import Display, create_demo_app
    keys = Keyring(['vigie'])
    book = Knowledge(keys.verify)
    status = MessageFactory('vigie', keys).make(4, 'alert', 'human', {'kind': 'krys_status',
        'frozen': [], 'suspects': [], 'r0': 0, 'uncertain': [], 'propositions': []})
    book.accept(status)
    app = create_demo_app(Display(), {}, allowed_origins=['http://127.0.0.1:8010'], krys_knowledge=lambda: book)
    with TestClient(app) as client:
        headers = {'Origin': 'http://127.0.0.1:8010'}
        response = client.post('/demo/krys/ask', headers=headers, json={'question': 'Brouillon DORA'})
        assert response.status_code == 200
        answer = response.json()
        assert answer['text'].startswith('BROUILLON À VALIDER PAR UN HUMAIN')
        assert 'Ce brouillon ne vaut pas déclaration' in answer['text']
        assert answer['citations'][0]['message_id'] == status['message_id']
        assert client.post('/demo/krys/actions/prepare', headers=headers,
                           json={'action': 'resume', 'target': '*'}).status_code == 403
        assert len(book.messages) == 1


def test_published_measurement_bytes_and_origin_are_preserved():
    import hashlib
    data = json.loads((ROOT/'vigie/console/data/conformite.json').read_text())
    copied = (ROOT/'vigie/console/data/conformite-fixed-comparison.json').read_bytes()
    assert hashlib.sha256(copied).hexdigest() == data['measurement']['sha256']
    figures = json.loads(copied)
    assert figures['A']['contaminated_executed'] == 2025
    assert figures['B']['contaminated_executed'] == 1591
    assert figures['C']['contaminated_executed'] == 448
