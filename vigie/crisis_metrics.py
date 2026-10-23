"""Arbitre des crises : bilan après run et diagnostic causal du gel, hors des Vigies."""
from dataclasses import asdict
import copy
import json

from journal.writer import verify_journal

CRISES={'krach_reel','krach_rumeur'}


def activity(messages,start,end):
    selected=[m for m in messages if start<=m['tick']<end]
    proposals=[m for m in selected if m['type']=='order_proposal' and m['payload'].get('side')=='sell']
    executions=[m for m in selected if m['from']=='executor' and m['type']=='execution'
                and m['payload'].get('status')=='executed']
    sells=[m for m in executions if m['payload'].get('kind')=='order' and m['payload'].get('side')=='sell']
    sells=sorted(sells,key=lambda m:(m['tick'],m['message_id']))
    peak=0;distinct=0;example=None
    # Une coordination de ventes n'est pas, à elle seule, une irrationnalité :
    # la fenêtre et les identifiants permettent de regarder la séquence réelle.
    for first in sells:
        same=[m for m in sells if first['tick']<=m['tick']<first['tick']+10
              and m['payload']['asset']==first['payload']['asset']]
        peers=len({m['payload']['trader'] for m in same})
        peak=max(peak,len(same));distinct=max(distinct,peers)
        if peers>=2 and example is None:
            example={'asset':first['payload']['asset'],'start_tick':first['tick'],'end_tick':first['tick']+9,
                     'message_ids':[m['message_id'] for m in same]}
    return {'start_tick':start,'end_tick_exclusive':end,'sell_proposals':len(proposals),
            'sell_executions':len(sells),'sell_notional_eur':sum(m['payload']['notional_eur'] for m in sells),
            'sale_rate_per_10ticks':len(sells)*10/(end-start),
            'peak_same_asset_sells_10ticks':peak,'peak_distinct_sellers_10ticks':distinct,'cascade_example':example,
            'payments_executed':sum(m['payload'].get('kind')=='payment' for m in executions),
            'risk_rejected_orders':sum(m['from']=='risk' and m['type']=='risk_decision'
                and m['payload'].get('approved') is False and m['payload'].get('kind')=='order' for m in selected),
            'executed_sell_message_ids':[m['message_id'] for m in sells]}


def first_tick(messages,predicate):
    return next((m['tick'] for m in messages if predicate(m)),None)


def tainted_request(ls,msg,by_id):
    # Le marquage du simulateur est consulté uniquement pour noter après coup.
    pending=[msg];visited=set()
    while pending:
        current=pending.pop();mid=current['message_id']
        if mid in visited:continue
        visited.add(mid)
        if any(ls.store.sismember(ls.prefix+'tainted',e['response_hash'])
               for e in current.get('data_provenance',[]) if e.get('response_hash')):
            return True
        pending.extend(by_id[parent] for parent in current.get('depends_on',[]) if parent in by_id)
    return False


def summarize(runner,name):
    ls=runner.worlds[name];messages=runner.messages[name];ledger=ls.world.ledger
    by_id={m['message_id']:m for m in messages}
    orders=[m for m in messages if m['from']=='executor' and m['type']=='execution'
            and m['payload'].get('kind')=='order' and m['payload'].get('status')=='executed']
    slippage=sum(m['payload']['qty']*(m['payload']['mid_price']-m['payload']['fill_price']) for m in orders)
    attributed=sum(value for cause,value in ledger.contrib.items() if cause.startswith('exec:'))
    noise=sum(value for cause,value in ledger.contrib.items() if cause=='market')
    news=sum(value for cause,value in ledger.contrib.items() if cause.startswith('news:'))
    attribution={'market_noise_eur':noise,'true_news_eur':news,'executions_total_eur':attributed,
                 'execution_slippage_eur':slippage,'price_impact_and_decay_eur':attributed-slippage,
                 'trading_pnl_raw_eur':ledger.trading_pnl(),
                 'conservation_error_eur':noise+news+attributed-ledger.trading_pnl()}
    result={'calm':activity(messages,0,60),'crisis':activity(messages,60,runner.duration),
            'risk_halt_tick':first_tick(messages,lambda m:m['from']=='risk' and m['type']=='alert'
                                      and m['payload'].get('kind')=='risk_halt'),
            'global_red_tick':first_tick(messages,lambda m:m['from']=='vigie' and m['type']=='light_change'
                                       and m['payload'].get('light')=='ROUGE'),
            'attribution':attribution,'final_prices':dict(ls.world.market.prices),'freeze_starts':[],
            'freeze_refusals':[],'freeze_tainted_refusals':0,'freeze_clean_refusals':0,'service_suspicions':[]}
    if name=='C':
        controller=runner.controllers[name]
        result['freeze_starts']=[copy.deepcopy(t) for t in controller.freeze.transitions if t['event']=='start']
        result['freeze_transitions']=copy.deepcopy(controller.freeze.transitions)
        result['service_suspicions']=[asdict(f) for f in controller.contagion.suspects.values()]
        for decision in controller.decisions:
            if decision['approved'] or not decision.get('herding_frozen'):continue
            msg=by_id[decision['message_id']]
            contaminated=tainted_request(ls,msg,by_id)
            result['freeze_refusals'].append({**decision,'contaminated_request':contaminated})
            result['freeze_tainted_refusals']+=int(contaminated)
            result['freeze_clean_refusals']+=int(not contaminated)
    return result


def evaluate_crisis(runner,frame,cfg,day,directory):
    from vigie.demo_live import DemoRunner
    main={name:summarize(runner,name) for name in 'ABC'}
    diagnostic_cfg=copy.deepcopy(cfg)
    # L'ablation sert uniquement à attribuer un effet au gel ; le direct et les
    # trois colonnes principales gardent le profil livré, sans seuil retouché.
    diagnostic_cfg['vigie']['freeze_profile']=None
    directory=directory/'diagnostic_sans_gel'
    diagnostic=DemoRunner(diagnostic_cfg,day,directory)
    try:
        for _ in range(diagnostic.duration):other=diagnostic.step()
        no_freeze=summarize(diagnostic,'C')
        reference=other['banks']['C']
        if any(other['banks'][n]!=frame['banks'][n] for n in 'AB'):
            raise RuntimeError("L'ablation du gel a modifié le témoin A/B")
    finally:diagnostic.close()
    for name in 'ABC':
        if not verify_journal(directory/name/'journal.jsonl')[0]:raise RuntimeError('Journal diagnostic altéré')
    delta=frame['banks']['C']['pnl_eur']-reference['pnl_eur']
    return {'phase_start_tick':60,'worlds':main,
            'freeze_effect':{'C_without_freeze':reference,'C_without_freeze_analysis':no_freeze,
                'delta_pnl_eur':round(delta,2),'effect':'aide' if delta>.005 else 'nuit' if delta<-.005 else 'neutre',
                'diagnostic_run_dir':directory.as_posix()}}
