#!/usr/bin/env python3
"""Fetch source-attributed public cases from the validation manifest.

Local MPS variants are not redistributed. Four public QPLIB LPs require Gurobi
to reproduce the MPS export; the original LP is retained and hashed separately.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=ROOT/'benchmarks/real-cases.json')
    p.add_argument('--cbf-only',action='store_true',help='skip QPLIB LP -> MPS exports')
    args=p.parse_args()
    for item in json.loads(args.manifest.read_text()):
        if not item.get('source_url'):
            print(item['id'],'local input; supply it or edit your manifest');continue
        if args.cbf_only and item['format']!='cbf':continue
        original=ROOT/item.get('original_path',item['path'])
        original.parent.mkdir(parents=True,exist_ok=True)
        expected=item.get('sha256') or item['source_sha256']
        if original.exists():data=original.read_bytes()
        else:data=urllib.request.urlopen(item['source_url'],timeout=40).read()
        if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('source checksum mismatch: '+item['id'])
        if not original.exists():
            with original.open('xb') as f:f.write(data)
        if item.get('source_format')=='lp':
            target=ROOT/item['path']
            if not target.exists():
                import gurobipy as gp
                with gp.Env(empty=True) as env:
                    env.setParam('OutputFlag',0);env.start()
                    with gp.read(str(original),env) as model:model.write(str(target))
        print(item['id'],'source checksum verified',flush=True)


if __name__=='__main__':main()
