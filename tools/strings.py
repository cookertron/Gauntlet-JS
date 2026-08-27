#!/usr/bin/env python3
"""strings.py -- printable runs in the image, plus a 'high-bit terminated' pass
(the common 8-bit convention where the last character of a string has bit 7 set)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def runs(mem, lo, hi, minlen):
    out = []
    s = None
    for a in range(lo, hi):
        c = mem[a]
        if 32 <= c < 127:
            if s is None:
                s = a
        else:
            if s is not None and a - s >= minlen:
                out.append((s, mem[s:a].decode('latin-1')))
            s = None
    return out


def runs_hi(mem, lo, hi, minlen):
    """runs where every byte is printable-with-bit-7-optionally-set"""
    out = []
    s = None
    for a in range(lo, hi):
        c = mem[a] & 0x7F
        if 32 <= c < 127:
            if s is None:
                s = a
        else:
            if s is not None and a - s >= minlen:
                out.append((s, ''.join(chr(b & 0x7F) for b in mem[s:a])))
            s = None
    return out


if __name__ == '__main__':
    args = sys.argv[1:]
    img = os.path.join(ROOT, 'build', 'image.bin')
    lo, hi, minlen = 0x4000, 0x10000, 5
    mode = 'ascii'
    while args:
        if args[0] == '--image':
            img = args[1]; del args[:2]
        elif args[0] == '--range':
            lo = int(args[1], 0); hi = int(args[2], 0); del args[:3]
        elif args[0] == '--min':
            minlen = int(args[1]); del args[:2]
        elif args[0] == '--hi':
            mode = 'hi'; del args[:1]
        else:
            del args[:1]
    mem = bytearray(open(img, 'rb').read())
    f = runs if mode == 'ascii' else runs_hi
    for a, s in f(mem, lo, hi, minlen):
        print(f'${a:04X}  {s!r}')
