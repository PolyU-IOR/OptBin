#!/usr/bin/env python3
"""Gurobi's MPS reader versus existing gzip/OptBin representations.

Gurobi read timing ends when the vendor model is loaded. Environment startup,
canonical comparison/extraction, and reference loading are recorded separately.
The binary loaders return validated numerical arrays, not Gurobi solver models.
"""
import argparse
import datetime
import hashlib
import importlib.util
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


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-report',type=Path,required=True)
    p.add_argument('--cases',nargs='+')
    p.add_argument('--gurobi-build',type=Path,default=ROOT/'build-gurobi')
    p.add_argument('--binary-build',type=Path,default=ROOT/'build-gzip')
    p.add_argument('--gurobi-library',type=Path)
    p.add_argument('--gurobi-interface',choices=['python','c'],default='python')
    p.add_argument('--python-validation',action='store_true',help='full small LP/QP/QCQP comparison with gurobipy')
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--work',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.repeats<1:p.error('repeats must be positive')
    spec=importlib.util.find_spec('gurobipy')
    active_library=next(Path(spec.origin).parent.rglob('libgurobi*.so'),None) if spec else None
    if not args.gurobi_library:args.gurobi_library=active_library
    if not args.gurobi_library:p.error('supply --gurobi-library or install gurobipy')
    args.gurobi_library=args.gurobi_library.resolve()
    if args.gurobi_interface=='python' and (not active_library or args.gurobi_library!=active_library.resolve()):
        p.error('Python mode must use the gurobipy library from this interpreter')
    args.gurobi_build=args.gurobi_build.resolve();args.binary_build=args.binary_build.resolve()
    args.output=args.output.resolve();args.work=args.work.resolve()
    if args.output.exists() or args.work.exists():p.error('output and work directory must be new')
    args.work.mkdir(parents=True);args.output.parent.mkdir(parents=True,exist_ok=True)
    baseline_bytes=args.baseline_report.read_bytes();baseline=json.loads(baseline_bytes)
    selected=[m for m in baseline['results'] if m.get('source_format')=='mps']
    if args.cases:
        selected=[m for m in selected if m['id'] in args.cases]
        if {m['id'] for m in selected}!=set(args.cases):p.error('case not found or not MPS')
    if not selected:p.error('no MPS cases')
    gurobi=args.gurobi_build/'optb_gurobi_bench';binary=args.binary_build/'optb_format_bench'
    sources=[Path(__file__),ROOT/'scripts/gurobi_read.py',ROOT/'python/optbin/reader.py']
    if args.gurobi_interface=='c':sources.append(ROOT/'src/gurobi_bench.c')
    if args.python_validation:sources.append(ROOT/'scripts/validate_real.py')
    executables=[binary,gurobi if args.gurobi_interface=='c' else Path(sys.executable).resolve()]
    report={'schema':1,'complete':False,'scope':'Independent Gurobi MPS source reader; practical loader comparison, no solving',
        'timing_contract':{'gurobi':('gurobipy.read' if args.gurobi_interface=='python' else 'GRBreadmodel')+' only, including source gzip decompression and vendor model construction',
            'gurobi_separate':'Environment startup, attribute extraction/comparison and OptBin reference loading excluded from read timer/RSS',
            'binary':'Native complete validated numerical arrays; no Gurobi model construction',
            'gzip':'Outer binary gzip expansion to a seekable temporary included',
            'cache':'Warm files; one verification/warmup per format, then shuffled fresh-process rounds'},
        'limitations':['Different public API endpoints: Gurobi solver model versus validated binary numerical arrays',
            'No optimization, presolve, GPU upload or compressed-file creation in read timings',
            'Peak RSS is sampled before reference extraction/loading; filesystem cache and temporary disk excluded',
            'Large comparison is LP-only; small LP/QP/QCQP may use the separate Python polynomial comparator',
            'Gurobi infinity sentinels, zero signs and explicit sparse zeros are normalized; no numerical tolerance'],
        'environment':{'hostname':platform.node(),'platform':platform.platform(),
            'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'cpu':next(x.split(':',1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name')),
            'python':sys.version,'repeats':args.repeats,'gurobi_threads_parameter':1,
            'gurobi_interface':args.gurobi_interface,
            'gurobi_library':str(args.gurobi_library),'gurobi_library_sha256':sha(args.gurobi_library),
            'executable_sha256':{str(x):sha(x) for x in executables},
            'binary_library_sha256':{name:sha(args.binary_build/name) for name in ['liboptbin.so','liboptbin_hdf5.so']},
            'source_sha256':{str(x.relative_to(ROOT)):sha(x) for x in sources}},
        'baseline_report':{'path':str(args.baseline_report.resolve()),'sha256':hashlib.sha256(baseline_bytes).hexdigest()},
        'results':[]}
    def save():args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    save();rng=random.Random(20261030)
    def execute(command,env,log):
        start=time.monotonic()
        with log.open('w') as err:
            proc=subprocess.Popen(list(map(str,command)),stdout=subprocess.PIPE,stderr=err,text=True,env=env)
            report['running']={'pid':proc.pid,'argv':list(map(str,command)),'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
            save();stdout,_=proc.communicate()
        report.pop('running',None)
        if proc.returncode:
            raise RuntimeError(f'exit {proc.returncode}: {command}; {log.read_text(errors="replace")[-3000:]}')
        result=json.loads(stdout);result['process_wall_seconds']=time.monotonic()-start
        return result
    try:
        for original in selected:
            id=original['id']
            if Path(id).name!=id:raise ValueError('invalid case ID')
            folder=args.work/id;folder.mkdir();scratch=folder/'scratch';scratch.mkdir()
            env=dict(os.environ,TMPDIR=str(scratch),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
            env['OPTBIN_LIBRARY']=str(args.binary_build/'liboptbin.so')
            layouts={x['format']:x for x in original['formats']}
            source=Path(layouts['text-gzip']['path']);reference=Path(layouts['optbin']['path'])
            row={'id':id,'source':str(source),'formats':[],'verification':[],'measurement_order':[],'status':'running'}
            report['results'].append(row);save()
            def command(kind,path,verify):
                if kind!='gurobi':return [binary,kind,path,'optbin',reference]
                if args.gurobi_interface=='python':
                    return [sys.executable,ROOT/'scripts/gurobi_read.py',path]+(['--reference',reference] if verify and not args.python_validation else [])
                return [gurobi,args.gurobi_library,path]+([] if args.python_validation else [reference])
            specifications=[('gurobi-mps-gzip','gurobi',source),('raw-hdf5-gzip','hdf5-gzip',Path(layouts['raw-hdf5-gzip']['path'])),
                ('optbin','optbin',reference),('optbin-gzip','optbin-gzip',Path(layouts['optbin-gzip']['path']))]
            if args.python_validation:
                os.environ['OPTBIN_LIBRARY']=str(args.binary_build/'liboptbin.so')
                sys.path.insert(0,str(ROOT/'python'));sys.path.insert(0,str(ROOT/'scripts'))
                from optbin import Model
                from validate_real import compare_gurobi_qc
                start=time.monotonic()
                with Model(reference) as model:checked=compare_gurobi_qc(model,source)
                row['python_source_validation']={**checked,'seconds':time.monotonic()-start,
                    'scope':'Full numerical LP/QP/QCQP polynomial comparison outside all read timers'}
                if not checked['pass']:raise ValueError('Gurobi source model differs: '+id)
                save()
            expected_fingerprint=None
            for label,kind,path in specifications:
                print(id,'verify',label,flush=True)
                digest=sha(path)
                old=layouts['text-gzip' if kind=='gurobi' else label]
                if digest!=old['sha256'] or path.stat().st_size!=old['bytes']:raise ValueError('stored input differs: '+str(path))
                entry={'format':label,'path':str(path),'bytes':path.stat().st_size,'sha256':digest,'runs':[]}
                row['formats'].append(entry);save()
                result=execute(command(kind,path,True),env,folder/(label+'-verification.log'))
                if kind=='gurobi':
                    if not args.python_validation and not result['exact_reference_equal']:raise ValueError('LP comparison failed')
                    row['gurobi_version']=result['gurobi_version']
                else:
                    if not result['bitwise_reference_equal']:raise ValueError('binary comparison failed')
                    if expected_fingerprint is not None and expected_fingerprint!=result['numerical_sha256']:raise ValueError('binary numerical fingerprint differs')
                    expected_fingerprint=result['numerical_sha256']
                row['verification'].append({'format':label,**result});save()
            row['numerical_sha256']=expected_fingerprint
            for repeat in range(args.repeats):
                batch=list(specifications);rng.shuffle(batch);row['measurement_order'].append([x[0] for x in batch]);save()
                for label,kind,path in batch:
                    print(id,'round',repeat+1,label,flush=True)
                    result=execute(command(kind,path,False),env,folder/(label+f'-{repeat+1}.log'))
                    if kind=='gurobi':
                        if (result['rows'],result['cols'])!=(row['verification'][0]['rows'],row['verification'][0]['cols']):raise ValueError('Gurobi dimensions changed')
                        verified=row['verification'][0]
                        if args.gurobi_interface=='python':
                            if result['gurobi_fingerprint']!=verified['gurobi_fingerprint']:raise ValueError('Gurobi model fingerprint changed')
                        elif not args.python_validation and not result['exact_reference_equal']:raise ValueError('LP comparison failed')
                    elif not result['bitwise_reference_equal'] or result['numerical_sha256']!=expected_fingerprint:
                        raise ValueError('binary numerical comparison failed')
                    entry=next(x for x in row['formats'] if x['format']==label);entry['runs'].append(result)
                    entry['median_seconds']=statistics.median(x['seconds'] for x in entry['runs'])
                    entry['median_peak_rss_bytes']=statistics.median(x['max_rss_bytes'] for x in entry['runs']);save()
            row['status']='complete';save();print(id,'complete',flush=True)
        report['complete']=True;save()
    except BaseException as error:
        report['error']=str(error);save();raise
    print(args.output)


if __name__=='__main__':main()
