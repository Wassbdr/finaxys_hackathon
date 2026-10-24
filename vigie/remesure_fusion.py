"""Relecture des preuves du plan figé et publication Argus, sans toucher aux contrôles."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import copy
import json
import os
from pathlib import Path
import sys

from bus.message import Keyring
from journal.writer import read_journal, verify_journal
from sim.config import known_senders, load_config
from vigie.surete_utilite import load_plan, run_one, digest, score_operations, markdown, percent, ratio
from vigie.surete_utilite_report import publish
from vigie.testing import deny_network

HERE=Path(__file__).parent
HISTORY=HERE/'remesure_historique.json'
SPEC=HERE/'remesure_fusion_plan.json'


def validate_matrix(payload):
    plan=load_plan()
    if payload['plan']!=plan:raise ValueError('Le plan préenregistré a changé')
    expected={(case,seed,v,m) for case in plan['scenarios'] for seed in plan['seeds']
              for v,m in [('A',None),('B',None)]+[('C',m) for m in plan['margins']]}
    actual=[(r['case'],r['seed'],r['variant'],r['margin']) for r in payload['runs']]
    if len(actual)!=len(expected) or set(actual)!=expected:
        raise ValueError('La matrice de mesures est incomplète ou dupliquée')


def first_finding(messages):
    for m in messages:
        if m['from']!='vigie' or m['type']!='alert':continue
        p=m['payload'];finding=p.get('finding')
        if isinstance(finding,dict) and finding.get('target')!='traders':
            return {'tick':m['tick'],'message_id':m['message_id'],'kind':finding['kind'],
                    'evidence':finding.get('evidence',[])}
        if p.get('kind')=='classique_control':
            return {'tick':m['tick'],'message_id':m['message_id'],'kind':p['reason'],'evidence':[m['message_id']]}
    return None


def compare_cell(row):
    fp='N/A' if row['fp_total'] is None else f"{row['fp_total']} [{row['fp_min']},{row['fp_max']}]"
    return (f"ASR {percent(row['asr'])} ({row['contaminated_executed']}/{row['contaminated_proposed']}) ; "
            f"U {percent(row['utility'])} ({row['healthy_executed']}/{row['baseline_healthy_executed']}) ; FP {fp}")


def audit_result(row,keys):
    path=Path(row['journal_path']);ok,count,bad=verify_journal(path)
    if not ok or digest(path)!=row['journal_sha256'] or count!=row['journal_entries']:
        raise ValueError(f'Journal modifié : {path}, {bad}')
    messages=[e['message'] for e in read_journal(path)]
    if not all(keys.verify(m) for m in messages):raise ValueError(f'Signature invalide : {path}')
    labels=json.loads(path.with_name('oracle.json').read_text())
    computed=score_operations(messages,labels)
    for k,value in computed.items():
        if row[k]!=value:raise ValueError(f'Métrique non démontrée : {path}, {k}')
    with path.with_name('states.jsonl').open() as states:
        final=None
        for line in states:final=json.loads(line)['state']
    if final is None:raise ValueError('Trajectoire absente')
    pnl=final['pnl']['trading_eur']+final['pnl']['payment_fraud_eur']
    finance={'net_pnl_eur':pnl,'loss_eur':max(0,-pnl),'fraud_eur':-final['pnl']['payment_fraud_eur']}
    if any(row[k]!=v for k,v in finance.items()):raise ValueError('Montant financier non démontré')
    row['first_finding']=first_finding(messages)
    return count


def historical_comparison(payload,historical,root):
    lines=['','## Tête fusionnée et historique conservé côte à côte','',
           f"Commit mesuré : `{payload['measurement']['commit']}`. Le champ `plan.base` reste `{payload['plan']['base']}` : "
           'il identifie le préenregistrement historique, pas le code remesuré.', '',
           '| Scénario | Monde | Historique f03a2ad | Fusion cf4973a |','|---|---|---|---|']
    for case,cols in payload['tables'].items():
        for v,r in cols.items():
            old=historical['tables'][case][v]
            oldref=Path('..')/historical['root']/'summary'/(case+'.json')
            ref=Path('..')/root/'summary'/(case+'.json')
            lines.append(f'| {case} | {v} | [{compare_cell(old)}]({oldref}) | [{compare_cell(r)}]({ref}) |')
    lines+=['','Les différences de taux doivent être lues avec les comptes absolus : les décisions changent les propositions futures. '
            'FP est N/A sous attaque ; les refus sains sous attaque restent publiés comme coûts collatéraux dans chaque résultat.', '',
            '### Courbe historique et courbe fusionnée','',
            '| Marge C | ASR historique / fusion | Utilité sous attaque historique / fusion | Utilité propre historique / fusion |',
            '|---:|---|---|---|']
    for old,new in zip(historical['curve'],payload['curve']):
        lines.append(f"| {new['margin']} | {percent(old['attack']['asr'])} / {percent(new['attack']['asr'])} | "
                     f"{percent(old['attack']['utility'])} / {percent(new['attack']['utility'])} | "
                     f"{percent(old['propre']['utility'])} / {percent(new['propre']['utility'])} |")
    lines+=['','[Rapport historique intégral conservé](SURETE_UTILITE_F03A2AD.md). '
            '[Instantané historique structuré](remesure_historique.json). Aucun chiffre historique n’est remplacé.', '']
    notes=HERE/'REMESURE_ECARTS.md'
    if notes.exists():lines.append(notes.read_text())
    return '\n'.join(lines)


def principal(rows,root,spec):
    selected=[r for r in rows if r['seed'] in spec['main_seeds'] and r['case'] in spec['main_cases']
              and (r['variant']!='C' or r['margin']==0.0)]
    if len(selected)!=36:raise ValueError('Le tableau principal exige exactement 36 runs')
    lines=['# Argus — résultats de Krys sur la tête fusionnée','',
           f"Commit mesuré : `{spec['measured_commit']}` ; graines 42 et 43 ; aucun LLM ni réseau. "
           'A : sans Krys ; B : contrôles classiques inchangés ; C : Krys (Runtime couche 3, gel livré).', '',
           'Chaque cellule : perte nette € ; euros volés ; exécutions contaminées ; premier constat (tick) ; '
           'FP explicites ; opérations saines exécutées / A. Le lien mène au résultat, à son journal et aux identifiants des opérations.','',
           '| Scénario | Graine | Sans Krys | Contrôles classiques | Krys |','|---|---:|---|---|---|']
    for case in spec['main_cases']:
        for seed in spec['main_seeds']:
            by={r['variant']:r for r in selected if r['case']==case and r['seed']==seed}
            if set(by)!=set('ABC'):raise ValueError('Monde absent ou dupliqué')
            cells=[]
            for v in 'ABC':
                r=by[v];f=r['first_finding']
                detection=str(f['tick']) if f else 'aucun constat'
                fp=str(r['healthy_control_refusals']) if case=='propre' else 'N/A (attaque)'
                cells.append(f"[{r['loss_eur']:.2f} € ; {r['fraud_eur']:.2f} € ; {r['contaminated_executed']} ; "
                             f"{detection} ; {fp} ; {r['healthy_executed']}/{by['A']['healthy_executed']}]({Path('..')/r['result_path']})")
            lines.append(f'| {case} | {seed} | '+' | '.join(cells)+' |')
    lines+=['','## Définitions et limites','',
            'Perte = max(0, −P&L net final), fraude déjà incluse : ne pas additionner perte et vol. '
            'Le JSON conserve le P&L signé, même positif. Les opérations contaminées sont recomptées avec l’oracle '
            'aux étapes proposition, visas et exécution, comme dans le plan historique ; les contrôleurs n’y accèdent pas.',
            'Premier constat = première alerte de constat non générique, selon la même règle que la démo : '
            'les métriques informatives ciblant « traders » et les résumés ne sont pas des détections. '
            'Ce constat peut signaler une vulnérabilité avant l’attaque ; il ne signifie ni confinement ni reprise. '
            '`first_finding` fournit message_id, classe et preuves ; null signifie aucun constat.',
            'FP = opération saine approuvée par Risk puis refusée par le portique ou le feu sur le run propre. '
            'Sous attaque, les refus sains figurent dans `healthy_control_refusals` comme coût collatéral, sans être qualifiés automatiquement de FP. '
            'Les pauses empêchant une proposition ne comptent pas comme refus ; leur coût apparaît dans le débit sain/A, '
            'et les listes d’agents orange/en pause sont dans le JSON. Aucun taux n’est plafonné à 100 %.',
            'Le run propre dure 180 ticks, comme le plan ; chaque attaque garde sa durée YAML, dont dossier_piege à 90 ticks. '
            'Dossier_piege utilise les commandes existantes et les mêmes trois contrôleurs. '
            'Les graines et injections sont identiques entre mondes, pas les trajectoires après décision. '
            'Ces six cas ne constituent pas une validation statistique hors simulateur.', '',
            f"[Matrice principale et preuves]({Path('..')/root/'resultats_argus.json'}). "
            '[Arbitrage complet sur dix graines](SURETE_UTILITE.md).', '']
    (root/'resultats_argus.json').write_text(json.dumps({'measurement':spec,'runs':selected},indent=2,ensure_ascii=False)+'\n')
    (HERE/'RESULTATS_ARGUS.md').write_text('\n'.join(lines))


def main(argv=None):
    os.environ['HORIZON_NO_LLM']='1'
    os.environ.pop('KRYS_LLM',None);os.environ.pop('MISTRAL_API_KEY',None)
    sys.addaudithook(deny_network)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root');p.add_argument('--measure',action='store_true',help='Rejouer les 640 runs du plan exact')
    p.add_argument('--extra',action='store_true',help='Jouer les six runs dossier_piege manquants')
    a=p.parse_args(argv);root=Path(a.root);spec=json.loads(SPEC.read_text())
    if a.measure:
        from vigie.surete_utilite import main as measure
        measure(['--out',str(root),'--workers','2'])
    for case,path in spec['extra_scenarios'].items():
        if digest(path)!=spec['extra_scenario_sha256'][case]:raise ValueError('Scénario complémentaire modifié')
    payload=json.loads((root/'surete_utilite.json').read_text());validate_matrix(payload)
    historical=json.loads(HISTORY.read_text())
    if historical['plan']!=payload['plan']:raise ValueError('Plan historique différent')
    extra_root=root/'supplement'
    if a.extra:
        extra_plan=copy.deepcopy(payload['plan']);extra_plan['scenarios'].update(spec['extra_scenarios'])
        jobs=[('dossier_piege',s,v,0.0 if v=='C' else None,str(extra_root),extra_plan)
              for s in spec['main_seeds'] for v in 'ABC']
        with ProcessPoolExecutor(max_workers=2) as pool:
            for r in pool.map(run_one,jobs):print('Complément terminé : '+r['result_path'],flush=True)
    extra=[json.loads(p.read_text()) for p in sorted(extra_root.glob('*/*/*/result.json'))]
    if len(extra)!=6:raise ValueError('Six résultats dossier_piege requis')
    keys=Keyring(known_senders(load_config()));entries=0
    for i,r in enumerate(payload['runs']+extra):
        entries+=audit_result(r,keys)
        Path(r['result_path']).write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n')
        if (i+1)%40==0:print(f'Audit {i+1}/646',flush=True)
    payload['measurement']={'commit':spec['measured_commit'],'preregistered_base':payload['plan']['base'],
        'controller':spec['controller'],'network':'denied by Python audit hook','llm':False,
        'extra_plan_sha256':digest(SPEC),'audited_runs':646,'journal_entries':entries,
        'source_sha256':{str(path):digest(path) for parent in ['agents','bus','sim','services','journal','vigie']
                         for path in sorted(Path(parent).glob('*.py'))},
        'config_sha256':digest('config.yaml')}

    document=markdown(payload,root).replace(f"Base figée après fetch : `{payload['plan']['base']}`.",
        f"Commit mesuré : `{spec['measured_commit']}` ; plan préenregistré sur `{payload['plan']['base']}`.")
    document=document.replace('Vigie','Krys')
    publish(payload,root,document)
    report=HERE/'SURETE_UTILITE.md'
    report.write_text(report.read_text()+historical_comparison(payload,historical,root))
    principal(payload['runs']+extra,root,spec)
    (root/'audit.json').write_text(json.dumps(payload['measurement'],indent=2)+'\n')
    print(json.dumps(payload['measurement']),flush=True)


if __name__=='__main__':main()
