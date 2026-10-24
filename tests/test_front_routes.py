"""Les adresses d'Argus : quatre espaces servis sans réseau, les anciennes adresses redirigées, une liste fermée."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from bus.message import Keyring
from sim.config import known_senders, load_config
from vigie.demo_security import protect_mutations
from vigie.krys import Knowledge
from vigie.krys_api import attach_krys

ROOT = Path(__file__).resolve().parents[1]
SPACES = ['/', '/simulation', '/console', '/regulation']
PAGES = ['vigie/landing.html', 'vigie/demo_page.html', 'vigie/dashboard.html', 'vigie/regulation.html']


def client():
    app = create_app({})
    protect_mutations(app, ['http://127.0.0.1:8010'])
    keys = Keyring(known_senders(load_config()))
    attach_krys(app, lambda: Knowledge(keys.verify))
    return TestClient(app)


def resources(html):
    """Ce que la page charge d'elle-même : scripts, feuilles, icône et l'importmap de la 3D."""
    found = re.findall(r'<(?:script|link)\b[^>]*?\b(?:src|href)="([^"]+)"', html)
    for block in re.findall(r'<script type="importmap">(.*?)</script>', html, re.S):
        found += json.loads(block)['imports'].values()
    return [url for url in found if not url.startswith('data:')]


def test_the_four_spaces_load_only_local_resources():
    c = client()
    for space in SPACES:
        page = c.get(space)
        assert page.status_code == 200 and page.headers['content-type'].startswith('text/html'), space
        for url in resources(page.text):
            assert url.startswith('/') and not url.startswith('//'), (space, url)
            if not url.endswith('/'):
                assert c.get(url).status_code == 200, (space, url)


def test_the_3d_and_its_addons_come_from_the_repository():
    c = client()
    html = c.get('/simulation').text
    imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1))['imports']
    core = c.get(imports['three'])
    assert core.status_code == 200 and core.headers['content-type'].startswith('text/javascript')
    # three.module.js importe son cœur à côté de lui
    assert "from './three.core.js'" in core.text
    assert "REVISION = '186'" in c.get(imports['three'].replace('three.module.js', 'three.core.js')).text
    addons = re.findall(r'from "three/addons/([^"]+)"', (ROOT/'vigie/simulation.js').read_text())
    assert len(addons) == 4
    for addon in addons:
        assert c.get(imports['three/addons/'] + addon).status_code == 200, addon
    assert 'cdn.jsdelivr' not in (ROOT/'vigie/dashboard.html').read_text()


def test_old_addresses_redirect_to_their_space_with_their_query():
    c = client()
    for old, new in [('/dashboard', '/console'),
                     ('/dashboard?details_message_id=m-1&details_mode=live', '/console?details_message_id=m-1&details_mode=live'),
                     ('/dashboard?embed=simulation', '/simulation?embed=simulation'),
                     ('/demo/page', '/simulation')]:
        reply = c.get(old, follow_redirects=False)
        assert reply.status_code == 307 and reply.headers['location'] == new, old
    assert c.get('/dashboard').text == (ROOT/'vigie/dashboard.html').read_text()
    assert 'id="simulation"' in c.get('/demo/page').text


def test_shared_files_are_a_closed_list():
    c = client()
    for name, kind in [('tokens.css', 'text/css'), ('simulation.css', 'text/css'), ('helper.js', 'text/javascript'),
                       ('helper.css', 'text/css'), ('fonts/Geist-Regular.woff2', 'font/woff2'),
                       ('fonts/GeistMono-Medium.woff2', 'font/woff2'), ('three/three.module.js', 'text/javascript')]:
        reply = c.get('/console/static/' + name)
        assert reply.status_code == 200 and reply.headers['content-type'].startswith(kind), name
        assert reply.headers['x-content-type-options'] == 'nosniff'
    for name in ['secret.css', 'static.py', '../static.py', '%2e%2e/static.py', 'three/../../static.py', 'fonts/',
                 'three/LICENSE', 'fonts/OFL.txt', 'three/addons/controls/MapControls.js']:
        assert c.get('/console/static/' + name).status_code == 404, name


def test_the_charter_is_defined_once_and_served_offline():
    tokens = (ROOT/'vigie/console/static/tokens.css').read_text()
    assert '--bg:' in tokens and tokens.count('@font-face') == 4 and 'http' not in tokens
    for font in re.findall(r'url\("([^"]+)"\)', tokens):
        assert client().get(font).status_code == 200, font
    for rel in PAGES + ['vigie/console/accueil.css']:
        text = (ROOT/rel).read_text()
        assert '--bg:' not in text, rel
        assert 'fonts.googleapis' not in text and 'cdn.jsdelivr' not in text, rel


def test_the_console_sends_the_anchors_that_left_to_their_space():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node absent : essai des ancres non joué')
    result = subprocess.run([node, str(ROOT/'tests/console_anchors_dom.cjs'), str(ROOT/'vigie/dashboard.html')],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_console_has_five_views_and_each_former_tab_lands_in_one():
    html = (ROOT/'vigie/dashboard.html').read_text()
    views = re.findall(r'<a href="#([a-z]+)" data-tab="\1"', html)
    assert views == ['krys', 'alertes', 'synthese', 'parc', 'gouvernance', 'tableau']
    sections = {m.group(1): m.group(2) for m in re.finditer(r'<section id="([a-z-]+)"[^>]*>(.*?)</section>', html, re.S)}
    assert set(views) <= set(sections) and 'hidden' not in re.search(r'<section id="krys"[^>]*>', html).group(0)
    moved = dict(re.findall(r'(\w+): "(\w+)"', re.search(r'moved: \{(.*?)\}', html).group(1)))
    assert set(moved) == {'summary', 'actions', 'agents', 'guardrails', 'improve', 'metrics', 'impact', 'details'}
    for block, view in moved.items():
        assert f'<div id="{block}"' in sections[view], (block, view)
    # the chat of the former Krys page, once, in the Krys view; the console loads its client once
    for element in ['krys-log', 'krys-question-form', 'krys-question', 'krys-action-form', 'krys-confirm', 'krys-recap', 'url']:
        assert html.count(f'id="{element}"') == 1 and f'id="{element}"' in sections['krys'], element
    assert html.count('<script src="/demo/krys.js"></script>') == 1
    assert '/demo/krys.js' not in re.sub(r'/\*.*?\*/', '', (ROOT/'vigie/console/mails-chat.js').read_text(), flags=re.S)


def test_the_console_reads_the_krys_bank_and_says_live_or_replay():
    code = (ROOT/'vigie/dashboard.js').read_text()
    for route in ('/state', '/vigie', '/graph'):
        assert f'getJson("{route}" + WORLD)' in code, route
    assert 'WORLD = h?.worlds?.includes("C") ? "?world=C" : ""' in code and 'getJson("/health")' in code
    html = (ROOT/'vigie/dashboard.html').read_text()
    assert '<a href="/console" aria-current="page">Console</a>' in html and '<a href="/sentinel">Sentinel</a>' in html
    assert 'id="mode"' in html and 'id="light"' in html and 'id="waiting"' in html


def test_regulation_serves_compliance_and_sentinel_in_argus_colours():
    c = client()
    page = c.get('/regulation').text
    assert '/console/conformite/conformite.js' in page and 'data-src="/pages/sentinel"' in page
    for resource in ('/console/conformite/conformite.js', '/console/conformite/data/conformite.json', '/console/static/sentinel.css'):
        assert c.get(resource).status_code == 200, resource
    sentinel = c.get('/pages/sentinel')
    assert sentinel.status_code == 200 and sentinel.headers['content-type'].startswith('text/html')
    assert 'fonts.googleapis' not in sentinel.text and 'fonts.gstatic' not in sentinel.text
    assert sentinel.text.count('/console/static/sentinel.css') == 1
    assert sentinel.text.index('</style>') < sentinel.text.index('/console/static/sentinel.css')
    # the colleague's file itself does not change
    assert 'fonts.googleapis' in (ROOT/'vendor/sentinel/docs/sentinel-demo.html').read_text()
    assert c.get('/pages/other').status_code == 404


def test_the_pitch_page_leads_to_the_new_spaces_only():
    html = (ROOT/'vigie/landing.html').read_text()
    links = re.findall(r'(?:href|src)="(/[^"]*)"', html)
    assert '/simulation?embed=simulation' in links and '/console' in links and '/simulation' in links
    for url in links:
        assert not re.match(r'/(dashboard|argus|demo/page)(?![/\w])', url), url
    assert 'localhost:8000' not in html


def test_the_dashboard_reads_only_published_measures():
    c = client()
    code = (ROOT/'vigie/console/tableau.js').read_text()
    reads = re.findall(r"get\((?:IMPACT \+ )?'([^']+)'\)", code)
    assert len(reads) == 7
    for name in reads:
        url = name if name.startswith('/') else '/dashboard/console/impact/' + name
        assert c.get(url).status_code == 200, url
    assert c.get('/console/static/tableau.js').status_code == 200 and c.get('/console/static/tableau.css').status_code == 200
    # every value reaches the page through esc(): no other HTML sink than the pages it writes
    assert 'innerHTML = PAGES[page]()' in code and code.count('.innerHTML') == 5
