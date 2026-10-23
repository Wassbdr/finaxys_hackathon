"""Processus passif : bus signé en entrée, instantané JSON sur vigie:analytics."""

import argparse
from dataclasses import asdict
from collections import deque

from bus.message import Keyring, canonical
from sim.config import agent_ids, known_senders, load_config, trader_ids
from vigie.herding import Herding


def version(value):
    return tuple(int(x) for x in str(value).split('.'))


class ContagionR0:
    def __init__(self, services, agents, window=300):
        self.services = services
        self.agents = set(agents)
        self.window = window
        self.events = deque()
        self.cves = []
        self.anomalies = set()

    def observe(self, message):
        p = message['payload']
        if message['type'] == 'alert':
            if p.get('kind') == 'cve_published' and message['from'] == 'secu':
                self.cves.append(p['cve'])
            if p.get('kind') == 'data_anomaly':
                self.anomalies.update(p.get('message_ids', []))
        self.events.append(message)

    def vulnerable(self, entry):
        spec = self.services.get(entry.get('service'), {})
        # Une version applicative différente du dernier /meta est inconnue.
        if entry.get('version') != spec.get('version'):
            return False
        return any(c.get('affects', {}).get('language', 'php').lower() == 'php' and
                   version(spec.get('php_version', '999')) < version(c['affects']['version_lt'])
                   for c in self.cves if 'version_lt' in c.get('affects', {}))

    def snapshot(self, tick, effective):
        self.events = deque(m for m in self.events if m['tick'] > tick-self.window)
        roots, inherited, reached, authors = {}, {}, {}, {}
        for m in self.events:
            mid = m['message_id']
            provenance = m.get('data_provenance', [])
            suspicious = mid in self.anomalies or any(self.vulnerable(e) for e in provenance)
            ancestry = set().union(*(inherited.get(parent, set()) for parent in m.get('depends_on', [])))
            # Le hash de réponse relie les consommateurs même sans depends_on explicite.
            hashes = {(e.get('service'), e.get('response_hash')) for e in provenance if e.get('response_hash')}
            for root, root_hashes in roots.items():
                if hashes & root_hashes:
                    ancestry.add(root)
            if suspicious and not ancestry:
                roots[mid] = hashes
                reached[mid] = set()
                authors[mid] = m['from']
                ancestry.add(mid)
            for root in ancestry:
                if mid != root and m['from'] in self.agents and m['from'] != authors[root]:
                    reached[root].add(m['from'])
            inherited[mid] = ancestry
        return {'effective_agents': effective,
                'contagion_r0_estimate': sum(map(len, reached.values()))/len(roots) if roots else None,
                'suspected_source_messages': len(roots),
                'window_ticks': self.window}


class Analytics:
    def __init__(self, cfg, keyring, clean_reference=None):
        self.keyring = keyring
        self.herding = Herding(trader_ids(cfg), clean_reference=clean_reference)
        self.contagion = ContagionR0(cfg['services'], agent_ids(cfg))
        self.tick = -1
        self.seen = {}

    def observe(self, message):
        if not self.keyring.verify(message) or message['message_id'] in self.seen:
            return False
        if message['tick'] <= self.tick-self.contagion.window:
            return False
        self.seen[message['message_id']] = message['tick']
        self.tick = max(self.tick, message['tick'])
        self.herding.observe(message)
        self.contagion.observe(message)
        return True

    def snapshot(self):
        self.seen = {mid: at for mid, at in self.seen.items() if at > self.tick-self.contagion.window}
        h = self.herding.snapshot(self.tick)
        return {'tick': self.tick, 'sampling': 'at_tick_open', 'herding': h, 'contagion': self.contagion.snapshot(self.tick, h['effective_agents']),
                'findings': [asdict(finding) for finding in self.herding.findings(self.tick)]}


def run(bus, engine):
    for message in bus.listen():
        if message is not None and engine.observe(message) and message['type'] == 'tick':
            bus.publish_raw('vigie:analytics', engine.snapshot())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clean-reference', type=float, help='indice mesuré sur un journal propre')
    parser.add_argument('--reference-journal', help='journal propre pour mesurer la référence sur la même fenêtre')
    parser.add_argument('--journal', help='rejoue un journal local sans Redis')
    args = parser.parse_args()
    cfg = load_config()
    keys = Keyring(known_senders(cfg))
    reference = args.clean_reference
    if args.reference_journal:
        from journal.writer import read_journal
        clean = Analytics(cfg, keys)
        values = []
        for entry in read_journal(args.reference_journal):
            message = entry['message']
            if message['type'] == 'tick' and clean.tick >= clean.herding.window:
                value = clean.snapshot()['herding']['herding_index']
                if value is not None:
                    values.append(value)
            clean.observe(message)
        if not values:
            parser.error('journal propre trop court ou sans décisions comparables')
        reference = sum(values)/len(values)
    engine = Analytics(cfg, keys, reference)
    if args.journal:
        from journal.writer import read_journal
        for entry in read_journal(args.journal):
            engine.observe(entry['message'])
        print(canonical(engine.snapshot()))
        return
    from bus.redis_bus import RedisBus, connect
    run(RedisBus(connect(cfg['redis']['url']), cfg['redis']['prefix']), engine)


if __name__ == '__main__':
    main()
