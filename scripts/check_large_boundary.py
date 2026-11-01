#!/usr/bin/env python3
"""Correctness-only check of an actual LP with more than 2^32 matrix entries."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import zlib

ROOT=Path(__file__).resolve().parents[1]


def run(command):
    r=subprocess.run(list(map(str,command)),capture_output=True,text=True)
    if r.returncode:raise RuntimeError(r.stderr[-4000:])
    return json.loads(r.stdout)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--build',type=Path,default=ROOT/'build-native')
    p.add_argument('--work',type=Path,default=ROOT/'benchmarks/runs/boundary')
    p.add_argument('--output',type=Path,default=ROOT/'benchmarks/large-boundary.json');args=p.parse_args()
    with args.input.open('rb') as f:h=f.read(128)
    if len(h)!=128 or h[:8]!=b'BQP1\r\n\x1a\n' or zlib.crc32(h[:124])!=struct.unpack_from('<I',h,124)[0]:
        raise ValueError('invalid boundary source header')
    rows,cols,nnz=struct.unpack_from('<3Q',h,16);width=struct.unpack_from('<I',h,68)[0]
    if not nnz>2**32 or width!=4:raise ValueError('select a real >2^32-entry, 32-bit-row-index model')
    args.work.mkdir(parents=True,exist_ok=True);args.output.parent.mkdir(parents=True,exist_ok=True)
    target=args.work/(args.input.stem+'.optb');prep=None
    if not target.exists():prep=run([args.build/'bqp2optb',args.input,target])
    checked=run([args.build/'optb_native_bench','optbin',target,'bqp',args.input])
    if not checked['bitwise_reference_equal']:raise ValueError('boundary comparison failed')
    result={'scope':'Correctness-only single full native load and exact comparison; not a repeated performance benchmark',
        'id':args.input.stem,'rows':rows,'cols':cols,'nnz_A':nnz,'row_index_bytes':width,
        'source':str(args.input),'optbin':str(target),'preparation':prep,'comparison':checked}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(args.output,flush=True)


if __name__=='__main__':main()
