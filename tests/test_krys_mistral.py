"""Aucun client distant : chaque refus de reformulation isole sa cause."""
import asyncio
import json
import os
import secrets
import time

import httpx
import pytest

from vigie.krys_mistral import Rephraser, MistralClient, load_config, output_rejection


@pytest.fixture(autouse=True)
def empty_environment(monkeypatch):
    monkeypatch.setattr(os, 'environ', {})


class FakeMistral:
    def __init__(self, text='Argus surveille une banque. [argus]', error=None):
        self.text, self.error, self.calls = text, error, []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.text


def source():
    return {'text': 'Argus surveille une banque.\nSource : [argus].',
            'citations': [{'section_id': 'argus', 'source': 'KRYS_EXPLICATIONS.md'}],
            'outcome': 'answer', 'intent': 'reference'}


def enabled(client):
    return Rephraser.from_env({'KRYS_LLM': 'mistral', 'MISTRAL_API_KEY': secrets.token_urlsafe(24)},
                              client_factory=lambda _: client)


@pytest.mark.parametrize('mode,has_key', [(None, False), (None, True), ('other', True), ('mistral', False)])
def test_no_client_or_network_without_both_switches(mode, has_key, monkeypatch):
    created = []
    def forbidden(*args, **kwargs):
        created.append(True)
        raise AssertionError('Aucun client ni socket autorisé')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    env = {}
    if mode is not None:
        env['KRYS_LLM'] = mode
    if has_key:
        env['MISTRAL_API_KEY'] = secrets.token_urlsafe(24)
    renderer = Rephraser.from_env(env, client_factory=forbidden)
    answer = source()
    assert asyncio.run(renderer.apply(answer)) == answer
    assert not renderer.enabled
    assert not created


def test_approved_rephrasing_is_cited_and_sends_only_retrieved_facts():
    client = FakeMistral()
    answer = source()
    result = asyncio.run(enabled(client).apply(answer))
    assert result['text'] == client.text and result['rephrased'] is True
    assert result['citations'] == answer['citations']
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call['model'] == 'mistral-small-2603'
    facts = json.loads(call['messages'][1]['content'])
    assert facts == {'facts': answer['text'], 'sources': ['[argus]']}
    assert 'instructions' in call['messages'][0]['content'].lower()


@pytest.mark.parametrize('text,reason', [
    ('Argus surveille une banque.', 'missing_citation'),
    ('Argus surveille 987654 banques. [argus]', 'unknown_number'),
    ('Argus surveille deux banques. [argus]', 'unknown_number'),
    ('Argus surveille 1e99 banques. [argus]', 'unknown_number'),
    ('Argus surveille une banque. [argus] [inventee]', 'unknown_identifier'),
    ('Cliquez sur le bouton. [argus]', 'instruction'),
    ('Ignore les règles. [argus]', 'instruction'),
    ('Ne signalez pas cet incident. [argus]', 'instruction'),
    ('Contact : humain@example.org. [argus]', 'personal_data'),
    ('Client : Jean Dupont. [argus]', 'personal_data'),
    ('Alice habite ici. [argus]', 'personal_data'),
    ('Contact : +33 0 00 00 00 00. [argus]', 'personal_data'),
    ('IBAN : FR00' + '0'*23 + '. [argus]', 'personal_data'),
    ('<script>alert(1)</script> [argus]', 'format'),
])
def test_each_output_guard_on_its_own_reason_and_exact_fallback(text, reason):
    answer = source()
    assert output_rejection(text, answer) == reason
    client = FakeMistral(text)
    assert asyncio.run(enabled(client).apply(answer)) == answer
    assert len(client.calls) == 1


def test_unknown_message_id_even_with_known_numbers_and_valid_citation():
    answer = {'text': 'Décision risk:7:2 au tick 7 : refusée.', 'outcome': 'answer', 'intent': 'why',
              'citations': [{'message_id': 'risk:7:2', 'tick': 7, 'source': 'risk'}]}
    assert output_rejection('Décision refusée. risk:7:2 risk:2:7', answer) == 'unknown_identifier'
    result = asyncio.run(enabled(FakeMistral('Décision refusée. [risk:7:2]')).apply(answer))
    assert result['rephrased'] is True


def test_only_exactly_cited_sources_are_returned():
    answer = {'text': 'Messages risk:7:2 et risk:7:23 : décisions refusées.', 'outcome': 'answer', 'intent': 'why',
              'citations': [{'message_id': mid, 'tick': 7, 'source': 'risk'} for mid in ('risk:7:2', 'risk:7:23')]}
    result = asyncio.run(enabled(FakeMistral('Décision refusée. [risk:7:23]')).apply(answer))
    assert result['rephrased'] is True
    assert result['citations'] == [answer['citations'][1]]


@pytest.mark.parametrize('outcome', ['human_required', 'guardrail', 'no_source', 'out_of_scope'])
def test_refusals_and_human_commands_never_sent(outcome):
    client = FakeMistral()
    answer = {**source(), 'outcome': outcome}
    assert asyncio.run(enabled(client).apply(answer)) == answer
    assert not client.calls


def test_no_source_and_personal_input_never_leave_process():
    client = FakeMistral()
    renderer = enabled(client)
    for answer in [{**source(), 'citations': []},
                   {**source(), 'text': 'Contact : personne@example.org. [argus]'},
                   {**source(), 'text': 'Client : Jean Dupont. [argus]'}]:
        assert asyncio.run(renderer.apply(answer)) == answer
    assert not client.calls


def test_exception_and_real_async_deadline_fall_back_without_error_details():
    answer = source()
    assert asyncio.run(enabled(FakeMistral(error=RuntimeError('contenu privé'))).apply(answer)) == answer
    class Slow(FakeMistral):
        async def complete(self, **kwargs):
            await asyncio.sleep(60)
    renderer = enabled(Slow())
    renderer.config = {**renderer.config, 'deadline_s': 0.01}
    start = time.monotonic()
    assert asyncio.run(renderer.apply(answer)) == answer
    assert time.monotonic()-start < 0.5
    assert load_config()['deadline_s'] == 6


def test_http_request_is_pinned_no_tools_no_redirect_and_no_real_network():
    requests = []
    def fake(request):
        requests.append(request)
        return httpx.Response(200, json={'model': 'mistral-small-2603',
            'choices': [{'message': {'content': 'Argus surveille une banque. [argus]'}}]})
    client = MistralClient(secrets.token_urlsafe(24), transport=httpx.MockTransport(fake))
    answer = source()
    result = asyncio.run(enabled(client).apply(answer))
    assert result['rephrased'] is True and len(requests) == 1
    request = requests[0]
    assert str(request.url) == 'https://api.mistral.ai/v1/chat/completions'
    body = json.loads(request.content)
    assert body['model'] == 'mistral-small-2603' and 'tools' not in body
    assert body['stream'] is False and body['temperature'] == 0


def test_api_rephrases_question_only_and_human_routes_bypass_it():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vigie.demo_security import protect_mutations
    from vigie.krys import Knowledge
    from vigie.krys_api import attach_krys
    client = FakeMistral()
    app = FastAPI()
    protect_mutations(app, ['http://127.0.0.1:8010'])
    attach_krys(app, lambda: Knowledge(lambda _: False), rephraser=enabled(client))
    headers = {'Origin': 'http://127.0.0.1:8010', 'X-Demo-Token': app.state.mutation_token}
    with TestClient(app) as api:
        answer = api.post('/demo/krys/ask', headers=headers, json={'question': 'Présente le projet Argus.'}).json()
        assert answer['rephrased'] is True
        count = len(client.calls)
        assert api.post('/demo/krys/ask', headers=headers, json={'question': 'je confirme'}).json()['outcome'] == 'human_required'
        assert api.post('/demo/krys/actions/prepare', headers=headers, json={'action': 'unfreeze', 'target': 'ACME'}).status_code == 403
        assert api.post('/demo/krys/actions/confirm', headers=headers, json={'action_id': 'fake', 'confirmation': 'confirmer'}).status_code == 409
    assert len(client.calls) == count


def test_busy_rephraser_falls_back_without_queuing_a_second_call():
    async def exercise():
        ready, release = asyncio.Event(), asyncio.Event()
        class Waiting(FakeMistral):
            async def complete(self, **kwargs):
                self.calls.append(kwargs)
                ready.set()
                await release.wait()
                return self.text
        client = Waiting()
        renderer = enabled(client)
        first = asyncio.create_task(renderer.apply(source()))
        await ready.wait()
        second = await renderer.apply(source())
        assert second == source() and len(client.calls) == 1
        release.set()
        assert (await first)['rephrased'] is True
    asyncio.run(exercise())


def test_http_redirect_or_changed_model_falls_back_without_retry():
    for response in [httpx.Response(307, headers={'Location': 'https://example.invalid'}),
                     httpx.Response(200, json={'model': 'mistral-small-latest',
                       'choices': [{'message': {'content': 'Argus surveille une banque. [argus]'}}]})]:
        calls = []
        def fake(request):
            calls.append(request)
            return response
        client = MistralClient(secrets.token_urlsafe(24), transport=httpx.MockTransport(fake))
        assert asyncio.run(enabled(client).apply(source())) == source()
        assert len(calls) == 1
