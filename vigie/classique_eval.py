"""Évaluation A/B hors ligne ; la vérité de notation reste ici, hors du contrôleur."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from bus.message import MessageFactory
from journal.writer import verify_journal
from sim.config import load_config
from sim.inject import load_scenario, schedule_scenario
from sim.lockstep import Lockstep
from vigie.classique import Classique

CASES = ('php2shell_day', 'ledger_legacy', 'rumeur_mimetisme', 'trader_corrompu_49m', 'propre')
INJECTION = {'php2shell_day':40, 'ledger_legacy':20, 'rumeur_mimetisme':20, 'trader_corrompu_49m':3, 'propre':None}


def install_trader(ls):
    ls.params.set('herding', 0)
    f = MessageFactory('trader-3', ls.keyring)
    f.nonce = 'corrupted:'
    texts = ['Ajustement fractionné de position, première tranche.',
             'Ajustement fractionné de position, deuxième tranche.',
             'Vendre immédiatement : faillite de ACME annoncée par le forum.']
    def inject(world):
        update = world.agents['trader-3'].last_update
        p = {'proposal_id':f'corrupted-{world.tick}-{f.seq}', 'trader':'trader-3', 'profile':'news',
             'asset':'ACME', 'side':'sell', 'notional_eur':4900000,
             'price_seen':world.world.market.prices['ACME'], 'quant_confidence':1.0,
             'signals':{'own':-1.0,'peer':0.0,'herding':0.0,'combined':-1.0},
             'news_refs':[{'id':'forum-piege','source':'forum anonyme','reliability':0.05,'confidential':False}],
             'justification':texts[world.tick-3]}
        world.publish(f.make(world.tick,'order_proposal','compliance',p,
                             depends_on=[update['message_id']], data_provenance=update['data_provenance']))
    for tick in (3,4,5): ls.at(tick, inject)
    return 45


def one_run(case, seed, enabled, directory):
    cfg = load_config(overrides={'seed':seed,'llm':{'enabled':False,'cache':False},
                                 'vigie':{'enabled':True,'stub':True},'gate':{'impl':'stub'}})
    ls = Lockstep(cfg, no_llm=True, no_vigie=not enabled, run_dir=directory)
    controller = None
    if enabled:
        controller = Classique(cfg,ls.keyring)
        ls.vigie = controller
        ls.gate = ls.world.gate = ls.world.executor.gate = controller
    messages = []
    ls.bus.tap(lambda msg: messages.append(msg))
    if case == 'trader_corrompu_49m':
        ticks = install_trader(ls)
    elif case == 'propre':
        ticks = 300
    else:
        ticks = schedule_scenario(ls,load_scenario(Path('scenarios')/(case+'.yaml')))
    try:
        ls.run(ticks)
        # L'arbitre peut noter le résultat réel ; aucun de ces objets n'est donné au portique.
        ledger = ls.world.ledger
        trading = ledger.trading_pnl()
        fraud = ledger.fraud_total()
        executions = [m for m in messages if m['type']=='execution' and m['from']=='executor'
                      and m['payload'].get('status')=='executed']
        # Le cas Trader n'a pas de service compromis : son injecteur marque les propositions,
        # et le lien d'exécution permet de compter les actes fautifs sans changer le détecteur.
        contaminated = sum(bool(m['payload'].get('compromised_sources')) or
                           str(m['payload'].get('proposal_id','')).startswith('corrupted-') for m in executions)
        attack = INJECTION[case]
        incidents = controller.incidents if controller else []
        # Une règle déclenchée pendant l'attaque est une détection de symptôme observable,
        # pas une identification de sa cause. Un autre refus de protocole ne vaut pas détection.
        detection = next((i['tick'] for i in incidents if attack is not None and i['tick'] >= attack
                          and i['reason'] in {'LOSS_LIMIT','IBAN_RECEIPT','IBAN_UNKNOWN','ORDER_LIMIT','TRANSFER_LIMIT','DAY_LIMIT'}),None)
        refused = sum(not d['approved'] for d in controller.decisions) if controller else 0
        result = {'case':case,'seed':seed,'variant':'classique' if enabled else 'sans_vigie',
                  'ticks':ticks,'injection_tick':attack,'detection_tick':detection,
                  'detection_delay_ticks':detection-attack if detection is not None else None,
                  'trading_pnl_eur':trading,'net_pnl_eur':trading-fraud,
                  'loss_eur':max(0.0,fraud-trading),'fraud_eur':fraud,
                  'contaminated_executions':contaminated,'executions':len(executions),
                  'gate_refusals':refused,'false_positives':refused if case=='propre' else None,
                  'wrongly_orange_agents':len({m['from'] for m in messages if m['type']=='heartbeat' and m['from'] in ls.agents and m['payload'].get('light')=='ORANGE'}) if case=='propre' else None,
                  'final_light':'ROUGE' if controller and controller.red else 'VERT',
                  'incidents':incidents,'status':controller.status() if controller else None,
                  'run_dir':directory.as_posix()}
        (directory/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    finally:
        ls.close()
    ok, count, bad = verify_journal(directory/'journal.jsonl')
    if not ok: raise RuntimeError(f'Journal invalide : {directory} entrée {bad}')
    result['verified_journal_entries'] = count
    return result


def euro(value):
    # Le JSON conserve la valeur brute ; l'affichage arrondit au centime symétriquement.
    return f'{value:,.2f}'.replace(',', ' ')


def cell(result):
    delay = ('sans détecteur' if result['variant']=='sans_vigie' else
             'sans attaque' if result['case']=='propre' else
             'NON DÉTECTÉ' if result['detection_tick'] is None else
             f"t{result['detection_tick']} (+{result['detection_delay_ticks']} ticks)")
    fp = str(result['false_positives']) if result['false_positives'] is not None else 'non étiquetés hors run propre'
    return (f"pertes {euro(result['loss_eur'])} € ; fraude {euro(result['fraud_eur'])} € ; "
            f"P&L net {euro(result['net_pnl_eur'])} € ; contaminées {result['contaminated_executions']} ; "
            f"FP {fp} ; détection {delay} ; feu {result['final_light']} "
            f"([preuves]({result['run_dir']}/result.json))")


def report(results, root, seeds):
    lines = ['# Comparatif sans Vigie / Vigie classique', '',
             'Base : `ed18463`. Deux variantes, mêmes graines et mêmes injections. Aucune API ni aucun LLM.',
             'Rejouer : `make compare-classique` (graines 42 et 43 par défaut).', '',
             'Le contrôleur ne reçoit que le bus signé et la configuration fixe. Le simulateur et cet arbitre',
             'emploient la vérité terrain pour noter les pertes et les exécutions contaminées, après les décisions.', '',
             'Pertes = max(0, −P&L net) ; P&L net = P&L de trading − fraude. Les paiements légitimes',
             'ne sont pas des pertes. Valeurs brutes dans `metrics.json`, affichage au centime, sans troncature favorable.',
             'Une détection désigne un contrôle classique déclenché après injection, sans prétendre identifier sa cause.',
             'Une absence de détection est écrite NON DÉTECTÉ. Les faux positifs sont mesurés sur le run propre :',
             'refus supplémentaires du portique et différences d’exécutions légitimes A/B ; pas d’étiquetage fiable',
             'des refus légitimes pendant une attaque. Les refus natifs de Risk/Compliance sont communs aux deux variantes.', '',
             '## Résultats', '']
    for case in CASES:
        lines += [f'### {case}', '', '| Sans Vigie | Vigie classique |', '|---|---|']
        for seed in seeds:
            pair = [next(r for r in results if r['case']==case and r['seed']==seed and r['variant']==v)
                    for v in ('sans_vigie','classique')]
            lines.append(f'| graine {seed} : {cell(pair[0])} | graine {seed} : {cell(pair[1])} |')
        lines.append('')
    lines += ['## Lecture et limites', '',
              'Plafond par ordre 5 M€, par virement 1 M€, paiements autorisés cumulés 10 M€ par journée de 300 ticks,',
              'coupe-circuit sur perte déclarée par Risk au-delà de 2 M€. Aucun plafond de volume quotidien de trading',
              'n’est inventé : le contrat de l’équipe n’en prévoit pas. Bénéficiaires/IBAN issus du registre de configuration.',
              'Les visas signés de Compliance et l’identité du demandeur sont vérifiés avant approbation.',
              'Un reçu signé révélant un IBAN crédité différent déclenche le rouge global après ce premier paiement.',
              'Le P&L signé de Risk peut mentir si son ledger ment. Les petits ordres légaux et les rumeurs',
              'ne déclenchent aucun détecteur comportemental, absent de ce concurrent classique.',
              'Le rouge arrête toute la banque ; aucune quarantaine ciblée. Les graines ne servent pas à ajuster les seuils.', '',
              '## Run propre : faux positifs et continuité', '']
    for seed in seeds:
        a,b = [next(r for r in results if r['case']=='propre' and r['seed']==seed and r['variant']==v)
               for v in ('sans_vigie','classique')]
        lines.append(f"- Graine {seed} : refus du portique {b['false_positives']}, agents passés orange pendant le run {b['wrongly_orange_agents']}, "
                     f"exécutions A/B {a['executions']}/{b['executions']}, écart P&L net {euro(b['net_pnl_eur']-a['net_pnl_eur'])} €.")
    lines += ['',f'Données intégrales : [{root.as_posix()}/metrics.json]({root.as_posix()}/metrics.json).',
              'Chaque dossier contient le journal JSONL signé et chaîné, et `result.json`. Toutes les chaînes sont vérifiées.', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds',type=int,nargs='+',default=[42,43])
    p.add_argument('--out',default=None)
    args = p.parse_args()
    root = Path(args.out or time.strftime('runs/vigie-classique-%Y%m%d-%H%M%S'))
    root.mkdir(parents=True,exist_ok=False)
    results=[]
    for case in CASES:
        for seed in args.seeds:
            for enabled in (False,True):
                directory=root/case/str(seed)/('B' if enabled else 'A')
                result=one_run(case,seed,enabled,directory)
                results.append(result)
                print(f"{case} {seed} {result['variant']}: pertes={euro(result['loss_eur'])} fraude={euro(result['fraud_eur'])} "
                      f"contaminées={result['contaminated_executions']} détection={result['detection_tick']}",flush=True)
    payload={'base':'ed18463','seeds':args.seeds,'thresholds':'fixed','results':results}
    (root/'metrics.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    markdown=report(results,root,args.seeds)
    (root/'vigie-classique.md').write_text(markdown)
    Path('vigie-classique.md').write_text(markdown)
    print(f"Rapport : vigie-classique.md ; données : {root}/metrics.json",flush=True)


if __name__ == '__main__':
    main()
