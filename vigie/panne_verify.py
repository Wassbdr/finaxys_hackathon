"""Audit hors ligne des processus, préfixes attestés, journaux et comptes de panne."""
import argparse
import json
from pathlib import Path
import sys

from bus.message import Keyring,canonical,sha256
from journal.writer import verify_journal
from sim.config import known_senders
from vigie.testing import deny_network


def verify_run(result):
    folder=Path(result['result_path']).parent
    controller=folder/'controllers'
    cfg=json.loads((controller/'config.json').read_text());keys=Keyring(known_senders(cfg))
    inputs=[json.loads(line) for line in (controller/'input-stream.jsonl').read_text().splitlines()]
    head='0'*64
    for seq,entry in enumerate(inputs,1):
        head=sha256(head+canonical(entry['request']))
        assert entry['seq']==seq and entry['head']==head,'Préfixe d’entrée altéré'
    cut=result['plan']['cut_tick'];duration=result['duration_ticks']
    restarts={e['pid']:e['tick'] for e in result['events'] if e['kind']=='process_restarted'}
    if duration is not None:
        killed=next(e for e in result['events'] if e['kind']=='process_killed')
        restarted=next(e for e in result['events'] if e['kind']=='process_restarted')
        assert killed['returncode']==-9 and killed['pid']!=restarted['pid'],'Le processus n’a pas réellement été remplacé'
        assert killed['tick']==cut and restarted['tick']==cut+duration
        assert 'checkpoint_unavailable' in restarted['uncertain'],'Le redémarrage a contourné le refus conservateur'
    receipt_count=0;incarnations=set()
    for path in controller.glob('*-receipts-*.jsonl'):
        name=path.name.split('-receipts-')[0];pid=int(path.stem.split('-')[-1])
        stream=inputs
        if pid in restarts:stream=[e for e in inputs if e['request']['message']['tick']>=restarts[pid]]
        elif name=='primary' and duration is not None:stream=[e for e in inputs if e['request']['message']['tick']<cut]
        certs=[json.loads(line) for line in path.read_text().splitlines()]
        assert len(certs)==len(stream),(str(path),len(certs),len(stream))
        head='0'*64;incarnation=None
        for seq,(cert,entry) in enumerate(zip(certs,stream),1):
            body=cert['payload'];head=sha256(head+canonical(entry['request']))
            assert keys.verify(cert) and cert['from']=='vigie','Reçu HMAC invalide'
            assert body['replica']==name and body['seq']==seq and body['head']==head,'Réception discontinue'
            if incarnation is None:incarnation=body['incarnation'];assert incarnation not in incarnations;incarnations.add(incarnation)
            assert body['incarnation']==incarnation and len(body['reply_hash'])==64
            receipt_count+=1
    ok,count,bad=verify_journal(folder/'world/journal.jsonl')
    assert ok,('Journal altéré',bad)
    messages=[json.loads(line)['message'] for line in (folder/'world/journal.jsonl').read_text().splitlines()]
    assert all(keys.verify(msg) for msg in messages),'Message de banque non signé'
    with (folder/'world/states.jsonl').open('rb') as stream:
        stream.seek(max(0,(folder/'world/states.jsonl').stat().st_size-1024*1024))
        final_state=json.loads(stream.read().splitlines()[-1])['state']
    pnl=final_state['pnl']['trading_eur']+final_state['pnl']['payment_fraud_eur']
    assert abs(pnl-result['pnl_eur'])<0.001,'Le P&L diffère du dernier état du monde'
    executed=[m for m in messages if m['from']=='executor' and m['type']=='execution' and m['payload'].get('status')=='executed']
    assert not any(m['payload'].get('compromised_sources') for m in executed),'Le run n’est pas propre'
    assert len(executed)==result['executed']==sum(o['executed'] for o in result['operations'])
    assert abs(sum(m['payload'].get('notional_eur',m['payload'].get('amount_eur',0)) for m in executed)-result['executed_eur'])<0.001
    humans=[m for m in messages if m['type']=='human_decision']
    if duration is not None:
        assert len(humans)==2 and all(m['from']=='human' and m['tick']==cut+duration+result['plan']['human_delay_after_restart_ticks'] for m in humans)
        if not result['relay']:
            assert not any(m['tick']>=cut for m in executed),'Reprise non attestée après redémarrage'
            assert not any(d['approved'] for d in result['decisions'] if d['tick']>=cut),'Portique relâché après la panne'
        else:
            switched=next(e for e in result['events'] if e['kind']=='failover')
            assert switched['tick']==cut and switched['to']=='standby'
            assert all(p['active']=='standby' for p in result['availability'] if p['tick']>=cut)
    else:assert not humans
    return {'journal_entries':count,'receipts':receipt_count,'operations':len(result['operations'])}


def verify(root):
    data=json.loads((Path(root)/'metrics.json').read_text())
    totals={'runs':0,'journal_entries':0,'receipts':0,'operations':0}
    for result in list(data['baselines'].values())+data['results']:
        stats=verify_run(result);totals['runs']+=1
        for k,v in stats.items():totals[k]+=v
        if result['duration_ticks'] is not None:
            baseline=data['baselines'][str(result['seed'])]
            base={tuple(o['key']):o for o in baseline['operations']}
            actual={tuple(o['key']):o for o in result['operations']}
            cut=result['plan']['cut_tick'];c=result['comparison']
            absent=[o for k,o in base.items() if o['tick']>=cut and k not in actual]
            lost=[o for k,o in base.items() if o['tick']>=cut and o['executed'] and (k not in actual or not actual[k]['executed'])]
            assert c['not_proposed']==len(absent) and abs(c['not_proposed_eur']-sum(o['requested_eur'] for o in absent))<0.001
            assert c['lost_baseline_executions']==len(lost) and abs(c['lost_baseline_eur']-sum(o['execution_eur'] for o in lost))<0.001
            assert abs(c['delta_pnl_eur']-(result['pnl_eur']-baseline['pnl_eur']))<0.001
            assert c['unavailable_ticks']==sum(not p['ready'] for p in result['availability'] if p['tick']>=cut)
    assert totals['runs']==14
    return totals


def main():
    sys.addaudithook(deny_network)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root');args=p.parse_args()
    result=verify(args.root)
    (Path(args.root)/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
