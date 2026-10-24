"""Extraits de mesures archivées : aucune lecture du moteur vivant ni décision financière."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import statistics

DATA=Path(__file__).parent/'data'/'impact'
PREFIX='/dashboard/console/impact/'
LABELS={'A':'Sans Krys','B':'Contrôles classiques','C':'Krys'}


def read(name):return json.loads((DATA/name).read_text())


def proof(name,label):return {'url':PREFIX+name,'label':label}


def rows_counts(values,denominators):
    return [{'label':LABELS[v],'numerator':values[v],'denominator':denominators[v],
             'value':values[v],'unit':'operations'} for v in 'ABC']


def build_cards():
    fixed=read('fixed.json');normal=read('normal.json');crash=read('crash.json')
    detected=read('detections.json');fraud=read('fraud.json');outage=read('outage.json')
    commit=fraud['measurement']['measured_commit']
    cards=[{'id':'contamination','title':'Opérations contaminées exécutées',
        'sample':'6 scénarios d’attaque × 10 journées simulées par banque ; seuil livré.',
        'groups':[{'label':'Exécutions / propositions ayant utilisé une donnée contaminée',
                   'rows':rows_counts({v:r['contaminated_executed'] for v,r in fixed.items()},
                                      {v:r['contaminated_proposed'] for v,r in fixed.items()})}],
        'meaning':'Krys réduit les exécutions contaminées ; il en reste, malgré les refus.',
        'limit':'Les décisions changent les propositions futures : les trois dénominateurs diffèrent.',
        'proofs':[proof('fixed.json','Comptes et références des 60 runs par banque')]},
      {'id':'utility','title':'Opérations saines sous attaque',
        'sample':'Les mêmes 6 scénarios × 10 journées ; référence : 545 exécutions saines sans Krys.',
        'groups':[{'label':'Exécutions saines / exécutions saines sans Krys',
          'rows':[{**r,'value':r['numerator']/r['denominator'],'unit':'ratio'} for r in
             rows_counts({v:r['healthy_executed'] for v,r in fixed.items()},
                         {v:r['baseline_healthy_executed'] for v,r in fixed.items()})]}],
        'meaning':'La protection a un coût de débit : les contrôles classiques et Krys laissent passer moins d’opérations saines.',
        'limit':'Il s’agit d’un nombre d’exécutions, pas d’un gain financier ni des mêmes opérations appariées.',
        'proofs':[proof('fixed.json','Débit sain et référence sans Krys')]},
      {'id':'normal','title':'Dix journées normales',
        'sample':'10 journées simulées sans attaque ; 319 opérations saines de référence.',
        'groups':[{'label':'Exécutions saines / référence sans Krys',
          'rows':rows_counts({v:r['healthy_executed'] for v,r in normal['metrics'].items()},
                            {v:r['baseline_healthy_executed'] for v,r in normal['metrics'].items()})}],
        'refusals':{v:r['false_positives'] for v,r in normal['clean'].items()},
        'bound':{'numerator':3,'denominator':normal['clean']['C']['eligible'],
                 'confidence':0.95,'independence_required':True},
        'clean_freezes':normal['clean']['C']['freeze_starts'],
        'crash_refusals':crash['clean']['C']['false_positives'],
        'crash_days_with_refusal':sum(r['healthy_control_refusals']>0 for r in detected['crash_runs']),
        'meaning':'Aucun refus explicite sur ces opérations saines ; un gel est néanmoins observé, sans refus.',
        'limit':'Pendant un vrai krach : 10 refus à tort, sur 1 journée sur 10 ; les opérations d’une journée sont corrélées, donc la borne statistique reste conditionnelle.',
        'proofs':[proof('normal.json','Journées normales : refus, gels et dénominateur'),proof('crash.json','Vrai krach : coût sur les opérations saines'),proof('detections.json','Comptes par journée')]},
      {'id':'detection','title':'Premier constat selon le scénario',
        'sample':'3 types d’attaque × 10 journées par banque ; un tick est une seconde simulée.',
        'groups':[], 'classical_after_theft_days':sum(r['theft_precedes_finding'] for r in detected['classical_theft']),
        'meaning':'La faille connue est signalée avant l’attaque ; le registre menteur et la rumeur sont signalés après leur injection.',
        'limit':'Un constat n’est pas un confinement ; le trader corrompu isolé reste non détecté dans les essais disponibles.',
        'proofs':[proof('detections.json','Ticks, absences de constat et message_id'),proof('limits.md','Périmètre et limite du trader isolé')]},
      {'id':'fraud','title':'Fraude au virement : comparaison',
        'sample':'php2shell_day, graines 42 et 43 : 2 journées simulées, échelle arbitraire, seul le rapport compte.',
        'groups':[],
        'meaning':'Krys empêche ici la fraude au virement ; les contrôles classiques la réduisent sans l’éliminer.',
        'limit':'Deux journées d’un simulateur ne prédisent pas un montant de perte bancaire réelle.',
        'proofs':[proof('fraud.json','Résultats des trois banques, graines 42 et 43')]},
      {'id':'outage','title':'Panne de Krys : essai du relais',
        'sample':'2 journées normales × 3 coupures (10, 30, 60 ticks) ; les deux témoins sont réutilisés.',
        'groups':[], 'classical_measured':False,
        'meaning':'Dans ce banc, le relais conserve toutes les opérations du témoin ; le redémarrage seul reste fermé jusqu’à la fin observée.',
        'limit':'Relais en banc d’essai, pas encore en direct ; Sans Krys et contrôles classiques non mesurés ici ; panne de l’hôte non couverte.',
        'proofs':[proof('outage.json','Douze cas de panne et leurs témoins'),proof('outage-method.md','Procédure réelle, reprise et limites')]}]
    for case,title,tick in [('php2shell_day','Faille connue',40),('ledger_legacy','Registre de soldes altéré',20),('rumeur_mimetisme','Rumeur et réactions en chaîne',20)]:
        group={'label':title,'injection_tick':tick,'rows':[]}
        for v in 'ABC':
            runs=[r for r in detected['runs'] if r['case']==case and r['variant']==v]
            ticks=[r['first_finding']['tick'] for r in runs if r['first_finding'] is not None]
            group['rows'].append({'label':LABELS[v],'value':statistics.median(ticks) if ticks else None,
              'tick_range':[min(ticks),max(ticks)] if ticks else None,
              'delay_range':[min(ticks)-tick,max(ticks)-tick] if ticks else None,
              'observed':len(ticks),'total':len(runs),'unit':'ticks'})
        cards[3]['groups'].append(group)
    totals={v:sum(r['fraud_eur'] for r in fraud['runs'] if r['case']=='php2shell_day' and r['variant']==v) for v in 'ABC'}
    cards[4]['groups']=[{'label':'Part des euros volés, référence sans Krys',
        'rows':[{'label':LABELS[v],'numerator':totals[v],'denominator':totals['A'],
                 'value':totals[v]/totals['A'] if totals['A'] else None,'unit':'euros_ratio'} for v in 'ABC']}]
    baseline=sum(outage['baselines'][str(r['seed'])]['executed'] for r in outage['results'] if not r['relay'])
    without=[r for r in outage['results'] if not r['relay']];with_relay=[r for r in outage['results'] if r['relay']]
    cards[5]['groups']=[{'label':'Exécutions saines / témoins sans panne (réutilisés pour chaque durée)',
        'rows':[{'label':label,'numerator':n,'denominator':baseline,'value':n/baseline,'unit':'ratio'}
                for label,n in [('Krys sans panne (référence)',baseline),('Krys sans relais',sum(r['executed'] for r in without)),
                                ('Krys avec relais',sum(r['executed'] for r in with_relay))]]}]
    cards[5].update(measured_commit=outage['plan']['base'],
        unavailable_without_relay_min=min(r['comparison']['unavailable_ticks'] for r in without),
        unavailable_with_relay_max=max(r['comparison']['unavailable_ticks'] for r in with_relay),
        right_censored=all(r['comparison']['right_censored'] for r in without))
    for card in cards:card.setdefault('measured_commit',commit)
    return cards


def export(measurements,outage_root):
    DATA.mkdir(parents=True,exist_ok=True)
    origin=json.loads((measurements/'surete_utilite.json').read_text())
    recorded=json.loads((outage_root/'metrics.json').read_text())
    manifest={'schema_version':1,'measured_commit':origin['measurement']['commit'],
       'analysis_commit':'48a980a4e2f4c575382248fdbeab9e281087a590','outage_analysis_commit':'dfdd053b40ce00db95edcb89bcb1069f89320871',
       'seeds':origin['plan']['seeds'],'baseline_margin':origin['plan']['baseline_margin'],
       'profile_sha256':origin['plan']['profile_sha256'],'sources':{}}
    def save(name,value,source,*,exact=False):
        raw=source.read_bytes()
        (DATA/name).write_bytes(raw if exact else (json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode())
        manifest['sources'][name]={'source_file':source.name,'source_sha256':hashlib.sha256(raw).hexdigest(),
            'copied_sha256':hashlib.sha256((DATA/name).read_bytes()).hexdigest(),
            'extraction':not exact,'measured_commit':recorded['plan']['base'] if name.startswith('outage') else origin['measurement']['commit']}
    for name,src in [('fixed.json','fixed-comparison.json'),('normal.json','summary/propre.json'),
                     ('crash.json','summary/krach_reel.json'),('fraud.json','resultats_argus.json')]:
        save(name,None,measurements/src,exact=True)
    chosen=[r for r in origin['runs'] if r['case'] in {'php2shell_day','ledger_legacy','rumeur_mimetisme'}
            and (r['variant']!='C' or r['margin']==0)]
    detections={'runs':[{k:r[k] for k in ['case','seed','variant','first_finding','result_path','journal_sha256']} for r in chosen],
       'crash_runs':[{k:r[k] for k in ['seed','healthy_control_refusals','result_path']} for r in origin['runs']
                     if r['case']=='krach_reel' and r['variant']=='C' and r['margin']==0]}
    detections['classical_theft']=[]
    for result in chosen:
        if result['case']!='php2shell_day' or result['variant']!='B':continue
        journal=measurements/result['case']/str(result['seed'])/'B'/'journal.jsonl'
        first=None;finding_index=None
        for index,line in enumerate(journal.open()):
            msg=json.loads(line)['message'];p=msg['payload']
            if (first is None and msg['type']=='execution' and p.get('kind')=='payment'
                    and p.get('status')=='executed' and p.get('iban_requested')!=p.get('iban_credited')):
                first={'tick':msg['tick'],'message_id':msg['message_id'],'journal_index':index}
            if result['first_finding'] and msg['message_id']==result['first_finding']['message_id']:finding_index=index
        detections['classical_theft'].append({'seed':result['seed'],'first_theft':first,
          'first_finding':result['first_finding'],'finding_journal_index':finding_index,
          'theft_precedes_finding':bool(first is not None and finding_index is not None and first['journal_index']<finding_index),
          'journal_sha256':result['journal_sha256']})
    save('detections.json',detections,measurements/'surete_utilite.json')
    selected={'plan':recorded['plan'],'baselines':{s:{k:b[k] for k in ['seed','executed','pnl_eur','result_path']} for s,b in recorded['baselines'].items()},
      'results':[{k:r[k] for k in ['seed','duration_ticks','relay','executed','pnl_eur','comparison','result_path','processes','events']} for r in recorded['results']]}
    save('outage.json',selected,outage_root/'metrics.json')
    project=Path(__file__).resolve().parents[2]
    save('limits.md',None,project/'vigie/README_COUCHE3.md',exact=True)
    save('outage-method.md',None,project/'vigie/COUT_PANNE.md',exact=True)
    (DATA/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    payload={'schema_version':1,'system':'Argus','controller':'Krys','cards':build_cards(),
       'method':'Banque simulée, mêmes journées et attaques, seuils appris sur d’autres journées ; chiffres tels quels, y compris défavorables.',
       'origin':manifest,'proofs':[proof('manifest.json','Origine, commits et empreintes des fichiers')],
       'glossary':{'contamination':'Une opération a utilisé une réponse marquée par le simulateur, même si la valeur n’a pas été altérée.',
                   'healthy':'Une opération sans donnée marquée par l’oracle de la simulation.',
                   'tick':'Une étape de simulation ; une seconde simulée dans ces mesures.',
                   'false_positive':'Une opération saine approuvée par Risk, puis refusée par le portique ou le feu.'}}
    (DATA/'data.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--measurements',type=Path,required=True);p.add_argument('--outage',type=Path,required=True)
    a=p.parse_args();export(a.measurements,a.outage)
