"""Optional native C HDF5 frontends: exact models, checksums, budgets, and failures."""
import ctypes as C
import json
import math
import os
from pathlib import Path
import struct
import unittest
import zlib
import test_optbin as base
from optbin import Model
from optbin.reader import Native

BUILD=Path(os.environ.get('OPTBIN_NATIVE_BUILD',base.ROOT/'build-native'))


class NativeHdf5Tests(unittest.TestCase):
    tearDown=base.OptBinTests.tearDown
    convert=base.OptBinTests.convert

    def setUp(self):
        base.OptBinTests.setUp(self)
        try:import h5py,numpy
        except ImportError as e:self.skipTest(str(e))
        if not (BUILD/'liboptbin_hdf5.so').exists():self.skipTest('optional native HDF5 build not available')
        self.lib=C.CDLL(str(BUILD/'liboptbin_hdf5.so'))
        self.lib.optb_h5_load.argtypes=[C.c_char_p,C.POINTER(Native),C.c_uint64,C.c_char_p,C.c_size_t]
        self.lib.optb_h5_export.argtypes=[C.c_char_p,C.c_char_p,C.c_uint,C.c_char_p,C.c_size_t]
        self.lib.optb_free.argtypes=[C.POINTER(Native)]

    def export(self,p,layout,name='native'):
        h5=self.root/(name+'.h5');e=C.create_string_buffer(1024)
        self.assertEqual(self.lib.optb_h5_export(os.fsencode(p),os.fsencode(h5),layout,e,len(e)),0,e.value.decode())
        return h5

    def read(self,p,ok=True,limit=0):
        n=Native();e=C.create_string_buffer(1024)
        status=self.lib.optb_h5_load(os.fsencode(p),C.byref(n),limit,e,len(e))
        try:
            self.assertEqual(status==0,ok,e.value.decode())
            if not ok:
                self.assertFalse(n.section);self.assertEqual(n.info.sections,0);return
            return {n.section[k].id:C.string_at(n.section[k].data,n.section[k].count*{1:4,2:8,3:8,4:1}[n.section[k].type])
                    for k in range(n.info.sections)}
        finally:self.lib.optb_free(C.byref(n))

    def test_native_layouts_and_exact_comparison(self):
        qc=base.QP.replace('ENDATA','QCMATRIX qc\n X X 2\n Y Y 4\nENDATA')
        for kind,text in [('mps',qc),('cbf',base.PSD)]:
            p=self.convert(text,format=kind,name=kind,args=['--block','2']+(['--mps-dialect','gurobi'] if kind=='mps' else []))
            with Model(p) as m:expected={i:m.raw(i) for i in m.sections}
            for layout in range(3):
                h5=self.export(p,layout,f'{kind}-{layout}')
                self.assertEqual(self.read(h5),expected)
                self.read(h5,ok=False,limit=1)
                result=json.loads(base.run([BUILD/'optb_native_bench','hdf5',h5,'optbin',p]).stdout)
                self.assertTrue(result['bitwise_reference_equal'])
                self.assertEqual(len(result['numerical_sha256']),64)

    def test_native_payload_checks_and_metadata(self):
        import h5py
        p=self.convert(base.QP)
        for layout in range(3):
            h5=self.export(p,layout,f'corrupt-{layout}')
            with h5py.File(h5,'r+') as f:
                d=f['data/3'];index=64 if layout==0 else 0;d[index]=d[index]+1
            self.read(h5,ok=False)
        h5=self.export(p,0,'metadata')
        with h5py.File(h5,'r+') as f:
            data=bytearray(f['manifest'][...].tobytes());struct.pack_into('<Q',data,72,99)
            struct.pack_into('<I',data,124,zlib.crc32(data[:124]));f['manifest'][...]=list(data)
        self.read(h5,ok=False)

    def test_cache_spanning_raw_blocks_and_signed_zero(self):
        n=200000
        sections=[(1,2,[0,n]),(2,1,list(range(n))),(3,3,[math.sin(i) for i in range(n)]),
                  (4,3,[-0.0]),(5,3,[0.]*n),(6,3,[math.inf]*n),(7,3,[-math.inf]),(8,3,[math.inf])]
        p=self.root/'independent.optb';base.raw_file(p,sections,n=1,m=n,block_elements=262144)
        with Model(p) as model:
            expected={i:model.raw(i) for i in model.sections}
            self.assertEqual(model.raw(4),struct.pack('<d',-0.0))
        for layout in range(3):
            h5=self.export(p,layout,f'large-block-{layout}')
            self.assertEqual(self.read(h5),expected)

    def test_atomic_export_and_bqp_repack(self):
        p=self.convert(base.QP);h5=self.export(p,0);before=h5.read_bytes();e=C.create_string_buffer(1024)
        self.assertNotEqual(self.lib.optb_h5_export(os.fsencode(p),os.fsencode(h5),0,e,len(e)),0)
        self.assertEqual(h5.read_bytes(),before);self.assertFalse(list(self.root.glob('*.partial.*')))
        old=self.root/'legacy.bqp';new=self.root/'repacked.optb'
        base.run([base.BUILD/'mps2bqp',self.root/'model.mps',old,'--block','2'])
        base.run([base.BUILD/'bqp2optb',old,new])
        with Model(old) as a,Model(new) as b:
            for id in range(1,12):self.assertEqual(a.raw(id),b.raw(id))
        base.run([base.BUILD/'bqp2optb',old,new],ok=False)
        data=bytearray(old.read_bytes());data[200]^=1;old.write_bytes(data)
        base.run([base.BUILD/'bqp2optb',old,self.root/'bad.optb'],ok=False)
        self.assertFalse((self.root/'bad.optb').exists());self.assertFalse(list(self.root.glob('*.partial.*')))


if __name__=='__main__':unittest.main(verbosity=2)
