"""Le texte affiché dit Argus, Krys et Sentinel, en français : ni « La Vigie », ni « KRYS », ni « Ours/Theirs », ni lettres de banque."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOWN = ['vigie/landing.html', 'vigie/dashboard.html', 'vigie/dashboard.js', 'vigie/simulation.js', 'vigie/demo_page.html',
         'vigie/regulation.html', 'vigie/krys_chat.js', 'vigie/console/static/simulation.css', 'vigie/console/static/header.css',
         *sorted(str(p.relative_to(ROOT)) for p in (ROOT/'vigie/console').glob('*.js'))]
OLD_NAMES = [r'\bLa Vigie\b', r'\bVigie classi', r'\bKRYS\b(?!_)', r'\bOurs\b', r'\bTheirs\b', r'\bBANQUE [ABC]\b', r'\b[ABC] · ']
# the titles and tabs of the console and of the 3D before they were French
OLD_TITLES = ['>Summary<', '>Guardrails<', '>Metrics<', '>Improvements<', 'Waiting for a human', 'Open incidents', 'Who feeds whom',
              'For the CEO', 'Thresholds in force', 'Proposals for a human', 'Problems only a human clears', 'What is approaching a limit',
              '"Guardrails"', 'Settings<', 'Save as default', 'Losses avoided', 'Extra losses', '"Refused"', 'Stop on a problem',
              'No replay yet', 'Our Vigie', 'Bank<', 'Console live', 'console live']


def shown(rel):
    """The file without its comments: they may keep the old names, the screen may not."""
    text = (ROOT/rel).read_text()
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return '\n'.join(re.sub(r'(^|[^:"\'`\\])//.*$', r'\1', line) for line in text.split('\n'))


def test_no_old_name_on_screen():
    for rel in SHOWN:
        text = shown(rel)
        for name in OLD_NAMES:
            assert not re.search(name, text), (rel, name, re.search(name, text) and text[max(0, re.search(name, text).start() - 60):][:140])


def test_titles_and_tabs_are_french():
    for rel in ['vigie/dashboard.html', 'vigie/dashboard.js', 'vigie/simulation.js', 'vigie/landing.html', 'vigie/demo_page.html',
                'vigie/console/helper.js', 'vigie/console/static/simulation.css']:
        text = shown(rel)
        for title in OLD_TITLES:
            assert title not in text, (rel, title)
    for rel in ['vigie/landing.html', 'vigie/dashboard.html', 'vigie/demo_page.html', 'vigie/regulation.html']:
        assert '<html lang="fr">' in (ROOT/rel).read_text(), rel


def test_the_unprotected_bank_is_never_shown_by_its_old_name():
    for rel in SHOWN:
        for line in shown(rel).split('\n'):
            # impact.js renames the archived label, or leaves its bank out: the only places the old name may be written
            if 'Sans Krys' in line:
                assert rel == 'vigie/console/impact.js' and ('Sans protection' in line or "!== 'Sans Krys'" in line), (rel, line.strip()[:120])
