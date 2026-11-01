#!/usr/bin/env python3
"""Compare complete converted models with independent source readers.

CBF: original CBLIB CBFdata parser (unmodified, with xrange=range at import).
MPS: Gurobi's source reader and complete polynomial coefficients by default.
HiGHS remains selectable for LP/QP; QCQP always uses Gurobi.
This script does not use an OptBin converter to generate the expected numbers.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from optbin import Model

REFERENCE_COMMIT='e9c08e93d30f4c98e96e2dacc3faf9b045383963'
REFERENCE_URL=f'https://raw.githubusercontent.com/HFriberg/cblib-base/{REFERENCE_COMMIT}/cblib/scripts/data/CBFdata.py'
REFERENCE_SHA256='8b32a672b08416539f88070013c36307dbecb137d8aeb8606699b35baa2e3108'


def cbf_reader():
    path=ROOT/'data/reference/CBFdata.py'
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(urllib.request.urlopen(REFERENCE_URL,timeout=40).read())
    if hashlib.sha256(path.read_bytes()).hexdigest()!=REFERENCE_SHA256:
        raise ValueError('independent CBF reader hash mismatch')
    spec=importlib.util.spec_from_file_location('independent_cbf',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.xrange=range  # Python 2 compatibility binding; original source unmodified.
    return module.CBFdata


def compare(model,expected,metadata):
    import numpy as np
    checks=[]
    for key,value in metadata.items():
        checks.append({'field':key,'pass':model.info[key]==value,'expected':value,'actual':model.info[key]})
    for id,values in sorted(expected.items()):
        actual=model.numpy(id);expected_array=np.asarray(values)
        if actual.dtype.kind=='f':expected_array=expected_array.astype('<f8')
        equal=actual.shape==expected_array.shape and np.array_equal(actual,expected_array)
        bitwise=bool(equal and actual.astype(expected_array.dtype).tobytes()==expected_array.tobytes())
        # Compare numeric integer values before casting, so an overflowing narrow
        # representation cannot be hidden by converting the reference to U32.
        if actual.dtype.kind!='f':bitwise=bool(equal)
        entry={'field':str(id),'elements':int(expected_array.size),'pass':bitwise}
        if not bitwise:
            entry['actual_count']=int(actual.size)
            if actual.shape==expected_array.shape:
                indices=np.flatnonzero(actual!=expected_array)
                if not len(indices):indices=np.flatnonzero(actual.view('u8')!=expected_array.view('u8'))
                entry['mismatches']=int(len(indices));entry['first']=int(indices[0]) if len(indices) else None
                if len(indices):entry['actual']=float(actual[indices[0]]);entry['expected']=float(expected_array[indices[0]])
        checks.append(entry)
    return {'pass':all(c['pass'] for c in checks),'checks':checks,
            'compared_elements':sum(c.get('elements',0) for c in checks)}


def csc(n,triples):
    triples=sorted(triples,key=lambda t:(t[1],t[0]))
    pointers=[0]*(n+1)
    for _,col,_ in triples:pointers[col+1]+=1
    for j in range(n):pointers[j+1]+=pointers[j]
    return pointers,[r for r,_,_ in triples],[v for _,_,v in triples]


def expected_cbf(path):
    ref=next(cbf_reader()(str(path)).iterator())
    if ref.intvarnum:raise ValueError('integer CBF is outside the declared continuous scope')
    expected={}
    expected[1],expected[2],expected[3]=csc(ref.varnum,list(zip(ref.asubi,ref.asubj,ref.aval)))
    expected[4]=[0.]*ref.varnum
    for j,v in zip(ref.objasubj,ref.objaval):expected[4][j]=v
    expected[18]=[0.]*ref.mapnum
    for i,v in zip(ref.bsubi,ref.bval):expected[18][i]=v
    kinds={'F':0,'L+':1,'L-':2,'L=':3,'Q':4,'QR':5,'EXP':6,'EXP*':7}
    expected[19]=[kinds[k] for k in ref.varstackdomain];expected[20]=ref.varstackdim
    expected[21]=[0]*(ref.varstacknum+1);expected[22]=[]
    expected[23]=[kinds[k] for k in ref.mapstackdomain];expected[24]=ref.mapstackdim
    expected[25]=[0]*(ref.mapstacknum+1);expected[26]=[]
    expected[27]=ref.psdvardim;expected[28]=ref.psdmapdim
    groups=[(29,[ref.objfsubj],ref.objfsubk,ref.objfsubl,ref.objfval),
            (33,[ref.fsubi,ref.fsubj],ref.fsubk,ref.fsubl,ref.fval),
            (38,[ref.hsubi,ref.hsubj],ref.hsubk,ref.hsubl,ref.hval),
            (43,[ref.dsubi],ref.dsubk,ref.dsubl,ref.dval)]
    for first,prefix,rows,cols,values in groups:
        entries=[]
        for k,value in enumerate(values):
            entries.append(tuple(p[k] for p in prefix)+(min(rows[k],cols[k]),max(rows[k],cols[k]),value))
        entries.sort(key=lambda t:t[:-3]+(t[-2],t[-3]))
        for j in range(len(prefix)+3):expected[first+j]=[e[j] for e in entries]
    meta={'profile':2,'rows':ref.mapnum,'cols':ref.varnum,'sense':1 if ref.obj=='MIN' else -1,'objective_offset':ref.objbval}
    return expected,meta,ref


def expected_highs(path):
    import highspy
    h=highspy.Highs();h.setOptionValue('output_flag',False)
    h.setOptionValue('small_matrix_value',1e-12)
    status=h.readModel(str(path))
    if status!=highspy.HighsStatus.kOk:raise ValueError('HiGHS did not read source successfully')
    source=h.getModel();lp=source.lp_;a=lp.a_matrix_;q=source.hessian_
    if any(v!=highspy.HighsVarType.kContinuous for v in lp.integrality_):raise ValueError('integer MPS outside scope')
    # pybind vector properties return whole Python lists. Materialize each once;
    # repeated property access inside entry loops otherwise becomes quadratic.
    astart,aindex,avalue=a.start_,a.index_,a.value_
    qstart,qindex,qvalue=q.start_,q.index_,q.value_
    entries=[]
    for col in range(lp.num_col_):
        for k in range(astart[col],astart[col+1]):entries.append((aindex[k],col,avalue[k]))
    ap,ai,ax=csc(lp.num_col_,entries)
    expected={1:ap,2:ai,3:ax,4:lp.col_cost_,5:lp.row_lower_,6:lp.row_upper_,7:lp.col_lower_,8:lp.col_upper_}
    if len(qvalue):
        triples=[]
        for col in range(q.dim_):
            for k in range(qstart[col],qstart[col+1]):
                i=qindex[k]
                # HiGHS supplies explicit zero diagonals for variables that had
                # no Hessian entry in MPS; they are not source nonzero entries.
                if qvalue[k]!=0:triples.append((min(i,col),max(i,col),qvalue[k]))
        expected[9],expected[10],expected[11]=csc(lp.num_col_,triples)
    else:expected.update({9:[],10:[],11:[]})
    meta={'profile':1,'rows':lp.num_row_,'cols':lp.num_col_,
          'sense':1 if lp.sense_==highspy.ObjSense.kMinimize else -1,'objective_offset':lp.offset_}
    return expected,meta,h


def mps_row_names(path):
    open_file=gzip.open if str(path).endswith('.gz') else open
    rows=[];section=None
    with open_file(path,'rt') as f:
        for line in f:
            if not line.strip() or line.startswith('*'):continue
            p=line.split()
            if p[0]=='ROWS' and len(p)==1:section='ROWS';continue
            if p[0]=='COLUMNS' and len(p)==1:break
            if section=='ROWS' and p[0]!='N':rows.append(p[1])
    return rows


def compare_gurobi_qc(model,path):
    import gurobipy as gp
    import numpy as np
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0);env.start()
        with gp.read(str(path),env=env) as ref:
            rows=mps_row_names(path);row_id={name:i for i,name in enumerate(rows)};n=ref.NumVars
            features={name:ref.getAttr(name) for name in ['IsMIP','NumIntVars','NumGenConstrs','NumSOS','NumPWLObjVars']}
            if any(features.values()):raise ValueError('source outside continuous single-objective LP/QP/QCQP scope: '+str(features))
            triples=[];lower=[-math_inf]*len(rows);upper=[math_inf]*len(rows)
            def bound(name,sense,rhs):
                i=row_id[name]
                if sense in ('=','>'):lower[i]=rhs
                if sense in ('=','<'):upper[i]=rhs
            A=ref.getA().tocoo();linear=ref.getConstrs()
            triples += [(row_id[linear[i].ConstrName],j,v) for i,j,v in zip(A.row,A.col,A.data)]
            for c in linear:bound(c.ConstrName,c.Sense,c.RHS)
            quad_expected={}
            for constraint in ref.getQConstrs():
                row=row_id[constraint.QCName];expression=ref.getQCRow(constraint);lin=expression.getLinExpr()
                triples += [(row,lin.getVar(k).index,lin.getCoeff(k)) for k in range(lin.size())]
                bound(constraint.QCName,constraint.QCSense,constraint.QCRHS)
                terms={}
                for k in range(expression.size()):
                    i,j=expression.getVar1(k).index,expression.getVar2(k).index
                    terms[min(i,j),max(i,j)]=expression.getCoeff(k)
                quad_expected[row]=terms
            ap,ai,ax=csc(n,triples)
            expected={1:ap,2:ai,3:ax,4:[v.Obj for v in ref.getVars()],5:lower,6:upper,
                      7:[v.LB for v in ref.getVars()],8:[v.UB for v in ref.getVars()]}
            result=compare(model,expected,{'profile':1,'rows':len(rows),'cols':n,
                'sense':1 if ref.ModelSense==1 else -1,'objective_offset':ref.ObjCon})
            expression=ref.getObjective();q_expected={}
            if isinstance(expression,gp.QuadExpr):
                for k in range(expression.size()):
                    i,j=expression.getVar1(k).index,expression.getVar2(k).index
                    q_expected[min(i,j),max(i,j)]=expression.getCoeff(k)
            qp=model.numpy(9).astype('int64');qi=model.numpy(10);qx=model.numpy(11);q_actual={}
            if len(qx):
                for col in range(n):
                    for k in range(qp[col],qp[col+1]):
                        i=int(qi[k]);q_actual[i,col]=float(qx[k])*(.5 if i==col else 1.)
            objective_pass=q_actual.keys()==q_expected.keys() and all(
                np.float64(q_actual[key]).tobytes()==np.float64(q_expected[key]).tobytes() for key in q_actual)
            result['checks'].append({'field':'objective quadratic polynomial','elements':len(q_expected),'pass':objective_pass})
            result['compared_elements']+=len(q_expected)
            actual_rows=model.numpy(12).astype('int64').tolist()
            result['checks'].append({'field':'quadratic row IDs','pass':actual_rows==sorted(quad_expected)})
            ptr=model.numpy(13).astype('int64');ii=model.numpy(14);jj=model.numpy(15);vv=model.numpy(16);scale=model.numpy(17)
            for k,row in enumerate(actual_rows):
                actual={(int(ii[t]),int(jj[t])):float(scale[k]*vv[t])*(1. if ii[t]==jj[t] else 2.) for t in range(ptr[k],ptr[k+1])}
                expect=quad_expected[row]
                passed=actual.keys()==expect.keys() and all(np.float64(actual[key]).tobytes()==np.float64(expect[key]).tobytes() for key in actual)
                result['checks'].append({'field':f'quadratic polynomial row {row}','elements':len(expect),'pass':passed})
                result['compared_elements']+=len(expect)
            result['pass']=all(c['pass'] for c in result['checks'])
            result['source_features']=features
            return result


math_inf=float('inf')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reconvert',action='store_true',help='regenerate this script\'s converted cache with the current converter')
    p.add_argument('--mps-reference',choices=['gurobi','highs'],default='gurobi',
                   help='independent LP/QP source reader; QCQP always uses Gurobi (default: gurobi)')
    args=p.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    records=[];outdir=ROOT/'data/converted';outdir.mkdir(parents=True,exist_ok=True)
    for item in json.loads(args.manifest.read_text()):
        result=dict(item);start=time.perf_counter()
        if item.get('integer') or item.get('error'):
            result['status']='excluded';result['reason']='integer model outside declared scope' if item.get('integer') else item['error']
            if item.get('integer'):
                target=outdir/(item['id']+'.rejected.optb')
                source=Path(item['path']);source=source if source.is_absolute() else ROOT/source
                command=[ROOT/'build/cbf2optb',source,target]
                rejected=subprocess.run(list(map(str,command)),capture_output=True,text=True)
                result['rejection_verified']=rejected.returncode!=0 and not target.exists() and 'integrality' in rejected.stderr
                if not result['rejection_verified']:result['status']='error';result['error']='integer rejection not verified'
            records.append(result);continue
        try:
            source=Path(item['path']);source=source if source.is_absolute() else ROOT/source
            result['input_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
            if item.get('sha256') and item['sha256']!=result['input_sha256']:raise ValueError('source hash differs from manifest')
            target=outdir/(item['id']+'.optb')
            if args.reconvert and target.exists():target.unlink()
            if not target.exists():
                command=[ROOT/'build'/(item['format']+'2optb'),source,target]
                if item['format']=='mps':command+=['--mps-dialect',item.get('dialect','gurobi')]
                converted=subprocess.run(list(map(str,command)),text=True,capture_output=True)
                if converted.returncode:raise ValueError(converted.stderr.strip())
            with Model(target) as model:
                if item['format']=='cbf':
                    expected,meta,_=expected_cbf(source);result.update(compare(model,expected,meta));result['reference']='CBLIB CBFdata '+REFERENCE_COMMIT
                elif args.mps_reference=='gurobi' or len(model.numpy(12)):
                    result.update(compare_gurobi_qc(model,source));result['reference']='Gurobi source MPS reader; complete LP/QP/QCQP polynomial comparison'
                else:
                    expected,meta,_=expected_highs(source);result.update(compare(model,expected,meta));result['reference']='HiGHS source MPS reader'
                result['converter_provenance']=model.raw(48).decode()
                result['model_info']=model.info;result['optbin_path']=str(target);result['status']='pass' if result['pass'] else 'mismatch'
                result['optbin_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
        except Exception as e:result['status']='error';result['error']=str(e)
        result['seconds']=time.perf_counter()-start;records.append(result)
        print(item['id'],result['status'],result.get('error',''),flush=True)
        args.output.write_text(json.dumps({'scope':'Full independent source-model comparisons; no optimization',
            'reference_reader':{'url':REFERENCE_URL,'sha256':REFERENCE_SHA256},
            'mps_reference':args.mps_reference,
            'reference_normalizations':['Sort sparse coordinates to the specified canonical order',
                'Compare quadratic expression coefficients with the explicit objective and constraint multipliers']+
                (['Remove zero Hessian diagonals inserted by HiGHS; no nonzero coefficients changed'] if args.mps_reference=='highs' else []),
            'results':records},indent=2)+'\n')
    if any(r['status'] in ('error','mismatch') for r in records):return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
