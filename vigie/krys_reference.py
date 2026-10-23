"""Recherche lexicale dans le texte approuvé, sans génération de faits."""
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata


def words(text):
    normalized = ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                         if unicodedata.category(c) != 'Mn')
    stop = {'a', 'au', 'aux', 'de', 'des', 'du', 'd', 'le', 'la', 'les', 'l', 'un', 'une',
            'et', 'ou', 'en', 'dans', 'sur', 'par', 'pour', 'avec', 'sans', 'ce', 'ces',
            'quel', 'quelle', 'quels', 'quelles', 'que', 'qu', 'est', 'sont', 'il', 'ils',
            'elle', 'elles', 't', 'se', 'me', 'mon', 'ma', 'mes', 'comment', 'fonctionne',
            'explique', 'presente', 'sert', 'fait', 'cas', 'passe', 'comme'}
    return {token[:-1] if len(token) > 3 and token.endswith('s') else token
            for token in re.findall(r'[a-z0-9]+', normalized) if token not in stop}


@dataclass(frozen=True)
class Section:
    identifier: str
    title: str
    keywords: tuple[str, ...]
    text: str


class Reference:
    def __init__(self, sections=()):
        self.sections = {section.identifier: section for section in sections}

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path is not None else Path(__file__).with_name('KRYS_EXPLICATIONS.md')
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            return cls()
        sections = []
        for match in re.finditer(r'^## \[([a-z0-9-]+)\] ([^\n]+)\nMots-clés : ([^\n]+)\n(.*?)(?=^## |\Z)',
                                 text, re.M | re.S):
            identifier, title, keywords, body = match.groups()
            sections.append(Section(identifier, title, tuple(keywords.split(', ')), body.strip()))
        return cls(sections)

    def search(self, question):
        query = words(question)
        if not query:
            return None
        tokenized = {sid: [words(keyword) for keyword in section.keywords]
                     for sid, section in self.sections.items()}
        frequency = {word: sum(any(word in phrase for phrase in phrases) for phrases in tokenized.values())
                     for word in query}
        scores = []
        for sid, section in self.sections.items():
            matches = [phrase for phrase in tokenized[sid] if phrase and phrase <= query]
            covered = set().union(*matches) if matches else set()
            # Un nom de produit dans une question étrangère au sujet ne suffit
            # pas. Les mots communs et les thèmes ambigus restent peu probants.
            if not covered or len(covered) / len(query) < 0.5:
                continue
            score = sum(1 / frequency[word] for word in covered)
            score += len(covered & words(section.title))
            scores.append((score, sid))
        scores.sort(reverse=True)
        if not scores or (len(scores) > 1 and scores[0][0] - scores[1][0] < 0.25):
            return None
        return self.sections[scores[0][1]]


def journal_question(question):
    """Les identifiants et demandes opérationnelles gardent leur chemin signé."""
    q = ''.join(c for c in unicodedata.normalize('NFD', question.lower())
                if unicodedata.category(c) != 'Mn')
    if re.search(r'\b[\w-]+:[\w:-]+\b', q):
        return True
    if re.search(r'\b(?:alertes?|incidents?|notifications?|dora|etat|statut|bilan|propositions?)\b|propose', q):
        return True
    definition = bool(re.search(r'\b(?:explique|definition|definir|comment|pourquoi)\b|c.est quoi|qu.est.ce que|a quoi', q))
    if not definition and re.search(r'\br0\b|quarantaine|suspect|gele|\bgel\b|evite|perte|bonjour', q):
        return True
    return bool(re.search(r'\b(?:operations?|virements?)\b.*\b(?:bloque|refuse)', q))
