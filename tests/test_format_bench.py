#!/usr/bin/env python3
"""Check the benchmark's numerical endpoint, gzip failures and cleanup."""
import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'build-gzip'


class FormatBenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.work=Path(self.tmp.name)
        self.env=dict(os.environ,TMPDIR=str(self.work))

    def tearDown(self):
        self.assertEqual(list(self.work.glob('optbin-gzip-bench-*')),[])
        self.tmp.cleanup()

    def run_tool(self,*args,success=True):
        p=subprocess.run([str(x) for x in args],capture_output=True,text=True,env=self.env)
        if success:
            self.assertEqual(p.returncode,0,p.stderr)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode,0)
        self.assertNotEqual(p.returncode,-11,'benchmark segfaulted')
        self.assertNotEqual(p.returncode,-6,'benchmark aborted')

    def converted(self,name):
        source=ROOT/'examples'/name
        if name=='lp.mps':
            source=self.work/name
            source.write_text('NAME LP\nROWS\n N obj\n L r\nCOLUMNS\n x obj 1 r 2\nRHS\n b r 3\nENDATA\n')
        binary=self.work/(name+'.optb')
        cmd=[BUILD/('cbf2optb' if name.endswith('.cbf') else 'mps2optb'),source,binary]
        if name.endswith('.mps'):cmd+=['--mps-dialect','gurobi']
        self.run_tool(*cmd)
        return source,binary

    def test_source_sink_matches_lp_qp_qcqp_conic(self):
        for name in ['lp.mps','qp.mps','qcqp.mps','socp.cbf','sdp.cbf']:
            with self.subTest(name=name):
                source,binary=self.converted(name)
                compressed=self.work/(name+'.gz')
                compressed.write_bytes(gzip.compress(source.read_bytes(),mtime=0))
                result=self.run_tool(BUILD/'optb_format_bench',
                    'cbf' if name.endswith('.cbf') else 'mps-gurobi',compressed,'optbin',binary)
                self.assertTrue(result['bitwise_reference_equal'])
                self.assertGreater(result['seconds'],0)

    def test_gzip_binary_paths_include_decompression(self):
        _,binary=self.converted('qp.mps')
        raw=self.work/'raw.h5'
        self.run_tool(BUILD/'optb_h5_export',binary,raw,'raw')
        for kind,path in [('hdf5-gzip',raw),('optbin-gzip',binary)]:
            compressed=path.with_suffix(path.suffix+'.gz')
            compressed.write_bytes(gzip.compress(path.read_bytes(),mtime=0))
            result=self.run_tool(BUILD/'optb_format_bench',kind,compressed,'optbin',binary)
            self.assertTrue(result['bitwise_reference_equal'])
            self.assertGreaterEqual(result['seconds'],result['gzip_to_temporary_seconds'])
            self.assertGreater(result['gzip_to_temporary_seconds'],0)

    def test_bad_gzip_crc_and_truncation_are_rejected(self):
        _,binary=self.converted('qp.mps')
        data=gzip.compress(binary.read_bytes(),mtime=0)
        bad=bytearray(data);bad[-8]^=1
        for i,payload in enumerate([bytes(bad),data[:-5]]):
            path=self.work/f'bad-{i}.gz';path.write_bytes(payload)
            self.run_tool(BUILD/'optb_format_bench','optbin-gzip',path,success=False)

    def test_source_mismatch_and_truncation_fail_without_crash(self):
        source,binary=self.converted('qp.mps')
        lp=self.work/'different.optb'
        self.run_tool(BUILD/'mps2optb',ROOT/'examples/qcqp.mps',lp,'--mps-dialect','gurobi')
        self.run_tool(BUILD/'optb_format_bench','mps-gurobi',source,'optbin',lp,success=False)
        short=self.work/'short.mps';short.write_text(source.read_text().replace('ENDATA',''))
        self.run_tool(BUILD/'optb_format_bench','mps-gurobi',short,success=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,default=BUILD)
    args,extra=p.parse_known_args();BUILD=args.build.resolve()
    unittest.main(argv=[__file__,*extra])
