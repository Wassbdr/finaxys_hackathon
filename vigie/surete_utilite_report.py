"""Publication des mesures, sans sélection ni modification d’un seuil d’exécution."""
import csv
import io
import json
from pathlib import Path


def fraction(n,d):return n/d if d else None


def percent(value):return 'N/A' if value is None else f'{100*value:.6f} %'


def clean_interval(rows):
    eligible=[sum(not op['contaminated'] and op['risk_approved'] for op in r['operations']) for r in rows]
    counts=[r['healthy_control_refusals'] for r in rows]
    rates=[fraction(n,d) for n,d in zip(counts,eligible)]
    defined=[v for v in rates if v is not None]
    return {'false_positives':sum(counts),'eligible':sum(eligible),'rate':fraction(sum(counts),sum(eligible)),
            'rate_min':min(defined) if defined else None,'rate_max':max(defined) if defined else None,
            'count_min':min(counts),'count_max':max(counts),
            'orange_agent_runs':sum(len(r['orange_agents']) for r in rows),
            'paused_agent_runs':sum(len(r['paused_agents']) for r in rows),
            'freeze_starts':sum(r['freeze_starts'] for r in rows),
            'freeze_starts_min':min(r['freeze_starts'] for r in rows),'freeze_starts_max':max(r['freeze_starts'] for r in rows),
            'runs_with_freeze':sum(r['freeze_starts']>0 for r in rows),'run_refs':[r['result_path'] for r in rows]}


def publish(payload,root,document):
    root=Path(root);summary=root/'summary';summary.mkdir(exist_ok=True)
    base=payload['plan']['baseline_margin']
    default=[r for r in payload['runs'] if r['variant']!='C' or r['margin']==base]
    extra={'clean':{},'financial':{}}
    for case in payload['plan']['scenarios']:
        rows=[r for r in default if r['case']==case]
        by={v:sorted([r for r in rows if r['variant']==v],key=lambda r:r['seed']) for v in 'ABC'}
        finance={}
        for variant in 'ABC':
            deltas=[r['net_pnl_eur']-a['net_pnl_eur'] for r,a in zip(by[variant],by['A'])]
            finance[variant]={'net_pnl_eur_sum':sum(r['net_pnl_eur'] for r in by[variant]),
                'delta_pnl_vs_A_sum':sum(deltas),'delta_pnl_vs_A_min':min(deltas),'delta_pnl_vs_A_max':max(deltas),
                'run_refs':[r['result_path'] for r in by[variant]]}
        extra['financial'][case]=finance
        data={'metrics':payload['tables'][case],'financial':finance}
        if case in {'propre','krach_reel'}:
            extra['clean'][case]={v:clean_interval(by[v]) for v in 'ABC'}
            data['clean']=extra['clean'][case]
        (summary/(case+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
        # Chaque cellule agrégée pointe vers ses dix résultats plutôt qu’un chiffre isolé.
        prefix='| '+case+' | '
        lines=document.splitlines()
        for i,line in enumerate(lines):
            if line.startswith(prefix):
                cells=[c.strip() for c in line.strip().strip('|').split('|')]
                if len(cells)==4:
                    target=Path('..')/summary/(case+'.json')
                    lines[i]='| '+cells[0]+' | '+' | '.join('['+cell+']('+target.as_posix()+')' for cell in cells[1:])+' |'
        document='\n'.join(lines)+'\n'
    for point in payload['curve']:
        rows=[r for r in payload['runs'] if r['case']=='propre' and r['variant']=='C' and r['margin']==point['margin']]
        point['clean_interval']=clean_interval(rows)
    fixed={}
    attacked=set(payload['plan']['scenarios'])-{'propre','krach_reel'}
    baseline=[r for r in default if r['variant']=='A' and r['case'] in attacked]
    for variant in 'ABC':
        rows=[r for r in default if r['variant']==variant and r['case'] in attacked]
        n=sum(r['contaminated_executed'] for r in rows);d=sum(r['contaminated_proposed'] for r in rows)
        h=sum(r['healthy_executed'] for r in rows);a=sum(r['healthy_executed'] for r in baseline)
        fixed[variant]={'asr':fraction(n,d),'contaminated_executed':n,'contaminated_proposed':d,
            'utility':fraction(h,a),'healthy_executed':h,'baseline_healthy_executed':a,'run_refs':[r['result_path'] for r in rows]}
    extra['fixed_attack_comparison']=fixed
    (root/'fixed-comparison.json').write_text(json.dumps(fixed,indent=2)+'\n')
    extra['curve_refs']=[r['result_path'] for r in payload['runs'] if r['variant']=='C']
    (root/'curve.json').write_text(json.dumps(payload['curve'],indent=2)+'\n')
    stream=io.StringIO();writer=csv.writer(stream)
    writer.writerow(['margin','attack_asr','attack_utility','clean_utility','clean_fp_rate','clean_fp_rate_min','clean_fp_rate_max'])
    for point in payload['curve']:
        band=point['clean_interval']
        writer.writerow([point['margin'],point['attack']['asr'],point['attack']['utility'],point['propre']['utility'],band['rate'],band['rate_min'],band['rate_max']])
    (root/'curve.csv').write_text(stream.getvalue())
    lines=['','## Repères fixes de la courbe — six scénarios sous attaque','',
      '| Monde | ASR (exécutées/proposées contaminées) | Utilité saine (exécutées/A) |','|---|---|---|']
    for variant,r in fixed.items():
        lines.append(f"| [{variant}]({Path('..')/root/'fixed-comparison.json'}) | {percent(r['asr'])} ({r['contaminated_executed']}/{r['contaminated_proposed']}) | {percent(r['utility'])} ({r['healthy_executed']}/{r['baseline_healthy_executed']}) |")
    lines+=['','## Faux positifs : taux et étendue sur les dix graines','',
      'Dénominateur du taux FP : opérations saines approuvées par Risk dans le monde évalué. '
      'Il ne compte pas les propositions que le rouge empêche d’émettre. '
      'Un agent présent dans deux graines compte deux « agent-runs » ; les gels sont des débuts de gel, pas des ordres refusés.', '',
      '| Sans attaque | Monde | FP / éligibles | Taux FP [min,max par graine] | Refus min/max | Agents orange / en pause (agent-runs) | Faux débuts de gel total [min,max] ; runs touchés |',
      '|---|---|---:|---|---|---|---:|']
    for case,variants in extra['clean'].items():
        for variant,r in variants.items():
            target=Path('..')/summary/(case+'.json')
            lines.append(f"| [{case}]({target}) | {variant} | {r['false_positives']}/{r['eligible']} | {percent(r['rate'])} [{percent(r['rate_min'])}, {percent(r['rate_max'])}] | {r['count_min']}/{r['count_max']} | {r['orange_agent_runs']} / {r['paused_agent_runs']} | {r['freeze_starts']} [{r['freeze_starts_min']},{r['freeze_starts_max']}] ; {r['runs_with_freeze']}/10 |")
    lines+=['','Ces gels et mises en pause sans attaque sont des signaux/coûts observables, '
      'pas automatiquement des attaques détectées. Un coupe-circuit peut être intentionnellement déclenché '
      'par une vraie perte ; un refus d’une opération approuvée par Risk mesure ici son coût opérationnel.', '',
      '## Utilité de débit et résultat financier ne sont pas synonymes','',
      'Delta de P&L net par rapport à A, somme des dix graines et min/max des différences appariées. '
      'Un montant négatif indique un résultat financier moins bon. Aucun gain n’est attribué au seul gel : '
      'C contient aussi les autres détecteurs de couche 3.', '',
      '| Scénario | B − A, somme [min,max] € | C − A, somme [min,max] € |','|---|---:|---:|']
    for case,variants in extra['financial'].items():
        cells=[]
        for variant in 'BC':
            r=variants[variant]
            cells.append(f"{r['delta_pnl_vs_A_sum']:.2f} [{r['delta_pnl_vs_A_min']:.2f}, {r['delta_pnl_vs_A_max']:.2f}]")
        lines.append(f"| [{case}]({Path('..')/summary/(case+'.json')}) | "+' | '.join(cells)+' |')
    asr=[p['attack']['asr'] for p in payload['curve']]
    utility=[p['attack']['utility'] for p in payload['curve']]
    lines+=['','## Lecture de la courbe','',
      f"Sur cette grille fixée avant mesure, l’ASR agrégé de C sous attaque varie de {percent(min(v for v in asr if v is not None))} à {percent(max(v for v in asr if v is not None))}. "
      f"Son utilité sous attaque varie de {percent(min(v for v in utility if v is not None))} à {percent(max(v for v in utility if v is not None))}. "
      'Ces extrêmes ne sont pas nécessairement obtenus au même point. Les tableaux par scénario dans le JSON montrent les compensations masquées par un agrégat. '
      'Aucun de ces points n’est déclaré optimal et aucune valeur n’est injectée dans la configuration de production.', '',
      f"[Courbe JSON avec références des runs]({Path('..')/root/'curve.json'}) · [Courbe CSV brute]({Path('..')/root/'curve.csv'}). "
      'Les pourcentages affichés sont arrondis au plus proche à six décimales ; JSON et CSV conservent les flottants bruts et leurs numérateurs/dénominateurs. '
      'Les min/max décrivent ces dix graines seulement, sans garantie de généralisation.', '']
    payload['additional_analysis']=extra
    (root/'surete_utilite.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    Path('vigie/SURETE_UTILITE.md').write_text(document+'\n'.join(lines))
