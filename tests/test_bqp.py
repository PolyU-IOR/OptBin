#!/usr/bin/env python3
"""Independent expected-array tests through the public C ABI; no Python deps."""
import ctypes as C
import gzip
import json
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get('BQP_BUILD', ROOT / 'build'))
CONVERT = BUILD / 'mps2bqp'
INSPECT = BUILD / 'bqp_inspect'

class Info(C.Structure):
    _fields_ = [(x, C.c_uint64) for x in
                ('rows', 'cols', 'nnz', 'qnnz', 'blocks', 'file_bytes', 'block_elements')] + [
        ('sense', C.c_int32), ('a_index_bytes', C.c_uint32),
        ('q_index_bytes', C.c_uint32), ('objective_offset', C.c_double)]

class Model(C.Structure):
    _fields_ = [('info', Info), ('device', C.c_int), ('array', C.c_void_p * 12)]

lib = C.CDLL(str(BUILD / 'libbqp.so'))
lib.bqp_load.argtypes = [C.c_char_p, C.POINTER(Model), C.c_uint64, C.c_char_p, C.c_size_t]
lib.bqp_load.restype = C.c_int
lib.bqp_free.argtypes = [C.POINTER(Model)]
lib.bqp_array_count.argtypes = [C.POINTER(Info), C.c_uint]
lib.bqp_array_count.restype = C.c_uint64
lib.bqp_element_bytes.argtypes = [C.POINTER(Info), C.c_uint]
lib.bqp_element_bytes.restype = C.c_uint

def call(args, ok=True):
    p = subprocess.run([str(x) for x in args], text=True, capture_output=True)
    if ok and p.returncode:
        raise AssertionError(p.stderr)
    if not ok and not p.returncode:
        raise AssertionError('unexpected success: ' + str(args))
    return p

def load(path):
    m, error = Model(), C.create_string_buffer(512)
    rc = lib.bqp_load(str(path).encode(), C.byref(m), 0, error, len(error))
    if rc:
        raise AssertionError(error.value.decode())
    try:
        info = {name: getattr(m.info, name) for name, _ in Info._fields_}
        arrays, raw = {}, {}
        for a in range(1, 12):
            n = lib.bqp_array_count(C.byref(m.info), a)
            w = lib.bqp_element_bytes(C.byref(m.info), a)
            raw[a] = C.string_at(m.array[a], n*w) if n else b''
            code = ('I' if w == 4 else 'Q') if a in (1, 2, 9, 10) else 'd'
            arrays[a] = list(struct.unpack('<' + str(n) + code, raw[a]))
        return info, arrays, raw
    finally:
        lib.bqp_free(C.byref(m))

def encodings(path, array):
    b = path.read_bytes()
    pos, result = 128, []
    while pos < len(b):
        a = struct.unpack_from('<I', b, pos+4)[0]
        enc = struct.unpack_from('<I', b, pos+24)[0]
        size = struct.unpack_from('<Q', b, pos+40)[0]
        if a == array:
            result.append(enc)
        pos += 64 + size + (-size % 8)
    return result

FIXTURE = '''NAME fixture
OBJSENSE
 MAX
OBJNAME COST
ROWS
 N COST
 E eq
 L le
 G ge
 E ep
 E en
 N free
COLUMNS
 X COST 1.25 le 2.5
 X eq 1
 X eq 2 COST .75
 X free -0.0
 Y COST -3 eq -1
 Y ge 4 ep 1
 Y en 2
 Z COST 0
RHS
 RHS1 COST -7 eq 6
 RHS1 le 8 ge 1
 RHS1 ep 10 en 20
 RHS1 free 9
RANGES
 RNG le 3 ge -4
 RNG ep 2 en -3
BOUNDS
 FR B X
 LO B Y -2
 UP B Y 5
 FX B Z 4
QUADOBJ
 X X 2
 X Y 0.5
 Y Y 4
 Z Z 0
ENDATA
'''

class BqpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, text, block=65536, compress=1, gz=False, name='model'):
        source = self.root / (name + ('.mps.gz' if gz else '.mps'))
        if gz:
            with gzip.open(source, 'wt') as f:
                f.write(text)
        else:
            source.write_text(text)
        target = self.root / (name + '.bqp')
        stats = json.loads(call([CONVERT, source, target, '--block', block, '--zstd', compress]).stdout)
        scan = json.loads(call([INSPECT, target, '--stream']).stdout)
        cpu = json.loads(call([INSPECT, target, '--load']).stdout)
        self.assertEqual(stats['array_crc32'], scan['array_crc32'])
        self.assertEqual(stats['array_crc32'], cpu['array_crc32'])
        return target, stats

    def test_semantics_and_qp(self):
        p, _ = self.convert(FIXTURE, block=2, gz=True)
        h, a, raw = load(p)
        self.assertEqual((h['rows'], h['cols'], h['nnz'], h['qnnz'], h['sense'], h['objective_offset']),
                         (6, 3, 7, 4, -1, 7))
        expected = {1:[0,3,7,7], 2:[0,1,5,0,2,3,4], 3:[3,2.5,-0.0,-1,4,1,2],
                    4:[2,-3,0], 5:[6,5,1,10,17,-math.inf], 6:[6,8,5,12,20,math.inf],
                    7:[-math.inf,-2,4], 8:[math.inf,5,4], 9:[0,1,3,4], 10:[0,0,1,2], 11:[2,.5,4,0]}
        self.assertEqual(a, expected)
        self.assertEqual(raw[3][16:24], struct.pack('<d', -0.0))
        # QP convention: x=(1,2,3), c'x=-4; .5*x'Q*x=10; c0=7 -> 13.
        x = [1, 2, 3]
        value = h['objective_offset'] + sum(ci*xi for ci, xi in zip(a[4], x))
        for j in range(3):
            for k in range(a[9][j], a[9][j+1]):
                i, q = a[10][k], a[11][k]
                value += q*x[i]*x[j]*(.5 if i == j else 1)
        self.assertEqual(value, 13)
        full = FIXTURE.replace('QUADOBJ', 'QMATRIX').replace(' X Y 0.5', ' X Y 0.5\n Y X 0.5')
        other, _ = self.convert(full, name='full')
        self.assertEqual(load(other)[2], raw)

    def test_all_value_encodings_and_exactness(self):
        cases = [(1, [math.pi]*500), (6, [1.0, math.nextafter(1.0, 0.0)]*250),
                 (2, list(range(-128, 128))), (3, list(range(1000, 1200))),
                 (4, list(range(100000, 100200))), (5, [i*.25+.125 for i in range(200)]),
                 (0, [math.sqrt(i+2) for i in range(200)]),
                 (6, [-1.0, 1.0]*250), (1, [-0.0]*200)]
        for k, (encoding, values) in enumerate(cases):
            text = 'NAME codec\nROWS\n N OBJ\n' + ''.join(f' E R{i+1}\n' for i in range(len(values)))
            text += 'COLUMNS\n X OBJ 0\n' + ''.join(f' X R{i+1} {v!r}\n' for i,v in enumerate(values)) + 'ENDATA\n'
            path, _ = self.convert(text, compress=0, name=f'codec{k}')
            self.assertEqual(encodings(path, 3), [encoding])
            self.assertEqual(load(path)[2][3], struct.pack('<' + str(len(values)) + 'd', *values))

    def test_random_sparse_models(self):
        rng = random.Random(20261019)
        for case in range(20):
            m, n = rng.randrange(1, 30), rng.randrange(1, 25)
            ap, ai, av, costs = [0], [], [], []
            text = 'NAME random\nROWS\n N OBJ\n' + ''.join(f' E R{i+1}\n' for i in range(m)) + 'COLUMNS\n'
            for j in range(n):
                cost = rng.uniform(-10, 10); costs.append(cost)
                text += f' C{j+1} OBJ {cost!r}\n'
                entries = [(r, rng.choice([-1., 1., math.pi, rng.uniform(-10,10)]))
                           for r in range(m) if rng.random() < .4]
                rng.shuffle(entries)
                for r,v in entries:
                    text += f' C{j+1} R{r+1} {v!r}\n'
                for r,v in sorted(entries): ai.append(r); av.append(v)
                ap.append(len(ai))
            p, _ = self.convert(text+'ENDATA\n', block=rng.randrange(1, 30), name=f'random{case}')
            _, a, _ = load(p)
            self.assertEqual([a[i] for i in (1,2,3,4)], [ap,ai,av,costs])

    def test_invalid_inputs_and_atomic_output(self):
        bad = [FIXTURE.replace('ENDATA', ''), FIXTURE.replace(' X eq 1', ' X missing 1'),
               FIXTURE.replace(' X eq 1', " MARK 'MARKER' 'INTORG'"),
               FIXTURE.replace(' FR B X', ' BV B X'),
               FIXTURE.replace('RHS1 le', 'RHS2 le'),
               FIXTURE.replace('QUADOBJ', 'QMATRIX'),
               FIXTURE.replace(' X eq 1', ' X eq nan')]
        for i, text in enumerate(bad):
            source, target = self.root/f'bad{i}.mps', self.root/f'bad{i}.bqp'
            source.write_text(text)
            call([CONVERT, source, target], ok=False)
            self.assertFalse(target.exists())
            self.assertFalse(list(self.root.glob(target.name+'.partial.*')))
        p, _ = self.convert(FIXTURE)
        before = p.read_bytes()
        call([CONVERT, self.root/'model.mps', p], ok=False)
        self.assertEqual(p.read_bytes(), before)

    def test_corruption_truncation_and_memory_limit(self):
        p, _ = self.convert(FIXTURE, compress=0)
        original = p.read_bytes()
        for i, offset in enumerate([0, 16, 128+4, 128+64, len(original)-1]):
            damaged = bytearray(original); damaged[offset] ^= 127
            q = self.root/f'corrupt{i}.bqp'; q.write_bytes(damaged)
            call([INSPECT, q], ok=False)
        q = self.root/'truncated.bqp'; q.write_bytes(original[:-8])
        call([INSPECT, q], ok=False)
        model, error = Model(), C.create_string_buffer(512)
        self.assertNotEqual(lib.bqp_load(str(p).encode(), C.byref(model), 1, error, 512), 0)
        self.assertIn(b'memory limit', error.value)

    def test_blank_column_continuation_and_negative_upper(self):
        text = 'NAME small\nROWS\n N OBJ\n L R1\nCOLUMNS\n X OBJ 1\n   R1 2D0\nBOUNDS\n UP B X -2\nENDATA\n'
        p, _ = self.convert(text)
        _, a, _ = load(p)
        self.assertEqual(a[3], [2.])
        self.assertEqual(a[7], [-math.inf]); self.assertEqual(a[8], [-2.])

    def test_independent_64bit_file_and_malformed_varint(self):
        # Build raw records independently of the C++ converter, exercising the
        # 64-bit-index C path even though this tiny model also fits uint32.
        values = {1:('Q',[0,1,2]), 2:('Q',[1,0]), 3:('d',[1,-1]),
                  4:('d',[0,1]), 5:('d',[0,0]), 6:('d',[math.inf,math.inf]),
                  7:('d',[0,0]), 8:('d',[math.inf,math.inf])}
        records = bytearray()
        for a, (code, data) in values.items():
            payload = struct.pack('<'+str(len(data))+code, *data)
            h = bytearray(64)
            struct.pack_into('<IIQQIIQQIII', h, 0, 0x314b4c42, a, 0, len(data),
                             0, 0, len(payload), len(payload), zlib.crc32(payload), 0, 0)
            struct.pack_into('<I', h, 60, zlib.crc32(h[:60]))
            records.extend(h+payload+b'\0'*(-len(payload)%8))
        h = bytearray(128)
        h[:8] = b'BQP1\r\n\x1a\n'
        struct.pack_into('<IIQQQQQQiIII', h, 8, 1, 128, 2, 2, 2, 0,
                         8, 128+len(records), 1, 8, 8, 1)
        struct.pack_into('<Q', h, 88, 65536)
        struct.pack_into('<I', h, 124, zlib.crc32(h[:124]))
        p = self.root/'independent.bqp'; p.write_bytes(h+records)
        info, arrays, _ = load(p)
        self.assertEqual(info['a_index_bytes'], 8)
        for a, (_, data) in values.items(): self.assertEqual(arrays[a], data)
        # Replace AI with a syntactically valid DELTA block that underflows.
        bad = bytearray(h+records)
        offset = 128+64+24
        struct.pack_into('<I', bad, offset+24, 7)
        struct.pack_into('<QQ', bad, offset+32, 9, 9)
        bad[offset+64:offset+80] = b'\0'*8+b'\x01'+b'\0'*7
        struct.pack_into('<I', bad, offset+60, zlib.crc32(bad[offset:offset+60]))
        p.write_bytes(bad)
        self.assertIn('negative decoded index', call([INSPECT,p], ok=False).stderr)

if __name__ == '__main__':
    unittest.main(verbosity=2)
