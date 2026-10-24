"""Pièces publiques de l'onglet : liste fermée, sans accès aux données de banque."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
router = APIRouter()
ASSETS = {
    'conformite.js': (HERE/'conformite.js', 'text/javascript'),
    'conformite.css': (HERE/'conformite.css', 'text/css'),
    'data/conformite.json': (HERE/'data/conformite.json', 'application/json'),
    'data/conformite-fixed-comparison.json': (HERE/'data/conformite-fixed-comparison.json', 'application/json'),
}
PROOFS = {
    'journal': ('journal/writer.py', 'JournalWriter.append et verify_journal'),
    'detection': ('vigie/contagion.py', 'Contagion'),
    'brouillon': ('vigie/krys.py', 'Chat.ask, branche DORA'),
    'humain': ('vigie/krys.py', 'HumanActions.prepare et confirm'),
    'controle': ('vigie/main.py', 'Runtime.on_message et request_approval'),
    'classique': ('vigie/classique.py', 'Classique.request_approval'),
    'mesure': ('vigie/SURETE_UTILITE.md', 'Résultats publiés, commit cf4973a'),
}


@router.get('/console/conformite/{name:path}')
def resource(name: str):
    if name in ASSETS:
        path, kind = ASSETS[name]
    elif name.startswith('preuves/') and name[8:] in PROOFS:
        relative, _ = PROOFS[name[8:]]
        path, kind = ROOT/relative, 'text/plain'
    else:
        raise HTTPException(404)
    # Aucun chemin fourni par le navigateur ne rejoint le système de fichiers.
    return FileResponse(path, media_type=kind, headers={'X-Content-Type-Options': 'nosniff',
                                                       'Cache-Control': 'no-cache'})
