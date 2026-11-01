#!/usr/bin/env python3
"""Optional public MOSEK conic-reference contracts; never calls optimize."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get('OPTBIN_BUILD', ROOT / 'build'))
sys.path.insert(0, str(ROOT / 'scripts'))
from mosek_read import check_reference, task_model
from test_solvers import cbf as make_cbf


def cbf(*args, **kwargs):
    # MOSEK requires numeric records immediately after their section headers.
    text = '\n'.join(line for line in make_cbf(*args, **kwargs).splitlines() if line.strip()) + '\n'
    # Omit absent optional sections; MOSEK rejects a terminal BCOORD 0 section.
    for section in ['OBJACOORD', 'ACOORD', 'BCOORD']:
        text = text.replace('\n' + section + '\n0\n', '\n')
    return text.replace('\nCON\n0 0\n', '\n')

PSD_VAR = '''VER
3
OBJSENSE
MAX
PSDVAR
1
3
VAR
1 1
L+ 1
CON
2 1
L= 2
OBJFCOORD
2
0 0 0 1
0 2 0 0.5
OBJACOORD
1
0 -2
OBJBCOORD
3
FCOORD
2
0 0 2 0 0.25
1 0 1 1 -2
ACOORD
1
0 0 -1
BCOORD
2
0 -1
1 2
'''


@unittest.skipUnless(importlib.util.find_spec('mosek'), 'optional MOSEK SDK missing')
class MosekBenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.work = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, text, name='model'):
        source = self.work / (name + '.cbf'); source.write_text(text)
        reference = self.work / (name + '.optb')
        p = subprocess.run([str(BUILD / 'cbf2optb'), str(source), str(reference)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return source, reference

    def read(self, source, reference=None, success=True):
        env = dict(os.environ, OPTBIN_LIBRARY=str(BUILD / 'liboptbin.so'))
        command = [sys.executable, str(ROOT / 'scripts/mosek_read.py'), str(source)]
        if reference:
            command += ['--reference', str(reference)]
        p = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode == 0, success, p.stdout + p.stderr)
        self.assertNotIn(p.returncode, [-11, -6])
        return json.loads(p.stdout) if success else p

    def test_complete_cone_families_plain_and_gzip(self):
        cases = []
        for cone in ['Q 3', 'QR 3', 'EXP 3', 'EXP* 3']:
            cases.append(cbf(3, [cone], 3, [cone], [(0, 1, 2), (2, 0, -1)], [(1, -2)], [(0, 1)]))
        for dual in ['', '*']:
            cases.append(cbf(3, [f'@0:POW{dual} 3'], 0, [], [], [], [(2, -1)],
                             f'POW{dual}CONES\n1 2\n2\n0.25\n0.75\n'))
        cases.extend([PSD_VAR, cbf(4, ['F 1', 'L+ 1', 'L- 1', 'L= 1'], 4,
            ['L+ 1', 'L- 1', 'L= 1', 'F 1'], [(i, i, i + 1) for i in range(4)], [(2, -3)], [(3, 1)])])
        for k, text in enumerate(cases):
            with self.subTest(case=k):
                source, reference = self.convert(text, f'case-{k}')
                zipped = source.with_suffix('.cbf.gz'); zipped.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
                plain = self.read(source, reference); compressed = self.read(zipped, reference)
                self.assertEqual(plain['canonical_sha256'], compressed['canonical_sha256'])
                for result in [plain, compressed]:
                    self.assertTrue(result['exact_reference_equal'])
                    self.assertTrue(all(result['comparison']['checks'].values()))
                    self.assertGreater(result['seconds'], 0.)
                    self.assertGreater(result['extraction_seconds'], 0.)
                    self.assertGreater(result['reference_compare_seconds'], 0.)

    def test_complete_model_mismatches_are_rejected_without_tolerance(self):
        source, _ = self.convert(PSD_VAR)
        changes = [('0 -2', '0 -2.0000000000000004'), ('MAX', 'MIN'),
                   ('OBJBCOORD\n3', 'OBJBCOORD\n4'), ('0 2 0 0.5', '0 2 0 0.5000000000000001'),
                   ('0 0 2 0 0.25', '0 0 2 0 0.25000000000000006'),
                   ('0 0 -1', '0 0 -2'), ('0 -1', '0 -2'), ('L+ 1', 'L- 1')]
        for k, (old, new) in enumerate(changes):
            with self.subTest(field=old):
                _, wrong = self.convert(PSD_VAR.replace(old, new), f'wrong-{k}')
                self.assertIn('conic reference mismatch', self.read(source, wrong, success=False).stderr)
        text = cbf(3, ['F 3'], 3, ['Q 3'], [(i, i, 1.) for i in range(3)], [], [])
        source, _ = self.convert(text, 'soc')
        _, wrong = self.convert(text.replace('Q 3', 'QR 3'), 'rsoc')
        self.assertIn('conic reference mismatch', self.read(source, wrong, success=False).stderr)

    def test_unsupported_models_are_explicit_and_missing_source_is_clean(self):
        import mosek
        text = cbf(3, ['F 3'], 0, [], [], [], [])
        path = self.work / 'integer.cbf'; path.write_text(text + 'INT\n1\n0\n')
        self.assertIn('continuous affine conic', self.read(path, success=False).stderr)
        # The reader supports CBF SDP constraints, but this exact Task comparator
        # rejects their sqrt(2)-scaled domain rather than silently dropping it.
        path = self.work / 'affine-psd.cbf'
        path.write_text(text + 'PSDCON\n1\n2\nHCOORD\n1\n0 0 0 1 1\n')
        self.assertIn('unsupported exact MOSEK reference domain', self.read(path, success=False).stderr)
        source, _ = self.convert(text, 'continuous')
        with mosek.Task() as task:
            task.readdata(str(source)); task.putqobj([0], [0], [1.])
            with self.assertRaisesRegex(ValueError, 'quadratic objective'):
                task_model(task)
        self.assertIn('Error', self.read(self.work / 'missing.cbf.gz', success=False).stderr)

    def test_acc_constants_and_psd_trace_are_extracted_through_public_api(self):
        import mosek
        os.environ['OPTBIN_LIBRARY'] = str(BUILD / 'liboptbin.so')
        source, reference = self.convert(PSD_VAR)
        with mosek.Task() as task:
            task.readdata(str(source)); actual = task_model(task)
            self.assertTrue(check_reference(actual, reference, source)['pass'])
            # Changing the vendor Task itself must also fail the independent
            # OptBin comparison, including its ACC constant and PSD objective.
            task.putcfix(task.getcfix() + 1.)
            with self.assertRaisesRegex(ValueError, 'conic reference mismatch'):
                check_reference(task_model(task), reference, source)
        text = cbf(3, ['F 3'], 3, ['Q 3'], [(i, i, 1.) for i in range(3)], [(0, 2)], [])
        source, reference = self.convert(text, 'acc')
        with mosek.Task() as task:
            task.readdata(str(source)); self.assertEqual(task.getnumacc(), 1)
            task.putaccb(0, [1., 0., 0.])
            with self.assertRaisesRegex(ValueError, 'conic reference mismatch'):
                check_reference(task_model(task), reference, source)
            # F*x + g - b is unchanged when the same coordinate is added to
            # both g and b. This checks the sign of the public ACC offset.
            task.putafeg(0, task.getafeg(0) + 1.)
            self.assertTrue(check_reference(task_model(task), reference, source)['pass'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
