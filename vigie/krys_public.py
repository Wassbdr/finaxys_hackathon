"""Instantané signé produit par Krys, sans exposition de l'état du simulateur."""
from dataclasses import asdict


def public_status(runtime):
    metrics = runtime.contagion.metrics()
    return runtime.factory.make(runtime.state.tick, 'alert', 'human', {
        'kind': 'krys_status', 'frozen': sorted(runtime.freeze.active),
        'suspects': sorted(runtime.contagion.suspects),
        'r0': metrics['r0'], 'uncertain': sorted(runtime.freeze.uncertain),
        'propositions': [asdict(p) for p in runtime.propositions.proposals()[:5]],
    })
