"""La boîte part des preuves signées ; le transport des essais reste factice."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bus.message import Keyring, MessageFactory
from vigie.demo_security import protect_mutations
from vigie.krys import Knowledge
from vigie.console.mails import Mailer, drafts, attach_mailbox


@pytest.fixture
def incident():
    keys = Keyring(['vigie', 'executor', 'risk'])
    book = Knowledge(keys.verify)
    factory = MessageFactory('vigie', keys)
    for tick in (4, 5):
        book.accept(factory.make(tick, 'alert', 'human', {'finding': {
            'kind': 'published_cve', 'target': 'php2shell', 'evidence': []}}))
    risk = MessageFactory('risk', keys).make(6, 'risk_decision', 'executor', {'approved': True})
    book.accept(risk)
    execution = MessageFactory('executor', keys).make(6, 'execution', '*', {
        'status': 'refused', 'decided_by': 'vigie',
        'gate': {'reason': '{"evidence":["vigie:4:1"]}'}}, depends_on=[risk['message_id']])
    book.accept(execution)
    return book, factory


def configured(tmp_path, **overrides):
    return {'KRYS_MAIL': 'smtp', 'KRYS_MAIL_TO': 'operator@example.invalid',
            'KRYS_SMTP_FROM': 'krys@example.invalid', 'KRYS_SMTP_HOST': 'smtp.example.invalid',
            'KRYS_SMTP_USER': 'test-user', 'KRYS_SMTP_PASSWORD': 'test-fixture-only',
            'KRYS_MAIL_STATE': str(tmp_path / 'mail.sqlite'), **overrides}


def test_drafts_group_deduplicate_cite_and_never_invent_loss(incident):
    book, factory = incident
    items = drafts(book)
    assert len(items) == 1
    item = items[0]
    assert item['alerts'] == 2 and item['operations'] == 1
    assert 'vigie:4:1' in item['evidence'] and 'executor:6:1' in item['evidence']
    assert 'perte évitée' in item['limit'] and 'non mesurée' in item['comparison']
    assert item['link'] == '/dashboard#mails-chat'
    factory_message = factory.make(7, 'alert', 'human', {'finding': {
        'kind': 'published_cve', 'target': 'php2shell', 'evidence': []}})
    book.accept(factory_message)
    assert drafts(book)[0]['id'] == item['id']
    factory_message['payload']['finding']['target'] = 'forged'
    assert not book.accept(factory_message)
    assert len(drafts(book)) == 1


def test_disabled_no_transport_or_state_file(tmp_path, incident):
    calls = []
    mailer = Mailer({}, transport=calls.append)
    with pytest.raises(PermissionError):
        mailer.send(drafts(incident[0])[0])
    assert not calls and not list(tmp_path.iterdir())


def test_at_most_once_persists_and_is_concurrent(tmp_path, incident):
    calls = []
    env = configured(tmp_path)
    item = drafts(incident[0])[0]
    mailer = Mailer(env, transport=calls.append)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: mailer.send(item), range(12)))
    assert len(calls) == 1
    assert sum(o['status'] == 'sent' for o in outcomes) == 1
    assert Mailer(env, transport=calls.append).send(item)['status'] == 'reserved'
    assert len(calls) == 1
    message = calls[0]
    assert message['To'] == env['KRYS_MAIL_TO']
    assert 'message_id' in message.get_content()
    assert 'test-fixture-only' not in message.as_string()


def test_ten_per_rolling_hour_and_failure_is_not_retried(tmp_path, incident):
    calls, now = [], [4000.0]
    mailer = Mailer(configured(tmp_path), transport=calls.append, clock=lambda: now[0])
    item = drafts(incident[0])[0]
    for i in range(10):
        assert mailer.send({**item, 'id': str(i)})['status'] == 'sent'
    with pytest.raises(ValueError, match='heure'):
        mailer.send({**item, 'id': 'eleven'})
    now[0] += 3601
    assert mailer.send({**item, 'id': 'eleven'})['status'] == 'sent'
    def broken(_):
        raise OSError('réponse distante éventuellement sensible')
    fail = Mailer(configured(tmp_path), transport=broken, clock=lambda: now[0])
    with pytest.raises(ValueError, match='incertain'):
        fail.send({**item, 'id': 'timeout'})
    assert fail.send({**item, 'id': 'timeout'})['status'] == 'reserved'


@pytest.mark.parametrize('override', [
    {'KRYS_MAIL_TO': 'a@example.invalid,b@example.invalid'},
    {'KRYS_MAIL_TO': 'a@example.invalid\nBcc: b@example.invalid'},
    {'KRYS_SMTP_TLS': 'none'}, {'KRYS_SMTP_HOST': ''}, {'KRYS_SMTP_PASSWORD': ''},
])
def test_invalid_config_fails_closed(tmp_path, override):
    with pytest.raises(ValueError):
        Mailer(configured(tmp_path, **override), transport=lambda _: None)


def test_no_freeform_or_personal_payload_leaves(tmp_path, incident):
    calls = []
    book, factory = incident
    book.accept(factory.make(8, 'alert', 'human', {'finding': {
        'kind': 'contact alice@example.invalid', 'target': 'Jean Dupont', 'evidence': []}}))
    mailer = Mailer(configured(tmp_path), transport=calls.append)
    mailer.send(drafts(book)[1])
    body = calls[0].get_content()
    assert 'Jean' not in body and 'alice' not in body
    assert 'Jean' not in str(calls[0]['Subject']) and 'alice' not in str(calls[0]['Subject'])
    assert 'php2shell' not in body
    bad = {**drafts(book)[0], 'evidence': ['alice@example.invalid']}
    with pytest.raises(ValueError, match='identifiant'):
        mailer.send(bad)


def test_api_recomputes_from_sources_and_requires_csrf(tmp_path, incident):
    app, calls = FastAPI(), []
    protect_mutations(app, ['http://127.0.0.1:8010'])
    attach_mailbox(app, lambda: incident[0], Mailer(configured(tmp_path), transport=calls.append))
    client = TestClient(app)
    result = client.get('/console/mails-chat/outbox').json()
    assert not calls and len(result['items']) == 1
    body = {'incident_id': result['items'][0]['id']}
    assert client.post('/console/mails-chat/send', json=body).status_code == 403
    headers = {'Origin': 'http://127.0.0.1:8010', 'X-Demo-Token': app.state.mutation_token}
    assert client.post('/console/mails-chat/send', json=body, headers={'Origin': headers['Origin']}).status_code == 403
    assert client.post('/console/mails-chat/send', json=body, headers={**headers, 'X-Demo-Token': 'incorrect'}).status_code == 403
    assert client.post('/console/mails-chat/send', json={**body, 'to': 'attacker'}, headers=headers).status_code == 422
    assert client.post('/console/mails-chat/send', json={'incident_id': 'missing'}, headers=headers).status_code == 404
    assert client.post('/console/mails-chat/send', json=body, headers={**headers, 'Origin': 'https://attacker.invalid'}).status_code == 403
    assert not calls
    assert client.post('/console/mails-chat/send', json=body, headers=headers).status_code == 200
    assert len(calls) == 1
    incident[0].complete = False
    assert client.post('/console/mails-chat/send', json=body, headers=headers).status_code == 409


def test_console_dom_safety_and_shared_chat():
    node = shutil.which('node')
    if not node:
        pytest.skip('node non installé')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root/'tests/console_mails_dom.cjs'),
                             str(root/'vigie/console/mails-chat.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_standalone_reads_only_verified_journal(cfg, tmp_path, incident):
    from journal.writer import JournalWriter
    from vigie.console.chat import attach_standalone
    path = tmp_path / 'journal.jsonl'
    message = incident[1].make(9, 'alert', 'human', {'finding': {
        'kind': 'published_cve', 'target': 'php2shell', 'evidence': []}})
    writer = JournalWriter(path)
    writer.append(message, True)
    writer.close()
    app = FastAPI()
    attach_standalone(app, cfg, journal=path)
    client = TestClient(app)
    assert len(client.get('/console/mails-chat/outbox').json()['items']) == 1
    assert client.get('/console/mails-chat/mails-chat.js').status_code == 200
    assert client.get('/console/mails-chat/mails-chat.css').status_code == 200
    assert client.get('/console/mails-chat/not-allowed').status_code == 404
    headers = {'Origin': f'http://127.0.0.1:{cfg["api"]["port"]}', 'X-Demo-Token': app.state.mutation_token}
    assert client.post('/demo/krys/ask', json={'question': 'alertes'}).status_code == 403
    result = client.post('/demo/krys/ask', json={'question': 'alertes'}, headers=headers).json()
    assert result['citations'][0]['message_id'] == message['message_id']
    assert client.post('/demo/krys/actions/prepare', json={'action': 'unfreeze', 'target': 'ACME'}, headers=headers).status_code == 403


def test_demo_console_uses_same_source_and_replay_bound():
    from vigie.demo_live import Display, create_demo_app
    # Un fournisseur explicite suffit ; ni Redis ni simulateur ne sont lus.
    keys = Keyring(['vigie'])
    book = Knowledge(keys.verify)
    app = create_demo_app(Display(), {}, krys_knowledge=lambda: book)
    client = TestClient(app)
    assert 'mails-chat' in client.get('/dashboard').text
    assert client.get('/console/mails-chat/outbox').json()['items'] == []


@pytest.mark.parametrize('tls', ['ssl', 'starttls'])
def test_smtp_transport_uses_tls_before_login_and_single_envelope(tmp_path, incident, monkeypatch, tls):
    from vigie.console import mails
    calls = []
    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls.append(('connect', kwargs))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def ehlo(self):
            calls.append(('ehlo',))
        def starttls(self, *, context):
            calls.append(('tls', context))
        def login(self, *args):
            calls.append(('login',))
        def send_message(self, message, *, from_addr, to_addrs):
            calls.append(('send', to_addrs))
    monkeypatch.setattr(mails.smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setattr(mails.smtplib, 'SMTP_SSL', FakeSMTP)
    mailer = Mailer(configured(tmp_path, KRYS_SMTP_TLS=tls))
    assert mailer.send(drafts(incident[0])[0])['status'] == 'sent'
    if tls == 'starttls':
        assert [c[0] for c in calls] == ['connect', 'ehlo', 'tls', 'ehlo', 'login', 'send']
        assert calls[2][1].check_hostname
    else:
        assert calls[0][1]['context'].check_hostname
    assert calls[-1] == ('send', ['operator@example.invalid'])
