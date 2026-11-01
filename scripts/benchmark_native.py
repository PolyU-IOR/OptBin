#!/usr/bin/env python3
"""Matched native C CPU loading, exact comparisons, process RSS, and paired trials.

Encoded HDF5 and OptBin share their manifest parser, block traversal, numeric
decoder, per-block CRCs, allocations, output layout and full model validator.
Runs are independent processes. Timing and RSS stop before SHA256/comparisons.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def execute(command):
    start=time.perf_counter()
    # A small exec launcher avoids inheriting Python's resident high-water mark
    # in child getrusage measurements on Linux. Its own timing output is unused.
    wrapped=['/usr/bin/time','-o',os.devnull,*list(map(str,command))]
    p=subprocess.run(wrapped,capture_output=True,text=True)
    if p.returncode:raise RuntimeError(str(command)+'\n'+p.stderr[-6000:])
    result=json.loads(p.stdout);result['process_wall_seconds']=time.perf_counter()-start
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',action='append',help='ID=FILE.optb or ID=FILE.bqp; repeatable')
    p.add_argument('--cases',nargs='+',default=['AUG2D','QPLIB_2676','nql30','nb_L2','LogExpCR-n20-m400','infeas_clean_10_10_1'])
    p.add_argument('--build',type=Path,default=ROOT/'build-native')
    p.add_argument('--work',type=Path,default=ROOT/'benchmarks/runs/native-cpu')
    p.add_argument('--output',type=Path,default=ROOT/'benchmarks/native-cpu.json')
    p.add_argument('--repeats',type=int,default=5)
    p.add_argument('--layouts',nargs='+',choices=['encoded','raw','gzip'],default=['encoded','raw','gzip'])
    p.add_argument('--reference',action='store_true',help='explicit memcmp with the source in addition to SHA256 fingerprints')
    args=p.parse_args()
    if args.repeats<1:p.error('repeats must be positive')
    inputs=[(x.split('=',1)[0],Path(x.split('=',1)[1]).resolve()) for x in args.input] if args.input else [
        (id,ROOT/'data/converted'/(id+'.optb')) for id in args.cases]
    args.output.parent.mkdir(parents=True,exist_ok=True);args.work.mkdir(parents=True,exist_ok=True)
    cpu=next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),'unknown')
    binary=args.build/'optb_native_bench'
    report={'scope':'Native C CPU read, allocation, decoding, block CRCs and full model validation; no solver setup or GPU',
        'cache':'Warm file cache: exports/verification plus one untimed warmup per format; independent-process trials in shuffled rounds; no global cache flush',
        'measurement':'CLOCK_MONOTONIC and getrusage snapshot before SHA256 and optional memcmp; RSS is Linux process peak resident bytes',
        'limitations':['Single host and single-threaded readers; no direct-I/O or controlled cold-cache measurements',
            'Typed HDF5 uses the original block partitions/checksums with contiguous or shuffle/deflate level 1 arrays',
            'HDF5 encoded byte reads use a bounded 1 MiB cache; OptBin uses stdio buffering',
            'BQP migration preserves existing encoded payloads; it does not measure MPS-to-v2 conversion',
            'Legacy BQP timing through the compatibility API includes v1 validation followed by v2 validation'],
        'environment':{'hostname':platform.node(),'platform':platform.platform(),'cpu':cpu,
            'logical_cpus':os.cpu_count(),'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'compiler':subprocess.run(['cc','--version'],capture_output=True,text=True).stdout.splitlines()[0],
            'launcher':'GNU time; native timer/RSS snapshot exclude hashing and comparisons',
            'binary_sha256':digest(binary),
            'library_sha256':{x:digest(args.build/x) for x in ['liboptbin.so','liboptbin_hdf5.so']},
            'script_sha256':digest(Path(__file__))},'results':[]}
    def save():args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    save();rng=random.Random(20261027)
    for id,original in inputs:
        if not id or Path(id).name!=id:raise ValueError('model ID must be a single path component')
        folder=args.work/id;folder.mkdir(parents=True,exist_ok=True)
        row={'id':id,'original':str(original),'original_bytes':original.stat().st_size,'preparation':{},'verification':[]}
        source=original
        if original.suffix=='.bqp':
            source=folder/(id+'.optb')
            if not source.exists():row['preparation']['bqp_repack']=execute([args.build/'bqp2optb',original,source])
            row['migration']='Encoded v1 blocks regrouped into v2 sections, no numerical re-encoding'
        formats=[('optbin','optbin',source)]
        for layout in args.layouts:
            target=folder/(layout+'.h5')
            if not target.exists():row['preparation'][layout]=execute([args.build/'optb_h5_export',source,target,layout])
            formats.append(('hdf5-'+layout,'hdf5',target))
            print(id,'prepared',layout,flush=True)
        if original.suffix=='.bqp':formats.append(('legacy-bqp','bqp',original))
        reference_kind='bqp' if original.suffix=='.bqp' else 'optbin'
        fingerprint=None
        for label,kind,path in formats:
            cmd=[binary,kind,path]
            if args.reference:cmd+=[reference_kind,original]
            result=execute(cmd)
            if fingerprint is None:fingerprint=result['numerical_sha256']
            if fingerprint!=result['numerical_sha256']:raise AssertionError('model fingerprint mismatch: '+id+' '+label)
            row['verification'].append({'format':label,**result})
            print(id,'verified',label,flush=True)
        measurements={name:[] for name,_,_ in formats};order=[]
        for repeat in range(args.repeats):
            batch=list(formats);rng.shuffle(batch);order.append([x[0] for x in batch])
            for label,kind,path in batch:
                result=execute([binary,kind,path])
                if result['numerical_sha256']!=fingerprint:raise AssertionError('model changed during measurement')
                measurements[label].append(result)
                print(id,'trial',repeat+1,label,round(result['seconds'],4),'s',flush=True)
        row['measurement_order']=order;row['numerical_sha256']=fingerprint
        row['formats']=[{'format':name,'path':str(path),'bytes':path.stat().st_size,'runs':measurements[name],
            'median_seconds':statistics.median(x['seconds'] for x in measurements[name]),
            'median_peak_rss_bytes':statistics.median(x['max_rss_bytes'] for x in measurements[name])}
            for name,_,path in formats]
        report['results'].append(row);save()
        print(id,'complete',flush=True)


if __name__=='__main__':main()
