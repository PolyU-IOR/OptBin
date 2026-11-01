"""Independent expected models and on-disk fixtures through the public C ABI."""
import ctypes as C
import gzip
import json
import math
import os
from pathlib import Path
import random
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from optbin import Model

BUILD = Path(os.environ.get('OPTBIN_BUILD', ROOT / 'build'))


def run(args, ok=True):
    p = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if (p.returncode == 0) != ok:
        raise AssertionError(f'{args}: {p.returncode}\n{p.stderr}')
    return p


def unpack(model, id):
    s = model.sections.get(id)
    if not s:
        return []
    return list(struct.unpack('<' + {1:'I',2:'Q',3:'d',4:'B'}[s['type']] * s['count'], model.raw(id)))


def raw_file(path, sections, *, profile=1, n=2, m=1, block_elements=65536):
    """An independent RAW-only writer; never calls either package converter."""
    sections = sorted(sections)
    blocks, dirs = [], []
    offset = 128 + 64 * len(sections)
    for id, type, values in sections:
        data = struct.pack('<' + {1:'I',2:'Q',3:'d',4:'B'}[type] * len(values), *values)
        bh = bytearray(64)
        if values:
            struct.pack_into('<4sIQQIIQQIII', bh, 0, b'BLK1', id, 0, len(values), 0, 0,
                             len(data), len(data), zlib.crc32(data), 0, 0)
            struct.pack_into('<I', bh, 60, zlib.crc32(bh[:60]))
            block = bytes(bh) + data + b'\0' * (-len(data) % 8)
        else:
            block = b''
        entry = bytearray(64)
        struct.pack_into('<IIQQQQQ', entry, 0, id, type, len(values), offset, len(block), bool(values), 1)
        struct.pack_into('<I', entry, 60, zlib.crc32(entry[:60]))
        dirs.append(bytes(entry)); blocks.append(block); offset += len(block)
    h = bytearray(128)
    struct.pack_into('<8sIIQQQQQIiQQdQ', h, 0, b'OPTBIN2\n', 2, 128, len(sections),
                     sum(bool(b) for b in blocks), offset, 128, 64*len(sections), profile, 1, n, m, 0., block_elements)
    struct.pack_into('<I', h, 124, zlib.crc32(h[:124]))
    path.write_bytes(h + b''.join(dirs) + b''.join(blocks))


MINIMAL = [(1,2,[0,1,2]),(2,1,[0,0]),(3,3,[1.,-1.]),(4,3,[0.,1.]),
           (5,3,[0.]),(6,3,[0.]),(7,3,[0.,0.]),(8,3,[math.inf,math.inf])]

QP = '''NAME qptest
OBJSENSE
 MIN
ROWS
 N obj
 E eq
 L qc
COLUMNS
 X obj 1 eq 1
 X qc -3
 Y obj -2 eq -1
 Y qc 4
RHS
 rhs obj -7 eq 2
 rhs qc 5
BOUNDS
 FR b X
 UP b Y 10
QUADOBJ
 X X 2
 X Y 0.5
 Y Y 4
ENDATA
'''

PSD = '''VER
3
OBJSENSE
MIN
PSDVAR
1
2
VAR
2 1
F 2
CON
1 1
L= 1
PSDCON
1
2
OBJFCOORD
2
0 0 0 1
0 0 1 0.5
OBJACOORD
1
1 -2
OBJBCOORD
3
FCOORD
1
0 0 1 0 2
ACOORD
2
0 1 -1
0 0 1
BCOORD
1
0 -1
HCOORD
2
0 0 0 0 1
0 1 1 0 -1
DCOORD
2
0 0 0 2
0 1 1 3
'''


class OptBinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, text, format='mps', args=(), name='model', gz=False, ok=True):
        source = self.root / (name + '.' + format + ('.gz' if gz else ''))
        if gz:
            with gzip.open(source, 'wt') as f: f.write(text)
        else:
            source.write_text(text)
        target = self.root / (name + '.optb')
        result = run([BUILD / (format+'2optb'), source, target, *args], ok=ok)
        if not ok:
            self.assertFalse(target.exists())
            self.assertFalse(list(self.root.glob('*.partial.*')))
            return result
        json.loads(result.stdout)
        run([BUILD/'optb_inspect', target, '--stream'])
        run([BUILD/'optb_inspect', target, '--load'])
        return target

    def test_lp_qp_and_legacy_exact(self):
        for name, text in [('qp', QP), ('lp', QP[:QP.index('QUADOBJ')]+'ENDATA\n')]:
            p = self.convert(text, name=name, args=['--block','2'], gz=True)
            source=self.root/(name+'.mps.gz'); old=self.root/(name+'.bqp')
            run([BUILD/'mps2bqp', source, old, '--block','2'])
            with Model(p) as a, Model(old) as b:
                for id in range(1,12): self.assertEqual(a.raw(id),b.raw(id))
                self.assertEqual(a.info['objective_offset'], 7.)
                self.assertEqual(unpack(a,5),[2.,-math.inf])
                self.assertEqual(unpack(a,6),[2.,5.])

    def test_qcqp_dialects_and_polynomial(self):
        terms='QCMATRIX qc\n X X 2\n X Y 3\n Y X 3\n Y Y 4\n'
        for dialect, scale in [('gurobi',1.),('mosek',1.)]:
            p=self.convert(QP.replace('ENDATA', terms+'ENDATA'),name=dialect,
                           args=['--mps-dialect',dialect,'--block','1'])
            with Model(p) as m:
                self.assertEqual(unpack(m,12),[1]); self.assertEqual(unpack(m,13),[0,3])
                self.assertEqual(unpack(m,14),[0,0,1]); self.assertEqual(unpack(m,15),[0,1,1])
                self.assertEqual(unpack(m,16),[2.,3.,4.]); self.assertEqual(unpack(m,17),[scale])
                x=[1.,2.]
                value=-3*x[0]+4*x[1]
                for i,j,v in zip(unpack(m,14),unpack(m,15),unpack(m,16)):
                    value+=scale*v*x[i]*x[j]*(1 if i==j else 2)
                self.assertEqual(value,5+scale*30)
        self.convert(QP.replace('ENDATA',terms+'ENDATA'),name='ambiguous',ok=False)
        bad=terms.replace(' Y X 3',' Y X 2')
        self.convert(QP.replace('ENDATA',bad+'ENDATA'),name='asymmetric',args=['--mps-dialect','gurobi'],ok=False)

    def test_qsection_multiple_quadratic_rows(self):
        text=QP.replace('QUADOBJ','QSECTION obj').replace('ENDATA',
            'QSECTION eq\n X X -1\nQSECTION qc\n X Y 2\n Y Y 3\nENDATA')
        p=self.convert(text,args=['--mps-dialect','mosek'])
        with Model(p) as m:
            self.assertEqual(unpack(m,12),[0,1]); self.assertEqual(unpack(m,13),[0,1,3])
            self.assertEqual(unpack(m,16),[-1,2,3]); self.assertEqual(unpack(m,17),[.5,.5])

    def test_zero_rows_negative_zero(self):
        text='NAME empty\nROWS\n N obj\nCOLUMNS\n X obj -0.0\nENDATA\n'
        p=self.convert(text)
        with Model(p) as m:
            self.assertEqual(m.info['rows'],0); self.assertEqual(unpack(m,1),[0,0])
            self.assertEqual(unpack(m,5),[])

    def test_unindented_free_mps(self):
        text='\n'.join(line.strip() for line in QP.splitlines())+'\n'
        p=self.convert(text)
        with Model(p) as m:
            self.assertEqual(unpack(m,11),[2.,.5,4.])

    def test_output_is_never_overwritten(self):
        p=self.convert(QP)
        original=p.read_bytes()
        run([BUILD/'mps2optb',self.root/'model.mps',p],ok=False)
        self.assertEqual(p.read_bytes(),original)
        self.assertFalse(list(self.root.glob('*.partial.*')))

    def test_encoding_ablations_are_lossless(self):
        for kind,text in [('mps',QP),('cbf',PSD)]:
            expected=None
            for mode in ['all','raw','values','indices']:
                for zstd in ['0','1']:
                    path=self.convert(text,format=kind,name=f'{kind}-{mode}-{zstd}',
                        args=['--encoding',mode,'--zstd',zstd,'--block','2'])
                    with Model(path) as model:
                        actual={i:model.raw(i) for i in model.sections}
                        if expected is None:expected=actual
                        self.assertEqual(actual,expected)
                    if mode=='raw':
                        data=path.read_bytes();count=struct.unpack_from('<Q',data,16)[0]
                        for k in range(count):
                            offset,length=struct.unpack_from('<QQ',data,128+64*k+16)
                            end=offset+length
                            while offset<end:
                                self.assertEqual(struct.unpack_from('<I',data,offset+24)[0],0)
                                size=struct.unpack_from('<Q',data,offset+40)[0]
                                offset+=64+size+(-size%8)
            self.convert(text,format=kind,name=f'{kind}-bad-encoding',
                         args=['--encoding','unknown'],ok=False)

    def test_cbf_psd_native_coefficients(self):
        p=self.convert(PSD,format='cbf',gz=True,args=['--block','1'])
        with Model(p) as m:
            self.assertEqual(m.info['profile'],2); self.assertEqual(m.info['objective_offset'],3.)
            expected={1:[0,1,2],2:[0,0],3:[1.,-1.],4:[0.,-2.],18:[-1.],
                      19:[0],20:[2],21:[0,0],22:[],23:[3],24:[1],25:[0,0],26:[],
                      27:[2],28:[2],29:[0,0],30:[0,0],31:[0,1],32:[1.,.5],
                      33:[0],34:[0],35:[0],36:[1],37:[2.],
                      38:[0,0],39:[0,1],40:[0,0],41:[0,1],42:[1.,-1.],
                      43:[0,0],44:[0,1],45:[0,1],46:[2.,3.]}
            for id,value in expected.items(): self.assertEqual(unpack(m,id),value, id)
            # Trace inner product: off-diagonal coefficient .5 contributes 1*X01.
            self.assertEqual(unpack(m,32)[0]*2+2*unpack(m,32)[1]*3,5.)

    def test_cbf_all_vector_cones_and_weights(self):
        domains=['F 1','L+ 1','L- 1','L= 1','Q 3','QR 3','EXP 3','EXP* 3','@0:POW 4','@0:POW* 3']
        dim=sum(int(x.split()[1]) for x in domains)
        text='VER\n3\nPOWCONES\n1 3\n3\n1\n2\n3\nPOW*CONES\n1 2\n2\n0.25\n0.75\nOBJSENSE\nMAX\nVAR\n'+f'{dim} 10\n'+'\n'.join(domains)+'\n'
        p=self.convert(text,format='cbf')
        with Model(p) as m:
            self.assertEqual(unpack(m,19),list(range(10))); self.assertEqual(unpack(m,22),[1,2,3,.25,.75])
            self.assertEqual(m.info['sense'],-1)
        for version in [1,2]:
            text=f'VER\n{version}\nOBJSENSE\nMIN\nVAR\n1 1\nF 1\n'
            self.convert(text,format='cbf',name=f'v{version}')

    def test_cbf_rejects_dropped_features_and_duplicates(self):
        for name,text in [
            ('int',PSD.replace('CON\n1 1','INT\n1\n0\nCON\n1 1',1)),
            ('change',PSD+'CHANGE\n'),('version',PSD.replace('VER\n3','VER\n4')),
            ('duplicate',PSD.replace('ACOORD\n2\n0 1 -1','ACOORD\n2\n0 0 -1')),
            ('mirror',PSD.replace('OBJFCOORD\n2','OBJFCOORD\n3').replace('0 0 1 0.5','0 0 1 0.5\n0 1 0 0.5')),
            ('dimensions',PSD.replace('F 2','F 3')),('unknown',PSD+'MYSTERY\n')]:
            self.convert(text,format='cbf',name=name,ok=False)

    def test_independent_raw_file_memory_limit_and_c_api(self):
        p=self.root/'independent.optb'; raw_file(p,MINIMAL)
        with Model(p) as m: self.assertEqual(unpack(m,3),[1,-1])
        with self.assertRaisesRegex(ValueError,'limit'): Model(p,decoded_limit=1)
        source=self.root/'consumer.c'; exe=self.root/'consumer'
        source.write_text('''#include "optbin.h"
int main(int argc,char **argv) { optb_model m={0}; char e[512];
if(argc!=2||optb_load(argv[1],&m,100000,e,sizeof e)) return 1;
const optb_section *s=optb_get(&m,OPTB_AX);
int ok=s&&s->count==2&&((double*)s->data)[1]==-1.; optb_free(&m); return !ok; }
''')
        run(['cc','-std=c11','-I'+str(ROOT/'include'),source,BUILD/'liboptbin.a','-lz','-ldl','-lpthread','-lm',*shlex.split(os.environ.get('OPTBIN_TEST_LDFLAGS','')),'-o',exe])
        run([exe,p])

    def test_limits_and_paths(self):
        p=self.root/'arguments.optb'; raw_file(p,MINIMAL)
        for limit in [-1,2**64,2**64+1]:
            with self.subTest(limit=limit),self.assertRaisesRegex(ValueError,'decoded_limit'):
                Model(p,decoded_limit=limit)
            run([BUILD/'optb_inspect',p,'--limit',limit],ok=False)
        for limit in [1.5,'1024',None]:
            with self.subTest(limit=limit),self.assertRaisesRegex(TypeError,'integer'):
                Model(p,decoded_limit=limit)
        class IndexLimit:
            def __index__(self):return 2**64-1
        for limit in [0,2**64-1,IndexLimit()]:
            with Model(p,decoded_limit=limit) as model:
                self.assertEqual(unpack(model,3),[1.,-1.])
                size=sum(s['count']*{1:4,2:8,3:8,4:1}[s['type']] for s in model.sections.values())
        with Model(p,decoded_limit=size):pass
        with self.assertRaisesRegex(ValueError,'limit'):Model(p,decoded_limit=size-1)
        for path in [str(p)+'\0suffix',os.fsencode(p)+b'\0suffix']:
            with self.assertRaisesRegex(ValueError,'null byte'):Model(path)
        model.close()
        with self.assertRaisesRegex(ValueError,'closed'):model.raw(3)

    def test_malformed_container_and_schema(self):
        p=self.root/'bad.optb'; raw_file(p,MINIMAL); original=p.read_bytes()
        for n in [0,8,100,len(original)-1]:
            p.write_bytes(original[:n])
            with self.assertRaises(ValueError): Model(p)
        for offset in [20,128+4,128+64*len(MINIMAL)+64]:
            data=bytearray(original); data[offset]^=1; p.write_bytes(data)
            with self.assertRaises(ValueError): Model(p)
        for sections in [MINIMAL+[(50,2,[1])],
                         [x if x[0]!=2 else (2,1,[0,9]) for x in MINIMAL],
                         [x if x[0]!=1 else (1,2,[0,2,1]) for x in MINIMAL],
                         [x if x[0]!=3 else (3,3,[math.nan,1]) for x in MINIMAL],
                         MINIMAL+[(12,2,[0]),(13,2,[0,1]),(14,2,[1]),(15,2,[0]),(16,3,[1]),(17,3,[1])]]:
            raw_file(p,sections)
            with self.assertRaises(ValueError): Model(p)

    def test_fuzz_corruption(self):
        p=self.root/'fuzz.optb'; raw_file(p,MINIMAL); original=p.read_bytes(); rng=random.Random(20261026)
        for _ in range(60):
            data=bytearray(original); index=rng.randrange(len(data)); data[index]^=1<<rng.randrange(8); p.write_bytes(data)
            with self.assertRaises(ValueError): Model(p)

    def test_v2_value_encodings_exact(self):
        cases=[[math.pi]*500,[-1.,1.]*250,list(range(-128,128)),list(range(1000,1200)),
               list(range(100000,100200)),[i*.25+.125 for i in range(200)],
               [math.sqrt(i+2) for i in range(200)],[-0.0]*200]
        for i,values in enumerate(cases):
            text='NAME codecs\nROWS\n N obj\n'+''.join(f' E R{k}\n' for k in range(len(values)))
            text+='COLUMNS\n X obj 0\n'+''.join(f' X R{k} {v!r}\n' for k,v in enumerate(values))+'ENDATA\n'
            p=self.convert(text,name=f'codec{i}',args=['--zstd','0'])
            with Model(p) as m: self.assertEqual(m.raw(3),struct.pack('<'+'d'*len(values),*values))


if __name__=='__main__': unittest.main(verbosity=2)
