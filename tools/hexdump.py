#!/usr/bin/env python3
"""hexdump.py -- hex + ASCII dump with an optional base address."""
import sys


def dump(data, base=0, off=0, length=None, width=16):
    end = len(data) if length is None else min(len(data), off + length)
    p = off
    while p < end:
        row = data[p:p + width]
        hx = ' '.join(f'{b:02X}' for b in row)
        tx = ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)
        print(f'{base + p:04X}  {hx:<{width*3}} |{tx}|')
        p += width


if __name__ == '__main__':
    a = sys.argv[1:]
    base = off = 0
    length = None
    if '--base' in a:
        i = a.index('--base'); base = int(a[i + 1], 0); del a[i:i + 2]
    if '--offset' in a:
        i = a.index('--offset'); off = int(a[i + 1], 0); del a[i:i + 2]
    if '--len' in a:
        i = a.index('--len'); length = int(a[i + 1], 0); del a[i:i + 2]
    dump(open(a[0], 'rb').read(), base, off, length)
