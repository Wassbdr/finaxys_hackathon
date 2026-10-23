"""Quotas réservés par émetteur ; une entrée rejetée n'est pas une panne interne."""
from collections import Counter, deque

from sim.config import known_senders, trader_ids
from vigie.freeze import MAX_EVENTS, MAX_REFERENCES


class Admission:
    def __init__(self, cfg):
        self.senders = frozenset(known_senders(cfg))
        self.traders = frozenset(trader_ids(cfg))
        self.trace_senders = self.traders | {'quant', 'compliance'}
        count = len(self.senders)
        self.message_limit = 20_000 // count
        self.byte_limit = 16*1024*1024 // count
        self.event_limit = MAX_EVENTS // count
        self.event_byte_limit = 1024*1024 // count
        self.trace_limit = 64 // len(self.trace_senders)
        self.reference_limit = MAX_REFERENCES // len(self.traders)
        self.cause_limit = (128-len(cfg['market']['assets'])) // len(self.traders)
        self.messages = Counter()
        self.bytes = Counter()
        self.references = {s:set() for s in self.traders}
        self.events = {s:deque(maxlen=self.event_limit) for s in self.senders}
        self.traces = {s:deque(maxlen=self.trace_limit) for s in self.trace_senders}
        self.event_sizes = {s:deque(maxlen=self.event_limit) for s in self.senders}
        self.unavailable_services = set()
        self.burst = {}
        self.gaps = {}
        # Les noms non authentifiés ne peuvent ni créer des clés sans borne,
        # ni consommer le quota de l'émetteur dont ils usurpent le nom.
        self.rejected = {}
        self.pending = set()
        self.last_notice = {}

    def reject(self, sender, reason, authenticated=False):
        sender = sender if isinstance(sender,str) and sender in self.senders else '?'
        bucket = ('signed:' if authenticated else 'unverified:') + sender
        counts = self.rejected.setdefault(bucket, Counter())
        counts[reason] = min(2**63-1, counts[reason]+1)
        self.pending.add(bucket)

    def check(self, msg, size, tick, freeze):
        sender, kind = msg['from'], msg['type']
        self.burst = {k:v for k,v in self.burst.items() if k[0]>=tick-1}
        if self.burst.get((tick,sender),0)>=128: return 'source_rate'
        if self.messages[sender]>=self.message_limit or self.bytes[sender]+size>self.byte_limit:
            return 'source_memory'
        events=self.events[sender]
        while events and events[0]<=tick-freeze.window: events.popleft()
        if kind in {'order_proposal','price_update','execution','llm_trace','alert'}:
            sizes=self.event_sizes[sender]
            while sizes and sizes[0][0]<=tick-freeze.window: sizes.popleft()
            if len(events)>=self.event_limit: return 'source_events'
            if sum(n for _,n in sizes)+size>self.event_byte_limit: return 'source_event_bytes'
        if kind=='llm_trace':
            if sender not in self.trace_senders: return 'source_trace'
            traces=self.traces[sender]
            while traces and traces[0]<=tick-freeze.window: traces.popleft()
            if len(traces)>=self.trace_limit: return 'source_trace'
        if kind=='order_proposal' and sender in self.traders:
            refs={r['id'] for r in msg['payload'].get('news_refs',[])}
            if len(self.references[sender] | refs)>self.reference_limit: return 'source_references'
            active={r['id'] for m in freeze.events if m['from']==sender and m['type']=='order_proposal'
                    and m['tick']>tick-freeze.window for r in m['payload'].get('news_refs',[])}
            if len(active | refs)>self.cause_limit: return 'source_causes'
        return None

    def commit(self, msg, size, tick):
        sender,kind=msg['from'],msg['type']
        self.messages[sender]+=1
        self.bytes[sender]+=size
        self.burst[(tick,sender)]=self.burst.get((tick,sender),0)+1
        if kind in {'order_proposal','price_update','execution','llm_trace','alert'}:
            self.events[sender].append(tick)
            self.event_sizes[sender].append((tick,size))
        if kind=='llm_trace': self.traces[sender].append(tick)
        if kind=='order_proposal' and sender in self.traders:
            self.references[sender].update(r['id'] for r in msg['payload'].get('news_refs',[]))

    def gap(self, sender, tick, window):
        self.gaps[sender]=max(self.gaps.get(sender,0),tick+window)

    def affected(self, msg, state, tick):
        pending=[msg]
        # La proposition imbriquée ne vaut pas preuve ; elle permet seulement
        # de refuser une opération dont la racine publique n'a pas été analysée.
        proposal=msg.get('payload',{}).get('proposal')
        if isinstance(proposal,dict) and proposal.get('message_id'):
            source=state.messages.get(proposal['message_id'])
            if source is None: return True
            pending.append(source)
        visited=set()
        while pending:
            current=pending.pop()
            mid=current['message_id']
            if mid in visited: continue
            visited.add(mid)
            if self.gaps.get(current['from'],-1)>=tick: return True
            if any(e.get('service') in self.unavailable_services for e in current.get('data_provenance',[])): return True
            for parent in current.get('depends_on',[]):
                if parent not in state.messages: return True
                pending.append(state.messages[parent])
        return False
