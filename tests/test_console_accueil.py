"""Accueil Argus : ressources locales, raccord console et garde existant."""
import re
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from bus.message import Keyring
from sim.config import known_senders, load_config
from vigie.demo_security import protect_mutations
from vigie.krys import Knowledge
from vigie.krys_api import attach_krys

ROOT = Path(__file__).resolve().parents[1]


def client_app():
    app = create_app({})
    protect_mutations(app, ['http://127.0.0.1:8010'])
    keys = Keyring(known_senders(load_config()))
    attach_krys(app, lambda: Knowledge(keys.verify))
    return TestClient(app), app


def test_accueil_route_local_resources_and_console_anchors():
    client, _ = client_app()
    response = client.get('/argus')
    assert response.status_code == 200
    html = response.text
    assert 'Voit. Isole. Prouve.' in html
    assert 'En attente des premières preuves signées' in html
    for resource in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert not resource.startswith(('http:', 'https:', '//'))
        if resource.startswith('/dashboard#'):
            anchor = resource.split('#')[1]
            dashboard = client.get('/dashboard').text
            assert f'id="{anchor}"' in dashboard
            assert f'data-tab="{anchor}"' in dashboard
        elif resource.startswith('/'):
            assert client.get(resource).status_code == 200, resource
    assert client.get('/argus/../api/app.py').status_code == 404
    assert client.get('/argus/secret.js').status_code == 404
    demo = client.get('/demo/page').text
    assert 'href="/argus"' in demo


def test_accueil_chat_empty_proofs_and_csrf():
    client, app = client_app()
    endpoints = [('ask', {'question': 'Quels actifs sont gelés ?'}),
                 ('actions/prepare', {'action': 'unfreeze', 'target': 'ACME'}),
                 ('actions/confirm', {'action_id': 'x', 'confirmation': 'confirmer'})]
    for endpoint, body in endpoints:
        assert client.post('/demo/krys/' + endpoint, json=body).status_code == 403
    headers = {'Origin': 'http://127.0.0.1:8010', 'X-Demo-Token': app.state.mutation_token}
    answer = client.post('/demo/krys/ask', headers=headers, json=endpoints[0][1])
    assert answer.status_code == 200
    assert answer.json()['citations'] == []
    headers['Origin'] = 'https://foreign.invalid'
    assert client.post('/demo/krys/ask', headers=headers, json=endpoints[0][1]).status_code == 403


def test_accueil_reuses_xss_safe_chat_dom():
    assert shutil.which('node'), 'Node requis pour vérifier le DOM du chat'
    html = (ROOT / 'vigie/argus.html').read_text()
    for element in ['krys-log', 'krys-confirm', 'krys-recap', 'krys-token', 'krys-question-form',
                    'krys-question', 'krys-action-form', 'krys-action', 'krys-target', 'url']:
        assert f'id="{element}"' in html
    assert '<script src="/demo/krys.js"></script>' in html
    subprocess.run(['node', str(ROOT / 'tests/krys_dom.cjs'), str(ROOT / 'vigie/krys_chat.js')], check=True)


def test_accueil_suggestions_token_mobile_and_impact_fallback():
    subprocess.run(['node', str(ROOT / 'tests/accueil_dom.cjs'),
                    str(ROOT / 'vigie/console/accueil.js')], check=True)
