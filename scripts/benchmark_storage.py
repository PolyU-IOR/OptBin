#!/usr/bin/env python3
"""Same-array CPU baseline including HDF5 with identical OptBin section codecs.

This is CPU read/decode/array-validation only. Whole-file buffers and Python
bindings make it a small/medium-model harness, not a large I/O benchmark.
"""
import argparse
import ctypes as C
import ctypes.util
import json
import platform
import random
from pathlib import Path
import statistics
import sys
import time
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))


def main():
    import numpy as np
    import h5py
    from optbin import Model
    from optbin.reader import Info, Native, Section, library
    from optbin.hdf5 import write_encoded,EncodedModel
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--repeats',type=int,default=5)
    args=p.parse_args()
    if args.repeats<1: p.error('repeats must be positive')
    args.output.mkdir(parents=True,exist_ok=True)
    lib=library()
    lib.optb_validate.argtypes=[C.POINTER(Native),C.c_char_p,C.c_size_t]
    lib.optb_validate.restype=C.c_int
    with Model(args.input) as model:
        info=model.info.copy(); metadata=model.sections
        arrays={i:model.numpy(i) for i in metadata}
    manifest={'info':info,'sections':metadata,
              'crc32':{i:zlib.crc32(a.tobytes()) for i,a in arrays.items()}}
    manifest_bytes=json.dumps(manifest,sort_keys=True).encode()
    (args.output/'manifest.json').write_bytes(manifest_bytes)
    encoded_h5=args.output/'encoded.h5'
    if encoded_h5.exists():encoded_h5.unlink()
    write_encoded(args.input,encoded_h5)
    raw=b''.join(a.tobytes() for a in arrays.values())
    (args.output/'arrays.raw').write_bytes(raw)
    z=C.CDLL(ctypes.util.find_library('zstd'))
    z.ZSTD_compressBound.argtypes=[C.c_size_t];z.ZSTD_compressBound.restype=C.c_size_t
    z.ZSTD_compress.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t,C.c_int];z.ZSTD_compress.restype=C.c_size_t
    z.ZSTD_decompress.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t];z.ZSTD_decompress.restype=C.c_size_t
    z.ZSTD_isError.argtypes=[C.c_size_t];z.ZSTD_isError.restype=C.c_uint
    z.ZSTD_versionString.restype=C.c_char_p
    out=C.create_string_buffer(z.ZSTD_compressBound(len(raw)))
    size=z.ZSTD_compress(out,len(out),raw,len(raw),1)
    if z.ZSTD_isError(size): raise RuntimeError('compression failed')
    (args.output/'arrays.raw.zst').write_bytes(out.raw[:size])
    for layout in ['contiguous','gzip-shuffle']:
        with h5py.File(args.output/(layout+'.h5'),'w') as f:
            f.attrs['manifest']=manifest_bytes.decode()
            for i,a in arrays.items():
                opts={} if layout=='contiguous' or not len(a) else {'chunks':(min(len(a),65536),),'compression':'gzip','compression_opts':1,'shuffle':True}
                f.create_dataset(str(i),data=a,**opts)

    def validate(result):
        sections=(Section*len(metadata))()
        native=Native();native.info=Info(**info);native.section=sections
        for k,(i,s) in enumerate(metadata.items()):
            a=result[i]
            if zlib.crc32(a.tobytes())!=manifest['crc32'][i]: raise AssertionError('decoded checksum mismatch')
            sections[k]=Section(i,s['type'],s['count'],0,0,0,1,a.ctypes.data)
        error=C.create_string_buffer(512)
        if lib.optb_validate(C.byref(native),error,len(error)): raise AssertionError(error.value.decode())

    def load_bytes(data):
        result={};pos=0
        for i,a in arrays.items():
            result[i]=np.frombuffer(data,dtype=a.dtype,count=len(a),offset=pos);pos+=a.nbytes
        if pos!=len(data): raise AssertionError('raw file length mismatch')
        validate(result)
        return result,None

    def load_zstd():
        data=(args.output/'arrays.raw.zst').read_bytes();output=C.create_string_buffer(len(raw))
        size=z.ZSTD_decompress(output,len(output),data,len(data))
        if z.ZSTD_isError(size) or size!=len(raw): raise AssertionError('decompression failed')
        return load_bytes(output.raw)

    def load_h5(layout):
        with h5py.File(args.output/(layout+'.h5'),'r') as f: result={i:f[str(i)][...] for i in metadata}
        validate(result)
        return result,None

    def load_optbin():
        owner=Model(args.input)
        return {i:owner.numpy(i,copy=False) for i in metadata},owner

    def load_encoded_h5():
        owner=EncodedModel(encoded_h5)
        return {i:owner.numpy(i,copy=False) for i in metadata},owner

    tests=[('optbin',args.input.stat().st_size,load_optbin),
           ('raw',(args.output/'arrays.raw').stat().st_size+len(manifest_bytes),lambda:load_bytes((args.output/'arrays.raw').read_bytes())),
           ('raw-zstd',(args.output/'arrays.raw.zst').stat().st_size+len(manifest_bytes),load_zstd),
           ('hdf5-contiguous',(args.output/'contiguous.h5').stat().st_size,lambda:load_h5('contiguous')),
           ('hdf5-gzip-shuffle',(args.output/'gzip-shuffle.h5').stat().st_size,lambda:load_h5('gzip-shuffle')),
           ('hdf5-identical-optbin-codecs',encoded_h5.stat().st_size,load_encoded_h5)]
    times={name:[] for name,_,_ in tests};order=[];rng=random.Random(20261026)
    def measure(name,load):
        start=time.perf_counter();result,owner=load();elapsed=time.perf_counter()-start
        try:
            for i,a in arrays.items():
                if result[i].tobytes()!=a.tobytes(): raise AssertionError(f'array mismatch: {name}, {i}')
        finally:
            result.clear()
            if owner:owner.close()
        return elapsed
    for name,_,load in tests:measure(name,load)  # Unmeasured warmup, all paths.
    for _ in range(args.repeats):
        batch=list(tests);rng.shuffle(batch);order.append([t[0] for t in batch])
        for name,_,load in batch:times[name].append(measure(name,load))
    results=[{'format':name,'bytes':file_bytes,'read_decode_validate_seconds':times[name],
              'median_seconds':statistics.median(times[name]),'bitwise_equal':True}
             for name,file_bytes,_ in tests]
    report={'scope':'CPU arrays only; no layout conversion, solver setup or solve',
            'cache':'Warm: one unmeasured load per format; shuffled measurement rounds; no cache flush',
            'measurement_order':order,
            'limitations':['Python/HDF5 vs C reader overhead differs','Whole-file buffers; no separate RSS measurement',
                           'Baseline manifest metadata is cached; OptBin rereads its directory',
                           'Encoded HDF5 uses the same C codecs, with Python frontend overhead',
                           'gzip level 1 and Zstd level 1 are different codecs'],
            'input':str(args.input.resolve()),'decoded_bytes':len(raw),
            'environment':{'platform':platform.platform(),'python':platform.python_version(),
                           'numpy':np.__version__,'h5py':h5py.__version__,
                           'hdf5':h5py.version.hdf5_version,'zstd':z.ZSTD_versionString().decode()},'results':results}
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
