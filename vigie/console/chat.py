"""Raccord de la console autonome : seul le journal signé alimente Krys."""
import os
from pathlib import Path

from bus.message import Keyring
from journal.writer import journal_path
from sim.config import known_senders
from vigie.demo_security import protect_mutations
from vigie.krys import Knowledge
from vigie.krys_api import attach_krys


def attach_standalone(app, cfg, *, journal=None):
    # Ne pas utiliser LiveSource.state, ses positions ou sa vérité de simulation.
    # Le journal peut être choisi par le lanceur, jamais par une question du chat.
    prefix = cfg.get('worlds', {}).get('C', cfg['redis']['prefix'])
    path = Path(journal or os.environ.get('KRYS_CONSOLE_JOURNAL') or
                journal_path({**cfg, 'redis': {**cfg['redis'], 'prefix': prefix}}))
    keys = Keyring(known_senders(cfg))
    protect_mutations(app, [f'http://127.0.0.1:{cfg["api"]["port"]}'])
    attach_krys(app, lambda: Knowledge.journal(path, keys.verify))
