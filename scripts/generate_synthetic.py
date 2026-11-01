#!/usr/bin/env python3
"""Generate a deliberately highly structured LP for storage harness smoke tests."""
import argparse
import math
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('output',type=Path)
p.add_argument('--rows',type=int,default=5000)
p.add_argument('--cols',type=int,default=20000)
p.add_argument('--degree',type=int,default=8)
a=p.parse_args()
if a.rows<1 or a.cols<1 or not 1<=a.degree<=a.rows: p.error('invalid dimensions/degree')
step=113
while math.gcd(step,a.rows)!=1: step+=2
with a.output.open('x') as f:
    f.write('NAME synthetic\nROWS\n N obj\n')
    for i in range(a.rows): f.write(f' L R{i}\n')
    f.write('COLUMNS\n')
    for j in range(a.cols):
        f.write(f' X{j} obj 1\n')
        for k in range(a.degree):
            f.write(f' X{j} R{(j*17+k*step)%a.rows} {1 if k%2 else -1}\n')
    f.write('ENDATA\n')
