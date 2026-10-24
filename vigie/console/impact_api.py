"""Fichiers publics de l’onglet Impact : liste fermée et lecture seule."""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

impact_router=APIRouter(prefix='/dashboard/console')
BASE=Path(__file__).parent
FILES={'data.json','fixed.json','normal.json','crash.json','detections.json','fraud.json',
       'outage.json','manifest.json','limits.md','outage-method.md'}


@impact_router.get('/impact.js')
def script():return FileResponse(BASE/'impact.js',headers={'Cache-Control':'no-cache'})


@impact_router.get('/impact.css')
def style():return FileResponse(BASE/'impact.css',headers={'Cache-Control':'no-cache'})


@impact_router.get('/impact/{name}')
def evidence(name:str):
    if name not in FILES:raise HTTPException(404)
    return FileResponse(BASE/'data'/'impact'/name,headers={'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'})
