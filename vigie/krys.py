"""Krys parle de preuves publiques ; seul le courtier humain peut signer une action.

Adaptation des principes génériques de garde-fous, citations et minimisation du
chatbot déposé par Édouard. Aucun contenu métier de cette archive n'est repris.
"""
from __future__ import annotations

from collections import deque
import copy
import json
import math
import re
import secrets
import threading
import time
import unicodedata

from bus.message import canonical
from journal.writer import GENESIS, entry_hash
from vigie.injection import instruction_signals
from vigie.validation import valid_message


def plain(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower().replace('’', "'"))
                   if unicodedata.category(c) != 'Mn')


_MANIPULATION = re.compile(
    r'prompt (?:systeme|system|interne)|system prompt|consignes? internes?|systeme interne|\[system\]|<\||jailbreak|'
    r'(?:sans|supprime|retire|omet).*\b(?:sources?|citations?)|'
    r'(?:commence|termine|reponds seulement|repond uniquement|format impose)|'
    r'(?:tu es|vous etes|je suis).*(?:administrateur|developpeur)|'
    r'cles? (?:api|secretes?)|mot de passe|secrets? internes?', re.I)


class Knowledge:
    """Projection bornée : aucun accès Redis, simulateur, agent ou modèle ici."""
    def __init__(self, verify, *, max_messages=20_000, max_bytes=16*1024*1024):
        self.verify = verify
        self.messages = {}
        self.identities = {}
        self.conflicts = set()
        self.max_messages = max_messages
        self.max_bytes, self.bytes = max_bytes, 0
        self.complete = True

    def accept(self, message):
        try:
            if not valid_message(message) or not self.verify(message):
                return False
            mid = message['message_id']
            identity = message['signature']
            if mid in self.conflicts:
                return False
            if mid in self.identities:
                if self.identities[mid] == identity:
                    return True
                self.messages.pop(mid, None)
                self.conflicts.add(mid)
                self.complete = False
                return False
            if len(self.identities) >= self.max_messages:
                self.complete = False
                return False
            # Les textes de documents et raisonnements ne deviennent jamais des
            # consignes ni des réponses. Seuls les champs utiles sont projetés.
            p = message['payload']
            if message['from'] == 'vigie' and p.get('kind') == 'krys_status':
                if (any(not isinstance(p.get(key), list) or any(not isinstance(v, str) for v in p[key])
                        for key in ('frozen', 'suspects', 'uncertain'))
                        or type(p.get('r0')) not in (float, int) or not math.isfinite(p['r0']) or p['r0'] < 0
                        or not isinstance(p.get('propositions', []), list)):
                    return False
            fields = {
                'alert': ('kind', 'finding', 'propositions', 'frozen', 'suspects', 'r0', 'uncertain'),
                'execution': ('status', 'amount_eur', 'notional_eur', 'asset', 'gate', 'decided_by'),
                'risk_decision': ('approved', 'amount_eur', 'notional_eur', 'asset'),
                'light_change': ('light', 'reason'),
                'human_decision': ('action', 'target'),
            }.get(message['type'], ())
            projected = {key: copy.deepcopy(p[key]) for key in fields if key in p}
            record = {key: message[key] for key in ('message_id', 'tick', 'from', 'type')}
            record['depends_on'] = list(message.get('depends_on', []))
            record['data_provenance'] = [{key: entry[key] for key in
                ('service', 'version', 'response_hash', 'tick') if key in entry}
                for entry in message.get('data_provenance', [])]
            record['payload'] = projected
            size = len(canonical(record).encode('utf-8'))
            if self.bytes + size > self.max_bytes:
                self.complete = False
                return False
            self.bytes += size
            self.identities[mid] = identity
            self.messages[mid] = record
            return True
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    def latest_status(self):
        return max((m for m in reversed(list(self.messages.values())) if m['from'] == 'vigie'
                    and m['type'] == 'alert' and m['payload'].get('kind') == 'krys_status'),
                   key=lambda m: m['tick'], default=None)

    @classmethod
    def journal(cls, path, verify, *, until_tick=None):
        book = cls(verify)
        previous, seq = GENESIS, 0
        try:
            with open(path, encoding='utf-8') as stream:
                for line in stream:
                    if not line.endswith('\n'):
                        break  # Une écriture en cours ne constitue pas une entrée.
                    if len(line) > 512_000:
                        book.complete = False
                        break
                    entry = json.loads(line)
                    if until_tick is not None and entry['message']['tick'] > until_tick:
                        break
                    seq += 1
                    if (entry['prev_hash'] != previous or entry['seq'] != seq or
                            entry['hash'] != entry_hash(previous, seq, entry['signature_valid'], entry['message'])):
                        book.complete = False
                        break
                    previous = entry['hash']
                    message = entry['message']
                    book.accept(message)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            book.complete = False
        return book


def amount(payload):
    value = payload.get('amount_eur', payload.get('notional_eur'))
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


class Chat:
    CAUSES = {
        'prompt_injection': 'Instruction cachée suspectée dans une donnée externe',
        'published_cve': 'Vulnérabilité publiée affectant une source',
        'ledger_mismatch': 'Écart entre le grand livre et les exécutions observées',
        'herding_freeze': 'Gel ciblé face au mimétisme',
        'marked_operation': 'Opération dépendant d’une source suspecte',
        'refus_portique': 'Refus explicite du portique',
        'herding_index': 'Mimétisme entre décisions',
        'effective_agents': 'Diversité effective des décisions',
        'llm_similarity': 'Similarité des justifications',
    }
    def __init__(self, knowledge, *, audit=None):
        self.book = knowledge
        # Aucun texte, identité personnelle, adresse réseau ni empreinte de la
        # question ne sont conservés ; seulement des compteurs de fonctionnement.
        self.audit = audit if audit is not None else deque(maxlen=128)

    def answer(self, text, ids=(), *, outcome='answer', intent='state'):
        ids = list(dict.fromkeys(mid for mid in ids if mid in self.book.messages))[:64]
        if outcome == 'answer' and not ids:
            text, outcome = 'Je ne peux pas confirmer cette information sans source signée disponible.', 'no_source'
        citations = [{'message_id': mid, 'tick': self.book.messages[mid]['tick'],
                      'source': self.book.messages[mid]['from']} for mid in ids]
        if outcome == 'answer':
            text += '\nPreuves : ' + ', '.join(ids) + '.'
            if not self.book.complete:
                text += '\nJournal incomplet : cette réponse ne couvre que les preuves disponibles.'
        self.audit.append({'outcome': outcome, 'intent': intent, 'sources': len(ids)})
        return {'text': text, 'citations': citations, 'outcome': outcome, 'intent': intent}

    def incidents(self):
        groups = {}
        for msg in self.book.messages.values():
            p = msg['payload']
            if msg['from'] == 'vigie' and msg['type'] == 'alert' and isinstance(p.get('finding'), dict):
                f = p['finding']
                key = (str(f.get('kind', 'constat')), str(f.get('target', 'inconnue')))
                item = groups.setdefault(key, {'cause': key[0], 'target': key[1], 'evidence': [],
                                               'alerts': 0, 'operations': {}, 'amount_unknown': 0})
                item['alerts'] += 1
                item['evidence'].extend([msg['message_id'], *[i for i in f.get('evidence', []) if i in self.book.messages]])
        # Une exécution refusée est une preuve de refus, pas une estimation de
        # perte évitée. La même opération ne compte qu'une fois par incident.
        for msg in self.book.messages.values():
            p = msg['payload']
            if msg['from'] != 'executor' or msg['type'] != 'execution' or p.get('status') != 'refused':
                continue
            if not str(p.get('decided_by', '')).startswith('vigie'):
                continue
            try:
                reason = json.loads(p.get('gate', {}).get('reason', '{}'))
            except (ValueError, TypeError, AttributeError):
                reason = {}
            proof = reason.get('evidence', []) if isinstance(reason, dict) else []
            matches = [i for i in groups.values() if set(proof) & set(i['evidence'])]
            if not matches:
                key = ('refus_portique', 'opérations')
                matches = [groups.setdefault(key, {'cause': key[0], 'target': key[1], 'evidence': [],
                                                   'alerts': 0, 'operations': {}, 'amount_unknown': 0})]
            # Attribution unique : pas d'addition du même euro entre causes.
            item = matches[0]
            op = next(iter(msg['depends_on']), msg['message_id'])
            if op not in item['operations']:
                value = amount(p)
                if value is None:
                    for parent in msg['depends_on']:
                        value = amount(self.book.messages.get(parent, {}).get('payload', {}))
                        if value is not None:
                            break
                item['operations'][op] = value
                item['amount_unknown'] += value is None
            item['evidence'].extend([msg['message_id'], *[i for i in proof if i in self.book.messages]])
        return list(groups.values())

    def ask(self, question):
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            return self.answer('Question vide ou trop longue (1 000 caractères maximum).', outcome='guardrail')
        q = plain(question)
        if instruction_signals(question) or _MANIPULATION.search(q):
            return self.answer('Cette demande tente de modifier les consignes ou de divulguer des informations internes. Reformulez une question sur les preuves de Krys.', outcome='guardrail')
        if re.search(r'confirm|degel|leve.*gel|debloq|repren|reprendre|reprise|(?:sortir|sors|sortez).*quarantaine|libere', q):
            return self.answer('Un texte dans le chat ne signe aucune action. Utilisez le formulaire humain : préparer, lire le récapitulatif, puis Confirmer. La sortie de quarantaine d’un agent n’est pas implémentée par le contrôleur actuel.', outcome='human_required')
        status = self.book.latest_status()
        ids = [status['message_id']] if status else []
        p = status['payload'] if status else {}
        if 'pourquoi' in q or 'provenance' in q or 'decision' in q:
            matches = [mid for mid in self.book.messages if mid in question]
            if not matches:
                return self.answer('Précisez le message_id de la décision à expliquer.', outcome='no_source', intent='why')
            todo, seen, lines = [max(matches, key=len)], set(), []
            while todo and len(seen) < 32:
                mid = todo.pop(0)
                if mid in seen:
                    continue
                seen.add(mid)
                msg = self.book.messages.get(mid)
                if msg is None:
                    lines.append('Preuve manquante : ' + mid + ' ; chaîne incomplète.')
                    continue
                lines.append(mid + ' : ' + msg['from'] + ' émet ' + msg['type'] + '.')
                if msg['depends_on']:
                    lines.append('Dépend de : ' + ', '.join(msg['depends_on']) + '.')
                todo.extend(msg['depends_on'])
                finding = msg['payload'].get('finding') if msg['from'] == 'vigie' else None
                if isinstance(finding, dict):
                    cause = str(finding.get('kind', 'constat'))
                    lines.append(self.CAUSES.get(cause, cause) + ' ; cible : ' + str(finding.get('target', '?')) + '.')
                    todo.extend(finding.get('evidence', []))
                if msg['from'] == 'executor':
                    lines.append('Résultat déclaré : ' + str(msg['payload'].get('status', 'inconnu')) + '.')
                    try:
                        reason = json.loads(msg['payload'].get('gate', {}).get('reason', '{}'))
                        todo.extend(reason.get('evidence', []) if isinstance(reason, dict) else [])
                        if isinstance(reason, dict):
                            kind = str(reason.get('kind', 'contrôle du portique'))
                            lines.append('Motif du portique : ' + self.CAUSES.get(kind, kind) + '.')
                    except (TypeError, ValueError, AttributeError):
                        pass
                for src in msg['data_provenance']:
                    lines.append('Source déclarée : ' + str(src.get('service', '?')) +
                                 ', version ' + str(src.get('version', '?')) + ', empreinte ' + str(src.get('response_hash', '?')) + '.')
            if todo:
                lines.append('Chaîne tronquée à 32 références ; aucune conclusion exhaustive.')
            return self.answer('\n'.join(lines), sorted(i for i in seen if i in self.book.messages), intent='why')
        if any(word in q for word in ('alerte', 'incident', 'notification', 'dora', 'bloqu', 'refus', 'virement')):
            incidents = self.incidents()
            lines, evidence = [], []
            relevant = [i for i in incidents if plain(i['target']) in q]
            ranked = sorted(relevant or incidents, key=lambda i: (
                -len(i['operations']), i['cause'] in {'effective_agents', 'llm_similarity', 'herding_index'},
                -i['alerts'], i['target']))
            for item in ranked[:5]:
                total = sum(v for v in item['operations'].values() if v is not None)
                lines.append(f"{item['cause']} — {item['target']} : {item['alerts']} alertes, "
                             f"{len(item['operations'])} opérations refusées, volume documenté {total:,.2f} €. "
                             f"Montants inconnus : {item['amount_unknown']}. Action proposée : examiner les preuves avant toute décision humaine.")
                lines[-1] = self.CAUSES.get(item['cause'], item['cause']) + lines[-1][len(item['cause']):]
                proof = list(dict.fromkeys(item['evidence']))
                # L'humain voit le constat et le dernier refus ; la chaîne
                # détaillée reste accessible par « Pourquoi message_id ? ».
                chosen = [proof[0]] if proof else []
                executions = [mid for mid in proof if self.book.messages[mid]['type'] == 'execution'
                              and self.book.messages[mid]['payload'].get('status') == 'refused']
                if executions:
                    chosen.append(executions[-1])
                lines[-1] += ' Preuves : ' + ', '.join(chosen) + '.'
                evidence.extend(chosen)
            prefix = f'{sum(i["alerts"] for i in incidents)} alertes brutes regroupées en {len(incidents)} incidents ; au plus 5 notifications affichées.\n'
            if 'dora' in q:
                prefix = 'BROUILLON À VALIDER PAR UN HUMAIN — notification initiale d’incident.\n' + prefix
                lines.append('À compléter par le responsable : dates réelles, services et clients affectés, qualification réglementaire, destinataire et échéance. Ce brouillon ne vaut pas déclaration.')
            return self.answer(prefix + '\n'.join(lines), evidence or ids, intent='incidents')
        if 'proposition' in q or 'propose' in q:
            proposals = [m for m in self.book.messages.values() if m['from'] == 'vigie' and m['payload'].get('kind') == 'propositions']
            latest = proposals[-1] if proposals else status
            props = latest['payload'].get('propositions', []) if latest else []
            lines = [str(item.get('constat', '')) + ' Proposition : ' + str(item.get('action', '')) for item in props[:5] if isinstance(item, dict)]
            return self.answer('\n'.join(lines) or 'Aucune proposition dans le dernier instantané signé disponible.', [latest['message_id']] if latest else [])
        if re.search(r'r0|quarantaine|suspect|gele|gel\b|etat|statut|evite|perte|bilan|bonjour|krys|argus', q):
            if not status:
                return self.answer('', [])
            text = (f"Instantané signé au tick {status['tick']}. Sources suspectes sous quarantaine de leurs dépendances : "
                    + (', '.join(p.get('suspects', [])) or 'aucune signalée dans cet instantané') + '. '
                    + 'Actifs gelés : ' + (', '.join(p.get('frozen', [])) or 'aucun signalé dans cet instantané') + '. '
                    + 'R0 de contamination : ' + str(p.get('r0', 'non disponible')) + '. '
                    + 'Pertes évitées : non établies par les observations ; un volume refusé n’est pas une perte évitée.')
            if p.get('uncertain'):
                text += ' État du contrôle dégradé : ' + ', '.join(p['uncertain']) + '.'
            return self.answer(text, ids)
        return self.answer('Je réponds sur les alertes, décisions et preuves d’Argus et Krys. Aucune information disponible pour cette question.', outcome='out_of_scope')


class HumanActions:
    """Autorité séparée, activée explicitement au lancement, jamais par le chat."""
    def __init__(self, book, factory=None, publish=None, *, clock=time.monotonic):
        self.book = book
        self.factory, self.publish, self.clock = factory, publish, clock
        self.pending = {}
        self.lock = threading.Lock()

    def _context(self, action, target):
        if self.factory is None or self.publish is None:
            raise PermissionError('Commandes humaines non autorisées sur cette instance (lecture seule).')
        status = self.book.latest_status()
        if not status or not self.book.complete:
            raise ValueError('État signé complet indisponible ; action refusée.')
        if action == 'unfreeze' and target in status['payload'].get('frozen', []):
            return status, canonical({'frozen': status['payload'].get('frozen'), 'uncertain': status['payload'].get('uncertain')})
        if action == 'resume' and target == '*':
            from bus.lights import LIGHT_AUTHORITIES
            lights = [m for m in self.book.messages.values() if
                      (m['type'] == 'light_change' and m['from'] in LIGHT_AUTHORITIES) or
                      (m['type'] == 'human_decision' and m['from'] == 'human' and m['payload'].get('action') == 'resume')]
            last = lights[-1] if lights else None
            if last and last['payload'].get('light') == 'ROUGE':
                return status, last['message_id']
        raise ValueError('Action non disponible : gel connu requis ; reprise après rouge uniquement. La libération de quarantaine d’agent n’est pas implémentée.')

    def prepare(self, action, target):
        with self.lock:
            status, context = self._context(action, target)
            now = self.clock()
            self.pending = {k: v for k, v in self.pending.items() if now < v['expires']}
            if len(self.pending) >= 32:
                raise ValueError('Trop de confirmations en attente.')
            nonce = secrets.token_urlsafe(24)
            self.pending[nonce] = {'action': action, 'target': target, 'context': context, 'expires': now + 120}
            return {'action_id': nonce, 'recap': f'Action humaine : {action} ; cible : {target}. Une demande signée human sera publiée ; le résultat reste soumis aux contrôles existants.',
                    'expires_in': 120, 'evidence': [status['message_id']]}

    def confirm(self, action_id, confirmation):
        with self.lock:
            if confirmation != 'confirmer':
                raise ValueError('Le bouton Confirmer est requis.')
            item = self.pending.pop(action_id, None)
            if not item or self.clock() >= item['expires']:
                raise ValueError('Confirmation absente, expirée ou déjà consommée.')
            status, context = self._context(item['action'], item['target'])
            if context != item['context']:
                raise ValueError('L’état a changé ; préparez un nouveau récapitulatif.')
            message = self.factory.make(status['tick'], 'human_decision', '*',
                                        {'action': item['action'], 'target': item['target'], 'by': 'operator'})
            self.publish(message)
            return {'message_id': message['message_id'], 'status': 'demande signée publiée ; exécution non présumée'}
