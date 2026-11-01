#!/usr/bin/env python3
"""Matched fresh-process MOSEK CBF.gz and binary reader trials; no solving."""
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

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-report', type=Path, required=True)
    p.add_argument('--binary-build', type=Path, default=ROOT / 'build-gzip')
    p.add_argument('--cases', nargs='+'); p.add_argument('--repeats', type=int, default=7)
    p.add_argument('--work', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.repeats < 1:
        p.error('repeats must be positive')
    import mosek
    args.binary_build = args.binary_build.resolve(); args.work = args.work.resolve(); args.output = args.output.resolve()
    if args.work.exists() or args.output.exists():
        p.error('output and work directory must be new')
    baseline_bytes = args.baseline_report.read_bytes(); baseline = json.loads(baseline_bytes)
    selected = [r for r in baseline['results'] if r.get('source_format') == 'cbf']
    if args.cases:
        selected = [r for r in selected if r['id'] in args.cases]
        if {r['id'] for r in selected} != set(args.cases):
            p.error('case missing or not CBF')
    if not selected:
        p.error('no CBF cases')
    args.work.mkdir(parents=True); args.output.parent.mkdir(parents=True, exist_ok=True)
    binary = args.binary_build / 'optb_format_bench'
    sources = [Path(__file__), ROOT / 'scripts/mosek_read.py', ROOT / 'scripts/validate_jump.py', ROOT / 'python/optbin/reader.py']
    sdk = Path(mosek.__file__).parent
    report = {'schema': 1, 'complete': False, 'scope': 'Public MOSEK CBF reader versus complete validated binary numerical arrays; no solving',
        'timing_contract': {'mosek': 'Task.readdata only, including CBF.gz decompression and vendor Task construction',
            'mosek_separate': 'Environment/Task startup, all attribute extraction, canonical comparison and reference loading excluded from read timer/RSS',
            'binary': 'Native complete validated numerical arrays; no MOSEK Task construction',
            'gzip': 'Outer binary gzip expansion to a seekable temporary included',
            'cache': 'Warm files; one full verification/warmup per format, then shuffled fresh-process rounds'},
        'limitations': ['Different API endpoints: MOSEK solver Task versus validated numerical arrays',
            'No optimization, presolve, GPU upload or compressed-file creation in read timings',
            'Read-phase process peak RSS includes process startup and is not interpreted as minimum reader memory',
            'Exact comparator covers scalar linear/SOC/RSOC/EXP/dual EXP, three-dimensional power/dual power and PSD variables',
            'svec PSD affine domains, generalized power, geometric mean, integer and disjunctive models explicitly rejected by reference helper',
            'Cone grouping, EXP coordinate order, PSD trace products and explicit zeros normalized; no numerical tolerance'],
        'baseline_report': {'path': str(args.baseline_report.resolve()), 'sha256': hashlib.sha256(baseline_bytes).hexdigest()},
        'environment': {'hostname': platform.node(), 'platform': platform.platform(),
            'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'python': sys.version,
            'cpu': next(x.split(':', 1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name')),
            'repeats': args.repeats, 'mosek_threads_parameter': 1, 'mosek_version': '.'.join(map(str, mosek.Env.getversion())),
            'mosek_library_sha256': {str(x.relative_to(sdk)): sha(x) for x in sorted(sdk.glob('*.so*'))},
            'executable_sha256': {str(x): sha(x) for x in [binary, Path(sys.executable).resolve()]},
            'binary_library_sha256': {name: sha(args.binary_build / name) for name in ['liboptbin.so', 'liboptbin_hdf5.so']},
            'source_sha256': {str(x.relative_to(ROOT)): sha(x) for x in sources}}, 'results': []}

    def save():
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')

    def execute(command, env, log):
        start = time.monotonic()
        with log.open('w') as err:
            proc = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=err, text=True, env=env)
            report['running'] = {'pid': proc.pid, 'argv': list(map(str, command))}; save()
            stdout, _ = proc.communicate()
        report.pop('running', None)
        if proc.returncode:
            raise RuntimeError(f'exit {proc.returncode}: {command}; {log.read_text(errors="replace")[-3000:]}')
        result = json.loads(stdout); result['process_wall_seconds'] = time.monotonic() - start
        return result

    save(); rng = random.Random(20261030)
    try:
        for original in selected:
            case_id = original['id']
            if Path(case_id).name != case_id:
                raise ValueError('invalid case ID')
            folder = args.work / case_id; folder.mkdir(); scratch = folder / 'scratch'; scratch.mkdir()
            env = dict(os.environ, TMPDIR=str(scratch), OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
                       OPTBIN_LIBRARY=str(args.binary_build / 'liboptbin.so'))
            layouts = {f['format']: f for f in original['formats']}
            source = Path(layouts['text-gzip']['path']); reference = Path(layouts['optbin']['path'])
            specifications = [('mosek-cbf-gzip', 'mosek', source),
                ('raw-hdf5-gzip', 'hdf5-gzip', Path(layouts['raw-hdf5-gzip']['path'])),
                ('optbin', 'optbin', reference), ('optbin-gzip', 'optbin-gzip', Path(layouts['optbin-gzip']['path']))]
            row = {'id': case_id, 'source': str(source), 'formats': [], 'verification': [], 'measurement_order': [], 'status': 'running'}
            report['results'].append(row); save()

            def command(kind, path, verify):
                if kind == 'mosek':
                    return [sys.executable, ROOT / 'scripts/mosek_read.py', path] + (['--reference', reference] if verify else [])
                return [binary, kind, path, 'optbin', reference]

            expected = None
            for label, kind, path in specifications:
                print(case_id, 'verify', label, flush=True)
                digest = sha(path); old = layouts['text-gzip' if kind == 'mosek' else label]
                if (digest, path.stat().st_size) != (old['sha256'], old['bytes']):
                    raise ValueError('stored input changed: ' + str(path))
                row['formats'].append({'format': label, 'path': str(path), 'bytes': path.stat().st_size, 'sha256': digest, 'runs': []})
                result = execute(command(kind, path, True), env, folder / (label + '-verification.log'))
                if kind == 'mosek':
                    if not result['exact_reference_equal'] or not result['comparison']['pass']:
                        raise ValueError('conic source comparison failed')
                    row['mosek_version'] = result['mosek_version']; row['source_canonical_sha256'] = result['canonical_sha256']
                else:
                    if not result['bitwise_reference_equal'] or (expected is not None and result['numerical_sha256'] != expected):
                        raise ValueError('binary numerical comparison failed')
                    expected = result['numerical_sha256']
                row['verification'].append({'format': label, **result}); save()
            row['numerical_sha256'] = expected
            for repeat in range(args.repeats):
                batch = list(specifications); rng.shuffle(batch); row['measurement_order'].append([x[0] for x in batch]); save()
                for label, kind, path in batch:
                    print(case_id, 'round', repeat + 1, label, flush=True)
                    result = execute(command(kind, path, False), env, folder / (label + f'-{repeat+1}.log'))
                    if kind == 'mosek':
                        if result['canonical_sha256'] != row['source_canonical_sha256']:
                            raise ValueError('source canonical model changed')
                    elif not result['bitwise_reference_equal'] or result['numerical_sha256'] != expected:
                        raise ValueError('binary numerical comparison failed')
                    entry = next(f for f in row['formats'] if f['format'] == label); entry['runs'].append(result)
                    entry['median_seconds'] = statistics.median(v['seconds'] for v in entry['runs'])
                    entry['median_peak_rss_bytes'] = statistics.median(v['max_rss_bytes'] for v in entry['runs']); save()
            row['status'] = 'complete'; save()
        report['complete'] = True; save()
    except BaseException as error:
        report['error'] = str(error); save(); raise
    print(args.output)


if __name__ == '__main__':
    main()
