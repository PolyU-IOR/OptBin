#!/usr/bin/env python3
"""Read large-instance headers/stat metadata without scanning payloads.

Existing public-path SHA256 values are identified as previously recorded, not
newly verified. Numerical model fingerprints/comparisons are separate evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bqp',type=Path,required=True)
    p.add_argument('--mps',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--verify-payloads',action='store_true',help='scan all BQP files and verify recorded SHA256; run outside timed benchmarks')
    args=p.parse_args()
    sums=args.bqp/'SHA256SUMS';recorded={}
    if sums.exists():
        for line in sums.read_text().splitlines():
            checksum,name=line.split(maxsplit=1);recorded[Path(name.lstrip('*')).name]=checksum
    result={'source_collection':'Oliver Hinder large-scale LP instances',
        'source_url':'https://www.oliverhinder.com/large-scale-lp-problems',
        'scope':('Header CRCs, stats and BQP payload SHA256 checked now' if args.verify_payloads else
                 'Header CRCs and file stats checked now; existing payload SHA256 values copied without rescanning huge files'),
        'checksum_manifest_sha256':hashlib.sha256(sums.read_bytes()).hexdigest() if sums.exists() else None,'instances':[]}
    for path in sorted(args.bqp.glob('*.bqp')):
        with path.open('rb') as f:h=f.read(128)
        if len(h)!=128 or h[:8]!=b'BQP1\r\n\x1a\n' or zlib.crc32(h[:124])!=struct.unpack_from('<I',h,124)[0]:
            raise ValueError('invalid BQP header: '+str(path))
        rows,cols,nnz,qnnz,blocks,size=struct.unpack_from('<6Q',h,16)
        if size!=path.stat().st_size:raise ValueError('BQP file size differs from header')
        ai,qi=struct.unpack_from('<2I',h,68);block=struct.unpack_from('<Q',h,88)[0]
        decoded=8*(cols+1)+nnz*(ai+8)+cols*24+rows*16+(8*(cols+1)+qnnz*(qi+8) if qnnz else 0)
        mps=args.mps/(path.stem+'.mps.gz')
        result['instances'].append({'id':path.stem,'bqp_path':str(path.resolve()),'bqp_bytes':size,
            'mps_path':str(mps),'mps_gzip_bytes':mps.stat().st_size if mps.exists() else None,
            'rows':rows,'cols':cols,'nnz_A':nnz,'nnz_Q':qnnz,'a_index_bytes':ai,'q_index_bytes':qi,
            'block_elements':block,'blocks':blocks,'decoded_array_bytes':decoded,
            'bqp_sha256_from_existing_manifest':recorded.get(path.name),'payload_sha256_verified_this_run':False,
            'header_sha256':hashlib.sha256(h).hexdigest()})
        if args.verify_payloads:
            digest=hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(8*1024*1024),b''):digest.update(chunk)
            observed=digest.hexdigest()
            if recorded.get(path.name) and observed!=recorded[path.name]:raise ValueError('payload SHA256 differs: '+str(path))
            result['instances'][-1].update(payload_sha256_verified_this_run=True,observed_payload_sha256=observed)
            print(path.stem,'payload SHA256 verified',flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(args.output)


if __name__=='__main__':main()
