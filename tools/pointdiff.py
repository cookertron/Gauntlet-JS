#!/usr/bin/env python3
"""
pointdiff.py -- run the point differential on both sides and report mismatches.

    python tools/pointdiff.py            all four directions at 30 and 60
    python tools/pointdiff.py down 60    one direction

It runs exactly the two commands the notes name --

    python tools/sim_move.py <dir> <n>       the REAL Z80, sampled at $8503
    node   tools/headless.js --table <dir> <n>   the BUILT artifact

-- and compares the table ROWS.  It exists because the two outputs cannot be
handed to `diff` unchanged: headless.js prints its whole check list before the
table, and the two interpreters disagree about line endings on Windows.  The
comparison itself is over the four printed fields (pass, x, y, note), so it is
no weaker than a textual diff.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW = re.compile(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*?)\s*$')


def rows(cmd):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                         cwd=ROOT).stdout
    got = []
    for line in out.splitlines():
        m = ROW.match(line)
        if m:
            got.append((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        m.group(4)))
    return got


def one(direction, n, verbose=True):
    a = rows(f'python tools/sim_move.py {direction} {n}')
    b = rows(f'node tools/headless.js --table {direction} {n}')
    if len(a) != n or len(b) != n:
        print(f'{direction} {n}: WRONG ROW COUNT orig={len(a)} engine={len(b)}')
        return 1
    bad = [(x, y) for x, y in zip(a, b) if x != y]
    print(f'{direction:>5} {n:>3} rows -> {len(bad)} mismatching')
    if bad and verbose:
        for x, y in bad[:6]:
            print(f'        orig {x}   engine {y}')
    return len(bad)


def main():
    if len(sys.argv) > 2:
        sys.exit(1 if one(sys.argv[1], int(sys.argv[2])) else 0)
    total = 0
    for d in ('right', 'left', 'up', 'down'):
        for n in (30, 60):
            total += one(d, n)
    print(f'\nTOTAL MISMATCHING ROWS: {total}')
    sys.exit(1 if total else 0)


if __name__ == '__main__':
    main()
