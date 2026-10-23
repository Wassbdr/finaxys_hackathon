"""Le navigateur doit prouver l’origine et la possession du jeton avant toute écriture."""
from types import SimpleNamespace
import secrets
import pytest
from fastapi.testclient import TestClient
from vigie import demo_live


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    display=demo_live.Display();display.directory=tmp_path
    writes=[]
    def save(path):
        writes.append(path)
        return tmp_path/'saved'
    monkeypatch.setattr(demo_live,'saved_recording',save)
    app=demo_live.create_demo_app(display,{'A':SimpleNamespace(kind='live')})
    return app,TestClient(app),writes


def test_missing_origin_cannot_record(endpoint):
    _,client,writes=endpoint
    response=client.post('/demo/record',json={})
    assert response.status_code==403
    assert writes==[]


def test_foreign_site_cannot_record(endpoint):
    _,client,writes=endpoint
    response=client.post('/demo/record',json={},headers={'Origin':'https://attacker.invalid'})
    assert response.status_code==403
    assert writes==[]


def test_simple_request_cannot_record(endpoint):
    _,client,writes=endpoint
    response=client.post('/demo/record',content='{}',headers={
        'Origin':'http://127.0.0.1:8010','Content-Type':'text/plain'})
    assert response.status_code in (403,415)
    assert writes==[]


def test_missing_token_cannot_record(endpoint):
    _,client,writes=endpoint
    response=client.post('/demo/record',json={},headers={'Origin':'http://127.0.0.1:8010'})
    assert response.status_code==403
    assert writes==[]


def test_json_is_required_even_with_credentials(endpoint):
    app,client,writes=endpoint
    response=client.post('/demo/record',content='{}',headers={
        'Origin':'http://127.0.0.1:8010','Content-Type':'text/plain',
        'X-Demo-Token':getattr(app.state,'mutation_token',secrets.token_urlsafe(32))})
    assert response.status_code==415
    assert writes==[]


def credentials(app, **extra):
    return {'Origin':app.state.allowed_origins[0],
            'X-Demo-Token':app.state.mutation_token,**extra}


def test_authorized_record_and_exact_cors(endpoint):
    app,client,writes=endpoint
    response=client.post('/demo/record',json={},headers=credentials(app))
    assert response.status_code==200 and len(writes)==1
    assert response.headers['access-control-allow-origin']==app.state.allowed_origins[0]
    assert 'access-control-allow-credentials' not in response.headers


@pytest.mark.parametrize('origin',['https://attacker.invalid','null','http://127.0.0.1:8010.attacker.invalid'])
def test_token_alone_never_authorizes_foreign_origin(endpoint,origin):
    app,client,writes=endpoint
    response=client.post('/demo/record',json={},headers=credentials(app,Origin=origin))
    assert response.status_code==403 and writes==[]
    assert 'access-control-allow-origin' not in response.headers


def test_wrong_or_previous_launch_token_is_refused(endpoint):
    app,client,writes=endpoint
    other=demo_live.create_demo_app(demo_live.Display(),{})
    assert app.state.mutation_token!=other.state.mutation_token
    for token in (secrets.token_urlsafe(32),other.state.mutation_token):
        assert client.post('/demo/record',json={},headers=credentials(app,**{'X-Demo-Token':token})).status_code==403
    assert writes==[]


@pytest.mark.parametrize('content_type',[None,'text/plain','application/x-www-form-urlencoded','multipart/form-data'])
def test_simple_content_types_refused_with_valid_credentials(endpoint,content_type):
    app,client,writes=endpoint
    headers=credentials(app)
    if content_type:headers['Content-Type']=content_type
    response=client.post('/demo/record',content='{}',headers=headers)
    assert response.status_code==415 and writes==[]


@pytest.mark.parametrize('method,path',[('POST','/record'),('POST','/inject'),
    ('PUT','/future-command'),('PATCH','/future-command'),('DELETE','/future-command')])
def test_all_mutation_routes_guarded_before_dispatch(endpoint,method,path):
    _,client,writes=endpoint
    response=client.request(method,path,json={})
    assert response.status_code==403 and writes==[]


def test_future_command_can_run_with_credentials(endpoint):
    app,client,writes=endpoint
    @app.delete('/future-command')
    def command():
        writes.append('future')
        return {'ok':True}
    assert client.delete('/future-command').status_code==403
    assert client.request('DELETE','/future-command',json={},headers=credentials(app)).json()=={'ok':True}
    assert writes==['future']


def test_reads_open_but_no_wildcard_or_token_leak(endpoint):
    app,client,_=endpoint
    response=client.get('/demo')
    assert response.status_code==200
    assert app.state.mutation_token not in response.text
    assert client.get('/health').status_code==200
    response=client.get('/demo',headers={'Origin':'https://attacker.invalid'})
    assert response.status_code==200 and 'access-control-allow-origin' not in response.headers


def test_configurable_origin_and_preflight(monkeypatch):
    monkeypatch.setenv('VIGIE_DEMO_ORIGINS','http://localhost:5173,https://demo.example')
    app=demo_live.create_demo_app(demo_live.Display(),{})
    client=TestClient(app)
    headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST',
             'Access-Control-Request-Headers':'content-type,x-demo-token'}
    response=client.options('/demo/record',headers=headers)
    assert response.status_code==200
    assert response.headers['access-control-allow-origin']=='http://localhost:5173'
    response=client.options('/demo/record',headers={**headers,'Origin':'https://attacker.invalid'})
    assert response.status_code==400 and 'access-control-allow-origin' not in response.headers
    explicit=demo_live.create_demo_app(demo_live.Display(),{},allowed_origins=['http://localhost:3000'])
    assert explicit.state.allowed_origins==('http://localhost:3000',)


@pytest.mark.parametrize('origin',['*','null','file:///tmp/page.html','https://example.com/path',
    'https://user:pass@example.com','http://localhost:bad','https://example.com?x=1',''])
def test_invalid_origin_configuration_fails_at_start(origin):
    with pytest.raises(ValueError):
        demo_live.create_demo_app(demo_live.Display(),{},allowed_origins=[origin])


def test_startup_announces_new_token_without_exposing_read_route(endpoint,capsys):
    from vigie.demo_security import announce_access
    app,client,_=endpoint
    announce_access(app)
    terminal=capsys.readouterr().out
    assert app.state.mutation_token in terminal and 'X-Demo-Token' in terminal
    assert app.state.allowed_origins[0] in terminal
    assert app.state.mutation_token not in client.get('/demo').text


def test_duplicate_security_headers_rejected(endpoint):
    app,client,writes=endpoint
    for name,value in [('Origin',app.state.allowed_origins[0]),('X-Demo-Token',app.state.mutation_token),
                       ('Content-Type','application/json')]:
        headers=list(credentials(app,**{'Content-Type':'application/json'}).items())+[(name,value)]
        assert client.post('/demo/record',content='{}',headers=headers).status_code in (403,415)
    assert writes==[]
