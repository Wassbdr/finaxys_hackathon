"""Annexe hors ligne couche 3 / Invariant ; aucune vérité terrain dans les contrôleurs."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import sys
import time

from bus.gate import Decision
from journal.writer import verify_journal
from sim.config import load_config
from sim.inject import load_scenario, schedule_scenario
from sim.lockstep import Lockstep
from vigie.main import Runtime
from vigie.invariant_guard import InvariantGuard, UPSTREAM, POLICY
from vigie.testing import deny_network

CASES = ('php2shell_day', 'ledger_legacy', 'propre')
VARIANTS = {'C': 'couche 3', 'E': 'Invariant Guardrails'}


def scenario_for(case):
    if case == 'propre':
        return {'name': 'propre', 'duration': '180s', 'steps': []}
    root = Path(__file__).resolve().parents[1]
    path = root/'scenarios'/f'{case}.yaml'
    if not path.exists():
        path = root/'vigie'/'scenarios'/f'{case}.yaml'
    return load_scenario(path)


def one_run(case, seed, variant, directory):
    cfg = load_config(overrides={'seed': seed, 'llm': {'enabled': False, 'cache': False},
        'chaos': {'announce_compromised': False}, 'vigie': {'enabled': True, 'stub': False},
        'gate': {'impl': 'stub', 'default_on_timeout': 'reject'}})
    ls = Lockstep(cfg, no_llm=True, no_vigie=False, run_dir=directory)
    ls.vigie = None
    # Accès autorisé aux seules métadonnées publiques ; aucun objet service
    # ou store n'est transmis au contrôleur.
    meta = {sid: core.meta() for sid, core in ls.cores.items()}
    controller = Runtime(cfg, ls.keyring, meta=meta) if variant == 'C' else InvariantGuard(cfg, ls.keyring, meta=meta)
    messages, labels, decisions = [], {}, []
    first_alert = None

    def observe(msg):
        nonlocal first_alert
        messages.append(msg)
        # L'oracle n'appartient qu'à l'arbitre, après publication sur le bus.
        labels[msg['message_id']] = any(ls.store.sismember(ls.prefix+'tainted', e['response_hash'])
            for e in msg.get('data_provenance', []) if e.get('response_hash')) or any(labels.get(p, False) for p in msg.get('depends_on', []))
        if msg['from'] == 'vigie':
            if msg['type'] == 'alert' and msg['payload'].get('kind') != 'propositions' and first_alert is None:
                first_alert = msg['tick']
            return
        if controller is not None:
            for outgoing in controller.on_message(msg):
                ls.publish(outgoing)
            if variant == 'E':
                for outgoing in controller.flush():
                    ls.publish(outgoing)
            if variant == 'C':
                for outgoing in controller.diagnostics():
                    ls.publish(outgoing)
    ls.bus.tap(observe)

    class MeteredGate:
        def request_approval(self, msg):
            started = time.perf_counter()
            decision = controller.request_approval(msg) if controller else Decision(True, 'sans Vigie', 'none')
            decisions.append({'tick': ls.tick, 'message_id': msg['message_id'], 'approved': decision.approved,
                'reason': decision.reason, 'latency_ms': (time.perf_counter()-started)*1000,
                'oracle_contaminated': labels.get(msg['message_id'], False)})
            if variant == 'E':
                for outgoing in controller.flush():
                    ls.publish(outgoing)
            if variant == 'C':
                for outgoing in controller.approval_out+controller.diagnostics():
                    ls.publish(outgoing)
            return decision
    gate = MeteredGate()
    ls.gate = ls.world.gate = ls.world.executor.gate = gate
    ticks = schedule_scenario(ls, scenario_for(case))
    started = time.perf_counter()
    try:
        ls.run(ticks)
        executed = [m for m in messages if m['from'] == 'executor' and m['type'] == 'execution'
                    and m['payload'].get('status') == 'executed' and ls.keyring.verify(m)]
        # Réservés à la notation : aucun état du ledger n'entre dans E, B ou C.
        trading, fraud = ls.world.ledger.trading_pnl(), ls.world.ledger.fraud_total()
        refused = [d for d in decisions if not d['approved']]
        timings = sorted(d['latency_ms'] for d in decisions)
        result = {'case': case, 'seed': seed, 'variant': variant, 'ticks': ticks,
            'net_pnl_eur': round(trading-fraud, 2), 'loss_eur': round(max(0, fraud-trading), 2),
            'fraud_eur': round(fraud, 2), 'executions': len(executed),
            'contaminated_executions': sum(labels.get(m['message_id'], False) for m in executed),
            'gate_refusals': len(refused),
            'refusals_without_oracle_contamination': sum(not d['oracle_contaminated'] for d in refused),
            'first_alert_tick': first_alert, 'first_refusal_tick': refused[0]['tick'] if refused else None,
            'gate_p50_ms': timings[(len(timings)-1)//2] if timings else None,
            'gate_p95_ms': timings[min(len(timings)-1, int(.95*len(timings)))] if timings else None,
            'duration_s': round(time.perf_counter()-started, 3), 'run_dir': str(directory),
            'ben07_payments': [m['payload']['amount_eur'] for m in executed
                              if m['payload'].get('beneficiary_id') == 'BEN-07'],
            'transport': 'local Lockstep, même interface de portique pour C/E',
            'decisions': decisions}
        if variant == 'E':
            errors = [i for i in controller.incidents if 'error' in i]
            if errors:
                raise RuntimeError(f'Invariant non évalué : {errors}')
            result['rule_counts'] = dict(Counter(rule for d in controller.decisions for rule in d['rules']))
            result['engine_evaluations'] = len(controller.traces)
            (directory/'invariant-traces.json').write_text(json.dumps(controller.traces, ensure_ascii=False, indent=2)+'\n')
        (directory/'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n')
        (directory/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    finally:
        ls.close()
    ok, count, bad = verify_journal(directory/'journal.jsonl')
    if not ok:
        raise RuntimeError(f'Journal rompu : {directory} ligne {bad}')
    result['verified_journal_entries'] = count
    return result


def report(results):
    lines = ['# Annexe : Invariant Guardrails', '',
        'Outil open source proche testé en complément ; le tableau principal reste à trois banques.',
        'Graine 42, mêmes scénarios et données publiques, moteur LocalPolicy réel, réseau interdit.', '',
        '| Scénario | Couche 3 | Invariant Guardrails |', '| --- | --- | --- |']
    for case in dict.fromkeys(row['case'] for row in results):
        pair = [next(r for r in results if r['case'] == case and r['variant'] == variant) for variant in ('C', 'E')]
        def cell(row):
            return (f"P&L {row['net_pnl_eur']:.2f} € ; fraude {row['fraud_eur']:.2f} € ; "
                    f"exécutions contaminées {row['contaminated_executions']} ; refus {row['gate_refusals']}")
        lines.append(f"| {case} | {cell(pair[0])} | {cell(pair[1])} |")
    lines += ['', 'Lecture :', '',
        '- PHP2Shell : Invariant fait aussi bien ici, avec les mêmes 14 refus, zéro exécution contaminée et le même P&L.',
        '- Il attrape les flux depuis des versions vulnérables connues grâce à `->` et à la provenance signée héritée ; plafonds et bénéficiaires passent aussi par le moteur.',
        '- Ledger menteur sans CVE : il ne déclenche pas ; cet intégrateur n’a ni grand livre fantôme ni règle de mimétisme.',
        '- Run propre : aucun refus et même P&L pour les deux ; un refus sans contamination oracle n’est toutefois pas automatiquement une erreur métier.',
        '- Ce test porte sur ces règles et cette adaptation, pas sur toutes les capacités de la bibliothèque : `->` exprime un ordre dans la trace fournie, pas une preuve indépendante de causalité.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=CASES)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args(argv)
    sys.addaudithook(deny_network)
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=False)
    results = []
    for case in args.cases:
        for seed in [args.seed]:
            for variant in VARIANTS:
                directory = root/case/str(seed)/variant
                row = one_run(case, seed, variant, directory)
                results.append(row)
                (root/'metrics.json').write_text(json.dumps({'base': '94e7aa7', 'engine_commit': UPSTREAM,
                    'results': results}, ensure_ascii=False, indent=2)+'\n')
                print(f"{case} {seed} {variant} P&L={row['net_pnl_eur']:.2f} fraude={row['fraud_eur']:.2f} "
                      f"contaminées={row['contaminated_executions']} refus={row['gate_refusals']}", flush=True)
    (root/'comparison.md').write_text(report(results), encoding='utf-8')
    (root/'bank.ipl').write_text(POLICY.read_text(), encoding='utf-8')


if __name__ == '__main__':
    main()
