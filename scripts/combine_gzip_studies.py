#!/usr/bin/env python3
"""Combine completed case studies, retaining their exact inputs and provenance."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('reports',type=Path,nargs='+')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():p.error('output already exists')
    result={'schema':2,'complete':True,'study_reports':[],'results':[],
        'scope':'Completed practical gzip case studies; per-study compression preparation settings retained'}
    reference=None;ids=set()
    for path in args.reports:
        data=path.read_bytes();study=json.loads(data)
        if not study.get('complete') or any(x.get('status')!='complete' for x in study['results']):
            raise ValueError('incomplete study: '+str(path))
        environment=study['environment']
        common={k:environment[k] for k in ['hostname','platform','cpu','binary_sha256','library_sha256']}
        if reference is not None and reference!=common:
            raise ValueError('studies must share host, reader executable and libraries')
        reference=common
        for row in study['results']:
            if row['id'] in ids:raise ValueError('duplicate model: '+row['id'])
            ids.add(row['id'])
            if not all(x['bitwise_reference_equal'] for x in row['verification']):
                raise ValueError('unverified numerical model')
            result['results'].append(row)
        resolved=path.resolve()
        label=str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(resolved)
        result['study_reports'].append({'path':label,'sha256':hashlib.sha256(data).hexdigest(),
            'gzip_version':environment['gzip_version'],
            'compression_threads':study['gzip'].get('compression_threads',1),
            'level_for_generated_files':study['gzip']['level_for_generated_files']})
    result['environment']=reference
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as out:out.write(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(args.output)


if __name__=='__main__':main()
