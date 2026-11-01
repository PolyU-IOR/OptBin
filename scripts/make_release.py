#!/usr/bin/env python3
"""Build a verified local release bundle from a clean committed Git snapshot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='new output directory (must not exist)')
    args = parser.parse_args()

    def git(*arguments):
        return subprocess.check_output(['git', *arguments], cwd=ROOT, text=True).strip()

    revision = git('rev-parse', 'HEAD')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Commit source changes before building a release; caches and outputs must be ignored')
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / 'python/optbin/__init__.py').read_text())[1]
    output = (args.output or ROOT / 'output/releases' / ('optbin-' + version)).resolve()
    bundle = output.parent / (output.name + '-bundle.tar.gz')
    if bundle.exists():
        raise FileExistsError(bundle)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    environment = dict(os.environ)
    environment['SOURCE_DATE_EPOCH'] = git('show', '-s', '--format=%ct', 'HEAD')
    environment['FORCE_SOURCE_DATE'] = '1'

    def run(name, command, cwd):
        with (output / (name + '.log')).open('w') as log:
            result = subprocess.run([str(x) for x in command], cwd=cwd, env=environment,
                                    stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'{name} failed; see {output / (name + ".log")}')

    with tempfile.TemporaryDirectory(prefix='optbin-release-') as directory:
        work = Path(directory); source = work / 'source'; source.mkdir()
        archive = work / 'snapshot.tar'
        subprocess.run(['git', 'archive', '--format=tar', '--output=' + str(archive), revision], cwd=ROOT, check=True)
        with tarfile.open(archive) as stream:
            for member in stream.getmembers():
                if (not (source / member.name).resolve().is_relative_to(source) or
                        not (member.isfile() or member.isdir())):
                    raise ValueError('unsupported Git archive member: ' + member.name)
            stream.extractall(source)
        run('build-distributions', [sys.executable, '-m', 'build', '--outdir', output, source], work)
        sdists = list(output.glob('*.tar.gz')); wheels = list(output.glob('*.whl'))
        if len(sdists) != 1 or len(wheels) != 1:
            raise RuntimeError('Expected one source distribution and one wheel')
        run('metadata-check', [sys.executable, '-m', 'twine', 'check', sdists[0], wheels[0]], work)
        run('install-verification', [sys.executable, source / 'scripts/check_install.py',
            '--sdist', sdists[0], '--wheel', wheels[0], '--output', output / 'install-check.json'], work)
        run('technical-report', [sys.executable, source / 'scripts/build_technical_report.py'], source)
        shutil.copy2(source / 'output/pdf/optbin-technical-report.pdf', output / 'optbin-technical-report.pdf')
        shutil.copy2(source / 'docs/report-evidence.json', output / 'report-evidence.json')
        shutil.copy2(source / 'LICENSE', output / 'LICENSE')
    install = json.loads((output / 'install-check.json').read_text())
    if install['status'] != 'pass' or install['version'] != version:
        raise RuntimeError('Release installation checks not complete')
    metadata = {'version': version, 'git_revision': revision, 'git_commit_time': git('show', '-s', '--format=%cI', revision),
        'status': 'Experimental release candidate; publication is a separate step',
        'wire_version': 2, 'legacy_read_version': 1,
        'build_platform': platform.platform(), 'python': sys.version,
        'installation_check': 'install-check.json',
        'limits': ['Validated locally on Linux x86-64', 'Linux platform wheel; no manylinux guarantee',
                   'See GitHub Actions for checks on the exact commit', 'No benchmark data or solver SDKs bundled'],
        'artifact_sha256': install['artifacts_sha256']}
    (output / 'release.json').write_text(json.dumps(metadata, indent=2) + '\n')
    files = sorted(p for p in output.rglob('*') if p.is_file())
    checksum = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(''.join(f'{checksum(p)}  {p.relative_to(output)}\n' for p in files))
    with tarfile.open(bundle, 'w:gz') as stream:
        stream.add(output, arcname='optbin-' + version)
    bundle.with_suffix(bundle.suffix + '.sha256').write_text(f'{checksum(bundle)}  {bundle.name}\n')
    print(bundle)
    print('Git revision:', revision)
    print('Installation checks:', install['status'])


if __name__ == '__main__':
    main()
