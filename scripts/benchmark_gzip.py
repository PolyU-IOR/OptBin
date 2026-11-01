#!/usr/bin/env python3
"""Practical gzip-inclusive text/raw-HDF5/OptBin storage and loading study.

The text baseline is OptBin's native MPS/CBF parser with an in-memory sink,
not JuMP or a solver. Outer gzip loading includes an on-disk seekable temporary
for HDF5/OptBin. gzip creation is preparation, never part of repeated reads.
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
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def execute(command, env=None):
    proc = subprocess.run(['/usr/bin/time', '-o', os.devnull, *map(str, command)],
                          capture_output=True, text=True, env=env)
    if proc.returncode:
        raise RuntimeError(str(command) + '\n' + proc.stderr[-6000:])
    return json.loads(proc.stdout)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, default=ROOT/'benchmarks/real-cases.json')
    p.add_argument('--cases', nargs='+', default=['AUG2D', 'AUG3DCQP', 'QPLIB_2676',
                        'nql30', 'nb_L2', 'LogExpCR-n20-m400', 'infeas_clean_10_10_1'])
    p.add_argument('--build', type=Path, default=ROOT/'build-gzip')
    p.add_argument('--work', type=Path, default=ROOT/'benchmarks/runs/gzip')
    p.add_argument('--output', type=Path, default=ROOT/'benchmarks/gzip-local.json')
    p.add_argument('--repeats', type=int, default=7)
    p.add_argument('--text-repeats', type=int, help='0 records only source-file storage; no text time')
    p.add_argument('--gzip-level', type=int, default=6)
    p.add_argument('--gzip-program', default='gzip', help='GNU gzip or pigz executable for preparation')
    p.add_argument('--gzip-threads', type=int, default=1, help='pigz compression threads; loading remains single-threaded')
    args = p.parse_args()
    if args.repeats < 1 or not 1 <= args.gzip_level <= 9:
        p.error('invalid repetition count or gzip level')
    text_repeats = args.repeats if args.text_repeats is None else args.text_repeats
    if text_repeats < 0:
        p.error('text repetitions cannot be negative')
    if args.gzip_threads < 1 or (args.gzip_threads != 1 and Path(args.gzip_program).name != 'pigz'):
        p.error('parallel preparation requires a pigz executable and positive thread count')
    args.build = args.build.resolve(); args.work = args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True); args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        p.error('report already exists; use a new output path')
    cases = {x['id']: x for x in json.loads(args.manifest.read_text())}
    if any(x not in cases for x in args.cases):
        p.error('unknown case ID')
    bench = args.build/'optb_format_bench'
    report = {'schema': 1, 'scope': 'Practical complete numerical model loading; no solve or model-file writing',
        'cache': 'Warm cache; one untimed numerical verification per measured format; shuffled fresh-process trials',
        'reader': 'Native OptBin source parsers with an in-memory array sink; same optb_validate as binary paths',
        'gzip': {'level_for_generated_files': args.gzip_level,
                 'program_for_generation': args.gzip_program, 'compression_threads': args.gzip_threads,
                 'existing_text_gzip': 'Retained verbatim; original producer compression settings unknown',
                 'binary_loading': 'zlib gzip to seekable disk temporary plus native load; all inside timer',
                 'optbin_gzip': 'Outer gzip around the default internally Zstd-compressed OptBin file',
                 'creation': 'Recorded separately from repeated reads; includes output write/close, no fsync'},
        'limitations': ['Text parser is not an independent performance baseline for JuMP/HiGHS/MOSEK',
            'Warm-cache single-host observations, not controlled cold-I/O or transfer timings',
            'Binary gzip paths require expanded seekable temporary storage; text gzip streams directly',
            'OptBin default versus gzip combines encoding and compressor choices',
            'Peak RSS excludes filesystem page cache and temporary-file space'],
        'environment': {'hostname': platform.node(), 'platform': platform.platform(),
            'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'cpu': next(x.split(':',1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines()
                        if x.startswith('model name')),
            'gzip_version': subprocess.run([args.gzip_program,'--version'], capture_output=True,text=True).stdout.splitlines()[0],
            'binary_sha256': sha(bench),
            'source_sha256': {str(x.relative_to(ROOT)):sha(x) for x in [Path(__file__),
                ROOT/'src/native_bench.c', ROOT/'src/text_bench_loader.cpp',
                ROOT/'src/mps_parser.hpp', ROOT/'src/cbf_parser.hpp', ROOT/'src/converter_common.hpp']},
            'library_sha256': {x:sha(args.build/x) for x in ['liboptbin.so','liboptbin_hdf5.so']}},
        'results': []}
    rng = random.Random(20261029)
    def save():
        args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    save()
    for id in args.cases:
        case = cases[id]
        if Path(id).name != id or case.get('integer'):
            raise ValueError('invalid ID or integer input')
        folder = args.work/id; folder.mkdir(exist_ok=False)
        source = Path(case['path'])
        if not source.is_absolute(): source = ROOT/source
        source_hash = sha(source)
        if case.get('sha256') and source_hash != case['sha256']:
            raise ValueError('source hash mismatch: '+id)
        row = {'id':id, 'source_format':case['format'], 'source':str(source),
               'source_sha256':source_hash, 'preparation':{}, 'formats':[], 'verification':[]}
        report['results'].append(row); save()
        def compress(original, target, label):
            start = time.perf_counter()
            with target.open('xb') as out:
                command=[args.gzip_program,'-n','-'+str(args.gzip_level)]
                if Path(args.gzip_program).name=='pigz':command += ['-p',str(args.gzip_threads)]
                subprocess.run([*command,'-c',str(original)], stdout=out,check=True)
            row['preparation'][label] = {'seconds':time.perf_counter()-start,
                'input_bytes':original.stat().st_size,'output_bytes':target.stat().st_size}
            save(); print(id,'compressed',label,flush=True)
        text = source
        if source.suffix != '.gz':
            text = folder/(id+'.'+case['format']+'.gz'); compress(source,text,'text_gzip')
        optb = Path(case['optbin']) if case.get('optbin') else folder/(id+'.optb')
        if not case.get('optbin'):
            cmd = [args.build/(case['format']+'2optb'), source, optb]
            if case['format']=='mps': cmd += ['--mps-dialect',case.get('dialect','gurobi')]
            row['preparation']['source_conversion'] = execute(cmd)
        raw = Path(case['raw_hdf5']) if case.get('raw_hdf5') else folder/'raw.h5'
        if not case.get('raw_hdf5'):
            row['preparation']['raw_hdf5_export'] = execute([args.build/'optb_h5_export',optb,raw,'raw'])
        hg = folder/'raw.h5.gz'; og = folder/(id+'.optb.gz')
        compress(raw,hg,'raw_hdf5_gzip'); compress(optb,og,'optbin_gzip')
        kind = 'cbf' if case['format']=='cbf' else 'mps-'+case.get('dialect','gurobi')
        formats = [('text-gzip',kind,text,text_repeats), ('raw-hdf5-gzip','hdf5-gzip',hg,args.repeats),
                   ('optbin','optbin',optb,args.repeats), ('optbin-gzip','optbin-gzip',og,args.repeats)]
        # Keep seekable gzip scratch next to each case, on the same measured disk.
        scratch=folder/'scratch'; scratch.mkdir(); env=dict(os.environ,TMPDIR=str(scratch))
        fingerprint = None
        for label,reader,path,repeats in formats:
            entry = {'format':label,'path':str(path),'bytes':path.stat().st_size,
                     'sha256':sha(path),'runs':[]}
            row['formats'].append(entry)
            if repeats:
                result = execute([bench,reader,path,'optbin',optb],env)
                if not result['bitwise_reference_equal']:
                    raise AssertionError('numerical comparison failed')
                if fingerprint is not None and fingerprint != result['numerical_sha256']:
                    raise AssertionError('numerical fingerprint differs')
                fingerprint = result['numerical_sha256']
                row['verification'].append({'format':label,**result})
                print(id,'verified',label,flush=True)
            else:
                entry['timing_status']='not measured; compressed storage only'
            save()
        row['numerical_sha256'] = fingerprint; row['measurement_order']=[]
        for repeat in range(max(args.repeats,text_repeats)):
            batch = [f for f in formats if repeat < f[3]]; rng.shuffle(batch)
            row['measurement_order'].append([f[0] for f in batch])
            for label,reader,path,_ in batch:
                result = execute([bench,reader,path],env)
                if result['numerical_sha256'] != fingerprint:
                    raise AssertionError('model changed during trial')
                entry = next(f for f in row['formats'] if f['format']==label)
                entry['runs'].append(result)
                print(id,'trial',repeat+1,label,round(result['seconds'],5),'s',flush=True)
                save()
        for entry in row['formats']:
            if entry['runs']:
                entry['median_seconds']=statistics.median(r['seconds'] for r in entry['runs'])
                entry['median_peak_rss_bytes']=statistics.median(r['max_rss_bytes'] for r in entry['runs'])
        row['status']='complete'; save()
    report['complete']=True; save(); print(args.output,flush=True)


if __name__=='__main__':
    main()
