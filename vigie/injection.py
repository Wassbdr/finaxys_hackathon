"""Heuristiques d'instructions dans les données ; propagation par réponse, sans oracle.

Ce module ne prétend pas remplacer un classifieur d'injection. Il fournit une
source suspecte et sa preuve au moteur de contamination jusqu'au portique.
"""

import base64
import binascii
import re
import unicodedata
from urllib.parse import unquote

from vigie.core import Finding

MAX_TEXT = 8192
MAX_BLOCKS = 8


def _plain(text):
    text = unicodedata.normalize('NFKC', text)
    text = ''.join(c for c in text if unicodedata.category(c) not in {'Cf', 'Cc'} or c in '\n\r\t')
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if unicodedata.category(c) != 'Mn')


_PATTERNS = {
    'override_instructions': re.compile(
        r'\b(?:ignore[rzs]?|oublie[rzs]?)\s+(?:(?:toutes?|all|the|tes|vos|les|previous|precedentes?)\s+){0,5}'
        r'(?:instructions?|consignes?|regles|limites|controles)\b'),
    'role_change': re.compile(
        r'(?:^|\n)\s*(?:system|systeme|assistant|developer|developpeur)\s*:|'
        r'<\|(?:im_start|start_header_id)\|>\s*(?:system|assistant)|'
        r'\b(?:(?:tu es|vous etes|you are) (?:desormais|maintenant|now) (?:un |une )?(?:assistant|agent|system|chatgpt)|'
        r'your new role|act as (?:the |an? )?(?:system|assistant)|change de role)\b', re.M),
    'bypass_approval': re.compile(
        r'\b(?:approuve[rzs]?|approve|valide[rzs]?)\s+(?:directement|immediatement|sans (?:controle|verification))\b|'
        r'\b(?:transfere[rzs]?|transfer|wire)\b[^.\n]{0,90}\b(?:sans (?:controle|verification)|without (?:checks|verification)|ignore)\b|'
        r'\b(?:agent|assistant|compliance)\s*[,;:]\s*(?:approuve[rzs]?|approve|transfere[rzs]?)\b'),
    'concealment': re.compile(
        r'\b(?:ne signale[rzs]? pas|ne mentionne[rzs]? pas|do not report|do not disclose)\s+'
        r'(?:(?:ce|cet|cette|ces|le|la|les|the|this|l[’\x27])\s*)?(?:instruction|consigne|virement|transaction|alerte|injection)\b'),
}


def instruction_signals(text, *, decode=True):
    """Ne confond pas un impératif métier banal et une tentative de détournement."""
    if not isinstance(text, str):
        return ()
    text = text[:MAX_TEXT]
    normalized = _plain(text)
    signals = {name for name, pattern in _PATTERNS.items() if pattern.search(normalized)}
    # Les contrôles de direction et séparateurs invisibles au milieu du texte
    # sont suspects. Le BOM initial, les accents, sauts de ligne et emoji ZWJ
    # usuels ne constituent pas seuls une instruction malveillante.
    if any(c in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200b\u2060' for c in text):
        signals.add('hidden_control')
    if re.search(r'[A-Za-z][\u200c\u200d\ufeff][A-Za-z]', text):
        signals.add('hidden_control')
    if signals and re.search(r'<!--|display\s*:\s*none|visibility\s*:\s*hidden|hidden\s*=', text, re.I):
        signals.add('hidden_markup')
    if decode:
        decoded = []
        for token in re.findall(r'(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,2048}={0,2}(?![A-Za-z0-9+/=])', text)[:4]:
            try:
                decoded.append(base64.b64decode(token + '='*((-len(token)) % 4), validate=True).decode('utf-8'))
            except (ValueError, UnicodeError, binascii.Error):
                pass
        if len(re.findall(r'%[0-9a-fA-F]{2}', text)) >= 4:
            decoded.append(unquote(text))
        for token in re.findall(r'\b(?:hex:|0x)([0-9a-fA-F]{32,2048})\b', text)[:4]:
            try:
                decoded.append(bytes.fromhex(token).decode('utf-8'))
            except (ValueError, UnicodeError):
                pass
        if any(instruction_signals(candidate, decode=False) for candidate in decoded):
            signals.add('encoded_instruction')
    return tuple(sorted(signals))


def external_blocks(msg):
    """Les consignes fiables et les sorties LLM ne sont jamais des sources externes."""
    p = msg['payload']
    if msg['type'] != 'llm_trace':
        return []
    blocks = []
    external = p.get('external', [])
    if isinstance(external, list):
        for item in external[:MAX_BLOCKS]:
            if isinstance(item, dict) and isinstance(item.get('source'), str) and isinstance(item.get('text'), str):
                blocks.append((item['source'], str(item.get('id', '')), item['text'][:MAX_TEXT]))
    if blocks:
        return blocks
    # Un ancien journal peut ne contenir que l'extrait. On ne scanne que
    # l'intérieur d'un bloc externe attribuable, même si l'extrait est coupé.
    excerpt = p.get('prompt_excerpt', '')
    if isinstance(excerpt, str):
        pattern = r'<donnee_externe\s+source="([^"]+)"\s+id="([^"]*)">(.*?)(?:</donnee_externe>|\Z)'
        for source, document, text in re.findall(pattern, excerpt[:MAX_TEXT], re.S)[:MAX_BLOCKS]:
            blocks.append((source, document, text))
    if msg['from'].startswith('svc:') and isinstance(p.get('response'), dict):
        text = p['response'].get('text')
        if isinstance(text, str):
            blocks.append((msg['from'][4:], str(p['response'].get('id', '')), text[:MAX_TEXT]))
    return blocks


class Injection:
    def __init__(self, state):
        self.state = state
        self.responses = {}
        self.seeds = {}
        self.signals = {}

    def on_message(self, msg):
        out = []
        for source, document, text in external_blocks(msg):
            signals = instruction_signals(text)
            if not signals:
                continue
            hashes = {entry.get('response_hash') for entry in msg.get('data_provenance', [])
                      if entry.get('service') == source and entry.get('response_hash')}
            # L'étiquette source du texte ne suffit pas à accuser un service :
            # elle doit correspondre à la provenance signée du consommateur.
            if len(hashes) != 1:
                continue
            response_hash = next(iter(hashes))
            key = (source, response_hash)
            target = f'response:{source}:{response_hash}'
            if key in self.responses:
                self.seeds.setdefault(msg['message_id'], set()).add(target)
                continue
            finding = Finding(self.state.tick, 3, 'prompt_injection', target, 'warning', [msg['message_id']], 1.0)
            self.responses[key] = finding
            self.seeds.setdefault(msg['message_id'], set()).add(target)
            self.signals[target] = {'source': source, 'document': document, 'reasons': signals}
            out.append(finding)
        return out

    def targets(self, msg):
        targets = set(self.seeds.get(msg['message_id'], ()))
        for entry in msg.get('data_provenance', []):
            finding = self.responses.get((entry.get('service'), entry.get('response_hash')))
            if finding is not None:
                targets.add(finding.target)
        return targets
