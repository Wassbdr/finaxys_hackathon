"""Admission bornée avant signature, copie ou analyse des données du bus."""
import math
import re
import json
from bus.message import MESSAGE_TYPES

MAX_MESSAGE_BYTES = 65_536
MAX_NODES = 2048
MAX_TEXT = 8192
MAX_REFS = 64


def cost(value):
    pending, seen, total, nodes = [(value, 0)], set(), 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > 12:
            return None
        if isinstance(item, str):
            if len(item) > MAX_TEXT:
                return None
            try: total += len(item.encode('utf-8')) + 8
            except UnicodeError: return None
        elif item is None or type(item) in {bool, int, float}:
            if isinstance(item, (int, float)) and (abs(item) > 1e15 or not math.isfinite(item)):
                return None
            total += 24
        elif type(item) in {dict, list}:
            if id(item) in seen or len(item) > MAX_NODES:
                return None
            seen.add(id(item))
            if isinstance(item, dict):
                if any(not isinstance(k, str) or len(k) > 128 for k in item):
                    return None
                pending.extend((v, depth+1) for pair in item.items() for v in pair)
            else:
                pending.extend((v, depth+1) for v in item)
            total += 32
        else:
            return None
        if total > MAX_MESSAGE_BYTES:
            return None
    return total


def valid_message(msg, assets=None):
    if cost(msg) is None or not isinstance(msg, dict):
        return False
    for field in ('message_id', 'from', 'type'):
        if not isinstance(msg.get(field), str) or not 0 < len(msg[field]) <= 256:
            return False
    if type(msg.get('tick')) is not int or not 0 <= msg['tick'] <= 10**12 or not isinstance(msg.get('payload'), dict):
        return False
    for field in ('depends_on', 'data_provenance'):
        values = msg.get(field, [])
        if not isinstance(values, list) or len(values) > MAX_REFS:
            return False
    if any(not isinstance(v, str) for v in msg.get('depends_on', [])):
        return False
    if any(not isinstance(v, dict) or not isinstance(v.get('service'), str) for v in msg.get('data_provenance', [])):
        return False
    if len(json.dumps(msg['message_id'],ensure_ascii=False).encode())>256: return False
    p, kind = msg['payload'], msg['type']
    if kind not in MESSAGE_TYPES: return False
    if kind=='heartbeat':
        view=p.get('view',{})
        if not isinstance(view,dict): return False
        if 'cash_eur' in view and type(view['cash_eur']) not in (int,float): return False
    if kind=='alert':
        cve=p.get('cve',{})
        if not isinstance(cve,dict) or not isinstance(cve.get('affects',{}),dict): return False
        if p.get('kind')=='cve_published' and 'cve' not in p: return False
        if 'language' in cve.get('affects',{}) and not isinstance(cve['affects']['language'],str): return False
        version=cve.get('affects',{}).get('version_lt')
        if version is not None and (not isinstance(version,str) or not re.fullmatch(r'[0-9]{1,8}(?:\.[0-9]{1,8}){0,4}',version)): return False
        if 'message_ids' in p and (not isinstance(p['message_ids'],list) or any(not isinstance(v,str) for v in p['message_ids'])): return False
    if kind=='risk_decision' and 'proposal' in p:
        if not isinstance(p['proposal'],dict) or not isinstance(p['proposal'].get('message_id'),str): return False
    for entry in msg.get('data_provenance',[]):
        if any(k in entry and not isinstance(entry[k],str) for k in ('version','response_hash')): return False
    if kind == 'tick' and (msg['from']!='clock' or p.get('tick')!=msg['tick']):
        return False
    if kind == 'order_proposal':
        if not isinstance(p.get('asset'), str) or p.get('side') not in ('buy', 'sell'):
            return False
        if assets is not None and p['asset'] not in assets:
            return False
        refs = p.get('news_refs', [])
        if not isinstance(refs, list) or len(refs) > MAX_REFS:
            return False
        if any(not isinstance(r, dict) or not isinstance(r.get('id'), str)
               or len(json.dumps(r['id'],ensure_ascii=False).encode())>256
               or ('source' in r and not isinstance(r['source'], str)) for r in refs):
            return False
        if 'notional_eur' in p and (type(p['notional_eur']) not in (int, float) or p['notional_eur'] <= 0):
            return False
    if kind == 'llm_trace':
        response = p.get('response', {})
        if (not isinstance(response, dict) or not isinstance(response.get('justification', ''), str)
                or len(response.get('justification','')) > 2048):
            return False
        if not isinstance(response.get('sentiment', {}), dict):
            return False
    if kind == 'price_update':
        prices = p.get('assets', {})
        if not isinstance(prices, dict) or len(prices) > 64:
            return False
        if assets is not None and any(asset not in assets for asset in prices): return False
        if any(not isinstance(v, dict) or type(v.get('price')) not in (int, float) or v['price'] <= 0 for v in prices.values()):
            return False
    if kind == 'execution':
        if 'exec_id' in p and not isinstance(p['exec_id'],str): return False
        for key in ('qty', 'fill_price', 'amount_eur'):
            if key in p and type(p[key]) not in (int, float):
                return False
        if p.get('qty',0)<0 and (not isinstance(p.get('asset'),str) or assets is not None and p['asset'] not in assets): return False
    if kind == 'human_decision' and (not isinstance(p.get('action',''),str) or not isinstance(p.get('target', '*'), str)):
        return False
    return True
