#!/usr/bin/env python3
"""Repeat real-model storage baselines and lossless codec ablations.

Sources come from the declared manifest. Timings cover conversion and warm CPU
array loading separately; no source-reader or solver speedup is inferred.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from optbin import Model


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=ROOT/'benchmarks/real-cases.json')
    p.add_argument('--cases',nargs='+',default=['AUG2D','QPLIB_2676','nql30','nb_L2',
                                              'LogExpCR-n20-m400','infeas_clean_10_10_1'])
    p.add_argument('--repeats',type=int,default=5)
    p.add_argument('--work',type=Path,default=ROOT/'benchmarks/runs/real-storage')
    p.add_argument('--output',type=Path,default=ROOT/'benchmarks/real-storage.json')
    args=p.parse_args()
    if args.repeats<1:p.error('repeats must be positive')
    manifest={x['id']:x for x in json.loads(args.manifest.read_text())}
    args.work.mkdir(parents=True,exist_ok=True);args.output.parent.mkdir(parents=True,exist_ok=True)
    cpu=next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
              if line.startswith('model name')),platform.processor())
    report={'scope':'Small/medium real-model CPU arrays; not large-scale or GPU performance',
        'timing_conditions':'Warm cache; one warmup; shuffled six-format rounds. Ablations sequential.',
        'limitations':['Python raw/HDF5 frontends and native C loading have different overheads',
            'Same-codec HDF5 uses Python orchestration around the identical C block decoder',
            'No separate RSS, disk-cold, GPU, layout-conversion or solver-setup measurement',
            'Conversion includes parsing/encoding/writing; it is not source-reader-only timing',
            'Stored sizes count metadata and checksums; raw baselines include a JSON manifest'],
        'environment':{'platform':platform.platform(),'cpu':cpu,'python':platform.python_version(),
                       'threads':{k:os.environ.get(k) for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS']}},
        'binaries':{name:sha256(ROOT/'build'/name) for name in ['mps2optb','cbf2optb','liboptbin.so']},
        'results':[]}
    for id in args.cases:
        item=manifest[id]
        if item.get('integer'):raise ValueError('choose continuous models')
        source=Path(item['path']);source=source if source.is_absolute() else ROOT/source
        folder=args.work/id;folder.mkdir(parents=True,exist_ok=True)
        baseline=None;variants=[]
        for mode in ['all','raw','values','indices']:
            for zstd in [0,1]:
                target=folder/f'{mode}-zstd{zstd}.optb'
                # Only replace this harness's own outputs inside its work folder.
                if target.exists():target.unlink()
                command=[ROOT/'build'/(item['format']+'2optb'),source,target,
                         '--encoding',mode,'--zstd',str(zstd)]
                if item['format']=='mps':command+=['--mps-dialect',item.get('dialect','gurobi')]
                start=time.perf_counter()
                result=subprocess.run(list(map(str,command)),capture_output=True,text=True,check=True)
                converted=json.loads(result.stdout);conversion_wall=time.perf_counter()-start
                with Model(target) as model:
                    arrays={i:model.raw(i) for i in model.sections};info=model.info
                if baseline is None:baseline=arrays
                if baseline!=arrays:raise AssertionError(f'ablation changes decoded bytes: {id}, {mode}, {zstd}')
                times=[]
                for run in range(args.repeats+1):
                    start=time.perf_counter()
                    owner=Model(target);elapsed=time.perf_counter()-start;owner.close()
                    if run:times.append(elapsed)
                variants.append({'encoding':mode,'zstd_level':zstd,'bytes':target.stat().st_size,
                    'conversion':converted,'conversion_wall_seconds':conversion_wall,
                    'load_validate_seconds':times,'median_seconds':statistics.median(times),
                    'bitwise_equal':True,'sha256':sha256(target)})
        directory=folder/'baselines'
        subprocess.run([sys.executable,str(ROOT/'scripts/benchmark_storage.py'),str(folder/'all-zstd1.optb'),
            '--output',str(directory),'--repeats',str(args.repeats)],capture_output=True,text=True,check=True)
        report['results'].append({'id':id,'source':str(source),'source_bytes':source.stat().st_size,
            'source_sha256':sha256(source),'model_info':info,'decoded_bytes':sum(map(len,baseline.values())),
            'ablations':variants,'baselines':json.loads((directory/'report.json').read_text())})
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(id,'8 codec variants and 6 storage paths matched bitwise',flush=True)


if __name__=='__main__':main()
