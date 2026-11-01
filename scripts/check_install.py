#!/usr/bin/env python3
"""Check real source/wheel artifacts using fresh CMake builds and Python venvs."""
import argparse
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdist', required=True, type=Path)
    parser.add_argument('--wheel', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    sdist, wheel, output = args.sdist.resolve(), args.wheel.resolve(), args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    logs = output.parent / (output.stem + '-logs'); logs.mkdir(exist_ok=True)
    environment = dict(os.environ)
    for name in ['PYTHONPATH', 'PYTHONHOME', 'OPTBIN_LIBRARY', 'OPTBIN_BUILD',
                 'BQP_BUILD', 'LD_LIBRARY_PATH', 'LD_PRELOAD']:
        environment.pop(name, None)
    environment['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    commands = []

    def run(name, command, cwd, *, success=True):
        command = [str(x) for x in command]
        start = time.monotonic()
        result = subprocess.run(command, cwd=cwd, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (logs / (name + '.txt')).write_text(result.stdout)
        commands.append({'step': name, 'argv': command, 'exit_code': result.returncode,
                         'seconds': time.monotonic() - start})
        if (result.returncode == 0) != success:
            raise RuntimeError(f'{name}: unexpected exit code {result.returncode}; see {logs / (name + ".txt")}\n{result.stdout[-3000:]}')
        return result.stdout

    with tempfile.TemporaryDirectory(prefix='optbin-install-') as directory:
        work = Path(directory)
        extracted = work / 'source'; extracted.mkdir()
        with tarfile.open(sdist) as archive:
            members = archive.getmembers()
            for member in members:
                target = extracted / member.name
                if (not target.resolve().is_relative_to(extracted) or
                        not (member.isfile() or member.isdir())):
                    raise ValueError('unsafe source-archive member: ' + member.name)
            archive.extractall(extracted)
        roots = list(extracted.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise ValueError('source archive must contain one root directory')
        source = roots[0]
        metadata = Parser().parsestr((source / 'PKG-INFO').read_text())
        version = metadata['Version']
        assert f'OPTBIN_VERSION "{version}"' in (source / 'include/optbin.h').read_text()
        assert f'__version__ = "{version}"' in (source / 'python/optbin/__init__.py').read_text()
        assert f'OPTBIN_RELEASE_VERSION "{version}"' in (source / 'CMakeLists.txt').read_text()
        assert '-cp310-abi3-' in wheel.name
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            assert any(n.endswith('/_core.abi3.so') for n in names)
            wheel_metadata = next(n for n in names if n.endswith('.dist-info/METADATA'))
            assert Parser().parsestr(archive.read(wheel_metadata).decode())['Version'] == version
        build, prefix = work / 'cmake', work / 'prefix'
        run('cmake-configure', ['cmake', '-S', source, '-B', build,
            '-DCMAKE_BUILD_TYPE=Release', '-DBUILD_SHARED_LIBS=ON', '-DBUILD_TESTING=ON'], work)
        run('cmake-build', ['cmake', '--build', build, '-j', args.jobs], work)
        run('cmake-ctest', ['ctest', '--test-dir', build, '--output-on-failure'], work)
        run('cmake-install', ['cmake', '--install', build, '--prefix', prefix], work)
        relocated = work / 'relocated'; prefix.rename(relocated)
        build.rename(work / 'unavailable-build')
        consumer = work / 'consumer'
        shutil.copytree(source / 'examples', consumer / 'examples')
        consumer_build = work / 'consumer-build'
        run('external-consumer-configure', ['cmake', '-S', consumer / 'examples/consumer',
            '-B', consumer_build, '-DCMAKE_PREFIX_PATH=' + str(relocated)], work)
        run('external-consumer-build', ['cmake', '--build', consumer_build, '-j', args.jobs], work)
        run('installed-cli-checks', [sys.executable, source / 'tests/smoke_cli.py',
            '--bin-dir', relocated / 'bin', '--examples', source / 'examples',
            '--consumer', consumer_build / 'consumer'], work)
        model = work / 'qp.optb'
        run('installed-conversion', [relocated / 'bin/mps2optb', source / 'examples/qp.mps', model], work)
        for kind, package in [('wheel', wheel), ('sdist', sdist)]:
            venv = work / ('venv-' + kind)
            run(kind + '-venv', [sys.executable, '-m', 'venv', venv], work)
            python = venv / 'bin/python'
            options = ['--no-index', '--no-deps'] if kind == 'wheel' else ['--no-deps', '--no-cache-dir']
            run(kind + '-install', [python, '-m', 'pip', 'install', *options, package], work)
            run(kind + '-python-checks', [python, '-I', source / 'tests/smoke_python.py', model, version], work)
            actual_version = run(kind + '-cli-version', [venv / 'bin/optbin-info', '--version'], work).strip()
            assert actual_version == version, actual_version
            info = json.loads(run(kind + '-cli-load', [venv / 'bin/optbin-info', model], work))
            assert info['rows'] == info['cols'] == 2 and info['validation'] == 'full-model'
            run(kind + '-cli-budget', [venv / 'bin/optbin-info', model, '--limit', '1'], work, success=False)
            run(kind + '-cli-invalid', [venv / 'bin/optbin-info', work / 'missing.optb'], work, success=False)
            run(kind + '-cli-negative-limit', [venv / 'bin/optbin-info', model, '--limit', '-1'], work, success=False)
            run(kind + '-pip-check', [python, '-m', 'pip', 'check'], work)
    report = {'status': 'pass', 'version': version, 'platform': platform.platform(),
              'python': sys.version, 'artifacts_sha256': {
                  p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [sdist, wheel]},
              'checks': ['source-distribution CMake build and CTest', 'relocated native installation',
                         'external find_package C consumer', 'four model-class CLI checks',
                         'fresh wheel and source-distribution installs', 'bundled reader without source checkout',
                         'known numerical arrays and byte budgets', 'CLI version/errors and pip check'],
              'commands': commands,
              'limits': ['Local host verification; hosted CI execution is separate',
                         'Linux platform wheel; no manylinux portability claim',
                         'Optional solver SDKs and large data are not installed by this check']}
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    main()
