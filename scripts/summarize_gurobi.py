#!/usr/bin/env python3
"""Validate and assemble public Gurobi MPS and MOSEK CBF comparisons."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[1]


def verify_study(study,repeats,python_comparison):
    if not study.get('complete'):raise ValueError('Gurobi study is incomplete')
    if study['environment']['gurobi_interface']!='python':raise ValueError('primary reference must use public gurobipy.read')
    ids=set()
    for row in study['results']:
        if row['id'] in ids or row['status']!='complete':raise ValueError('duplicate or incomplete case')
        ids.add(row['id'])
        if python_comparison:
            checked=row.get('python_source_validation',{})
            if not checked.get('pass') or not all(c['pass'] for c in checked['checks']):raise ValueError('source model comparison failed')
        source=next(v for v in row['verification'] if v['format']=='gurobi-mps-gzip')
        if not python_comparison and not source.get('exact_reference_equal'):raise ValueError('large LP reference comparison failed')
        if not python_comparison and source['matrix_nnz_checked']!=source['nnz']:
            raise ValueError('large LP verification does not cover all nonzeros')
        if not python_comparison:
            checks={c['field']:c for c in source['checks']}
            if not all(c['pass'] for c in source['checks']):raise ValueError('large source comparison failed')
            for field in ['reference quadratic objective count','reference quadratic constraints count']:
                if not checks[field]['pass'] or checks[field]['elements']!=0:raise ValueError('reference is not LP-only')
            covered=0
            for first,last in source['matrix_row_partitions']:
                if first!=covered or last<first:raise ValueError('incomplete matrix row coverage')
                covered=last
            if covered!=source['rows']:raise ValueError('incomplete matrix row coverage')
        layouts={f['format']:f for f in row['formats']}
        if set(layouts)!={'gurobi-mps-gzip','raw-hdf5-gzip','optbin','optbin-gzip'}:raise ValueError('missing format')
        for name,layout in layouts.items():
            if len(layout['runs'])!=repeats:raise ValueError('wrong repetition count')
            if statistics.median(v['seconds'] for v in layout['runs'])!=layout['median_seconds']:raise ValueError('incorrect time median')
            if statistics.median(v['max_rss_bytes'] for v in layout['runs'])!=layout['median_peak_rss_bytes']:raise ValueError('incorrect RSS median')
            for run in layout['runs']:
                if name=='gurobi-mps-gzip':
                    if (run['rows'],run['cols'],run['nnz'],run['gurobi_fingerprint'])!=(source['rows'],source['cols'],source['nnz'],source['gurobi_fingerprint']):
                        raise ValueError('Gurobi model metadata/fingerprint changed')
                elif not run['bitwise_reference_equal'] or run['numerical_sha256']!=row['numerical_sha256']:
                    raise ValueError('binary model comparison failed')


def combine_case_studies(paths,case_concurrency=1):
    """Combine complete large-LP studies with identical readers and contracts."""
    if not paths or not 1<=case_concurrency<=len(paths):raise ValueError('invalid case concurrency')
    result=None;ids=set();entries=[]
    for path in paths:
        path=Path(path);raw=path.read_bytes();study=json.loads(raw)
        verify_study(study,3,False)
        environment={k:v for k,v in study['environment'].items() if k!='started_utc'}
        if result is None:
            result=copy.deepcopy(study);result['results']=[];common=environment
        elif (environment!=common or study['baseline_report']!=result['baseline_report'] or
              study['timing_contract']!=result['timing_contract'] or study['limitations']!=result['limitations']):
            raise ValueError('case studies must share host, readers, input baseline and timing contracts')
        for row in study['results']:
            if row['id'] in ids:raise ValueError('duplicate case: '+row['id'])
            ids.add(row['id']);result['results'].append(row)
        resolved=path.resolve()
        label=str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(resolved)
        entries.append({'path':label,'sha256':hashlib.sha256(raw).hexdigest()})
    result['study_reports']=entries
    result['execution_policy']={'concurrent_case_jobs':case_concurrency,
        'within_case':'One format at a time, shuffled fresh-process rounds; shared server, no global cache flush'}
    return result


def verify_mosek_study(study,repeats=7):
    if not study.get('complete'):raise ValueError('MOSEK study is incomplete')
    if study['environment']['mosek_threads_parameter']!=1:raise ValueError('unexpected MOSEK thread parameter')
    ids=set()
    for row in study['results']:
        if row['id'] in ids or row['status']!='complete':raise ValueError('duplicate or incomplete CBF case')
        ids.add(row['id'])
        source=next(v for v in row['verification'] if v['format']=='mosek-cbf-gzip')
        checked=source['comparison']
        if (set(checked['checks'])!={'variables','sense','objective','constraints'} or
            not source['exact_reference_equal'] or not checked['pass'] or not all(checked['checks'].values()) or
            checked['reference_canonical_sha256']!=checked['native_canonical_sha256'] or
            checked['reference_canonical_sha256']!=source['canonical_sha256'] or
            source['canonical_sha256']!=row['source_canonical_sha256']):
            raise ValueError('MOSEK full conic model comparison failed')
        if source['reader']!='MOSEK.Task.readdata':raise ValueError('unexpected CBF reference reader')
        layouts={f['format']:f for f in row['formats']}
        if set(layouts)!={'mosek-cbf-gzip','raw-hdf5-gzip','optbin','optbin-gzip'}:raise ValueError('missing CBF format')
        if len(row['verification'])!=4 or {v['format'] for v in row['verification']}!=set(layouts):
            raise ValueError('missing CBF verification warmup')
        for verified in row['verification']:
            if verified['format']!='mosek-cbf-gzip' and (not verified['bitwise_reference_equal'] or
                verified['numerical_sha256']!=row['numerical_sha256']):raise ValueError('CBF warmup binary comparison failed')
        for name,layout in layouts.items():
            if len(layout['runs'])!=repeats:raise ValueError('wrong CBF repetition count')
            if statistics.median(v['seconds'] for v in layout['runs'])!=layout['median_seconds']:raise ValueError('incorrect CBF time median')
            if statistics.median(v['max_rss_bytes'] for v in layout['runs'])!=layout['median_peak_rss_bytes']:raise ValueError('incorrect CBF RSS median')
            for run in layout['runs']:
                if name=='mosek-cbf-gzip':
                    if run['reader']!='MOSEK.Task.readdata' or run['canonical_sha256']!=source['canonical_sha256']:
                        raise ValueError('MOSEK model changed')
                elif not run['bitwise_reference_equal'] or run['numerical_sha256']!=row['numerical_sha256']:
                    raise ValueError('CBF binary model comparison failed')


def merge_studies(original_small,original_large,gurobi_small,gurobi_large,mosek_small):
    verify_study(gurobi_small,7,True);verify_study(gurobi_large,3,False)
    verify_mosek_study(mosek_small)
    if len(gurobi_small['results'])!=13 or len(gurobi_large['results'])!=2:raise ValueError('unexpected case coverage')
    if len(mosek_small['results'])!=9:raise ValueError('unexpected CBF case coverage')
    for previous,current in [(original_small,gurobi_small),(original_large,gurobi_large),(original_small,mosek_small)]:
        old=previous['environment'];new=current['environment']
        if any(old[k]!=new[k] for k in ['hostname','platform','cpu']):raise ValueError('baseline host changed')
        if old['library_sha256']!=new['binary_library_sha256']:raise ValueError('binary reader libraries changed')
        binaries=[h for p,h in new['executable_sha256'].items() if Path(p).name=='optb_format_bench']
        if binaries!=[old['binary_sha256']]:raise ValueError('binary reader executable changed')
    def replace(old,new):
        out=copy.deepcopy(new);out['source_format']=old['source_format']
        out['source_reader']='gurobipy.read' if old['source_format']=='mps' else 'MOSEK.Task.readdata'
        out['preparation']=copy.deepcopy(old.get('preparation',{}))
        old_layouts={f['format']:f for f in old['formats']}
        for f in out['formats']:
            name='text-gzip' if f['format'] in ('gurobi-mps-gzip','mosek-cbf-gzip') else f['format']
            if (f['bytes'],f['sha256'])!=(old_layouts[name]['bytes'],old_layouts[name]['sha256']):raise ValueError('different input representation')
            f['original_format']=f['format'];f['format']=name
        if out['numerical_sha256']!=old['numerical_sha256']:raise ValueError('different canonical binary model')
        return out
    lookup={m['id']:m for m in gurobi_small['results']}
    cbf_lookup={m['id']:m for m in mosek_small['results']}
    small=copy.deepcopy(original_small)
    small['reader']='MPS: public gurobipy.read; CBF: public MOSEK.Task.readdata'
    small['scope']='Independent vendor source readers and matched binary array trials; no solving'
    small['gurobi_environment']=copy.deepcopy(gurobi_small['environment'])
    small['mosek_environment']=copy.deepcopy(mosek_small['environment'])
    for i,old in enumerate(original_small['results']):
        if old['source_format']=='mps':
            if old['id'] not in lookup:raise ValueError('missing MPS case')
            small['results'][i]=replace(old,lookup.pop(old['id']))
        else:
            if old['id'] not in cbf_lookup:raise ValueError('missing CBF case')
            small['results'][i]=replace(old,cbf_lookup.pop(old['id']))
    if lookup:raise ValueError('unexpected MPS cases')
    if cbf_lookup:raise ValueError('unexpected CBF cases')
    lookup={m['id']:m for m in gurobi_large['results']}
    large=copy.deepcopy(gurobi_large)
    large['results']=[replace(old,lookup[old['id']]) for old in original_large['results']]
    return small,large


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--combine',type=Path,nargs='+',help='combine completed three-repetition large-LP case studies')
    p.add_argument('--case-concurrency',type=int,default=1)
    args=p.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.combine:
        data=combine_case_studies(args.combine,args.case_concurrency)
        with args.output.open('x') as f:f.write(json.dumps(data,indent=2,allow_nan=False)+'\n')
        print(args.output);return
    names=['gzip-readers-20261029.json','gzip-large-20261029.json','gurobi-small-20261030.json','gurobi-large-20261030.json','mosek-cbf-20261030.json']
    reports=[json.loads((ROOT/'benchmarks'/n).read_text()) for n in names]
    small,large=merge_studies(*reports)
    data={'complete':True,'scope':'Public Gurobi MPS and MOSEK CBF reader references',
        'sources':{n:hashlib.sha256((ROOT/'benchmarks'/n).read_bytes()).hexdigest() for n in names},
        'small':small,'large':large}
    with args.output.open('x') as f:f.write(json.dumps(data,indent=2)+'\n')
    print(args.output)


if __name__=='__main__':main()
