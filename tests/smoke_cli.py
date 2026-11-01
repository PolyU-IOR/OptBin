#!/usr/bin/env python3
"""Exercise installed converters, complete/stream validation and a C consumer."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def run(args, success=True):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if (result.returncode == 0) != success:
        raise RuntimeError(f"{args}\n{result.stdout}\n{result.stderr}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, required=True)
    parser.add_argument('--examples', type=Path, required=True)
    parser.add_argument('--consumer', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='optbin-cli-') as directory:
        work = Path(directory)
        cases = [('qp.mps', 'mps2optb', [], 2, 2, 1),
                 ('qcqp.mps', 'mps2optb', ['--mps-dialect', 'gurobi'], 2, 2, 1),
                 ('socp.cbf', 'cbf2optb', [], 3, 2, 2),
                 ('sdp.cbf', 'cbf2optb', [], 1, 0, 2)]
        for name, converter, options, rows, cols, profile in cases:
            target = work / (name + '.optb')
            command = [args.bin_dir / converter, args.examples / name, target, *options]
            run(command)
            for mode in ['--load', '--stream']:
                info = json.loads(run([args.bin_dir / 'optb_inspect', target, mode]).stdout)
                assert (info['rows'], info['cols'], info['profile']) == (rows, cols, profile), info
            result = run([args.consumer, target])
            assert f'profile={profile} rows={rows} columns={cols}' in result.stdout
            run(command, success=False)
            run([args.bin_dir / 'optb_inspect', target, '--limit', '1'], success=False)
            damaged = work / (name + '.bad')
            contents = bytearray(target.read_bytes()); contents[124] ^= 1
            damaged.write_bytes(contents)
            run([args.bin_dir / 'optb_inspect', damaged], success=False)
        for limit in ['-1', '-18446744073709551616', '18446744073709551616', '', '1.5', 'invalid']:
            run([args.bin_dir / 'optb_inspect', target, '--limit', limit], success=False)
        for limit in ['0', '18446744073709551615']:
            run([args.bin_dir / 'optb_inspect', target, '--limit', limit])
        run([args.bin_dir / 'mps2optb', args.examples / 'qcqp.mps', work / 'ambiguous.optb'], success=False)
    print('Four model classes: conversion, full/stream validation, C consumer, budgets and rejection checks passed')


if __name__ == '__main__':
    main()
