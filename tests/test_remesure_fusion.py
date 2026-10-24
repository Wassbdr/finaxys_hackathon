"""Les résultats publiés doivent rester liés à leurs preuves et au plan figé."""
import copy
import pytest

from vigie.remesure_fusion import validate_matrix, first_finding, compare_cell
from vigie.surete_utilite import load_plan


def matrix():
    plan=load_plan()
    rows=[{'case':case,'seed':seed,'variant':v,'margin':m}
          for case in plan['scenarios'] for seed in plan['seeds']
          for v,m in [('A',None),('B',None)]+[('C',m) for m in plan['margins']]]
    return {'plan':plan,'runs':rows}


def test_missing_or_duplicate_measurement_cannot_be_published():
    payload=matrix();validate_matrix(payload)
    payload['runs'][-1]=copy.deepcopy(payload['runs'][0])
    with pytest.raises(ValueError,match='matrice'):
        validate_matrix(payload)


def test_historical_plan_is_not_relabeled_as_the_measured_commit():
    payload=matrix();payload['plan']['margins'][-1]=0.3
    with pytest.raises(ValueError,match='plan'):
        validate_matrix(payload)


def test_first_finding_excludes_information_only_and_preserves_evidence():
    def alert(tick,payload):
        return {'tick':tick,'from':'vigie','type':'alert','message_id':str(tick),'payload':payload}
    messages=[alert(2,{'kind':'vigie_summary'}),
              alert(3,{'finding':{'target':'traders','kind':'herding'}}),
              alert(7,{'finding':{'target':'market-feed','kind':'monotone_drift','evidence':['q:6']}})]
    assert first_finding(messages)=={'tick':7,'message_id':'7','kind':'monotone_drift','evidence':['q:6']}
    assert first_finding(messages[:2]) is None


def test_comparison_keeps_regression_and_undefined_denominator():
    row={'asr':None,'contaminated_executed':0,'contaminated_proposed':0,
         'utility':0.8,'healthy_executed':8,'baseline_healthy_executed':10,
         'fp_total':2,'fp_min':0,'fp_max':2}
    text=compare_cell(row)
    assert 'N/A' in text and '80.000000' in text and '8/10' in text and '2 [0,2]' in text


def test_financial_amount_is_verified_against_saved_trajectory(tmp_path):
    from vigie.remesure_fusion import audit_result
    from vigie.surete_utilite import run_one
    from bus.message import Keyring
    from sim.config import known_senders, load_config
    plan=load_plan();plan['clean_duration_ticks']=4
    row=run_one(('propre',42,'A',None,str(tmp_path/'evidence'),plan))
    keys=Keyring(known_senders(load_config()))
    assert audit_result(row,keys)>0
    row['loss_eur']+=1
    with pytest.raises(ValueError,match='financier'):
        audit_result(row,keys)
