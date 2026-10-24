"""Fichiers partagés des pages d'Argus : liste fermée, lecture seule, servis depuis le dépôt pour que la démo tourne
sans réseau. three.js 0.186.1 et Geist 1.7.2 sont copiés tels quels des archives npm (intégrité sha512 vérifiée),
leurs licences à côté d'eux."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

HERE = Path(__file__).resolve().parent
STATIC = HERE / 'static'
router = APIRouter()
CSS, JS, FONT = 'text/css', 'text/javascript', 'font/woff2'
FILES = {
    'tokens.css': (STATIC/'tokens.css', CSS),
    'header.css': (STATIC/'header.css', CSS),
    'sentinel.css': (STATIC/'sentinel.css', CSS),
    'accueil.js': (HERE/'accueil.js', JS),
    'accueil.css': (HERE/'accueil.css', CSS),
    'simulation.css': (STATIC/'simulation.css', CSS),
    'helper.js': (HERE/'helper.js', JS),
    'helper.css': (HERE/'helper.css', CSS),
    **{f'fonts/{name}': (STATIC/'fonts'/name, FONT) for name in
       ('Geist-Regular.woff2', 'Geist-Medium.woff2', 'GeistMono-Regular.woff2', 'GeistMono-Medium.woff2')},
    **{f'three/{name}': (STATIC/'three'/name, JS) for name in
       ('three.module.js', 'three.core.js', 'addons/controls/OrbitControls.js', 'addons/renderers/CSS2DRenderer.js',
        'addons/geometries/RoundedBoxGeometry.js', 'addons/environments/RoomEnvironment.js')},
}


@router.get('/console/static/{name:path}')
def resource(name: str):
    if name not in FILES:
        raise HTTPException(404)
    path, kind = FILES[name]
    # Aucun chemin fourni par le navigateur ne rejoint le système de fichiers.
    return FileResponse(path, media_type=kind, headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-cache'})
