#!/usr/bin/env python3
"""Paired source-reader vs OptBin solver checks, with bounded child processes."""
import argparse
import datetime
import hashlib
import importlib.metadata
import json
import math
import os
import platform
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from optbin import Model
from validate_real import expected_cbf


def clean(value):
    import numpy as np
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [clean(v) for v in value]
    if isinstance(value,np.generic):value=value.item()
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def worker(item,engine):
    result={'engine':engine};start=time.perf_counter();source=Path(item['path'])
    source=source if source.is_absolute() else ROOT/source
    target=ROOT/'data/converted'/(item['id']+'.optb')
    if item['format']=='cbf':
        _,_,ref=expected_cbf(source)
        from reference_conic import reference_solve,evaluate
        if engine=='source-cvxpy':result.update(reference_solve(ref))
        else:
            from optbin.solvers import solve
            with Model(target) as model:
                options={'max_iters':30000,'time_limit_secs':20.,'eps_abs':1e-7,'eps_rel':1e-7} if engine=='scs' else {'time_limit':20.}
                native=solve(model,engine,**options)
                result.update({k:native[k] for k in ('status','objective','details')})
                if native['x'] is not None:result['original_model']=evaluate(ref,native['x'])
                if native.get('certificate') is not None:result['certificate_returned']=True
    else:
        if engine=='source-gurobi':
            import gurobipy as gp
            with gp.Env(empty=True) as env:
                env.setParam('OutputFlag',0);env.start()
                # LP-origin QPLIB cases are solved from the original public LP.
                original=Path(item.get('original_path',source));original=original if original.is_absolute() else ROOT/original
                with gp.read(str(original),env=env) as g:
                    g.Params.Threads=1;g.Params.TimeLimit=20.;g.Params.NonConvex=2
                    g.Params.FeasibilityTol=1e-8;g.Params.OptimalityTol=1e-8
                    g.optimize();result['status']=int(g.Status)
                    result['objective']=float(g.ObjVal) if g.SolCount else None
                    result['runtime']=g.Runtime
        else:
            from optbin.solvers import solve
            with Model(target) as model:
                params=({'TimeLimit':20.,'NonConvex':2,'FeasibilityTol':1e-8,'OptimalityTol':1e-8}
                        if engine=='gurobi' else {'time_limit':10.})
                native=solve(model,engine,**params)
                result.update({k:v for k,v in native.items() if k!='x'})
    result['wall_seconds']=time.perf_counter()-start
    return clean(result)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=ROOT/'benchmarks/real-cases.json')
    p.add_argument('--output',type=Path,default=ROOT/'benchmarks/real-solvers.json')
    p.add_argument('--worker',choices=['source-cvxpy','source-gurobi','scs','clarabel','gurobi','scip'])
    p.add_argument('--id');args=p.parse_args()
    items=json.loads(args.manifest.read_text())
    if args.worker:
        item=next(i for i in items if i['id']==args.id)
        try:record=worker(item,args.worker)
        except Exception as e:record={'engine':args.worker,'error':str(e),'type':type(e).__name__}
        print(json.dumps(record,allow_nan=False));return
    results=[]
    environment={'platform':platform.platform(),'python':platform.python_version(),
        'versions':{name:importlib.metadata.version(name) for name in
                    ['numpy','scipy','cvxpy','scs','clarabel','gurobipy','pyscipopt']},
        'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    for item in items:
        if item.get('integer') or item.get('error'):continue
        engines=['source-cvxpy','clarabel','scs'] if item['format']=='cbf' else ['source-gurobi','gurobi']
        if item['id'] in ('QPLIB_2482','QPLIB_2676'):engines.append('scip')
        source=Path(item.get('original_path',item['path']));source=source if source.is_absolute() else ROOT/source
        target=ROOT/'data/converted'/(item['id']+'.optb')
        row={'id':item['id'],'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
             'optbin_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'results':[]}
        for engine in engines:
            command=[sys.executable,__file__,'--manifest',str(args.manifest),'--worker',engine,'--id',item['id']]
            try:
                out=subprocess.run(command,capture_output=True,text=True,timeout=55,env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
                if not out.returncode:
                    record=json.loads(out.stdout.strip().splitlines()[-1])
                else:record={'engine':engine,'error':out.stderr[-2000:],'exit_code':out.returncode}
            except subprocess.TimeoutExpired:record={'engine':engine,'error':'55-second process limit'}
            row['results'].append(record);print(item['id'],engine,record.get('status'),record.get('objective'),record.get('error',''),flush=True)
        baseline=row['results'][0];objective=baseline.get('objective')
        for record in row['results'][1:]:
            if objective is not None and record.get('objective') is not None:
                record['objective_relative_difference']=abs(record['objective']-objective)/max(1.,abs(objective))
        results.append(row)
        args.output.write_text(json.dumps({'scope':'Independent source modeling vs native adapters; 20s solver limits (SCIP 10s); not a performance benchmark',
            'environment':environment,
            'limitations':['Weakly infeasible SDP stress cases can give inconclusive or inaccurate solver statuses',
                           'Certificates are returned, but full original-coordinate dual-cone certificate validation remains pending'],
            'results':results},indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
