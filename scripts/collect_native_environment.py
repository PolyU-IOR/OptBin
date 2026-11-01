#!/usr/bin/env python3
"""Record native runtime/compiler/source provenance without reading model payloads."""
import argparse
import ctypes as C
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,default=ROOT/'build-native');p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    versions={}
    for name,function in [('z','zlibVersion'),('zstd','ZSTD_versionString')]:
        library=ctypes.util.find_library(name)
        if library:
            lib=C.CDLL(library);query=getattr(lib,function);query.restype=C.c_char_p
            versions[name]={'library':library,'version':query().decode()}
    lib=C.CDLL(str(args.build/'liboptbin_hdf5.so'));lib.optb_h5_version.restype=C.c_char_p
    versions['hdf5']=lib.optb_h5_version().decode()
    settings=ROOT/'data/deps/hdf5-install/lib/libhdf5.settings'
    files=[*sorted((ROOT/'src').glob('*.c')),*sorted((ROOT/'src').glob('*.cpp')),
           *sorted((ROOT/'src').glob('*.h')),*sorted((ROOT/'src').glob('*.hpp')),
           *sorted((ROOT/'include').glob('*.h')),ROOT/'CMakeLists.txt',ROOT/'Makefile']
    report={'platform':platform.platform(),'hostname':platform.node(),'python':platform.python_version(),
        'memory_info':{line.split(':')[0]:line.split(':',1)[1].strip() for line in Path('/proc/meminfo').read_text().splitlines()
                       if line.startswith(('MemTotal:','MemAvailable:'))},
        'cpu_affinity':sorted(os.sched_getaffinity(0)),'runtime_versions':versions,
        'compiler':subprocess.run(['cc','--version'],capture_output=True,text=True).stdout.splitlines()[0],
        'binary_sha256':{n:sha(args.build/n) for n in ['optb_native_bench','optb_h5_export','bqp2optb','liboptbin.so','liboptbin_hdf5.so']},
        'source_sha256':{str(f.relative_to(ROOT)):sha(f) for f in files},
        'hdf5_settings':settings.read_text() if settings.exists() else None}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(args.output)


if __name__=='__main__':main()
