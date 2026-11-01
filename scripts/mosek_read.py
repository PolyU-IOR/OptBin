#!/usr/bin/env python3
"""Time public MOSEK Task.readdata, then compare complete conic functions.

The timer/RSS snapshot ends before any Task attribute extraction or OptBin
loading. The optional reference check uses public Task API data, not a solver
run or a CBF file written back by MOSEK. No optimization license is needed.
"""
import argparse
import json
import math
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from validate_jump import canonical, compare, fingerprint, interval, native_cbf, scalar, triangle_index


def scalarize_linear_cones(model):
    """Ignore grouping differences between linear cone blocks and Task bounds."""
    constraints = []
    for constraint in model['constraints']:
        function, domain = constraint['function'], constraint['set']
        if function['kind'] == 'vector' and domain['kind'] in ('Reals', 'Nonnegatives', 'Nonpositives', 'Zeros'):
            if domain['kind'] == 'Reals':
                continue
            rows = [[] for _ in range(domain['dimension'])]
            for i, j, value in function['terms']:
                rows[i].append([j, value])
            lower = 0. if domain['kind'] in ('Nonnegatives', 'Zeros') else -math.inf
            upper = 0. if domain['kind'] in ('Nonpositives', 'Zeros') else math.inf
            constraints.extend(interval(scalar(row, constant=constant), lower, upper)
                               for row, constant in zip(rows, function['constants']))
        else:
            constraints.append(constraint)
    return {**model, 'constraints': constraints}


def task_model(task):
    """Extract all supported continuous conic data through public MOSEK APIs.

    Scalars keep source column IDs; unscaled PSD triangle coordinates follow.
    PSD inner products double off-diagonal coefficients. EXP coordinates are
    reversed to the same function-in-set convention as the JuMP comparator.
    The benchmark corpus has PSD variables but no svec PSD affine domains;
    the latter are rejected here rather than accepting rounded sqrt(2) data.
    """
    import mosek
    n, m, nafe = task.getnumvar(), task.getnumcon(), task.getnumafe()
    unsupported = {'integer variables': task.getnumintvar(), 'legacy cones': task.getnumcone(),
                   'disjunctive constraints': task.getnumdjc(), 'quadratic objective': task.getnumqobjnz(),
                   'quadratic constraints': sum(task.getnumqconknz(k) for k in range(m))}
    if any(unsupported.values()):
        raise ValueError('continuous affine conic comparison only: ' + str(unsupported))

    def bounds(key, lower, upper):
        lo = float(lower) if key in (mosek.boundkey.lo, mosek.boundkey.ra, mosek.boundkey.fx) else -math.inf
        up = float(upper) if key in (mosek.boundkey.up, mosek.boundkey.ra, mosek.boundkey.fx) else math.inf
        return lo, up

    psd_maps = []; total = n; constraints = []
    for k in range(task.getnumbarvar()):
        d = task.getdimbarvarj(k); count = d * (d + 1) // 2
        ids = list(range(total, total + count)); total += count; psd_maps.append(ids)
        constraints.append({'set': {'kind': 'PositiveSemidefiniteConeTriangle', 'dimension': count, 'side_dimension': d},
            'function': {'kind': 'vector', 'terms': [[i, j, 1.] for i, j in enumerate(ids)], 'constants': [0.] * count}})

    def psd_terms(data, rows=None):
        # Public block triplets include a leading number of entries.
        if rows is None:
            count, blocks, ii, jj, values = data
            return [[psd_maps[b][triangle_index(i, j)], float(v) * (1. if i == j else 2.)]
                    for b, i, j, v in zip(blocks, ii, jj, values)]
        count, rr, blocks, ii, jj, values = data
        for r, b, i, j, v in zip(rr, blocks, ii, jj, values):
            rows[r].append([psd_maps[b][triangle_index(i, j)], float(v) * (1. if i == j else 2.)])

    objective = scalar(enumerate(task.getclist(list(range(n)))), constant=task.getcfix())
    objective['linear'].extend(psd_terms(task.getbarcblocktriplet()))
    keys, lower, upper = task.getvarboundslice(0, n)
    constraints.extend(interval(scalar([[j, 1.]]), *bounds(key, lo, up))
                       for j, (key, lo, up) in enumerate(zip(keys, lower, upper)))
    linear_rows = [[] for _ in range(m)]
    for k in range(m):
        _, columns, values = task.getarow(k)
        linear_rows[k].extend(zip(columns, values))
    psd_terms(task.getbarablocktriplet(), linear_rows)
    constraints.extend(interval(scalar(row), *bounds(*task.getconbound(k))) for k, row in enumerate(linear_rows))

    afe_rows = [[] for _ in range(nafe)]
    for i, j, value in zip(*task.getafeftrip()):
        afe_rows[i].append([j, float(value)])
    psd_terms(task.getafebarfblocktriplet(), afe_rows)
    constants = task.getafegslice(0, nafe)
    domain_names = {mosek.domaintype.r: 'Reals', mosek.domaintype.rzero: 'Zeros',
        mosek.domaintype.rplus: 'Nonnegatives', mosek.domaintype.rminus: 'Nonpositives',
        mosek.domaintype.quadratic_cone: 'SecondOrderCone', mosek.domaintype.rquadratic_cone: 'RotatedSecondOrderCone',
        mosek.domaintype.primal_exp_cone: 'ExponentialCone', mosek.domaintype.dual_exp_cone: 'DualExponentialCone',
        mosek.domaintype.primal_power_cone: 'PowerCone', mosek.domaintype.dual_power_cone: 'DualPowerCone'}
    for k in range(task.getnumacc()):
        index = task.getaccdomain(k); kind = task.getdomaintype(index); d = task.getdomainn(index)
        if kind not in domain_names:
            raise ValueError('unsupported exact MOSEK reference domain: ' + str(kind))
        domain = {'kind': domain_names[kind], 'dimension': d}
        if kind in (mosek.domaintype.primal_power_cone, mosek.domaintype.dual_power_cone):
            size, left = task.getpowerdomaininfo(index); alpha = list(task.getpowerdomainalpha(index))
            if size != 3 or left != 2 or len(alpha) != 2:
                raise ValueError('exact MOSEK reference supports only three-dimensional, two-weight power cones')
            domain['exponent'] = alpha[0] / sum(alpha)
        indices = task.getaccafeidxlist(k); b = task.getaccb(k)
        rows = [afe_rows[i] for i in indices]; g = [float(constants[i] - v) for i, v in zip(indices, b)]
        if domain['kind'] in ('ExponentialCone', 'DualExponentialCone'):
            rows = rows[::-1]; g = g[::-1]
        constraints.append({'set': domain, 'function': {'kind': 'vector',
            'terms': [[i, j, value] for i, row in enumerate(rows) for j, value in row], 'constants': g}})
    return scalarize_linear_cones({'variables': total,
        'sense': 1 if task.getobjsense() == mosek.objsense.minimize else -1,
        'objective': objective, 'constraints': constraints})


def check_reference(actual, reference, source):
    from optbin import Model
    with Model(reference) as expected:
        if expected.info['profile'] != 2:
            raise ValueError('MOSEK CBF reference comparison requires the affine conic profile')
        native = scalarize_linear_cones(native_cbf(expected, source, canonical_variables=True))
    checked = compare(native, actual)
    if not checked['pass']:
        raise ValueError('MOSEK conic reference mismatch: ' + json.dumps(checked))
    return checked


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path); p.add_argument('--reference', type=Path)
    args = p.parse_args()
    import mosek
    start = time.perf_counter()
    with mosek.Env() as env:
        with env.Task(0, 0) as task:
            task.putintparam(mosek.iparam.num_threads, 1)
            environment_seconds = time.perf_counter() - start
            before = resource.getrusage(resource.RUSAGE_SELF)
            start = time.perf_counter(); task.readdata(str(args.source)); seconds = time.perf_counter() - start
            after = resource.getrusage(resource.RUSAGE_SELF)
            result = {'reader': 'MOSEK.Task.readdata', 'mosek_version': '.'.join(map(str, mosek.Env.getversion())),
                'seconds': seconds, 'environment_seconds': environment_seconds, 'max_rss_bytes': after.ru_maxrss * 1024,
                'user_seconds': after.ru_utime - before.ru_utime, 'system_seconds': after.ru_stime - before.ru_stime,
                'scalar_variables': task.getnumvar(), 'linear_constraints': task.getnumcon(),
                'affine_conic_constraints': task.getnumacc(), 'affine_expression_rows': task.getnumafe(),
                'psd_variables': task.getnumbarvar(), 'mosek_threads_parameter': 1,
                'exact_reference_equal': None, 'reference_compare_seconds': 0.}
            start = time.perf_counter(); actual = task_model(task)
            result['canonical_sha256'] = fingerprint(canonical(actual))
            result['variables'] = actual['variables']
            result['normalized_constraints'] = sum(canonical(actual)['constraints'].values())
            result['extraction_seconds'] = time.perf_counter() - start
            if args.reference:
                start = time.perf_counter(); result['comparison'] = check_reference(actual, args.reference, args.source)
                result['reference_compare_seconds'] = time.perf_counter() - start
                result['exact_reference_equal'] = True
            print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
