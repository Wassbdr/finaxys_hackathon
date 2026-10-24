"""Mimétisme observable : décisions, textes et séquences de prix du bus."""

import math
import re
from collections import Counter, defaultdict, deque
from vigie.core import Finding
from itertools import combinations


def effective_agents(rows):
    """Participation spectrale (Σλ)²/Σλ², sans diagonalisation ni dépendance.

    Pour une corrélation symétrique, Σλ² = trace(C²). Les séries constantes
    ne permettent pas d'estimer une corrélation : elles sont exclues.
    """
    centered = []
    for row in rows:
        mean = sum(row) / len(row) if row else 0
        values = [x - mean for x in row]
        norm = math.sqrt(sum(x*x for x in values))
        if norm > 1e-12:
            centered.append([x / norm for x in values])
    n = len(centered)
    if not n:
        return None
    square = sum(sum(a*b for a, b in zip(x, y))**2 for x in centered for y in centered)
    return n*n / square


def text_similarity(documents):
    """Cosinus TF-IDF moyen entre auteurs distincts ; absence de texte = inconnu."""
    docs = [(author, Counter(re.findall(r"[^\W\d_]+", text.lower()))) for author, text in documents]
    docs = [(author, words) for author, words in docs if words]
    df = Counter(word for _, words in docs for word in words)
    vectors = []
    for author, words in docs:
        vec = {w: count * (1 + math.log((1 + len(docs))/(1 + df[w]))) for w, count in words.items()}
        norm = math.sqrt(sum(x*x for x in vec.values()))
        vectors.append((author, {w: x/norm for w, x in vec.items()}))
    scores = [sum(x*y.get(w, 0) for w, x in a.items())
              for (aa, a), (ab, y) in combinations(vectors, 2) if aa != ab]
    return sum(scores)/len(scores) if scores else None


class Herding:
    def __new__(cls, traders, *args, **kwargs):
        # L'ancien constructeur public acceptait la configuration complète.
        # Sa métrique distincte est conservée sans modifier les calculs Dev 2.
        if isinstance(traders, dict):
            from vigie.herding_marius import Herding as MariusHerding
            return MariusHerding(traders, *args, **kwargs)
        return super().__new__(cls)

    def __init__(self, traders, window=30, clean_reference=None):
        self.traders = tuple(traders)
        self.window = window
        self.clean_reference = clean_reference
        self.events = deque()
        self._loop_evidence = []

    def observe(self, message):
        if message['type'] in {'order_proposal', 'price_update', 'execution', 'llm_trace'}:
            self.events.append(message)

    def snapshot(self, tick):
        self.events = deque(m for m in self.events if m['tick'] > tick-self.window)
        decisions = defaultdict(dict)
        docs = []
        prices = {}
        sales = {}
        drops = {}
        loops = 0
        self._loop_evidence = []
        price_ids = {}
        for m in self.events:
            p, kind = m['payload'], m['type']
            if kind == 'execution' and p.get('status') == 'executed' and p.get('kind') == 'order' and p.get('qty', 0) < 0:
                sales[p['asset']] = (m['tick'], m['message_id'])
            elif kind == 'price_update' and m['from'] == 'quant':
                for asset, value in p.get('assets', {}).items():
                    price = value['price']
                    if asset in prices and price < prices[asset] and asset in sales and sales[asset][0] < m['tick']:
                        drops[asset] = (m['message_id'], sales[asset][1], price_ids[asset])
                    else:
                        drops.pop(asset, None)
                    prices[asset] = price
                    price_ids[asset] = m['message_id']
            elif kind == 'order_proposal' and m['from'] in self.traders:
                decisions[(m['tick'], p['asset'])][m['from']] = 1 if p['side'] == 'buy' else -1
                if p['side'] == 'sell' and p['asset'] in drops and drops[p['asset']][0] in m.get('depends_on', []):
                    loops += 1
                    self._loop_evidence.extend([*drops[p['asset']], m['message_id']])
            elif kind == 'llm_trace' and m['from'] in self.traders:
                text = p.get('response', {}).get('justification', '')
                if text:
                    docs.append((m['from'], text))
        # Les traders décident de façon asynchrone : leur dernière direction reste
        # observable jusqu'à la prochaine décision, dans la fenêtre seulement.
        columns = sorted(decisions)
        rows = [[] for _ in self.traders]
        latest = defaultdict(dict)
        assets = sorted({asset for _, asset in columns})
        for at in range(max(0, tick-self.window+1), tick+1):
            for asset in assets:
                latest[asset].update(decisions.get((at, asset), {}))
                for row, agent in zip(rows, self.traders):
                    row.append(latest[asset].get(agent, 0))
        groups = defaultdict(dict)
        for (at, asset), votes in decisions.items():
            for agent, side in votes.items():
                groups[asset][agent] = groups[asset].get(agent, 0) + side
        alignment = []
        for votes in groups.values():
            active = [1 if x > 0 else -1 for x in votes.values() if x]
            if len(active) >= 2:
                alignment.append(max(active.count(1), active.count(-1))/len(active))
        index = sum(alignment)/len(alignment) if alignment else None
        return {'herding_index': index,
                'herding_excess': index-self.clean_reference if index is not None and self.clean_reference is not None else None,
                'clean_reference': self.clean_reference,
                'effective_agents': effective_agents(rows), 'decision_samples': len(columns),
                'llm_similarity': text_similarity(docs), 'reflexive_loops': loops}


    def findings(self, tick):
        """Produit les observations disponibles ; aucune décision de portique."""
        metrics = self.snapshot(tick)
        orders = [m['message_id'] for m in self.events
                  if m['type'] == 'order_proposal' and m['from'] in self.traders]
        traces = [m['message_id'] for m in self.events
                  if m['type'] == 'llm_trace' and m['from'] in self.traders
                  and m['payload'].get('response', {}).get('justification')]
        findings = []
        for kind in ('herding_index', 'herding_excess', 'effective_agents', 'llm_similarity', 'reflexive_loops'):
            value = metrics[kind]
            if value is None or (kind == 'reflexive_loops' and not value):
                continue
            evidence = traces if kind == 'llm_similarity' else self._loop_evidence if kind == 'reflexive_loops' else orders
            if evidence:
                findings.append(Finding(tick=tick, layer=3, kind=kind, target='traders',
                                        severity='info', evidence=list(dict.fromkeys(evidence)), score=float(value)))
        return findings

# Seuils de la métrique historique, conservés pour ses appelants existants.
from vigie.herding_marius import THRESHOLDS
