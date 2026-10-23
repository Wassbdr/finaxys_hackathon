"""Évaluation hors ligne de l'injection ; l'oracle reste dans vigie.eval.play."""

import argparse
import json
from pathlib import Path
import sys

from sim.config import load_config
from sim.clock import Clock, parse_at
from sim.inject import load_scenario
from vigie.eval import play
from vigie.testing import deny_network

SCENARIOS = ('dossier_piege', 'clean', 'php2shell_day', 'ledger_legacy')


def evaluate(name, seed):
    if name not in SCENARIOS:
        raise ValueError(name)
    cfg = load_config(overrides={'seed': seed, 'llm': {'enabled': False, 'cache': False}})
    scenario = ({'name': 'clean', 'duration': '180s', 'steps': []} if name == 'clean'
                else load_scenario(Path(__file__).resolve().parents[1]/'scenarios'/f'{name}.yaml'))
    without, protected = play(cfg, scenario, None, False, capture_actions=True), play(cfg, scenario, None, True, capture_actions=True)
    def compact(run):
        injections = {key: value for key, value in run['detections'].items()
                      if value['kind'] == 'prompt_injection'}
        for target, finding in injections.items():
            source = target.split(':', 2)[1]
            attack = next((parse_at(step['at'], Clock.from_config(cfg)) for step in scenario.get('steps', [])
                           if step.get('do') == 'compromise '+source), None)
            finding['attack_tick'] = attack
            finding['delay_from_attack_ticks'] = finding['tick']-attack if attack is not None else None
        return {'payment_actions': run['payment_actions'], 'pnl_net_eur': run['pnl_net_eur'], 'fraud_loss_eur': run['fraud_loss_eur'],
                'trapped_operations': run['trapped_operations'], 'marking': run['marking'],
                'online_marking': run['online_marking'], 'injection_findings': injections,
                'detections': run['detections'], 'blocked_operations': run['blocked_operations'],
                'freeze_guard_refusals': run['freeze_guard_refusals'],
                'freeze_blocked_operations': run['freeze_blocked_operations']}
    return {'scenario': name, 'seed': seed, 'sans_vigie': compact(without), 'avec_couche3': compact(protected),
            'avoided_loss_eur': round(protected['pnl_net_eur']-without['pnl_net_eur'], 2),
            'avoided_fraud_eur': round(without['fraud_loss_eur']-protected['fraud_loss_eur'], 2)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', action='append', choices=SCENARIOS)
    parser.add_argument('--seed', action='append', type=int)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    sys.addaudithook(deny_network)
    results = [evaluate(name, seed) for seed in args.seed or [42, 43]
               for name in args.scenario or SCENARIOS]
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for result in results:
        protected = result['avec_couche3']
        print(f"{result['scenario']} seed={result['seed']} : {len(protected['injection_findings'])} réponses suspectes, "
              f"fraude évitée={result['avoided_fraud_eur']:.2f} €, "
              f"faux positifs={protected['marking']['false_positives']}")


if __name__ == '__main__':
    main()
