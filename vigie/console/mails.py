"""Brouillons issus du journal vérifié ; SMTP facultatif, sans texte libre sortant."""
from email.message import EmailMessage
import hashlib
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
import time

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from vigie.krys import Chat


LABELS = {
    'published_cve': 'Faille publiée', 'prompt_injection': 'Instruction suspecte',
    'ledger_mismatch': 'Divergence du registre', 'herding_freeze': 'Gel pour mimétisme',
    'herding_index': 'Mimétisme', 'effective_agents': 'Diversité des décisions',
    'llm_similarity': 'Similarité des justifications', 'marked_operation': 'Dépendance suspecte',
    'refus_portique': 'Refus du portique',
}
LIMIT = 'Limite : volume refusé observé, aucune perte évitée établie ; journal potentiellement incomplet.'
COMPARISON = 'Contrôles classiques : comparaison non mesurée dans cette boîte, observations de la banque Krys uniquement.'
ACTION = 'Examiner les preuves dans la console ; toute reprise exige une décision humaine distincte.'
LINK = '/dashboard#mails-chat'
# Un message_id peut lui aussi être malveillant : seuls les identifiants techniques
# attendus sont exportables. Ni cible, ni document, ni texte d’alerte ne sortent.
PROOF = re.compile(r'(?:vigie|executor|risk|human|clock|quant|payment|compliance|trader-\d+|svc:[a-z0-9_-]{1,64}):(?:[a-f0-9]{6}:)?\d{1,8}:\d{1,8}\Z')
ADDRESS = re.compile(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z')


def drafts(book):
    items = []
    for group in Chat(book).incidents():
        evidence = list(dict.fromkeys(group['evidence']))
        anchor = book.messages[evidence[0]] if evidence else None
        if not anchor:
            continue
        identity = '\0'.join((group['cause'], group['target'], book.identities[anchor['message_id']]))
        ticks = [book.messages[mid]['tick'] for mid in evidence]
        operations = len(group['operations'])
        items.append({
            'id': hashlib.sha256(identity.encode()).hexdigest(),
            'subject': 'Krys · ' + LABELS.get(group['cause'], 'Constat signé'),
            'alerts': group['alerts'], 'operations': operations,
            'impact': f'{operations} opération(s) refusée(s) distincte(s) observée(s) pour cet incident ; {group["alerts"]} alerte(s) brute(s).',
            'period': f'Journal signé, ticks {min(ticks)} à {max(ticks)} ; {len(evidence)} message(s) de preuve.',
            'evidence': evidence, 'action': ACTION, 'link': LINK,
            'limit': LIMIT, 'comparison': COMPARISON,
        })
    return items


class Mailer:
    def __init__(self, environ=None, *, transport=None, clock=time.time):
        env = os.environ if environ is None else environ
        self.enabled = env.get('KRYS_MAIL') == 'smtp'
        self.clock, self.transport = clock, transport
        if not self.enabled:
            return
        self.recipient = env.get('KRYS_MAIL_TO', '')
        self.sender = env.get('KRYS_SMTP_FROM', '')
        self.host = env.get('KRYS_SMTP_HOST', '')
        self.user = env.get('KRYS_SMTP_USER', '')
        self.password = env.get('KRYS_SMTP_PASSWORD', '')
        self.tls = env.get('KRYS_SMTP_TLS', 'ssl')
        try:
            self.port = int(env.get('KRYS_SMTP_PORT', '465' if self.tls == 'ssl' else '587'))
        except ValueError:
            raise ValueError('Port SMTP invalide') from None
        if (not ADDRESS.fullmatch(self.recipient) or not ADDRESS.fullmatch(self.sender)
                or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', self.host)
                or not self.user or not self.password or self.tls not in ('ssl', 'starttls')
                or not 1 <= self.port <= 65535):
            raise ValueError('Configuration SMTP invalide : adresse unique, authentification et TLS requis')
        self.state = Path(env.get('KRYS_MAIL_STATE', str(Path.home() / '.cache/krys/mail.sqlite')))

    def _reserve(self, incident_id):
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.state, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(self.state, timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS deliveries (incident TEXT PRIMARY KEY, stamp REAL NOT NULL)')
            # Réserver AVANT l’appel SMTP évite le doublon si le serveur a reçu
            # le mail mais que son accusé de réception se perd. Pas de retry.
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM deliveries WHERE incident=?', (incident_id,)).fetchone():
                return False
            now = self.clock()
            count = db.execute('SELECT COUNT(*) FROM deliveries WHERE stamp>?', (now - 3600,)).fetchone()[0]
            if count >= 10:
                raise ValueError('Limite de dix mails par heure atteinte')
            db.execute('INSERT INTO deliveries VALUES (?, ?)', (incident_id, now))
        return True

    def _smtp(self, message):
        context = ssl.create_default_context()
        if self.tls == 'ssl':
            connection = smtplib.SMTP_SSL(self.host, self.port, timeout=6, context=context)
        else:
            connection = smtplib.SMTP(self.host, self.port, timeout=6)
        with connection as smtp:
            if self.tls == 'starttls':
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(self.user, self.password)
            smtp.send_message(message, from_addr=self.sender, to_addrs=[self.recipient])

    def send(self, item):
        if not self.enabled:
            raise PermissionError('SMTP désactivé ; boîte de démonstration uniquement')
        if not item['evidence'] or any(not PROOF.fullmatch(mid) for mid in item['evidence']):
            raise ValueError('Export refusé : identifiant de preuve non technique')
        message = EmailMessage()
        message['From'], message['To'] = self.sender, self.recipient
        # Les champs viennent exclusivement du constructeur interne, jamais du POST.
        message['Subject'] = item['subject']
        message.set_content('\n\n'.join((item['impact'], item['period'],
            'Preuves (message_id) : ' + ', '.join(item['evidence'][:20]),
            ACTION, 'Console locale : ' + LINK, COMPARISON, LIMIT)))
        if not self._reserve(item['id']):
            return {'status': 'reserved', 'detail': 'Incident déjà réservé ; aucun nouvel envoi.'}
        try:
            (self.transport if self.transport is not None else self._smtp)(message)
        except Exception:
            # Les erreurs du serveur peuvent contenir l’adresse ou les paramètres
            # d’authentification : elles ne sont jamais rendues ni journalisées.
            raise ValueError('Envoi incertain ; réservation conservée, aucune relance automatique.') from None
        return {'status': 'sent', 'detail': 'Mail remis au transport SMTP.'}


class SendRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    incident_id: str = Field(min_length=1, max_length=64)


def attach_mailbox(app, knowledge, mailer=None):
    mailer = mailer if mailer is not None else Mailer()

    @app.get('/console/mails-chat/outbox')
    def outbox():
        book = knowledge()
        return {'items': drafts(book), 'complete': book.complete, 'smtp_enabled': mailer.enabled}

    @app.post('/console/mails-chat/send')
    def send(body: SendRequest):
        # Le raccord exige le garde de la démo ; aucun corps utilisateur n’est
        # utilisé comme contenu de mail, destinataire ou preuve.
        if not getattr(app.state, 'mutation_guard_enabled', False):
            raise HTTPException(403, 'Garde des écritures indisponible')
        book = knowledge()
        if not book.complete:
            raise HTTPException(409, 'Journal incomplet ; envoi refusé')
        item = next((i for i in drafts(book) if i['id'] == body.incident_id), None)
        if item is None:
            raise HTTPException(404, 'Incident absent des preuves courantes')
        try:
            return mailer.send(item)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.get('/console/mails-chat/{asset}')
    def asset(asset: str):
        if asset not in ('mails-chat.js', 'mails-chat.css'):
            raise HTTPException(404)
        return FileResponse(Path(__file__).with_name(asset), media_type=(
            'text/javascript' if asset.endswith('.js') else 'text/css'))
