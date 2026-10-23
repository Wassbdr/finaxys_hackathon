"""Les compteurs de continuité notent après coup, sans modifier les Vigies."""
from vigie.demo_metrics import continuity


def execution(mid,tick,kind,*,contaminated=False,status='executed'):
    return {'message_id':mid,'tick':tick,'from':'executor','type':'execution',
            'payload':{'kind':kind,'status':status,
                       'compromised_sources':['market-feed'] if contaminated else [],
                       'decided_by':'vigie-layer3' if status=='refused' else 'executor'}}


def test_common_anchor_and_strict_after_window():
    messages=[execution('executor:old',45,'order',contaminated=True),
              execution('executor:boundary',46,'payment'),
              execution('executor:payment',47,'payment'),
              execution('executor:bad',48,'order',contaminated=True),
              execution('executor:good',49,'order'),
              execution('executor:denied',50,'order',status='refused')]
    counts=continuity(messages,46)
    assert counts['anchor_tick']==46
    assert counts['after_anchor']=={
        'executed_orders':2,'contaminated_orders':1,'healthy_orders':1,
        'executed_payments':1,'contaminated_payments':0,'healthy_payments':1,
        'gate_refused_orders':1,'gate_refused_payments':0,
        'payment_message_ids':['executor:payment'],'contaminated_order_message_ids':['executor:bad'],
        'gate_refusal_message_ids':['executor:denied']}


def test_no_detection_is_unknown_window_not_zero():
    assert continuity([execution('executor:payment',47,'payment')],None)['after_anchor'] is None


def test_refusal_is_never_counted_as_success_and_payment_contamination_is_not_healthy():
    counts=continuity([execution('executor:denied',47,'payment',status='refused'),
                       execution('executor:tainted',48,'payment',contaminated=True)],46)['after_anchor']
    assert counts['executed_payments']==1 and counts['healthy_payments']==0
    assert counts['gate_refused_payments']==1
