"""Recommandations humaines sur les seules preuves publiques, sans remédiation."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from bus.message import Keyring, MessageFactory, canonical
from journal.writer import GENESIS, entry_hash
from sim.config import known_senders, load_config
from vigie.core import Finding


@dataclass
class Proposition:
    code: str
    target: str
    constat: str
    evidence: list[str]
    action: str
    gain: str
    cout_risque: str
    decideur: str
    score: float

    def finding(self, tick):
        return Finding(tick, 3, 'proposition', self.target, 'info', self.evidence, self.score)


def _version(value):
    parts = tuple(int(part) for part in value.split('.'))
    return parts + (0,) * max(0, 3-len(parts))


class Propositions:
    """Score de priorité explicite (0–100), sans estimation financière du gain.

    Il classe des pistes d'amélioration ; il ne pilote ni feux ni portique.
    Aucun accès à Redis, au moteur bancaire ou à ses annotations cachées.
    """

    def __init__(self, keyring, meta=None, *, max_age=5, factory=None, max_messages=None, max_bytes=None):
        self.keyring = keyring
        self.meta = {sid: {key: value[key] for key in
                     ('version', 'language', 'php_version', 'patchable') if key in value}
                     for sid, value in (meta or {}).items()}
        self.max_age = max_age
        self.factory = factory or MessageFactory('vigie', keyring, deterministic=False)
        self.max_messages = max_messages
        self.max_bytes = max_bytes
        self.observed_bytes = 0
        self.history_complete = True
        self.messages = {}
        self.tick = 0
        self.journal_check = None

    def observe(self, raw):
        try:
            if not self.keyring.verify(raw) or raw['message_id'] in self.messages:
                return False
            if type(raw['tick']) is not int or raw['tick'] < 0:
                return False
            # Une projection explicite protège aussi contre les champs de vérité
            # présents dans certains journaux du simulateur.
            allowed = {
                'order_proposal': ('notional_eur', 'asset'),
                'payment_proposal': ('amount_eur',),
                'risk_decision': ('approved',),
                'execution': ('status',),
                'price_update': ('assets',),
                'llm_trace': ('prompt_excerpt', 'prompt_hash', 'response', 'prompt'),
                'heartbeat': ('view',),
                'alert': ('kind', 'cve', 'decision', 'approved'),
                'human_decision': ('action',),
            }
            p = raw['payload']
            record = {key: raw[key] for key in ('message_id', 'tick', 'from', 'type')}
            record['payload'] = {key: p[key] for key in allowed.get(raw['type'], ()) if key in p}
            record['depends_on'] = list(raw.get('depends_on', []))
            record['data_provenance'] = [{key: entry[key] for key in
                ('service', 'version', 'response_hash', 'tick', 'response') if key in entry}
                for entry in raw.get('data_provenance', [])]
            if not all(isinstance(parent, str) for parent in record['depends_on']):
                return False
            size = len(canonical(record).encode('utf-8'))
            # Le direct ne doit pas recréer un historique illimité en doublant
            # les entrées bornées et les alertes produites par la Vigie.
            if ((self.max_messages is not None and len(self.messages) >= self.max_messages)
                    or (self.max_bytes is not None and self.observed_bytes + size > self.max_bytes)):
                self.history_complete = False
                return False
            self.observed_bytes += size
            self.messages[raw['message_id']] = record
            self.tick = max(self.tick, raw['tick'])
            return True
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    def _chain(self, message):
        pending, visited, sources, evidence = [message], set(), [], []
        complete = True
        while pending:
            current = pending.pop()
            mid = current['message_id']
            if mid in visited:
                continue
            visited.add(mid)
            evidence.append(mid)
            for entry in current['data_provenance']:
                if entry.get('service') and entry.get('response_hash'):
                    sources.append(entry)
                else:
                    complete = False
            if not current['depends_on'] and not current['data_provenance']:
                complete = False
            for parent in current['depends_on']:
                if parent not in self.messages:
                    complete = False
                else:
                    pending.append(self.messages[parent])
        return complete and bool(sources), sources, evidence

    def proposals(self):
        actions = [m for m in self.messages.values() if m['type'] in {'order_proposal', 'payment_proposal'}
                   or (m['type'] == 'risk_decision' and m['payload'].get('approved') is True)
                   or (m['type'] == 'execution' and m['payload'].get('status') == 'executed')]
        chains = [(m, *self._chain(m)) for m in actions]
        out = []
        def add(code, target, constat, evidence, action, gain, cost, owner, score):
            if evidence:
                out.append(Proposition(code, target, constat, list(dict.fromkeys(evidence)),
                                       action, gain, cost, owner, score))

        holes = [m['message_id'] for m, complete, _, _ in chains if not complete]
        if holes:
            rate = (len(actions)-len(holes))/len(actions)
            add('traceability', 'journal', f'{len(holes)}/{len(actions)} messages d’action ne remontent pas complètement à une source ; reconstitution {rate:.1%}.', holes,
                'Exiger une source identifiable et les références des messages utilisés, puis contrôler les liens manquants.',
                'Permettre de reconstituer les décisions et d’examiner un incident.',
                'Adaptation des émetteurs et volume supplémentaire de journal.', 'Responsable des données et responsables des agents', 90)

        unique = []
        unique_services = set()
        for m, _, sources, evidence in chains:
            feeds = [self.messages[mid] for mid in evidence if self.messages[mid]['type'] == 'price_update']
            if feeds:
                for feed in feeds:
                    providers = {e.get('service') for e in feed['data_provenance'] if e.get('service')}
                    if len(providers) == 1:
                        unique.extend([m['message_id'], feed['message_id']])
                        unique_services.update(providers)
            elif len({e.get('service') for e in sources if e.get('service')}) == 1:
                unique.extend(evidence)
                unique_services.update(e.get('service') for e in sources if e.get('service'))
        if unique:
            add('single_source', 'sources', f'Des décisions observées utilisent une seule source pour une donnée ({", ".join(sorted(unique_services))}), sans corroboration visible dans leurs références.', unique,
                'Ajouter une source indépendante pour cette donnée et comparer les valeurs avant décision.',
                'Réduire la dépendance à une donnée erronée ou indisponible.',
                'Abonnement éventuel, intégration et traitement des divergences.', 'Responsable des données de marché', 80)

        cves = [m for m in self.messages.values() if m['from'] == 'secu' and m['type'] == 'alert' and m['payload'].get('kind') == 'cve_published']
        exposed, cve_evidence, amounts = [], [], {}
        vulnerable_services = set()
        for m, _, sources, evidence in chains:
            for entry in sources:
                meta = self.meta.get(entry.get('service'), {})
                if meta.get('patchable') is not False or meta.get('version') != entry.get('version'):
                    continue
                for cve in cves:
                    affects = cve['payload'].get('cve', {}).get('affects', {})
                    try:
                        vulnerable = (meta.get('language') == affects.get('language') and
                                      _version(meta['php_version']) < _version(affects['version_lt']))
                    except (KeyError, ValueError, AttributeError):
                        vulnerable = False
                    if vulnerable:
                        exposed.extend(evidence)
                        vulnerable_services.add(entry['service'])
                        cve_evidence.append(cve['message_id'])
                        if m['type'] in {'order_proposal', 'payment_proposal'}:
                            amount = m['payload'].get('notional_eur', m['payload'].get('amount_eur', 0))
                            if isinstance(amount, (int, float)) and math.isfinite(amount) and amount >= 0:
                                amounts[m['message_id']] = amount
        if exposed:
            add('vulnerable_chain', 'services', f'Une version connue vulnérable et déclarée non patchable figure dans des chaînes de décision ({", ".join(sorted(vulnerable_services))}) ; propositions concernées : {sum(amounts.values()):,.2f} € (volume proposé, pas pertes).', exposed+cve_evidence,
                'Faire valider l’isolement ou le retrait de cette source ; étudier un remplacement permettant la mise à jour.',
                'Réduire l’exposition aux versions visées par la CVE publiée.',
                'Risque de rupture de service ; plan de continuité nécessaire.', 'Responsable sécurité et propriétaire du service', 100)

        stale = []
        ages = []
        hashes = []
        for m, _, sources, _ in chains:
            for entry in sources:
                at = entry.get('tick')
                if type(at) is int and m['tick']-at > self.max_age:
                    stale.append(m['message_id'])
                    ages.append(m['tick']-at)
                if entry.get('response_hash') and 'response' not in entry:
                    hashes.append(m['message_id'])
        if stale:
            add('stale_data', 'donnees', f'Des actions utilisent des données âgées de plus de {self.max_age} ticks ; âge maximal observé : {max(ages)} ticks.', stale,
                'Définir une durée de validité avec le métier et demander un rafraîchissement au-delà.',
                'Éviter des décisions fondées sur des données dépassées.',
                'Appels supplémentaires et traitement des indisponibilités.', 'Responsable métier et propriétaire des données', 85)

        truncated = [m['message_id'] for m in self.messages.values() if m['type'] == 'llm_trace'
                     and m['payload'].get('prompt_hash') and not m['payload'].get('prompt')
                     and isinstance(m['payload'].get('prompt_excerpt'), str)
                     and len(m['payload']['prompt_excerpt']) > 600
                     and m['payload']['prompt_excerpt'].endswith('…')]
        journal_issues, journal_evidence = [], []
        if truncated:
            journal_issues.append('des requêtes au modèle sont limitées à un extrait et une empreinte')
            journal_evidence.extend(truncated)
        if hashes:
            journal_issues.append('des réponses de services ne sont représentées que par leur empreinte dans les chaînes observées')
            journal_evidence.extend(hashes)
        vigie = [m['message_id'] for m in self.messages.values() if m['from'] == 'vigie' and m['type'] == 'heartbeat']
        decisions = [m for m in self.messages.values() if m['from'] == 'vigie' and m['type'] == 'alert'
                     and m['payload'].get('kind') in {'gate_decision', 'vigie_decision', 'decision_log'}]
        if actions and vigie and not decisions:
            journal_issues.append('le journal fourni contient des actions et des présences de la Vigie, mais aucune trace explicite de ses décisions')
            journal_evidence.extend([vigie[0], actions[0]['message_id']])
        if self.journal_check and not self.journal_check['ok']:
            journal_issues.append(f"la chaîne d’intégrité du journal est rompue ou illisible à la ligne {self.journal_check['line']}")
            journal_evidence.append(f"journal:ligne:{self.journal_check['line']}")
        if journal_issues:
            add('audit_journal', 'journal', 'Dans les éléments fournis, '+ ' ; '.join(journal_issues)+'.', journal_evidence,
                'Conserver les contenus nécessaires à la relecture dans un stockage protégé, tracer les décisions de la Vigie et vérifier la chaîne d’intégrité à chaque clôture.',
                'Faciliter l’audit et la relecture des incidents ; cela ne constitue pas une attestation de conformité.',
                'Volume, accès restreint, confidentialité et durée de conservation à faire valider.', 'Responsable audit et responsable de la protection des données', 95 if self.journal_check and not self.journal_check['ok'] else 65)

        timing = []
        for m in self.messages.values():
            if m['type'] == 'heartbeat' and m['from'] == 'risk':
                view = m['payload'].get('view', {})
                if 'cash_eur' in view and not any(type(view.get(key)) is int and 0 <= view[key] <= m['tick'] for key in ('as_of_tick', 'calculation_tick', 'tick')):
                    timing.append(m['message_id'])
        by_sender = {}
        for m in self.messages.values():
            if m['type'] == 'heartbeat':
                by_sender.setdefault(m['from'], []).append(m)
        for messages in by_sender.values():
            ordered = sorted(messages, key=lambda m: m['tick'])
            if len(ordered) < 3:
                continue
            gaps = [b['tick']-a['tick'] for a, b in zip(ordered, ordered[1:]) if b['tick'] > a['tick']]
            if not gaps:
                continue
            cadence = min(gaps)
            for a, b in zip(ordered, ordered[1:]):
                if b['tick']-a['tick'] > cadence:
                    timing.extend([a['message_id'], b['message_id']])
        if timing:
            add('timestamps', 'horodatage', 'Une trésorerie de Risk n’indique pas son tick de calcul, ou des intervalles de présence sont irréguliers ; la cause du retard n’est pas établie.', timing,
                'Ajouter le tick de calcul des valeurs et les instants de réception et de traitement ; préciser la cadence attendue.',
                'Distinguer une donnée ancienne, un retard de traitement et une absence de message.',
                'Instrumentation des agents et gestion du décalage des horloges.', 'Responsable de l’exploitation et responsable Risk', 70)
        return sorted(out, key=lambda p: (-p.score, p.code))[:8]

    def findings(self):
        return [p.finding(self.tick) for p in self.proposals()]

    def alert(self):
        proposals = self.proposals()
        return self.factory.make(self.tick, 'alert', 'human', {
            'kind': 'propositions', 'history_complete': self.history_complete, 'decision_required': True,
            'propositions': [asdict(p) for p in proposals],
            'findings': [asdict(p.finding(self.tick)) for p in proposals]},
            depends_on=list(dict.fromkeys(mid for p in proposals for mid in p.evidence if mid in self.messages)))

    def report(self):
        proposals = self.proposals()
        paragraphs = ['# Propositions soumises à décision humaine',
                      'Aucune action automatique. Chaque proposition attend la décision du responsable indiqué. Le classement est une priorité qualitative, pas un gain financier estimé.']
        if not proposals:
            paragraphs.append('Aucun défaut démontré dans les éléments fournis ; aucune proposition.')
        for index, p in enumerate(proposals, 1):
            paragraphs.append(f'{index}. **{p.constat}** Proposition : {p.action} Gain attendu : {p.gain} Coût ou risque : {p.cout_risque} Décideur : {p.decideur}. Preuves : '+', '.join(p.evidence[:6])+ (f' (et {len(p.evidence)-6} autres références dans propositions.alert.json).' if len(p.evidence)>6 else '.'))
        return '\n\n'.join(paragraphs)+'\n'


def from_journal(path, keyring, meta=None, *, max_age=5):
    engine = Propositions(keyring, meta, max_age=max_age)
    prev, sequence = GENESIS, 0
    engine.journal_check = {'ok': True, 'line': None}
    with Path(path).open(encoding='utf-8') as stream:
        for line, text in enumerate(stream, 1):
            try:
                entry = json.loads(text)
                sequence += 1
                valid_chain = (entry['seq'] == sequence and entry['prev_hash'] == prev
                    and entry['hash'] == entry_hash(prev, sequence, entry['signature_valid'], entry['message']))
                if not valid_chain and engine.journal_check['ok']:
                    engine.journal_check = {'ok': False, 'line': line}
                prev = entry['hash']
                # Le drapeau du journal ne remplace jamais la vérification HMAC.
                engine.observe(entry['message'])
            except (KeyError, TypeError, ValueError):
                if engine.journal_check['ok']:
                    engine.journal_check = {'ok': False, 'line': line}
    return engine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--meta', help='copie JSON des réponses /meta ; sinon <run>/meta.json si présent')
    parser.add_argument('--max-age', type=int, default=5)
    args = parser.parse_args(argv)
    if args.max_age < 0:
        parser.error('âge maximal négatif')
    cfg = load_config()
    run = Path(args.run)
    path = Path(args.meta) if args.meta else run/'meta.json'
    meta = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    engine = from_journal(run/'journal.jsonl', Keyring(known_senders(cfg)), meta, max_age=args.max_age)
    (run/'propositions.md').write_text(engine.report(), encoding='utf-8')
    (run/'propositions.alert.json').write_text(json.dumps(engine.alert(), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'{run}/propositions.md : {len(engine.proposals())} propositions en attente de décision humaine')
    if not meta:
        print('Versions /meta absentes : aucune exposition CVE affirmée.')


if __name__ == '__main__':
    main()
