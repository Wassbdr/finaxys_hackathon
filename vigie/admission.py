"""Quotas réservés par émetteur ; une entrée rejetée n'est pas une panne interne."""
from collections import Counter, deque
import hashlib
from bus.message import FIELDS, canonical

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
        self.services=frozenset(cfg['services'])
        self.identities={}
        self.proposal_identities={}
        self.unanalysed=set()
        self.inline_roots={}
        self.direct_proofs={}
        self.provenance_roots={}
        self.rejected_provenance={s:set() for s in self.senders}
        self.opaque_producers=set()
        self.last_reason=None
        self.last_evidence=[]
        self.direct_services={}
        self.root_kinds={}
        for sender in self.senders:
            role='trader' if sender in self.traders else sender
            self.direct_services[sender]=set(cfg['agents'].get(role,{}).get('depends_on',[]))
            self.root_kinds[sender]={
                'quant':{'price_update'}, 'trader':{'order_proposal','llm_trace'},
                'compliance':{'compliance_decision','llm_trace'}, 'payment':{'payment_proposal'},
                'risk':{'heartbeat','alert'}, 'executor':{'execution'},
            }.get(role,set())
            if role=='trader':
                # L'appel de règlement est public dans le code producteur, bien
                # qu'il soit volontairement absent de depends_on dans la config.
                self.direct_services[sender].add('payment-gateway')

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

    @staticmethod
    def identity(msg):
        # Seuls les champs signés comptent, jamais les annotations du simulateur.
        return hashlib.sha256(canonical({k:msg.get(k) for k in FIELDS}).encode()).hexdigest()

    def same_message(self,msg):
        return self.identities.get(msg['message_id'])==self.identity(msg)

    @staticmethod
    def proposal_identity(msg):
        payload=msg.get('payload')
        if not isinstance(payload,dict): return None
        # Compliance transmet une copie courte et ajoute son commentaire KYC ;
        # les données financières doivent rester celles de la racine signée.
        payload={k:v for k,v in payload.items() if k!='kyc_justification'}
        return hashlib.sha256(canonical({'message_id':msg.get('message_id'),
            'from':msg.get('from'),'type':msg.get('type'),'payload':payload}).encode()).hexdigest()

    def same_proposal(self,msg):
        mid=msg.get('message_id')
        if all(k in msg for k in FIELDS): return self.same_message(msg)
        return (set(msg)=={'message_id','from','type','payload'}
                and self.proposal_identities.get(mid)==self.proposal_identity(msg))

    def provenance_key(self,entry,tick):
        if not isinstance(entry,dict) or not isinstance(entry.get('service'),str) or entry['service'] not in self.services: return None
        if any(not isinstance(entry.get(k),str) or not 0<len(entry[k])<=256
               for k in ('version','response_hash')): return None
        if type(entry.get('tick')) is not int or not 0<=entry['tick']<=tick+1: return None
        return tuple(entry[k] for k in ('service','version','response_hash','tick'))

    def can_introduce(self,msg,key):
        return (msg['from'] not in self.opaque_producers
                and key not in self.rejected_provenance.get(msg['from'],())
                and key[0] in self.direct_services.get(msg['from'],())
                and msg['type'] in self.root_kinds.get(msg['from'],()))

    def reject_provenance(self,msg,tick):
        # Cette mémoire n'est alimentée qu'après vérification HMAC. Un paquet
        # forgé ne peut pas empoisonner une preuve déjà admise d'un autre acteur.
        sender=msg.get('from')
        if sender not in self.senders: return
        entries=msg.get('data_provenance',[])
        if not isinstance(entries,list): return
        rejected=self.rejected_provenance[sender]
        for entry in entries[:64]:
            key=self.provenance_key(entry,tick)
            if key is None: continue
            if len(rejected)>=self.message_limit:
                self.opaque_producers.add(sender)
                break
            rejected.add(key)

    def record(self,msg,state,tick):
        mid=msg['message_id']
        self.identities[mid]=self.identity(msg)
        self.proposal_identities[mid]=self.proposal_identity(msg)
        proposal=msg['payload'].get('proposal')
        if isinstance(proposal,dict):
            source=proposal['message_id']
            if source not in state.messages or not self.same_proposal(proposal):
                self.unanalysed.add(mid)
                self.last_reason='unknown_or_changed_proposal'
                self.last_evidence=[mid,source]
                return False
            self.inline_roots[mid]=source
        if self.affected(msg,state,tick,introducing=True):
            self.unanalysed.add(mid)
            return False
        keys={self.provenance_key(e,tick) for e in msg.get('data_provenance',[])}
        direct={key for key in keys if key is not None and self.can_introduce(msg,key)}
        self.direct_proofs[mid]=direct
        for key in direct:
            self.provenance_roots.setdefault(key,{})[msg['from']]=mid
        return True

    def affected(self,msg,state,tick,*,introducing=False):
        self.last_reason=None
        self.last_evidence=[]
        visiting=set()
        memo={}
        remaining=2048
        candidate=msg['message_id']

        def fail(reason,mid):
            self.last_reason=reason
            if mid not in self.last_evidence and len(self.last_evidence)<64:
                self.last_evidence.append(mid)
            return False

        def covered(current,depth=0):
            nonlocal remaining
            mid=current['message_id']
            if mid in memo: return memo[mid]
            remaining-=1
            if remaining<0 or depth>128: return fail('proof_budget',mid)
            if mid in visiting: return fail('proof_cycle',mid)
            if mid in self.unanalysed: return fail('unanalysed_input',mid)
            if self.gaps.get(current['from'],-1)>=tick: return fail('incomplete_source',mid)
            visiting.add(mid)
            try:
                parents=list(current.get('depends_on',[]))
                if mid in self.inline_roots: parents.append(self.inline_roots[mid])
                for parent in parents:
                    previous=state.messages.get(parent)
                    if previous is None: return fail('unknown_parent',parent)
                    if not covered(previous,depth+1): return fail(self.last_reason,mid)
                for e in current.get('data_provenance',[]):
                    if e.get('service') in self.unavailable_services:
                        return fail('unavailable_service',mid)
                    key=self.provenance_key(e,tick)
                    if key is None: return fail('unknown_provenance',mid)
                    if key in self.direct_proofs.get(mid,()): continue
                    if introducing and mid==candidate and self.can_introduce(current,key): continue
                    witnesses=self.provenance_roots.get(key,{})
                    saved_reason=self.last_reason
                    saved_evidence=list(self.last_evidence)
                    found=False
                    for witness in witnesses.values():
                        root=state.messages.get(witness)
                        if root is not None and covered(root,depth+1):
                            found=True
                            self.last_reason=saved_reason
                            self.last_evidence=saved_evidence
                            break
                    if not found: return fail('unknown_or_rejected_provenance',mid)
                memo[mid]=True
                return True
            finally:
                visiting.discard(mid)

        return not covered(msg)
