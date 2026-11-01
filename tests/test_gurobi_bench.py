#!/usr/bin/env python3
"""Optional Gurobi reference reader: complete LP checks and timing boundaries."""
import argparse
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'build-gurobi'
LIBRARY=None
INTERFACE='python'
LP='''NAME reference
OBJSENSE
 MAX
ROWS
 N obj
 E eq
 L le
 G ge
COLUMNS
 x obj 1 eq 2
 x le 3 ge -1
 y obj -2 eq -4
 y le 0 ge 2
RHS
 b obj -7 eq 2
 b le 5 ge -3
BOUNDS
 FR b x
 LO b y -2
 UP b y 10
ENDATA
'''


class GurobiBenchTests(unittest.TestCase):
    def setUp(self):
        if not LIBRARY:self.skipTest('Gurobi runtime not installed; set OPTBIN_GUROBI_LIBRARY')
        self.tmp=tempfile.TemporaryDirectory();self.work=Path(self.tmp.name)

    def tearDown(self):
        if hasattr(self,'tmp'):self.tmp.cleanup()

    def command(self,*args,success=True):
        p=subprocess.run(list(map(str,args)),capture_output=True,text=True)
        self.assertEqual(p.returncode==0,success,p.stdout+'\n'+p.stderr)
        self.assertNotIn(p.returncode,[-11,-6])
        return p

    def convert(self,text,name='model',options=()):
        source=self.work/(name+'.mps');source.write_text(text)
        output=self.work/(name+'.optb')
        self.command(BUILD/'mps2optb',source,output,*options)
        return source,output

    def read(self,source,reference=None,success=True,options=()):
        if INTERFACE=='python':
            env=dict(os.environ,OPTBIN_LIBRARY=str(BUILD/'liboptbin.so'))
            command=[sys.executable,ROOT/'scripts/gurobi_read.py',source,*(['--reference',reference] if reference else []),*options]
            p=subprocess.run(list(map(str,command)),capture_output=True,text=True,env=env)
            self.assertEqual(p.returncode==0,success,p.stdout+'\n'+p.stderr)
            self.assertNotIn(p.returncode,[-11,-6]);return p
        return self.command(BUILD/'optb_gurobi_bench',LIBRARY,source,
                            *([reference] if reference else []),success=success)

    def test_complete_lp_and_gzip(self):
        source,reference=self.convert(LP)
        compressed=source.with_suffix('.mps.gz');compressed.write_bytes(gzip.compress(source.read_bytes(),mtime=0))
        for path in [source,compressed]:
            r=json.loads(self.read(path,reference).stdout)
            self.assertTrue(r['exact_reference_equal'])
            self.assertEqual((r['rows'],r['cols']),(3,2))
            self.assertEqual(r['ignored_explicit_sparse_zeros'],1)
            self.assertGreater(r['compared_elements'],20)
            self.assertGreater(r['seconds'],0)
            self.assertGreater(r['reference_compare_seconds'],0)
            self.assertGreater(r['environment_seconds'],0)

    def test_complete_model_mismatches_are_rejected(self):
        source,_=self.convert(LP)
        changes=[('x obj 1','x obj 1.0000000000000002'),('x obj 1 eq 2','x obj 1 eq 3'),
                 ('b obj -7','b obj -8'),('LO b y -2','LO b y -3'),
                 ('b obj -7 eq 2','b obj -7 eq 3'),(' MAX',' MIN'),
                 ('UP b y 10','UP b y 11'),('b le 5 ge -3','b le 6 ge -3'),
                 ('b le 5 ge -3','b le 5 ge -4'),('y obj -2 eq -4','y obj -2 le -4'),
                 ('RHS\n',' z obj 0\nRHS\n'),(' G ge\n',' G ge\n E empty\n')]
        for i,(old,new) in enumerate(changes):
            with self.subTest(field=old):
                _,wrong=self.convert(LP.replace(old,new),f'wrong-{i}')
                self.assertIn('reference mismatch',self.read(source,wrong,success=False).stderr)

    def test_public_row_partitions_cover_complete_matrix(self):
        if INTERFACE!='python':self.skipTest('public Python row-copy fallback')
        spec=importlib.util.spec_from_file_location('reference_worker',ROOT/'scripts/gurobi_read.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        self.assertEqual(list(worker.nonzero_bounded_rows([0]*11+[4,8,12,12],5)),[(0,11),(11,12),(12,14)])
        self.assertEqual(list(worker.nonzero_bounded_rows([0,0,1_500_000_000,3_000_000_000],1_500_000_000)),[(0,2),(2,3)])
        self.assertEqual(list(worker.nonzero_bounded_rows([0],5)),[])
        source,reference=self.convert(LP)
        r=json.loads(self.read(source,reference,options=['--matrix-block-rows','1']).stdout)
        self.assertTrue(r['exact_reference_equal'])
        self.assertEqual(r['matrix_row_partitions'],[[0,1],[1,2],[2,3]])
        self.assertEqual(r['matrix_reference_method'],'public row-submodel getA')
        self.assertEqual(r['matrix_nnz_checked'],5)

    def test_unsupported_sources_and_references_are_not_partially_validated(self):
        for name in ['qp.mps','qcqp.mps']:
            source=ROOT/'examples'/name;reference=self.work/(name+'.optb')
            self.command(BUILD/'mps2optb',source,reference,'--mps-dialect','gurobi')
            r=json.loads(self.read(source).stdout)
            self.assertIsNone(r['exact_reference_equal'])
            self.assertGreater(r['q_nnz']+r['q_constraints'],0)
            self.assertIn('LP-only',self.read(source,reference,success=False).stderr)
        source,_=self.convert(LP,'linear')
        for name,section in [('objective','QUADOBJ\n x x 2\n'),('constraint','QCMATRIX le\n x x 2\n')]:
            _,reference=self.convert(LP.replace('ENDATA',section+'ENDATA'),name,options=['--mps-dialect','gurobi'])
            self.assertIn('LP-only',self.read(source,reference,success=False).stderr)
        path=self.work/'multiobjective.mps'
        path.write_text(LP.replace(' N obj\n',' N obj 2 1 0 0\n N second 1 1 0 0\n').replace(
            ' x le 3 ge -1\n',' x le 3 ge -1\n x second 1\n'))
        _,reference=self.convert(LP,'linear-reference')
        self.assertIn('LP-only',self.read(path,reference,success=False).stderr)
        if INTERFACE=='python':
            r=json.loads(self.read(path).stdout)
            self.assertEqual((r['is_mip'],r['is_multiobjective'],r['objectives']),(1,1,2))
            sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'python'))
            from validate_real import compare_gurobi_qc
            from optbin import Model
            os.environ['OPTBIN_LIBRARY']=str(BUILD/'liboptbin.so')
            with Model(reference) as expected:
                with self.assertRaisesRegex(ValueError,'single-objective'):
                    compare_gurobi_qc(expected,path)

    def test_missing_library_and_source_fail_cleanly(self):
        if INTERFACE=='c':self.command(BUILD/'optb_gurobi_bench',self.work/'missing.so',self.work/'missing.mps',success=False)
        self.assertIn('gurobi',self.read(self.work/'missing.mps',success=False).stderr.lower())


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,default=BUILD)
    p.add_argument('--gurobi-library',default=os.environ.get('OPTBIN_GUROBI_LIBRARY'))
    p.add_argument('--interface',choices=['python','c'],default='python')
    args,extra=p.parse_known_args();BUILD=args.build.resolve();LIBRARY=args.gurobi_library;INTERFACE=args.interface
    if not LIBRARY:
        spec=importlib.util.find_spec('gurobipy')
        if spec and spec.origin:
            LIBRARY=next(Path(spec.origin).parent.rglob('libgurobi*.so'),None)
    unittest.main(argv=[__file__,*extra])
