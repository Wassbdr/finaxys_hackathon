"""Deux crises sans pouvoir d'injection supplémentaire, seuils des Vigies conservés."""
import pytest

from sim.config import load_config
from vigie.demo_live import load_day


@pytest.mark.parametrize('name',['krach_reel','krach_rumeur'])
def test_crisis_has_sixty_calm_ticks_then_herding(name,cfg):
    day=load_day(f'vigie/scenarios/{name}.yaml',cfg)
    assert day['duration']=='180s'
    assert day['params']['herding']==.3
    active=[(tick,args) for tick,args in day['commands'] if args[0]!='note']
    assert min(t for t,_ in active)==60
    assert (60,['set','herding','0.8']) in active


def test_real_crash_is_only_public_true_news(cfg):
    day=load_day('vigie/scenarios/krach_reel.yaml',cfg)
    active=[(tick,args) for tick,args in day['commands'] if args[0]!='note']
    assert all(args[0] in {'news','set'} for _,args in active)
    shocks=[(tick,args) for tick,args in active if args[0]=='news']
    assert {args[1] for _,args in shocks}=={'ACME','BOREAL','CITRA'}
    assert len(shocks)==6
    assert all(-12<=float(args[2])<=-8 for _,args in shocks)
    assert max(t for t,_ in shocks)-min(t for t,_ in shocks)<=15


def test_false_crash_compromises_only_newswire_and_never_shocks_truth(cfg):
    day=load_day('vigie/scenarios/krach_rumeur.yaml',cfg)
    active=[(tick,args) for tick,args in day['commands'] if args[0]!='note']
    assert active==[(60,['set','herding','0.8']),(60,['compromise','news-wire'])]


def test_activity_distinguishes_proposed_executed_and_refused_sales():
    from vigie.crisis_metrics import activity
    def event(mid,tick,agent,asset,*,kind='execution',status='executed'):
        return {'message_id':mid,'tick':tick,'from':agent if kind=='order_proposal' else 'executor','type':kind,
                'payload':{'kind':'order','status':status,'asset':asset,'side':'sell','trader':agent,
                           'notional_eur':100000,'impact_pct':.05}}
    messages=[event('old',59,'trader-1','ACME'),event('one',60,'trader-1','ACME'),
              event('two',69,'trader-2','ACME'),event('other',62,'trader-3','BOREAL'),
              event('refused',63,'trader-1','ACME',status='refused'),
              event('proposal',64,'trader-1','ACME',kind='order_proposal')]
    result=activity(messages,60,180)
    assert result['sell_proposals']==1 and result['sell_executions']==3
    assert result['sell_notional_eur']==300000
    assert result['peak_same_asset_sells_10ticks']==2
    assert result['peak_distinct_sellers_10ticks']==2
    assert result['cascade_example']['message_ids']==['one','two']
    assert result['sale_rate_per_10ticks']==.25


def test_parallel_measurements_never_reuse_a_second_based_directory(monkeypatch):
    from vigie import demo_live
    outputs=[]
    monkeypatch.setattr(demo_live.time,'strftime',lambda *_:'runs/meme-seconde')
    monkeypatch.setattr(demo_live,'measure',lambda args:outputs.append(args.out))
    demo_live.main(['measure','--scenario','vigie/scenarios/krach_reel.yaml'])
    demo_live.main(['measure','--scenario','vigie/scenarios/krach_rumeur.yaml'])
    assert len(set(outputs))==2


@pytest.mark.parametrize('name',['krach_reel','krach_rumeur'])
@pytest.mark.parametrize('seed',[42,43])
def test_crisis_attribution_and_freeze_effect_are_measured_after_run(tmp_path,cfg,name,seed):
    from vigie.demo_live import DemoRunner
    from vigie.crisis_metrics import evaluate_crisis
    cfg['seed']=seed
    day=load_day(f'vigie/scenarios/{name}.yaml',cfg)
    runner=DemoRunner(cfg,day,tmp_path/name/str(seed))
    try:
        for _ in range(runner.duration):frame=runner.step()
        result=evaluate_crisis(runner,frame,cfg,day,runner.directory)
        for world in result['worlds'].values():
            assert abs(world['attribution']['conservation_error_eur'])<.01
        c=result['worlds']['C']
        # Le jeu de données est figé : aucun résultat économique n'est imposé
        # au détecteur ; on éprouve le bilan constaté avec le profil déjà livré.
        if name=='krach_reel':
            assert frame['banks']['C']['contaminated_executions']==0
            assert not c['service_suspicions']
            assert c['freeze_clean_refusals']==0
            assert result['freeze_effect']['effect']=='neutre'
            assert len(c['freeze_starts'])==(0 if seed==42 else 2)
        else:
            assert c['freeze_tainted_refusals']>0 and c['freeze_clean_refusals']==0
            # La quarantaine d'injection modifie l'ablation : en graine 43,
            # moins de ventes contaminées ne signifie plus un meilleur P&L.
            if seed==43:
                assert result['freeze_effect']['effect']=='nuit'
                assert result['freeze_effect']['delta_pnl_eur']==pytest.approx(-4300.81,abs=.01)
            else:
                assert result['freeze_effect']['effect']=='aide'
                assert result['freeze_effect']['delta_pnl_eur']>0
            assert c['crisis']['sell_executions']<result['freeze_effect']['C_without_freeze_analysis']['crisis']['sell_executions']
    finally:runner.close()
