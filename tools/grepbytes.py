#!/usr/bin/env python3
"""
grepbytes.py -- byte-pattern search over the image with instruction-boundary
confirmation (manual 3.9 / A7).

For each hit, try start offsets 2..40 bytes back, disassemble forward, and keep
the first window whose instruction boundaries land exactly on the candidate.
A hit that no window confirms is reported as UNCONFIRMED (operand byte, table
entry or graphics), not silently dropped.

Usage:  python tools/grepbytes.py "CD 3B FF" [--image F] [--range LO HI]
"""
import os
import sys

import z80dis.z80 as z

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def confirm(mem, addr, back=40):
    """Is `addr` an instruction boundary?  Return the back-off that proves it."""
    for b in range(2, back):
        p = addr - b
        if p < 0:
            continue
        ok = True
        while p < addr:
            try:
                d = z.decode(bytes(mem[p:p + 4]), p)
            except Exception:                       # noqa: BLE001
                ok = False
                break
            if d.status != z.DECODE_STATUS.OK:
                ok = False
                break
            p += d.len
        if ok and p == addr:
            return b
    return None


def main():
    args = sys.argv[1:]
    img = os.path.join(ROOT, 'build', 'image.bin')
    lo, hi = 0, 0x10000
    if '--image' in args:
        i = args.index('--image'); img = args[i + 1]; del args[i:i + 2]
    if '--range' in args:
        i = args.index('--range')
        lo = int(args[i + 1], 0); hi = int(args[i + 2], 0); del args[i:i + 3]
    pat = bytes(int(x, 16) for x in args[0].split())
    mem = bytearray(open(img, 'rb').read())
    hits = []
    p = lo
    while True:
        p = mem.find(pat, p, hi)
        if p < 0:
            break
        hits.append(p)
        p += 1
    print(f'pattern {args[0]}: {len(hits)} raw hits in ${lo:04X}-${hi:04X}')
    conf = 0
    for h in hits:
        b = confirm(mem, h)
        if b is None:
            print(f'  ${h:04X}  UNCONFIRMED (not an instruction boundary)')
        else:
            conf += 1
            d = z.decode(bytes(mem[h:h + 4]), h)
            print(f'  ${h:04X}  confirmed (backoff {b})  {z.decoded2str(d)}')
    print(f'{conf} confirmed / {len(hits)} hits')


if __name__ == '__main__':
    main()
