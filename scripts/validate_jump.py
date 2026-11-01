#!/usr/bin/env python3
"""Compare source files read by JuMP/MOI with fresh OptBin conversion and C loading.

Canonicalization compares the complete numerical function-in-set model, not
source names, row ordering, or identical physical arrays. It uses no solver.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def number(value):
    return struct.unpack('<d', bytes.fromhex(value)[::-1])[0] if isinstance(value, str) else float(value)


def bits(value):
    value = number(value)
    if math.isnan(value):
        raise ValueError('NaN in comparison')
    return struct.pack('>d', 0. if value == 0. else value).hex()


def aggregate(entries, quadratic=False):
    values = defaultdict(float)
    for entry in entries:
        coordinates = tuple(map(int, entry[:-1]))
        if quadratic:
            coordinates = tuple(sorted(coordinates))
        values[coordinates] += number(entry[-1])
    return tuple((*key, bits(value)) for key, value in sorted(values.items()) if value != 0.)


def canonical(model):
    objective = model['objective']
    objective_key = (aggregate(objective['linear']), aggregate(objective['quadratic'], True), bits(objective['constant']))
    constraints = Counter()
    for constraint in model['constraints']:
        function, domain = constraint['function'], constraint['set']
        if function['kind'] == 'scalar':
            assert domain['kind'] == 'interval', domain
            linear, quadratic = aggregate(function['linear']), aggregate(function['quadratic'], True)
            constant = number(function['constant'])
            lower, upper = number(domain['lower']), number(domain['upper'])
            if lower != -math.inf:
                constraints[('scalar', 'lower', linear, quadratic, bits(lower - constant))] += 1
            if upper != math.inf:
                constraints[('scalar', 'upper', linear, quadratic, bits(upper - constant))] += 1
        else:
            if domain['kind'] == 'Reals':
                continue
            descriptor = (domain['kind'], int(domain['dimension']), int(domain.get('side_dimension', 0)),
                          bits(domain['exponent']) if 'exponent' in domain else '')
            constants = tuple(bits(x) for x in function['constants'])
            assert len(constants) == domain['dimension']
            constraints[('vector', descriptor, aggregate(function['terms']), constants)] += 1
    return {'variables': int(model['variables']), 'sense': int(model['sense']),
            'objective': objective_key, 'constraints': constraints}


def fingerprint(model):
    data = {**model, 'constraints': sorted((key, count) for key, count in model['constraints'].items())}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def scalar(linear=(), quadratic=(), constant=0.):
    return {'kind': 'scalar', 'linear': list(linear), 'quadratic': list(quadratic), 'constant': constant}


def interval(function, lower, upper):
    return {'function': function, 'set': {'kind': 'interval', 'lower': lower, 'upper': upper}}


def sparse_columns(model, pointer_id, index_id, value_id):
    pointers, indices, values = (model.numpy(i) for i in [pointer_id, index_id, value_id])
    for col in range(max(0, len(pointers) - 1)):
        for k in range(int(pointers[col]), int(pointers[col + 1])):
            yield int(indices[k]), col, float(values[k])


def native_mps(model):
    n, m = model.info['cols'], model.info['rows']
    rows = [[] for _ in range(m)]
    for i, j, value in sparse_columns(model, 1, 2, 3):
        rows[i].append([j, value])
    objective_q = [[i, j, value * (.5 if i == j else 1.)] for i, j, value in sparse_columns(model, 9, 10, 11)]
    objective = scalar(enumerate(model.numpy(4).tolist()), objective_q, model.info['objective_offset'])
    qc = {}
    ids, pointers, ii, jj, vv, scales = (model.numpy(i) for i in range(12, 18))
    for k, row in enumerate(ids):
        qc[int(row)] = [[int(ii[t]), int(jj[t]), float(scales[k] * vv[t]) * (1. if ii[t] == jj[t] else 2.)]
                        for t in range(int(pointers[k]), int(pointers[k + 1]))]
    constraints = [interval(scalar(rows[i], qc.get(i, [])), float(lo), float(up))
                   for i, (lo, up) in enumerate(zip(model.numpy(5), model.numpy(6)))]
    constraints.extend(interval(scalar([[j, 1.]]), float(lo), float(up))
                       for j, (lo, up) in enumerate(zip(model.numpy(7), model.numpy(8))))
    return {'variables': n, 'sense': model.info['sense'], 'objective': objective, 'constraints': constraints}


def mosek_model(path):
    """Adjudicate MOSEK-dialect differences through its actual SDK, without solving."""
    import mosek
    with mosek.Task() as task:
        task.readdata(str(path))
        n, m = task.getnumvar(), task.getnumcon()

        def bounds(key, lower, upper):
            lo = lower if key in (mosek.boundkey.lo, mosek.boundkey.ra, mosek.boundkey.fx) else -math.inf
            up = upper if key in (mosek.boundkey.up, mosek.boundkey.ra, mosek.boundkey.fx) else math.inf
            return lo, up

        def quadratic(data):
            _, ii, jj, values = data
            return [[i, j, v * (.5 if i == j else 1.)] for i, j, v in zip(ii, jj, values)]

        objective = scalar(enumerate(task.getclist(list(range(n)))), quadratic(task.getqobj()), task.getcfix())
        constraints = []
        for row in range(m):
            _, columns, coefficients = task.getarow(row)
            function = scalar(zip(columns, coefficients), quadratic(task.getqconk(row)))
            constraints.append(interval(function, *bounds(*task.getconbound(row))))
        constraints.extend(interval(scalar([[j, 1.]]), *bounds(*task.getvarbound(j))) for j in range(n))
        return {'variables': n, 'sense': 1 if task.getobjsense() == mosek.objsense.minimize else -1,
                'objective': objective, 'constraints': constraints}, '.'.join(map(str, mosek.Env.getversion()))


CONE_NAMES = ['Reals', 'Nonnegatives', 'Nonpositives', 'Zeros', 'SecondOrderCone',
              'RotatedSecondOrderCone', 'ExponentialCone', 'DualExponentialCone', 'PowerCone', 'DualPowerCone']


def cone_groups(model, first):
    kinds, dimensions, pointers, parameters = (model.numpy(i) for i in range(first, first + 4))
    for k, (kind, dimension) in enumerate(zip(kinds, dimensions)):
        domain = {'kind': CONE_NAMES[int(kind)], 'dimension': int(dimension)}
        if int(kind) in (8, 9):
            weights = parameters[int(pointers[k]):int(pointers[k + 1])].tolist()
            if len(weights) != 2 or dimension != 3:
                raise NotImplementedError('JuMP/MOI CBF reader supports only three-dimensional, two-weight power cones')
            domain['exponent'] = weights[0] / sum(weights)
        yield domain


def triangle_index(row, col):
    row, col = min(row, col), max(row, col)
    return col * (col + 1) // 2 + row


def native_cbf(model, path, canonical_variables=False):
    # Only header order is read from source here, never numerical coefficients.
    # MOI creates PSD/scalar variables in source-section order; EXP variables
    # reverse their CBF coordinates at creation. This builds that bijection.
    scalar_map = [None] * model.info['cols']; psd_map = []; total = 0
    variable_groups = list(cone_groups(model, 19))
    if canonical_variables:
        # Solver comparisons use scalar IDs followed by unscaled PSD triangles,
        # independently of source-section and MOI variable-creation order.
        scalar_map = list(range(model.info['cols']))
        total = model.info['cols']; order = ['PSDVAR']
    else:
        opener = gzip.open if str(path).endswith('.gz') else open
        with opener(path, 'rt') as source:
            order = [line.strip() for line in source if line.strip() in ('PSDVAR', 'VAR')]
    for section in order:
        if section == 'PSDVAR':
            for dimension in model.numpy(27):
                count = int(dimension) * (int(dimension) + 1) // 2
                psd_map.append(list(range(total, total + count))); total += count
        else:
            start = 0
            for domain in variable_groups:
                d = domain['dimension']; indices = list(range(total, total + d)); total += d
                if domain['kind'] in ('ExponentialCone', 'DualExponentialCone'):
                    indices.reverse()
                scalar_map[start:start + d] = indices; start += d
    assert all(x is not None for x in scalar_map)
    assert len(psd_map) == len(model.numpy(27))
    linear = [[scalar_map[j], float(value)] for j, value in enumerate(model.numpy(4))]
    for block, i, j, value in zip(*(model.numpy(s) for s in range(29, 33))):
        linear.append([psd_map[int(block)][triangle_index(int(i), int(j))], float(value) * (1 if i == j else 2)])
    constraints = []; start = 0

    def vector(domain, rows, constants):
        if domain['kind'] in ('ExponentialCone', 'DualExponentialCone'):
            rows = rows[::-1]; constants = constants[::-1]
        terms = [[i, j, value] for i, row in enumerate(rows) for j, value in row]
        return {'set': domain, 'function': {'kind': 'vector', 'terms': terms, 'constants': constants}}

    for domain in variable_groups:
        d = domain['dimension']
        constraints.append(vector(domain, [[[scalar_map[start + j], 1.]] for j in range(d)], [0.] * d)); start += d
    for block, dimension in enumerate(model.numpy(27)):
        domain = {'kind': 'PositiveSemidefiniteConeTriangle', 'dimension': len(psd_map[block]), 'side_dimension': int(dimension)}
        constraints.append(vector(domain, [[[j, 1.]] for j in psd_map[block]], [0.] * len(psd_map[block])))
    rows = [[] for _ in range(model.info['rows'])]
    for i, j, value in sparse_columns(model, 1, 2, 3):
        rows[i].append([scalar_map[j], value])
    for row, block, i, j, value in zip(*(model.numpy(s) for s in range(33, 38))):
        rows[int(row)].append([psd_map[int(block)][triangle_index(int(i), int(j))], float(value) * (1 if i == j else 2)])
    start = 0; constants = model.numpy(18).tolist()
    for domain in cone_groups(model, 23):
        end = start + domain['dimension']
        constraints.append(vector(domain, rows[start:end], constants[start:end])); start = end
    psd_rows = [[[] for _ in range(int(d) * (int(d) + 1) // 2)] for d in model.numpy(28)]
    psd_constants = [[0.] * len(rows) for rows in psd_rows]
    for block, variable, i, j, value in zip(*(model.numpy(s) for s in range(38, 43))):
        psd_rows[int(block)][triangle_index(int(i), int(j))].append([scalar_map[int(variable)], float(value)])
    for block, i, j, value in zip(*(model.numpy(s) for s in range(43, 47))):
        psd_constants[int(block)][triangle_index(int(i), int(j))] = float(value)
    for k, dimension in enumerate(model.numpy(28)):
        domain = {'kind': 'PositiveSemidefiniteConeTriangle', 'dimension': len(psd_rows[k]), 'side_dimension': int(dimension)}
        constraints.append(vector(domain, psd_rows[k], psd_constants[k]))
    return {'variables': total, 'sense': model.info['sense'],
            'objective': scalar(linear, constant=model.info['objective_offset']), 'constraints': constraints}


def describe_difference(counter):
    result = []
    for key, count in list(counter.items())[:3]:
        encoded = json.dumps(key, separators=(',', ':'))
        result.append({'count': count, 'sha256': hashlib.sha256(encoded.encode()).hexdigest(), 'prefix': encoded[:800]})
    return result


def compare(native, reference):
    native, reference = canonical(native), canonical(reference)
    checks = {key: native[key] == reference[key] for key in ['variables', 'sense', 'objective', 'constraints']}
    result = {'pass': all(checks.values()), 'checks': checks,
              'native_canonical_sha256': fingerprint(native), 'reference_canonical_sha256': fingerprint(reference),
              'variables': native['variables'], 'normalized_constraints': sum(native['constraints'].values())}
    if not checks['objective']:
        result['native_objective_prefix'] = str(native['objective'])[:1500]
        result['reference_objective_prefix'] = str(reference['objective'])[:1500]
    if not checks['constraints']:
        result['native_only'] = describe_difference(native['constraints'] - reference['constraints'])
        result['reference_only'] = describe_difference(reference['constraints'] - native['constraints'])
    return result


def fixtures(work):
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_optbin import QP, PSD
    from test_solvers import cbf
    examples = [('fixture_lp', 'mps', QP[:QP.index('QUADOBJ')] + 'ENDATA\n', 'gurobi'),
                ('fixture_qp', 'mps', QP, 'gurobi'),
                ('fixture_qcqp_gurobi', 'mps', (ROOT / 'examples/qcqp.mps').read_text(), 'gurobi'),
                ('fixture_qcqp_mosek', 'mps', (ROOT / 'examples/qcqp.mps').read_text(), 'mosek'),
                ('fixture_psd_operators', 'cbf', PSD, None),
                ('fixture_soc', 'cbf', (ROOT / 'examples/socp.cbf').read_text(), None)]
    examples.append(('fixture_qmatrix', 'mps', QP.replace('QUADOBJ', 'QMATRIX').replace(
        ' X Y 0.5', ' X Y 0.5\n Y X 0.5'), 'gurobi'))
    ranges = '''NAME ranges
OBJSENSE
 MAX
ROWS
 N cost
 E eq
 L less
 G greater
 E ranged
COLUMNS
 x cost 1 eq 3
 x less 2
 x greater -1
 y cost -3 eq -1
 y ranged 1
 z cost 0
RHS
 R cost -7 eq 2
 R less 4 greater -5
 R ranged 6
RANGES
 RNG less 3 greater -4
 RNG ranged -2
BOUNDS
 FR B x
 MI B y
 UP B y -2
 FX B z 4
ENDATA
'''
    examples.append(('fixture_ranges_bounds_max', 'mps', ranges, 'gurobi'))
    lp = QP[:QP.index('QUADOBJ')] + 'ENDATA\n'
    examples.append(('fixture_negative_upper_default', 'mps', lp.replace('UP b Y 10', 'UP b Y -2'), 'gurobi'))
    examples.append(('fixture_duplicate_entries', 'mps', lp.replace(' X qc -3', ' X eq 2\n X qc -3'), 'gurobi'))
    for section, mirror in [('QCMATRIX', ' Y X 3\n'), ('QSECTION', '')]:
        text = QP.replace('QUADOBJ', 'QSECTION obj').replace('ENDATA',
            section + ' qc\n X X 2\n X Y 3\n' + mirror + ' Y Y 4\nENDATA')
        examples.append(('fixture_mosek_' + section, 'mps', text, 'mosek'))
        examples.append(('fixture_mosek_' + section + '_OBJ', 'mps', text.replace('obj', 'OBJ'), 'mosek'))
    for kind in ['EXP', 'EXP*', 'QR', '@0:POW', '@0:POW*']:
        extra = 'POWCONES\n1 2\n2\n2\n3\n' if kind == '@0:POW' else 'POW*CONES\n1 2\n2\n2\n3\n' if kind == '@0:POW*' else ''
        text = cbf(3, [kind + ' 3'], 3, [kind + ' 3'], [(0, 0, 1.25), (1, 1, -2.), (2, 2, 3.)],
                   [(0, 4.), (1, 5.), (2, -6.)], [(0, .5), (1, -3.), (2, 7.)], extra)
        examples.append(('fixture_cone_' + kind.replace('@0:', '').replace('*', '_dual'), 'cbf', text, None))
    power = 'VER\n3\nPOWCONES\n1 3\n3\n1\n2\n3\nOBJSENSE\nMIN\nVAR\n4 1\n@0:POW 4\n'
    examples.append(('fixture_generalized_power', 'cbf', power, None))
    records = []
    for name, format, text, dialect in examples:
        path = work / (name + '.' + format); path.write_text(text)
        records.append({'id': name, 'format': format, 'path': str(path), 'dialect': dialect, 'fixture': True})
    workaround = dict(next(x for x in records if x['id'] == 'fixture_qcqp_mosek'))
    workaround.update(id='fixture_qcqp_mosek_gurobi_option', reference_dialect='gurobi')
    records.append(workaround)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--julia', default=shutil.which('julia'))
    parser.add_argument('--build', type=Path, default=ROOT / 'build-release')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'benchmarks/real-cases.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'benchmarks/jump-correctness.json')
    parser.add_argument('--work', type=Path, default=ROOT / 'benchmarks/runs/jump')
    parser.add_argument('--reuse-reference', action='store_true')
    parser.add_argument('--fixtures-only', action='store_true')
    parser.add_argument('--strict-reference', action='store_true',
                        help='exit nonzero for reviewed reference discrepancies as well as new differences')
    parser.add_argument('--known-reference-issues', type=Path, default=ROOT / 'tests/jump/known-reference-issues.json')
    args = parser.parse_args()
    if not args.julia and not args.reuse_reference:
        parser.error('Julia was not found; supply --julia /absolute/path/to/julia')
    harness_sha256 = sha(Path(__file__))
    args.work = args.work.resolve(); args.work.mkdir(parents=True, exist_ok=True)
    args.build = args.build.resolve()
    os.environ['OPTBIN_LIBRARY'] = str(args.build / 'liboptbin.so')
    from optbin import Model
    records = [] if args.fixtures_only else [x for x in json.loads(args.manifest.read_text()) if not x.get('integer') and not x.get('error')]
    for item in records:
        path = Path(item['path']); item['path'] = str(path if path.is_absolute() else ROOT / path)
    records += fixtures(args.work)
    for item in records:
        item['sha256'] = item.get('sha256') or sha(item['path'])
        assert sha(item['path']) == item['sha256'], item['id']
    job = args.work / 'cases.json'; job.write_text(json.dumps(records, indent=2) + '\n')
    reference_path = args.work / 'reference.jsonl'
    julia_command = [args.julia, '--startup-file=no', '--project=' + str(ROOT / 'tests/jump'),
                     str(ROOT / 'scripts/jump_reference.jl'), str(job), str(reference_path)]
    if not args.reuse_reference:
        with (args.work / 'julia.log').open('w') as log:
            result = subprocess.run(julia_command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError('JuMP reader process failed; see ' + str(args.work / 'julia.log'))
    with reference_path.open() as stream:
        environment = json.loads(next(stream))['environment']
        assert environment['exporter_sha256'] == sha(ROOT / 'scripts/jump_reference.jl')
        reference = {x['id']: x for x in map(json.loads, stream)}
    known = json.loads(args.known_reference_issues.read_text()) if args.known_reference_issues.exists() else {}
    same_versions = all(environment['packages'][name]['version'] == version
                        for name, version in known.get('reference_packages', {}).items())
    known_cases = {x['id']: x for x in known.get('issues', [])} if same_versions else {}
    results = []
    for item in records:
        path = Path(item['path']); target = args.work / (item['id'] + '.optb')
        if target.exists():
            target.unlink()
        command = [str(args.build / (item['format'] + '2optb')), str(path), str(target)]
        if item['format'] == 'mps':
            command += ['--mps-dialect', item.get('dialect', 'gurobi')]
        converted = subprocess.run(command, capture_output=True, text=True)
        record = {'id': item['id'], 'fixture': item.get('fixture', False), 'format': item['format'],
                  'input_sha256': item['sha256'], 'dialect': item.get('dialect'),
                  'converter_exit_code': converted.returncode}
        try:
            if converted.returncode:
                raise ValueError('OptBin conversion failed: ' + converted.stderr)
            record['optbin_sha256'] = sha(target)
            ref = reference[item['id']]
            assert ref['source_sha256'] == item['sha256']
            if ref['status'] == 'reference_error':
                with Model(target) as model:
                    record['native_full_validation'] = True
                unsupported = item['id'] == 'fixture_generalized_power' and 'Only 3-dimensional power cones' in ref['error']
                record.update(status='reference_unsupported' if unsupported else 'reference_error',
                              error=ref['error'], error_type=ref['error_type'])
            else:
                record['reference_dialect'] = ref['reference_dialect']
                with Model(target) as model:
                    record['native_full_validation'] = True
                    native = native_mps(model) if item['format'] == 'mps' else native_cbf(model, path)
                    record.update(compare(native, ref))
                    record['status'] = 'pass' if record['pass'] else 'mismatch'
                    if item.get('dialect') == 'mosek':
                        try:
                            vendor, version = mosek_model(path)
                        except ImportError:
                            record['mosek_sdk'] = {'status': 'not_installed'}
                        else:
                            record['mosek_sdk'] = {'version': version, **compare(native, vendor)}
                            if not record['mosek_sdk']['pass']:
                                record['status'] = 'vendor_mismatch'
                    if item['id'] == 'fixture_negative_upper_default':
                        try:
                            import gurobipy as gp
                            from validate_real import compare_gurobi_qc
                        except ImportError:
                            record['gurobi_sdk'] = {'status': 'not_installed'}
                        else:
                            try:
                                vendor = compare_gurobi_qc(model, path)
                            except gp.GurobiError as error:
                                if error.errno not in (10009, 10010):raise
                                record['gurobi_sdk'] = {'status': 'license_unavailable'}
                            else:
                                record['gurobi_sdk'] = {'version': '.'.join(map(str, gp.gurobi.version())), **vendor}
                                if not vendor['pass']:record['status'] = 'vendor_mismatch'
                    if record['status'] == 'mismatch' and item['id'] in known_cases:
                        issue = known_cases[item['id']]
                        fields = ['input_sha256', 'native_canonical_sha256', 'reference_canonical_sha256', 'checks']
                        if all(record[field] == issue[field] for field in fields):
                            record['status'] = 'known_reference_discrepancy'
                            record['reference_issue'] = issue['reason']
        except Exception as error:
            record.update(status='error', error=str(error), error_type=type(error).__name__)
        results.append(record)
        print(record['id'], record['status'], flush=True)
    report = {'scope': 'Full canonical numerical function-in-set comparison with JuMP.read_from_file / MOI; no optimization',
        'environment': environment, 'build': str(args.build),
        'binary_sha256': {p: sha(args.build / p) for p in ['mps2optb', 'cbf2optb', 'liboptbin.so']},
        'harness_sha256': harness_sha256, 'julia_project_manifest_sha256': sha(ROOT / 'tests/jump/Manifest.toml'),
        'known_reference_issues_sha256': sha(args.known_reference_issues) if args.known_reference_issues.exists() else None,
        'normalizations': ['Aggregate duplicate terms and discard numerical zero terms, treating both signs of zero as equal',
            'Expand scalar intervals/equalities into lower/upper constraints; move affine constants to bounds',
            'Ignore ordering/names and unconstrained Reals domains; preserve duplicate nontrivial constraints',
            'Map MOI quadratic coefficients to polynomial monomials, including the half factor on diagonals',
            'Lift PSD variables to unscaled triangular coordinates, counting trace off-diagonals twice',
            'Apply CBF-to-MOI exponential coordinate permutations and two-weight power normalization'],
        'limits': ['Canonical model comparison, not bytewise equality of unlike physical representations',
                   'Reference-reader unsupported cases are recorded separately from passes',
                   'No original scalar-row names or ordering are compared'],
        'summary': dict(Counter(x['status'] for x in results)), 'results': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(args.output)
    acceptable = ['pass', 'reference_unsupported'] + ([] if args.strict_reference else ['known_reference_discrepancy'])
    return int(any(x['status'] not in acceptable for x in results))


if __name__ == '__main__':
    raise SystemExit(main())
