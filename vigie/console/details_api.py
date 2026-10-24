"""Consultation bornée des preuves publiques, sans accès à l'oracle ni écriture."""
import json
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse

from journal.writer import GENESIS, entry_hash

HERE = Path(__file__).parent
DATA = HERE / 'data' / 'details'


def references(message):
    refs = list(message.get('depends_on', []))
    payload = message.get('payload', {})
    finding = payload.get('finding') if message.get('from') == 'vigie' else None
    if isinstance(finding, dict):
        refs.extend(finding.get('evidence', []))
    try:
        reason = payload.get('gate', {}).get('reason', '{}')
        reason = json.loads(reason) if isinstance(reason, str) else reason
        if isinstance(reason, dict):
            refs.extend(reason.get('evidence', []))
    except (ValueError, TypeError, AttributeError):
        pass
    return list(dict.fromkeys(r for r in refs if isinstance(r, str)))


def trace(messages, message_id, *, limit=64):
    pending, seen, found, missing = [message_id], set(), [], []
    while pending and len(seen) < limit:
        mid = pending.pop(0)
        if mid in seen:
            continue
        seen.add(mid)
        msg = messages.get(mid)
        if msg is None:
            missing.append(mid)
        else:
            found.append(msg)
            pending.extend(r for r in references(msg) if r not in seen)
    truncated = any(mid not in seen for mid in pending)
    return {'messages': found, 'missing': missing, 'truncated': truncated,
            'complete': not missing and not truncated}


def project(message):
    # Les documents et raisonnements bruts ne sont pas nécessaires à la preuve.
    return {key: message.get(key) for key in
            ('message_id', 'tick', 'from', 'type', 'depends_on', 'data_provenance')} | {
        'payload': {k: v for k, v in message.get('payload', {}).items() if k in
                    ('status', 'approved', 'gate', 'decided_by', 'finding', 'kind', 'asset')}}


def archive_report(path):
    previous, count, valid, signatures = GENESIS, 0, True, 0
    excerpt, decisions, messages = [], [], {}
    try:
        with open(path, encoding='utf-8') as stream:
            for line in stream:
                entry = json.loads(line)
                count += 1
                msg = entry['message']
                if (entry['seq'] != count or entry['prev_hash'] != previous or
                        entry['hash'] != entry_hash(previous, count, entry['signature_valid'], msg)):
                    valid = False
                    break
                previous = entry['hash']
                signatures += entry['signature_valid'] is True
                messages[msg['message_id']] = project(msg)
                if len(excerpt) < 3:
                    excerpt.append(entry)
                if msg['type'] == 'execution':
                    decisions.append(project(msg))
    except (OSError, ValueError, TypeError, KeyError):
        valid = False
    return {'chain_valid': valid and count > 0, 'entries': count,
            'recorded_valid_signatures': signatures, 'signature_scope': 'recorded',
            'head_hash': previous, 'excerpt': excerpt, 'decisions': decisions[-30:],
            'messages': messages}


@lru_cache(maxsize=1)
def archived():
    return archive_report(DATA / 'journal.jsonl')


def attach_details(app):
    @app.get('/console/details.js')
    def script():
        return FileResponse(HERE/'details.js', media_type='text/javascript', headers={'Cache-Control': 'no-cache'})

    @app.get('/console/details.css')
    def style():
        return FileResponse(HERE/'details.css', media_type='text/css')

    @app.get('/console/details/data/{name}')
    def data(name: str):
        # Une liste fermée évite de transformer le lien de preuve en lecteur de fichiers.
        allowed = set(json.loads((DATA/'manifest.json').read_text())['files']) | {'manifest.json'}
        if name not in allowed:
            raise HTTPException(404)
        media = 'text/plain' if name.endswith(('.md', '.jsonl')) else 'application/json'
        return FileResponse(DATA/name, media_type=media, headers={'X-Content-Type-Options': 'nosniff'})

    @app.get('/console/details/journal')
    def journal():
        return {k: v for k, v in archive_report(DATA / 'journal.jsonl').items() if k != 'messages'}

    @app.get('/console/details/provenance')
    def provenance(message_id: str = Query('', max_length=256), mode: str = 'archive'):
        if mode == 'archive':
            report = archived()
            messages, complete = report['messages'], report['chain_valid']
        elif mode == 'live':
            provider = getattr(app.state, 'details_knowledge', None)
            if provider is None:
                raise HTTPException(409, 'Aucun journal de démo connecté ; choisissez la mesure archivée.')
            book = provider()
            messages, complete = book.messages, book.complete
        else:
            raise HTTPException(422, 'Mode inconnu')
        if not message_id:
            decisions = [m for m in messages.values() if m['type'] == 'execution'][-30:]
            return {'mode': mode, 'decisions': decisions, 'complete': complete}
        result = trace(messages, message_id)
        return {**result, 'mode': mode, 'complete': result['complete'] and complete}
