"""Les chiffres de l’onglet restent attribués et ses lectures ne peuvent rien commander."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from vigie.console.impact_data import build_cards, DATA


def test_measured_counts_denominators_and_limits_are_preserved():
    cards=build_cards()
    assert len(cards)==6
    assert [r['numerator'] for r in cards[0]['groups'][0]['rows']]==[2025,1591,448]
    assert [r['denominator'] for r in cards[0]['groups'][0]['rows']]==[2288,1707,2489]
    assert [r['numerator'] for r in cards[1]['groups'][0]['rows']]==[545,531,515]
    assert cards[2]['bound']['numerator']==3 and cards[2]['bound']['denominator']==319
    assert cards[2]['bound']['independence_required'] is True
    assert cards[2]['crash_refusals']==10 and cards[2]['crash_days_with_refusal']==1
    for card in cards:
        assert card['sample'] and card['meaning'] and card['limit'] and card['proofs']
        assert card['measured_commit']


def test_detection_does_not_turn_missing_findings_into_zero_delay():
    card=build_cards()[3]
    cve,ledger,rumour=card['groups']
    assert card['classical_after_theft_days']==10
    assert cve['injection_tick']==40 and cve['rows'][2]['delay_range']==[-10,-10]
    assert ledger['rows'][1]['observed']==0 and ledger['rows'][1]['value'] is None
    assert ledger['rows'][2]['delay_range']==[2,2]
    assert rumour['rows'][1]['observed']==4 and rumour['rows'][2]['observed']==10


def test_outage_retains_censoring_and_its_different_commit():
    card=build_cards()[5]
    assert card['measured_commit'].startswith('d179dd0')
    assert card['classical_measured'] is False
    assert card['unavailable_without_relay_min']==240 and card['right_censored'] is True
    assert card['unavailable_with_relay_max']==0
    assert [r['numerator'] for r in card['groups'][0]['rows']]==[273,42,273]


def test_impact_routes_are_read_only_and_do_not_expose_arbitrary_files():
    client=TestClient(create_app({}))
    for path in ['/dashboard/console/impact.js','/dashboard/console/impact.css','/dashboard/console/impact/data.json']:
        response=client.get(path)
        assert response.status_code==200
        assert client.post(path,json={}).status_code==405
    assert client.get('/dashboard/console/impact/config.yaml').status_code==404
    assert client.get('/dashboard/console/impact/%2e%2e%2fconfig.yaml').status_code==404
    assert len(client.get('/dashboard/console/impact/data.json').json()['cards'])==6


def test_published_archive_matches_sources_and_generated_cards():
    import hashlib
    payload=json.loads((DATA/'data.json').read_text())
    assert payload['cards']==build_cards()
    for name,meta in payload['origin']['sources'].items():
        assert hashlib.sha256((DATA/name).read_bytes()).hexdigest()==meta['copied_sha256']
    assert payload['origin']['measured_commit'].startswith('cf4973a')
    assert payload['origin']['analysis_commit'].startswith('48a980a')


def test_dom_renders_measured_cards_and_treats_hostile_text_as_text():
    node=shutil.which('node')
    if not node:pytest.skip('Node requis pour éprouver le rendu DOM')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([node,str(root/'tests/console_impact_dom.cjs'),str(root/'vigie/console/impact.js'),str(DATA/'data.json')],
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
