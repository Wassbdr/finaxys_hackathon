"""Origine exacte et JSON précèdent toute écriture, sans secret de lancement."""
import ast
from pathlib import Path
from types import SimpleNamespace
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


def credentials(app, **extra):
    return {'Origin':app.state.allowed_origins[0],**extra}


def test_missing_origin_cannot_record(endpoint):
    _,client,writes=endpoint
    response=client.post('/demo/record',json={})
    assert response.status_code==403
    assert response.json()['detail']=='Origine non autorisée' and writes==[]


@pytest.mark.parametrize('origin',['https://attacker.invalid','null','http://127.0.0.1:8010.attacker.invalid',
    'http://127.0.0.1:8011','https://127.0.0.1:8010','http://localhost:8010',
    'http://127.0.0.1:8010/','http://127.0.0.1:8010 https://attacker.invalid'])
def test_foreign_origin_is_refused_on_its_reason(endpoint,origin):
    app,client,writes=endpoint
    response=client.post('/demo/record',json={},headers=credentials(app,Origin=origin))
    assert response.status_code==403 and writes==[]
    assert response.json()['detail']=='Origine non autorisée'
    assert 'access-control-allow-origin' not in response.headers


@pytest.mark.parametrize('content_type',[None,'text/plain','application/x-www-form-urlencoded','multipart/form-data','text/json'])
def test_non_json_is_refused_on_its_reason(endpoint,content_type):
    app,client,writes=endpoint
    headers=credentials(app)
    if content_type:headers['Content-Type']=content_type
    response=client.post('/demo/record',content='{}',headers=headers)
    assert response.status_code==415 and writes==[]
    assert response.json()['detail']=='Content-Type application/json requis'


def test_authorized_json_without_launch_secret(endpoint):
    app,client,writes=endpoint
    response=client.post('/demo/record',json={},headers=credentials(app))
    assert response.status_code==200 and len(writes)==1
    assert response.headers['access-control-allow-origin']==app.state.allowed_origins[0]
    assert 'access-control-allow-credentials' not in response.headers
    assert not hasattr(app.state,'mutation_token')


def test_questions_need_no_extra_input(endpoint):
    app,client,_=endpoint
    response=client.post('/demo/krys/ask',json={'question':'Présente Argus'},headers=credentials(app))
    assert response.status_code==200 and response.json()['text']


@pytest.mark.parametrize('path',['/demo/record','/record','/inject','/demo/krys/ask',
    '/demo/krys/actions/prepare','/demo/krys/actions/confirm','/console/mails-chat/send'])
@pytest.mark.parametrize('bad_origin',[True,False])
def test_every_mutation_checks_origin_and_json_before_dispatch(endpoint,path,bad_origin):
    app,client,writes=endpoint
    headers=credentials(app,**{'Content-Type':'text/plain'})
    if bad_origin:headers['Origin']='https://attacker.invalid'
    response=client.post(path,content='{}',headers=headers)
    assert response.status_code==(403 if bad_origin else 415)
    assert response.json()['detail']==('Origine non autorisée' if bad_origin else 'Content-Type application/json requis')
    assert writes==[]


@pytest.mark.parametrize('method',['PUT','PATCH','DELETE'])
def test_future_commands_are_guarded_before_dispatch(endpoint,method):
    _,client,writes=endpoint
    response=client.request(method,'/future-command',json={})
    assert response.status_code==403 and response.json()['detail']=='Origine non autorisée' and writes==[]


def test_future_command_can_run_with_exact_origin_and_json(endpoint):
    app,client,writes=endpoint
    @app.delete('/future-command')
    def command():
        writes.append('future')
        return {'ok':True}
    assert client.request('DELETE','/future-command',json={},headers=credentials(app)).json()=={'ok':True}
    assert writes==['future']


def test_reads_open_but_cors_is_not_wildcard(endpoint):
    _,client,_=endpoint
    assert client.get('/demo').status_code==200
    assert client.get('/health').status_code==200
    response=client.get('/demo',headers={'Origin':'https://attacker.invalid'})
    assert response.status_code==200 and 'access-control-allow-origin' not in response.headers


def test_configurable_origin_and_json_preflight(monkeypatch):
    monkeypatch.setenv('VIGIE_DEMO_ORIGINS','http://localhost:5173,https://demo.example')
    app=demo_live.create_demo_app(demo_live.Display(),{})
    client=TestClient(app)
    headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST',
             'Access-Control-Request-Headers':'content-type'}
    response=client.options('/demo/record',headers=headers)
    assert response.status_code==200
    assert response.headers['access-control-allow-origin']=='http://localhost:5173'
    assert '*' not in response.headers['access-control-allow-headers']
    assert 'x-demo-token' not in response.headers['access-control-allow-headers'].lower()
    response=client.options('/demo/record',headers={**headers,'Origin':'https://attacker.invalid'})
    assert response.status_code==400 and 'access-control-allow-origin' not in response.headers
    explicit=demo_live.create_demo_app(demo_live.Display(),{},allowed_origins=['http://localhost:3000'])
    assert explicit.state.allowed_origins==('http://localhost:3000',)


@pytest.mark.parametrize('origin',['*','null','file:///tmp/page.html','https://example.com/path',
    'https://user:pass@example.com','http://localhost:bad','https://example.com?x=1',''])
def test_invalid_origin_configuration_fails_at_start(origin):
    with pytest.raises(ValueError):demo_live.create_demo_app(demo_live.Display(),{},allowed_origins=[origin])


def test_startup_announces_origins_only(endpoint,capsys):
    from vigie.demo_security import announce_access
    app,_,_=endpoint
    announce_access(app)
    assert capsys.readouterr().out=='Origines autorisées : '+', '.join(app.state.allowed_origins)+'\n'


@pytest.mark.parametrize('name,value,status,detail',[
    ('Origin','http://127.0.0.1:8010',403,'Origine non autorisée'),
    ('Content-Type','application/json',415,'Content-Type application/json requis')])
def test_duplicate_security_headers_rejected_on_their_reason(endpoint,name,value,status,detail):
    app,client,writes=endpoint
    headers=list(credentials(app,**{'Content-Type':'application/json'}).items())+[(name,value)]
    response=client.post('/demo/record',content='{}',headers=headers)
    assert response.status_code==status and response.json()['detail']==detail and writes==[]


def test_only_loopback_listeners_remain():
    root=Path(__file__).resolve().parents[1]
    calls=[]
    for rel in ['api/app.py','vigie/demo_live.py']:
        tree=ast.parse((root/rel).read_text())
        calls += [n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                  and isinstance(n.func.value,ast.Name) and n.func.value.id=='uvicorn' and n.func.attr=='run']
    assert len(calls)==3
    assert all(any(k.arg=='host' and isinstance(k.value,ast.Constant) and k.value.value=='127.0.0.1'
                   for k in c.keywords) for c in calls)
