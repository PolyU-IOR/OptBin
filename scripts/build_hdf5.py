#!/usr/bin/env python3
"""Build optional, pinned HDF5 C dependencies in an isolated local prefix."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
URL='https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.6/hdf5-1.14.6.tar.gz'
SHA256='e4defbac30f50d64e1556374aa49e574417c9e72c6b1de7a4ff88c4b1bea6e9b'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--jobs',type=int,default=8)
    p.add_argument('--work',type=Path,default=ROOT/'data/deps');args=p.parse_args()
    args.work.mkdir(parents=True,exist_ok=True)
    archive=args.work/'hdf5-1.14.6.tar.gz';prefix=(args.work/'hdf5-install').resolve()
    if not archive.exists():
        with urllib.request.urlopen(URL,timeout=60) as r,archive.open('xb') as f:
            while chunk:=r.read(1024*1024):f.write(chunk)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=SHA256:raise ValueError('HDF5 source SHA256 mismatch')
    source=args.work/'hdf5-1.14.6';build=args.work/'hdf5-build';log=args.work/'hdf5-build.log'
    if not source.exists():
        with tarfile.open(archive) as tar:
            for member in tar:
                parts=Path(member.name).parts
                if not parts or parts[0]!='hdf5-1.14.6' or '..' in parts:
                    raise ValueError('unexpected source archive path')
        subprocess.run(['tar','--no-same-owner','-xzf',str(archive),'-C',str(args.work)],check=True)
    command=['cmake','-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
        '-DCMAKE_INSTALL_PREFIX='+str(prefix),'-DBUILD_SHARED_LIBS=OFF','-DBUILD_TESTING=OFF',
        '-DHDF5_BUILD_TOOLS=OFF','-DHDF5_BUILD_EXAMPLES=OFF','-DHDF5_BUILD_HL_LIB=OFF',
        '-DHDF5_BUILD_CPP_LIB=OFF','-DHDF5_BUILD_FORTRAN=OFF','-DHDF5_ENABLE_SZIP_SUPPORT=OFF',
        '-DHDF5_ENABLE_Z_LIB_SUPPORT=ON','-DHDF5_ENABLE_ROS3_VFD=OFF']
    if not (prefix/'include/hdf5.h').exists():
        with log.open('w') as f:
            for cmd in [command,['cmake','--build',str(build),'-j',str(args.jobs)],['cmake','--install',str(build)]]:
                completed=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
                if completed.returncode:raise RuntimeError(log.read_text()[-5000:])
    (args.work/'hdf5-provenance.json').write_text(json.dumps({'url':URL,'sha256':SHA256,
        'version':'1.14.6','configure':command,'prefix':str(prefix)},indent=2)+'\n')
    print(prefix,flush=True)


if __name__=='__main__':main()
