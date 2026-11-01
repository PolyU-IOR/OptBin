#!/usr/bin/env python3
"""Time public gurobipy.read; optionally verify every continuous LP array.

Environment startup and public-API matrix/attribute extraction are outside the
read timer and its process RSS snapshot. No private Gurobi pointers are used.
"""
import argparse
from bisect import bisect_right
import json
import os
from pathlib import Path
import resource
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def nonzero_bounded_rows(pointers,limit):
    """Cover CSR rows in order, packing zero/short rows into each query."""
    if limit<1:raise ValueError('nonzero query limit must be positive')
    rows=len(pointers)-1;first=0
    while first<rows:
        last=max(first+1,min(rows,bisect_right(pointers,int(pointers[first])+limit)-1))
        yield first,last
        first=last


def compare_lp(source,reference,matrix_block_rows=0):
    import gurobipy as gp
    import numpy as np
    from scipy.sparse import csc_matrix
    sys.path.insert(0,str(ROOT/'python'))
    from optbin import Model
    checks=[];chunk=1024*1024
    def scalar(field,actual,expected):
        passed=actual==expected;checks.append({'field':field,'pass':bool(passed)})
        if not passed:raise ValueError('Gurobi reference mismatch: '+field)
    def array(field,actual,expected,normalize_bounds=False):
        scalar(field+' count',len(actual),len(expected))
        for start in range(0,len(actual),chunk):
            a=np.asarray(actual[start:start+chunk]);b=expected[start:start+chunk]
            if normalize_bounds:
                a=a.copy();a[a==gp.GRB.INFINITY]=np.inf;a[a==-gp.GRB.INFINITY]=-np.inf
            if not np.array_equal(a,b):
                mismatch=int(np.flatnonzero(a!=b)[0])+start
                raise ValueError(f'Gurobi reference mismatch: {field} at {mismatch}')
        checks.append({'field':field,'elements':len(actual),'pass':True})
    for name in ['IsMIP','NumIntVars','NumQNZs','NumQConstrs','NumGenConstrs','NumSOS','NumPWLObjVars']:
        if source.getAttr(name):raise ValueError('Large public-API verification is continuous LP-only: '+name)
    def source_matrices():
        # Gurobi 13.0.3 getA() queries legacy NumNZs and fails above 2^31.
        # Public row-only copies preserve variable order while each query stays
        # below that API limit. The original model is never modified.
        if not matrix_block_rows and source.DNumNZs<=1_500_000_000:
            yield 0,source.NumConstrs,source.getA()
            return
        rows=source.NumConstrs
        if not rows:
            yield 0,0,source.getA()
            return
        if matrix_block_rows:
            pending=[(a,min(rows,a+matrix_block_rows)) for a in range(0,rows,matrix_block_rows)]
        else:
            pending=list(nonzero_bounded_rows(reference_csr.indptr,1_500_000_000))
        while pending:
            first,last=pending.pop(0)
            print(f'extracting public Gurobi row copy [{first},{last})',file=sys.stderr,flush=True)
            part=source.copy()
            try:
                constraints=part.getConstrs()
                if first:part.remove(constraints[:first])
                if last<rows:part.remove(constraints[last:])
                part.update();del constraints
                if part.DNumNZs>1_500_000_000:
                    if last-first<2:raise ValueError('one row exceeds the public getA query limit')
                    middle=(first+last)//2
                    pending[:0]=[(first,middle),(middle,last)]
                    continue
                yield first,last,part.getA()
            finally:part.dispose()
    with Model(reference) as expected:
        scalar('profile',expected.info['profile'],1)
        for name,id in [('quadratic objective',11),('quadratic constraints',12)]:
            count=expected.sections.get(id,{}).get('count',0)
            if count:raise ValueError('Large public-API verification is continuous LP-only: reference '+name)
            checks.append({'field':'reference '+name+' count','pass':True,'elements':count})
        scalar('rows',source.NumConstrs,expected.info['rows']);scalar('cols',source.NumVars,expected.info['cols'])
        scalar('sense',source.ModelSense,expected.info['sense']);scalar('objective offset',source.ObjCon,expected.info['objective_offset'])
        ptr=expected.numpy(1,copy=False);index=expected.numpy(2,copy=False);values=expected.numpy(3,copy=False)
        has_zeros=any(np.any(values[start:start+chunk]==0) for start in range(0,len(values),chunk))
        removed=0
        if has_zeros:
            canonical=csc_matrix((values.copy(),index.copy(),ptr.copy()),shape=(source.NumConstrs,source.NumVars))
            canonical.eliminate_zeros();removed=len(values)-canonical.nnz
            ptr,index,values=canonical.indptr,canonical.indices,canonical.data
        partitioned=bool(matrix_block_rows or source.DNumNZs>1_500_000_000)
        reference_csr=csc_matrix((values,index,ptr),shape=(source.NumConstrs,source.NumVars)).tocsr() if partitioned else None
        partitions=[];covered=0;matrix_nnz=0
        for first,last,raw in source_matrices():
            scalar('row block coverage',first,covered);covered=last
            matrix=raw.tocsc();matrix.sum_duplicates();matrix.eliminate_zeros();matrix.sort_indices();del raw
            actual_expected=reference_csr[first:last].tocsc() if partitioned else None
            ep,ei,ex=(actual_expected.indptr,actual_expected.indices,actual_expected.data) if partitioned else (ptr,index,values)
            label=f'A rows [{first},{last})'
            array(label+' column pointers',matrix.indptr,ep)
            array(label+' row indices',matrix.indices,ei);array(label+' coefficients',matrix.data,ex)
            matrix_nnz+=matrix.nnz;partitions.append([first,last])
            del matrix,actual_expected
        scalar('complete row coverage',covered,source.NumConstrs)
        scalar('complete nonzero coverage',matrix_nnz,len(values))
        del reference_csr
        for name,id in [('Obj',4),('LB',7),('UB',8)]:
            print('extracting Gurobi',name,file=sys.stderr,flush=True)
            actual=source.getAttr(name)
            array(name,actual,expected.numpy(id,copy=False),name in ['LB','UB']);del actual
        rhs=np.asarray(source.getAttr('RHS'),dtype=np.float64)
        senses=np.asarray(source.getAttr('Sense'),dtype='S1')
        lower=expected.numpy(5,copy=False);upper=expected.numpy(6,copy=False)
        scalar('RHS count',len(rhs),len(lower));scalar('Sense count',len(senses),len(lower))
        for start in range(0,len(rhs),chunk):
            s=senses[start:start+chunk];v=rhs[start:start+chunk]
            if not np.all((s==b'=')|(s==b'<')|(s==b'>')):raise ValueError('unknown Gurobi row sense')
            lo=np.where((s==b'=')|(s==b'>'),v,-np.inf)
            hi=np.where((s==b'=')|(s==b'<'),v,np.inf)
            array('row lower',lo,lower[start:start+chunk]);array('row upper',hi,upper[start:start+chunk])
    return {'exact_reference_equal':True,'checks':checks,'ignored_explicit_sparse_zeros':removed,
        'matrix_reference_method':'public row-submodel getA' if partitioned else 'public getA',
        'matrix_row_partitions':partitions,'matrix_nnz_checked':matrix_nnz,
        'compared_elements':sum(c.get('elements',0) for c in checks),
        'normalization':'Gurobi infinity sentinels, zero signs and explicit sparse zeros; no tolerance',
        'numpy_version':np.__version__,'scipy_version':__import__('scipy').__version__}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('--reference',type=Path)
    p.add_argument('--matrix-block-rows',type=int,default=0,help='force public row-copy verification blocks; 0 selects automatically')
    args=p.parse_args()
    if args.matrix_block_rows<0:p.error('matrix block rows must be nonnegative')
    import gurobipy as gp
    start=time.perf_counter()
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0);env.setParam('Threads',1);env.start()
        environment_seconds=time.perf_counter()-start
        before=resource.getrusage(resource.RUSAGE_SELF)
        start=time.perf_counter();model=gp.read(str(args.source),env=env);seconds=time.perf_counter()-start
        after=resource.getrusage(resource.RUSAGE_SELF)
        try:
            result={'reader':'gurobipy.read','gurobi_version':'.'.join(map(str,gp.gurobi.version())),
                'seconds':seconds,'environment_seconds':environment_seconds,'max_rss_bytes':after.ru_maxrss*1024,
                'user_seconds':after.ru_utime-before.ru_utime,'system_seconds':after.ru_stime-before.ru_stime,
                'rows':model.NumConstrs,'cols':model.NumVars,'nnz':int(model.DNumNZs),
                'q_nnz':model.NumQNZs,'q_constraints':model.NumQConstrs,'gurobi_threads_parameter':1,
                'is_mip':model.IsMIP,'is_multiobjective':model.IsMultiObj,'objectives':model.NumObj,
                'integer_variables':model.NumIntVars,'general_constraints':model.NumGenConstrs,
                'sos_constraints':model.NumSOS,'pwl_objective_variables':model.NumPWLObjVars,
                'exact_reference_equal':None,'reference_compare_seconds':0}
            print(f'gurobipy.read complete: {seconds:.3f}s; {result["rows"]} rows, {result["cols"]} columns',file=sys.stderr,flush=True)
            if args.reference:
                start=time.perf_counter();result.update(compare_lp(model,args.reference,args.matrix_block_rows))
                result['reference_compare_seconds']=time.perf_counter()-start
            result['gurobi_fingerprint']=model.Fingerprint
            print(json.dumps(result,allow_nan=False))
        finally:model.dispose()


if __name__=='__main__':main()
