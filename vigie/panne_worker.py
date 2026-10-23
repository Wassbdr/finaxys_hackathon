"""Processus Krys réel sur des pipes privés, sans serveur réseau de mesure."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from bus.message import Keyring,MessageFactory,canonical,sha256
from sim.config import known_senders
from vigie.freeze_store import FileCheckpoint
from vigie.main import Runtime
from vigie.testing import deny_network


def main():
    sys.addaudithook(deny_network)
    p=argparse.ArgumentParser()
    p.add_argument('config');p.add_argument('meta');p.add_argument('checkpoint');p.add_argument('replica');p.add_argument('incarnation')
    p.add_argument('--fresh',action='store_true');a=p.parse_args()
    cfg=json.loads(Path(a.config).read_text());keys=Keyring(known_senders(cfg))
    rt=Runtime(cfg,keys,meta=json.loads(Path(a.meta).read_text()),checkpoint=FileCheckpoint(a.checkpoint),fresh_checkpoint=a.fresh)
    receipt=MessageFactory('vigie',keys);receipt.nonce='receipt-'+a.replica+':'
    seq=0;head='0'*64
    print(canonical({'ready':True,'pid':__import__('os').getpid(),'uncertain':sorted(rt.freeze.uncertain)}),flush=True)
    for line in sys.stdin:
        request=json.loads(line)
        if request.get('command')=='status':
            print(canonical({'uncertain':sorted(rt.freeze.uncertain),'clock':rt.freeze.clock,'seq':seq,'head':head}),flush=True)
            continue
        seq+=1;head=sha256(head+canonical(request))
        if request['command']=='observe':out=rt.on_message(request['message']);decision=None
        elif request['command']=='approve':
            decision=asdict(rt.request_approval(request['message']));out=rt.approval_out
        else:raise ValueError('Commande de mesure inconnue')
        out+=rt.diagnostics()
        reply={'out':out,'decision':decision,'uncertain':sorted(rt.freeze.uncertain),'clock':rt.freeze.clock}
        cert=receipt.make(max(0,rt.state.tick),'heartbeat','*',{'seq':seq,'head':head,'replica':a.replica,'incarnation':a.incarnation,'reply_hash':sha256(canonical(reply))})
        print(canonical({'reply':reply,'receipt':cert}),flush=True)


if __name__=='__main__':main()
