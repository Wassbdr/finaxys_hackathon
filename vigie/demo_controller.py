"""Deux processus de contrôle limités au bus signé et à l'instantané public /meta."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import threading
import time

from bus.message import Keyring, canonical
from sim.config import known_senders
from vigie.classique import Classique
from vigie.main import Runtime


class Bridge:
    def __init__(self,cfg,client,variant,*,meta):
        self.client=client
        self.prefix=cfg['redis']['prefix']
        self.keys=Keyring(known_senders(cfg))
        self.controller=(Classique(cfg,self.keys) if variant=='B' else Runtime(cfg,self.keys,meta=meta))

    def handle(self,raw):
        channel=raw['channel']
        if isinstance(channel,bytes): channel=channel.decode()
        try:
            data=json.loads(raw['data'])
            if channel==self.prefix+'bus':
                out=self.controller.on_message(data)
                # L'accusé ordonne le transport ; il ne représente aucune preuve financière.
                ack=data.get('message_id') if isinstance(data,dict) else None
                decision=None
            elif channel==self.prefix+'vigie.approval_requests':
                msg=data['message']
                if data['request_id'] != msg['message_id']: return
                out=self.controller.on_message(msg)
                decision=self.controller.request_approval(msg)
                if isinstance(self.controller,Classique): out+=self.controller.flush()
                ack=None
            else: return
            for msg in out:
                self.client.publish(self.prefix+'bus',canonical(msg))
            if decision is not None:
                self.client.publish(self.prefix+'vigie.approval_responses',canonical({
                    'request_id':data['request_id'],**asdict(decision)}))
            if ack:
                self.client.publish(self.prefix+'demo:ack',canonical({'message_id':ack}))
        except (KeyError,TypeError,ValueError,AttributeError):
            # Une enveloppe invalide laisse le portique expirer en refus.
            return


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    p.add_argument('--meta',required=True)
    p.add_argument('--variant',choices=['B','C'],required=True)
    args=p.parse_args()
    cfg=json.loads(Path(args.config).read_text())
    # Le fichier contient exactement les réponses core.meta(), sans état du monde.
    meta=json.loads(Path(args.meta).read_text())
    from bus.redis_bus import connect
    client=connect(cfg['redis']['url'],wait_s=10)
    bridge=Bridge(cfg,client,args.variant,meta=meta)
    prefix=cfg['redis']['prefix']
    sub=client.pubsub(ignore_subscribe_messages=False)
    channels={prefix+'bus',prefix+'vigie.approval_requests'}
    sub.subscribe(*sorted(channels))
    confirmed=set();deadline=time.monotonic()+5
    while confirmed!=channels:
        raw=sub.get_message(ignore_subscribe_messages=False,timeout=.05)
        if raw and raw['type']=='subscribe':confirmed.add(raw['channel'])
        if time.monotonic()>deadline:raise TimeoutError('Souscriptions non confirmées')
    client.set(prefix+'agent:vigie:pid',os.getpid())
    client.set(prefix+'demo:ready','1')
    stop=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    try:
        while not stop.is_set():
            raw=sub.get_message(ignore_subscribe_messages=True,timeout=.1)
            if raw is not None: bridge.handle(raw)
    finally:
        sub.close();client.close()


if __name__=='__main__': main()
