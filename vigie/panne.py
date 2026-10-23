"""Panne de Krys : vrais processus, portique réel et relais à réception attestée."""
from collections import deque
from dataclasses import asdict
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid

from bus.gate import BusGate,Decision
from bus.message import Keyring,MessageFactory,canonical,sha256
from journal.writer import verify_journal
from sim.config import load_config,known_senders
from sim.lockstep import Lockstep

PLAN=Path(__file__).with_name('panne_plan.json')


def stream_hash(value):return sha256(canonical(value))


def receipt_valid(keys,cert,reply,seq,head,replica,incarnation):
    try:
        return (keys.verify(cert) and cert['from']=='vigie' and cert['type']=='heartbeat'
            and cert['payload']=={'seq':seq,'head':head,'replica':replica,'incarnation':incarnation,'reply_hash':stream_hash(reply)})
    except Exception:return False


class Replica:
    def __init__(self,directory,name,*,fresh):
        self.name=name;self.seq=0;self.head='0'*64;self.incarnation=uuid.uuid4().hex
        self.keys=Keyring(known_senders(json.loads((directory/'config.json').read_text())))
        self.log=(directory/(name+'-'+str(time.time_ns())+'.log')).open('w')
        command=[sys.executable,'-m','vigie.panne_worker',str(directory/'config.json'),str(directory/'meta.json'),str(directory/(name+'-checkpoint.json')),name,self.incarnation]
        if fresh:command.append('--fresh')
        self.proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,bufsize=0)
        self.selector=selectors.DefaultSelector();self.selector.register(self.proc.stdout,selectors.EVENT_READ)
        self.buffer=b'';self.start=self.read(10)
        if not self.start or not self.start.get('ready'):raise RuntimeError('Krys ne démarre pas')
        self.receipts=[]

    def read(self,timeout):
        deadline=time.monotonic()+timeout
        while b'\n' not in self.buffer:
            remaining=deadline-time.monotonic()
            if remaining<=0 or not self.selector.select(remaining):return None
            data=os.read(self.proc.stdout.fileno(),65536)
            if not data:return None
            self.buffer+=data
        line,self.buffer=self.buffer.split(b'\n',1)
        return json.loads(line)

    @property
    def alive(self):return self.proc.poll() is None

    def send(self,request):
        if not self.alive:return False
        self.seq+=1;self.head=sha256(self.head+canonical(request))
        try:self.proc.stdin.write((canonical(request)+'\n').encode());self.proc.stdin.flush()
        except BrokenPipeError:return False
        return True

    def receive(self,timeout):
        packet=self.read(timeout)
        if packet is None:return None
        reply=packet.get('reply');cert=packet.get('receipt')
        if not receipt_valid(self.keys,cert,reply,self.seq,self.head,self.name,self.incarnation):return None
        self.receipts.append(cert)
        return reply

    def kill(self):
        pid=self.proc.pid
        if self.alive:self.proc.kill()
        self.proc.wait(timeout=5)
        return {'pid':pid,'returncode':self.proc.returncode}

    def close(self):
        if self.alive:self.proc.terminate()
        try:self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=5)
        self.selector.close();self.proc.stdin.close();self.proc.stdout.close();self.log.close()


class ReplySubscription:
    def __init__(self,hub):self.hub=hub
    def subscribe(self,*_):pass
    def close(self):pass
    def get_message(self,timeout):
        started=time.monotonic()
        if self.hub.pending:
            reply=self.hub.collect(timeout)
            if reply and reply.get('decision') is not None:
                decision=reply['decision']
                return {'data':canonical({'request_id':self.hub.request_id,**decision})}
        threading.Event().wait(max(0,timeout-(time.monotonic()-started)))
        return None


class Hub:
    def __init__(self,ls,directory,*,relay):
        self.ls=ls;self.directory=directory;self.relay=relay
        self.replicas={'primary':Replica(directory,'primary',fresh=True)}
        if relay:self.replicas['standby']=Replica(directory,'standby',fresh=True)
        self.active='primary';self.seq=0;self.head='0'*64;self.pending=[];self.request_id=None
        self.events=[];self.transcript=(directory/'input-stream.jsonl').open('w')
        self.gate=BusGate(self,ls.prefix,ls.cfg['gate']['timeout_s'],'reject')
        self.decisions=[];self.processes=[{'replica':r.name,**r.start} for r in self.replicas.values()]

    def pubsub(self,**_):return ReplySubscription(self)

    def dispatch(self,command,msg):
        if self.pending:raise RuntimeError('Commandes superposées : état non attestable')
        request={'command':command,'message':msg}
        self.seq+=1;self.head=sha256(self.head+canonical(request))
        self.transcript.write(canonical({'seq':self.seq,'head':self.head,'request':request})+'\n');self.transcript.flush()
        self.pending=[name for name,r in self.replicas.items() if r.send(request)]

    def collect(self,timeout):
        replies={name:self.replicas[name].receive(timeout) for name in self.pending}
        self.pending=[]
        current=replies.get(self.active)
        active=self.replicas[self.active]
        if self.relay and (active.seq!=self.seq or active.head!=self.head):current=None
        if self.relay and current is None:
            standby=self.replicas.get('standby');candidate=replies.get('standby')
            if (standby and candidate and standby.seq==self.seq and standby.head==self.head and not candidate['uncertain']):
                self.active='standby';current=candidate
                self.events.append({'tick':self.ls.tick,'kind':'failover','to':'standby','seq':self.seq,'head':self.head})
        self.current_ready=current is not None and not current['uncertain']
        if current is not None:
            self.last_uncertain=current['uncertain']
            for out in current['out']:self.ls.publish(out)
        return current

    def observe(self,msg):
        if msg['from']=='vigie':return
        self.dispatch('observe',msg);self.collect(5.)

    def publish(self,channel,data):
        if channel!=self.ls.prefix+'vigie.approval_requests':raise ValueError('Canal inattendu')
        request=json.loads(data);self.request_id=request['request_id']
        if self.request_id!=request['message']['message_id']:raise ValueError('Requête incohérente')
        self.dispatch('approve',request['message'])

    def request_approval(self,msg):
        decision=self.gate.request_approval(msg)
        self.decisions.append({'tick':self.ls.tick,'message_id':msg['message_id'],**asdict(decision)})
        return decision

    def cut(self):
        self.events.append({'tick':self.ls.tick,'kind':'process_killed',**self.replicas['primary'].kill()})

    def restart(self):
        old=self.replicas['primary'];self.save_receipts(old);old.close()
        self.replicas['primary']=Replica(self.directory,'primary',fresh=False)
        self.processes.append({'replica':'primary',**self.replicas['primary'].start})
        self.events.append({'tick':self.ls.tick,'kind':'process_restarted',**self.replicas['primary'].start})

    def save_receipts(self,replica):
        path=self.directory/(replica.name+'-receipts-'+str(replica.proc.pid)+'.jsonl')
        path.write_text(''.join(canonical(r)+'\n' for r in replica.receipts))

    def close(self):
        for r in self.replicas.values():self.save_receipts(r);r.close()
        self.transcript.close()


def prepare(ls,directory):
    directory.mkdir(parents=True,exist_ok=False)
    cfg=dict(ls.cfg);cfg.pop('_path',None)
    (directory/'config.json').write_text(json.dumps(cfg,indent=2)+'\n')
    (directory/'meta.json').write_text(json.dumps({sid:core.meta() for sid,core in ls.cores.items()},indent=2)+'\n')


def operations(messages):
    roots={m['message_id']:m for m in messages if m['type'] in {'order_proposal','payment_proposal'}}
    rows={mid:{'message_id':mid,'key':[m['from'],m['type'],m['tick'],m['payload'].get('proposal_id') or m['payload'].get('payment_id')],
        'tick':m['tick'],'requested_eur':m['payload'].get('notional_eur',m['payload'].get('amount_eur')),
        'executed':False,'blocked':False,'execution_eur':0.,'blocked_eur':0.,'proof':[]} for mid,m in roots.items()}
    risks={m['message_id']:m for m in messages if m['type']=='risk_decision' and m['payload'].get('proposal',{}).get('message_id') in roots}
    for msg in messages:
        if msg['from']!='executor' or msg['type']!='execution':continue
        risk=[risks[mid] for mid in msg['depends_on'] if mid in risks]
        if len(risk)!=1:raise ValueError('Exécution sans décision unique')
        decision=risk[0];row=rows[decision['payload']['proposal']['message_id']]
        row['proof'].append(msg['message_id']);p=msg['payload']
        if p.get('status')=='executed':
            if row['executed']:raise ValueError('Double exécution')
            row['executed']=True
            row['execution_eur']=p.get('notional_eur',p.get('amount_eur',0.))
        elif p.get('status')=='refused':
            row['blocked']=True;row['blocked_eur']=decision['payload']['notional_eur']
            row['blocked_by']=p.get('decided_by');row['reason']=p.get('reason')
    return list(rows.values())


def measure(directory,seed,*,duration=None,relay=False,plan=None):
    plan=plan or json.loads(PLAN.read_text());directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    cfg=load_config(overrides={'seed':seed,'llm':{'enabled':False,'cache':False},
        'vigie':{'enabled':True,'stub':False},'gate':{'impl':'stub','default_on_timeout':'reject'}})
    ls=Lockstep(cfg,no_llm=True,run_dir=directory/'world');ls.vigie=None
    prepare(ls,directory/'controllers');hub=Hub(ls,directory/'controllers',relay=relay)
    ls.gate=ls.world.gate=ls.world.executor.gate=hub
    messages=[];ls.bus.tap(hub.observe);ls.bus.tap(messages.append)
    frames=[];human_ids=[];availability=[]
    try:
        for tick in range(plan['ticks']):
            if duration is not None and tick==plan['cut_tick']:hub.cut()
            if duration is not None and tick==plan['cut_tick']+duration:hub.restart()
            if duration is not None and tick==plan['cut_tick']+duration+plan['human_delay_after_restart_ticks']:
                factory=MessageFactory('human',ls.keyring);factory.nonce='panne-human:'
                for payload in plan['human_actions']:
                    msg=factory.make(tick,'human_decision','*',payload);human_ids.append(msg['message_id']);ls.publish(msg)
                hub.events.append({'tick':tick,'kind':'human_decision','messages':list(human_ids)})
            state=ls.step()
            active=hub.replicas[hub.active]
            # L’attestation du dernier message prouve une réception courante ;
            # elle ne change jamais un refus métier du Runtime.
            ready=(active.alive and getattr(hub,'current_ready',False))
            availability.append({'tick':tick,'ready':ready,'active':hub.active})
            frames.append({'tick':tick,'pnl_eur':state['pnl']['trading_eur']+state['pnl']['payment_fraud_eur'],
                           'agents':{a:{'light':obj.light,'paused':obj.paused} for a,obj in ls.agents.items()},
                           'executor_light':ls.world.executor.light})
        ops=operations(messages)
        if state['trapped_operations']:raise ValueError('Attaque ou contamination inattendue dans le run propre')
        result={'seed':seed,'duration_ticks':duration,'relay':relay,'ticks':plan['ticks'],
            'plan':plan,'events':hub.events,'processes':hub.processes,'operations':ops,'availability':availability,
            'frames':frames,'decisions':hub.decisions,'human_message_ids':human_ids,
            'pnl_eur':frames[-1]['pnl_eur'],'executed':sum(o['executed'] for o in ops),
            'executed_eur':sum(o['execution_eur'] for o in ops),'blocked':sum(o['blocked'] for o in ops),
            'blocked_eur':sum(o['blocked_eur'] for o in ops),'result_path':str(directory/'result.json')}
    finally:hub.close();ls.close()
    ok,count,bad=verify_journal(directory/'world/journal.jsonl')
    if not ok:raise ValueError('Journal invalide '+str(bad))
    result['journal_entries']=count
    (directory/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


def compare(result,baseline):
    cut=result['plan']['cut_tick'];horizon=result['ticks']
    reference={tuple(o['key']):o for o in baseline['operations']}
    actual={tuple(o['key']):o for o in result['operations']}
    absent=[o for k,o in reference.items() if k not in actual and o['tick']>=cut]
    lost=[o for k,o in reference.items() if o['executed'] and o['tick']>=cut and (k not in actual or not actual[k]['executed'])]
    gained=[o for k,o in actual.items() if o['executed'] and o['tick']>=cut and (k not in reference or not reference[k]['executed'])]
    blocks=[o for o in result['operations'] if o['blocked'] and o['tick']>=cut]
    executed=[o for o in result['operations'] if o['executed'] and o['tick']>=cut]
    reference_availability={p['tick']:p['ready'] for p in baseline['availability']}
    unavailable=[p['tick'] for p in result['availability'] if p['tick']>=cut and not p['ready']]
    result['comparison']={'baseline':baseline['result_path'],'delta_pnl_eur':result['pnl_eur']-baseline['pnl_eur'],
        'not_proposed':len(absent),'not_proposed_eur':sum(o['requested_eur'] for o in absent),
        'lost_baseline_executions':len(lost),'lost_baseline_eur':sum(o['execution_eur'] for o in lost),
        'additional_executions':len(gained),'additional_executions_eur':sum(o['execution_eur'] for o in gained),
        'blocked_after_cut':len(blocks),'blocked_after_cut_eur':sum(o['blocked_eur'] for o in blocks),
        'first_block_tick':min((o['tick'] for o in blocks),default=None),
        'first_orange_tick':min((f['tick'] for f in result.get('frames',[]) if f['tick']>=cut and f['executor_light']=='ORANGE'),default=None),
        'first_green_after_restart_tick':min((f['tick'] for f in result.get('frames',[]) if f['tick']>=cut+result['duration_ticks'] and f['executor_light']=='VERT'),default=None),
        'first_execution_after_cut_tick':min((o['tick'] for o in executed),default=None),
        'restart_tick':cut+result['duration_ticks'],'human_tick':cut+result['duration_ticks']+result['plan']['human_delay_after_restart_ticks'],
        'first_execution_after_restart_tick':min((o['tick'] for o in executed if o['tick']>=cut+result['duration_ticks']),default=None),
        'unavailable_ticks':len(unavailable),'extra_unavailable_ticks':sum(reference_availability[t] for t in unavailable),
        'right_censored':horizon-1 in unavailable,'proof_not_proposed':[o['message_id'] for o in absent],
        'proof_lost_baseline':[o['message_id'] for o in lost]}
    Path(result['result_path']).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


def report(results,baselines,root):
    def euros(value):return f'{value:,.2f}'.replace(',',' ').replace('.',',')
    def signed_euros(value):return ('+' if value>=0 else '−')+euros(abs(value))
    lines=['# Argus — coût d’une panne de Krys','',
        'Base d179dd0 ; aucune attaque, aucun LLM, aucun réseau. Deux graines fixées : 42 et 43. '
        'Une journée = 300 ticks de 1 seconde (5 minutes simulées). Coupure SIGKILL à t60 ; '
        'nouveau PID après 10, 30 ou 60 ticks. Amorçage initial explicite sur fichier absent, comme --fresh-freeze-state ; '
        'pas de réamorçage après la panne. Décisions humaines signées resume et unfreeze(*) '
        'à redémarrage + 10 ticks, délai fixé avant mesure. Aucun effacement de checkpoint ni réamorçage neuf.', '',
        'Rejouer : `make cout-panne` ; vérifier : `make verify-cout-panne RUN=<dossier>`. '
        'Reprendre/restituer sans écraser les preuves : `python -m vigie.panne --resume <dossier>`.','',
        '## Résultats','',
        '| Graine | Coupure (temps simulé) | Mode | Refus / montant € | Non proposées / montant demandé € | Exécutions du témoin perdues / montant € | Δ P&L € | Premier refus | Reprise effective / première opération | Indisponibilité |',
        '|---:|---:|---|---:|---:|---:|---:|---:|---|---|']
    for r in results:
        c=r['comparison'];resume=c['first_execution_after_restart_tick']
        unavailable=('≥' if c['right_censored'] else '')+str(c['unavailable_ticks'])+' ticks'
        link=Path('..')/r['result_path']
        recovery=('continu dès t'+str(r['plan']['cut_tick'])+' ; opération t'+str(c['first_execution_after_cut_tick']) if r['relay']
                  else 'PAS DE REPRISE (redém. t'+str(c['restart_tick'])+', humain t'+str(c['human_tick'])+')')
        mode='relais attesté' if r['relay'] else 'redémarrage actuel'
        lines.append(f"| {r['seed']} | {r['duration_ticks']} ticks | [{mode}]({link}) | {c['blocked_after_cut']} / {euros(c['blocked_after_cut_eur'])} | {c['not_proposed']} / {euros(c['not_proposed_eur'])} | {c['lost_baseline_executions']} / {euros(c['lost_baseline_eur'])} | {signed_euros(c['delta_pnl_eur'])} | {c['first_block_tick'] if c['first_block_tick'] is not None else 'aucun'} | {recovery} | {unavailable} |")
    lines+=['','## Trois phrases pour le jury','']
    for duration,label in [(10,'1/6 de minute'),(30,'1/2 minute'),(60,'1 minute')]:
        plain=[r['comparison'] for r in results if r['duration_ticks']==duration and not r['relay']]
        mitigated=[r['comparison'] for r in results if r['duration_ticks']==duration and r['relay']]
        money=[r['lost_baseline_eur'] for r in plain];delta=[r['delta_pnl_eur'] for r in plain]
        other=[r['lost_baseline_eur'] for r in mitigated]
        relay_cost=euros(other[0]) if min(other)==max(other) else euros(min(other))+' à '+euros(max(other))
        lines.append(f"Sur ces deux journées simulées, une coupure de {label} immobilise {euros(min(money))} à {euros(max(money))} € de volume brut d’exécutions du témoin, avec un écart de P&L de {signed_euros(min(delta))} à {signed_euros(max(delta))} € ; avec le relais attesté, le volume du témoin perdu est de {relay_cost} €.")
        lines.append('')
    lines+=['','## Lecture et limites','',
        'Le montant immobilisé est un volume brut d’ordres/virements, pas une perte financière ni un chiffre d’affaires. '
        'Δ P&L = résultat du cas − résultat du témoin avec Krys sans panne ; une valeur positive est un gain observé, '
        'une valeur négative une dégradation. Les deux graines ne constituent pas une estimation statistique ni une garantie.',
        '« Non proposées » compare les identifiants métier (acteur/type/tick/proposal_id ou payment_id) des propositions du témoin. '
        'La dynamique peut aussi créer de nouvelles opérations : le JSON conserve additional_executions, leurs montants et '
        'les identifiants des opérations perdues. Ne pas additionner les volumes non proposés et les exécutions perdues : '
        'ils se recouvrent. Les refus indiquent les montants approuvés par Risk réellement présentés à Executor, '
        'pas les montants initiaux avant réduction de risque. Toutes les opérations sont saines au sens « aucune attaque », '
        'pas nécessairement opportunes financièrement.',
        'Indisponibilité = ticks où le processus actif n’offre pas de réception attestée courante sans incertitude interne ; '
        'un refus métier de Krys en fonctionnement n’est pas une panne. Le JSON distingue extra_unavailable_ticks face au témoin. '
        '≥ indique une observation tronquée en fin de journée, jamais une reprise supposée. '
        'Le premier refus dépend de l’arrivée d’une opération, pas seulement du tick de coupure. '
        'Les agents peuvent redevenir VERT au retour des heartbeats alors que le portique reste en refus : '
        'le JSON donne first_orange_tick et first_green_after_restart_tick, distincts de la reprise réelle. '
        'La première opération après redémarrage n’est pas le temps technique de bascule : le relais peut rester disponible '
        'en continu sans qu’aucun ordre arrive immédiatement. Les événements détaillent la bascule et les PID.',
        'Le Runtime actuel ne sauvegarde pas toute son analyse : son démarrage avec checkpoint restaure le gel et pose '
        'checkpoint_unavailable ; la commande humaine unfreeze ne lève que les gels actifs, pas cette incertitude globale. '
        'Une commande humaine signée n’est donc pas présentée comme une reprise si le code ne la permet pas.',
        '## Mitigation mesurée','',
        'Un second processus Runtime identique observe dès le début le même flux et les demandes d’approbation, '
        'avec son propre checkpoint. Ses sorties restent privées tant qu’il est passif. À la panne du primaire, '
        'la publication bascule seulement après vérification HMAC d’un reçu qui couvre le numéro, le hash du préfixe complet '
        'de messages/approbations et le hash de la réponse courante, sans incertitude interne. '
        'Un seul Hub publie vers la banque : jamais deux émetteurs concurrents. Le primaire relancé reste non autoritaire '
        'tant que son préfixe n’est pas celui du flux complet. Aucun compteur de suspicion ni verrou n’est effacé.',
        'Ce relais conserve tout l’état analytique vivant ; c’est plus simple que de prétendre restaurer toute l’analyse '
        'depuis un checkpoint de gel incomplet. La signature atteste réception et réponse, pas la vérité des données. '
        'Le point unique du routeur/transport n’est pas supprimé : ce banc mesure la panne d’un processus Krys seul, '
        'pas celle de l’hôte, du disque, du journal ni d’une clé commune compromise.',
        'Le transport de laboratoire remplace Redis par des pipes privés, sans socket. Le portique est le BusGate du dépôt, '
        'avec son délai réel de 2 secondes et refus sur dépassement. Les enfants exécutent le Runtime et FileCheckpoint actuels '
        '(fsync et écriture atomique), réseau interdit par audit Python. L’ordonnancement en ticks est déterministe ; '
        'les durées de panne simulées ne sont pas des durées murales de production. '
        'Les frais bancaires, pénalités SLA, coûts humains, coûts des deux processus et frais de financement ne sont pas modélisés. '
        'Le basculement est un prototype local à éditeur unique, pas une architecture de haute disponibilité déployée.', '',
        f"[Données et références des témoins]({Path('..')/root/'metrics.json'}). Les journaux, flux d’entrée, reçus signés, "
        'checkpoints, décisions du portique et trajectoires sont conservés dans chaque dossier.', '']
    return '\n'.join(lines)



def run_seed(job):
    seed,root,plan=job;root=Path(root)
    baseline=measure(root/str(seed)/'sans-panne',seed,plan=plan)
    print(seed,'sans-panne',baseline['executed'],'exécutions',flush=True)
    results=[]
    for duration in plan['durations']:
        for relay in (False,True):
            name=('relais-' if relay else 'panne-')+str(duration)
            result=compare(measure(root/str(seed)/name,seed,duration=duration,relay=relay,plan=plan),baseline)
            results.append(result)
            c=result['comparison']
            print(seed,name,'blocages',c['blocked_after_cut'],'indisponibilité',c['unavailable_ticks'],'ΔPNL',c['delta_pnl_eur'],flush=True)
    return seed,baseline,results


def measure_job(job):
    directory,seed,duration,relay,plan=job
    result=measure(directory,seed,duration=duration,relay=relay,plan=plan)
    print(seed,Path(directory).name,'terminé',flush=True)
    return result


def fresh_job(root,seed,name):
    directory=root/str(seed)/name
    if directory.exists() and not (directory/'result.json').exists():
        # Une mesure interrompue reste disponible, sans être confondue avec un run vert.
        archive=root/'incomplete';archive.mkdir(exist_ok=True)
        directory.rename(archive/(str(seed)+'-'+name+'-'+uuid.uuid4().hex))
    return directory


def main():
    import argparse
    from concurrent.futures import ProcessPoolExecutor
    from vigie.testing import deny_network
    sys.addaudithook(deny_network)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',help='Reprendre un dossier de preuves, sans écraser un run terminé')
    args=parser.parse_args()
    plan=json.loads(PLAN.read_text())
    root=Path(args.resume) if args.resume else Path(time.strftime('runs/cout-panne-%Y%m%d-%H%M%S-')+uuid.uuid4().hex)
    if args.resume:
        if json.loads((root/'plan.json').read_text())!=plan:raise ValueError('Le plan de reprise diffère')
    else:
        root.mkdir(parents=True,exist_ok=False)
        (root/'plan.json').write_text(json.dumps(plan,indent=2,ensure_ascii=False)+'\n')
    baselines={};jobs=[]
    for seed in plan['seeds']:
        directory=fresh_job(root,seed,'sans-panne')
        if (directory/'result.json').exists():baselines[seed]=json.loads((directory/'result.json').read_text())
        else:jobs.append((str(directory),seed,None,False,plan))
    with ProcessPoolExecutor(max_workers=4) as pool:
        for baseline in pool.map(measure_job,jobs):baselines[baseline['seed']]=baseline
        jobs=[];results=[]
        for seed in plan['seeds']:
            for duration in plan['durations']:
                for relay in (False,True):
                    name=('relais-' if relay else 'panne-')+str(duration)
                    directory=fresh_job(root,seed,name)
                    if (directory/'result.json').exists():
                        results.append(compare(json.loads((directory/'result.json').read_text()),baselines[seed]))
                    else:jobs.append((str(directory),seed,duration,relay,plan))
        for result in pool.map(measure_job,jobs):
            results.append(compare(result,baselines[result['seed']]))
    results.sort(key=lambda r:(r['seed'],r['duration_ticks'],r['relay']))
    (root/'metrics.json').write_text(json.dumps({'plan':plan,'baselines':baselines,'results':results},indent=2,ensure_ascii=False)+'\n')
    Path('vigie/COUT_PANNE.md').write_text(report(results,baselines,root))
    print(root/'metrics.json',flush=True)


if __name__=='__main__':main()
