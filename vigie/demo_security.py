"""Protection des écritures de l’API locale contre les pages web étrangères."""
import os
import secrets
from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse


def configured_origins(explicit=None, port=8010):
    values=explicit if explicit is not None else os.environ.get('VIGIE_DEMO_ORIGINS',f'http://127.0.0.1:{port}').split(',')
    origins=[]
    for value in values:
        origin=value.strip()
        parsed=urlsplit(origin)
        if (parsed.scheme not in ('http','https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment or '*' in origin):
            raise ValueError('Origine HTTP(S) exacte requise, sans chemin ni joker')
        # Une configuration invalide doit échouer au lancement, pas lors d’une requête.
        _=parsed.port
        origins.append(origin)
    if not origins:raise ValueError('Au moins une origine autorisée est requise')
    return tuple(dict.fromkeys(origins))


def protect_mutations(app, allowed_origins=None):
    origins=configured_origins(allowed_origins)
    token=secrets.token_urlsafe(32)
    app.state.mutation_token=token
    app.state.allowed_origins=origins
    # Le CORS générique de l’équipe est trop ouvert pour les commandes de cette démo.
    app.user_middleware=[m for m in app.user_middleware if m.cls is not CORSMiddleware]

    @app.middleware('http')
    async def guard(request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.getlist('origin')
            if len(origin)!=1 or origin[0] not in origins:
                return JSONResponse({'detail':'Origine non autorisée'},status_code=403)
            supplied=request.headers.getlist('x-demo-token')
            if len(supplied)!=1 or not secrets.compare_digest(supplied[0].encode(),token.encode()):
                return JSONResponse({'detail':'Jeton de lancement requis'},status_code=403)
            content=request.headers.getlist('content-type')
            if len(content)!=1 or content[0].split(';',1)[0].strip().lower()!='application/json':
                return JSONResponse({'detail':'Content-Type application/json requis'},status_code=415)
        return await call_next(request)

    # Placé à l’extérieur du garde pour conserver le CORS exact même sur les refus.
    app.add_middleware(CORSMiddleware,allow_origins=list(origins),
        allow_methods=['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'],
        allow_headers=['Content-Type','X-Demo-Token'],allow_credentials=False)


def announce_access(app):
    print('Origines autorisées : '+', '.join(app.state.allowed_origins),flush=True)
    print('Jeton démo (en-tête X-Demo-Token) : '+app.state.mutation_token,flush=True)
