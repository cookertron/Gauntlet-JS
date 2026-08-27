#!/usr/bin/env python3
"""packsurvey.py -- what the 307 decoded levels actually contain.

Cross-checks the record length byte (+0, 9th bit in bit 7 of +2) against the
pack's own sub-block length table, and enumerates every value that reaches the
map, every RLE literal, every actor type and every flag bit that is used.
"""
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import packdecode as PD                      # noqa: E402


def main():
    cells = Counter()
    lits = Counter()
    acts = Counter()
    flagbits = Counter()
    b2bits = Counter()
    tags_point = Counter()
    tags_run = Counter()
    hdr0 = 0
    lenbad = []
    stubs = set()
    lead6 = Counter()
    nlevels = 0
    nplayer = 0
    for n in range(1, 32):
        pack = PD.load_pack(n)
        stubs.add(bytes(pack[6:PD.LENTAB]))
        lead6[bytes(pack[0:6])] += 1
        lens = PD.sub_lengths(pack)
        starts = [PD.HDR]
        for x in lens:
            starts.append(starts[-1] + x)
        for s, ln in enumerate(lens):
            if ln == 0:
                continue
            body = pack[starts[s]:]
            buf = bytearray(PD.FIRST_COPY + PD.SECOND_COPY)
            k = min(len(body), len(buf))
            buf[:k] = body[:k]
            declared = buf[0] + (0x100 if buf[2] & 0x80 else 0)
            if declared != ln:
                lenbad.append((n, s, declared, ln))
            nlevels += 1
            flags = buf[1]
            for b in range(8):
                if flags & (1 << b):
                    flagbits[b] += 1
            for b in range(7):
                if buf[2] & (1 << b):
                    b2bits[b] += 1
            if buf[3] == 0:
                hdr0 += 1
            tr = []
            mp, info = PD.expand(buf, 0, trace=tr)
            if mp.player:
                nplayer += 1
            for c in mp.cell:
                cells[c] += 1
            for a in mp.actors:
                acts[a[2]] += 1
            for e in tr:
                if e[0] == 'point':
                    tags_point[e[1] >> 5] += 1
                else:
                    tags_run[e[2]] += 1
            de = 4 + buf[3]
            c = (buf[0] - ((buf[3] + 4) & 0xFF)) & 0xFF
            ninth = bool(buf[2] & 0x80) and buf[0] >= ((buf[3] + 4) & 0xFF)
            total = (c if c else 256) + (256 if ninth else 0)
            for i in range(total):
                v = buf[de + i]
                if 0x13 <= v < 0x80:
                    lits[v] += 1

    print('levels decoded: %d   (with a $3F player start: %d)' % (nlevels, nplayer))
    print('record length byte vs the pack length table: %s' %
          ('AGREES on all %d' % nlevels if not lenbad else lenbad))
    print('distinct pack stubs ($006..$0B8): %d' % len(stubs))
    print('the 6 bytes the loader zeroes: %s' %
          {b.hex(): c for b, c in lead6.items()})
    print('records with an empty vector table: %d' % hdr0)
    print()
    print('MAP CELL VALUES over %d x 1024 cells:' % nlevels)
    for v, c in sorted(cells.items()):
        print('   $%02X  %8d' % (v, c))
    print()
    print('RLE LITERAL tile bytes ($13..$7F):')
    for v, c in sorted(lits.items()):
        kind = ('map tile' if v < 0x3F else
                'PLAYER START' if v == 0x3F else 'actor')
        print('   $%02X  %6d   %s' % (v, c, kind))
    print()
    print('ACTOR type bytes (((t>>1)&$1C | t&3) * 8):')
    for v, c in sorted(acts.items()):
        print('   $%02X  %6d' % (v, c))
    print()
    print('vector POINT tags:', dict(sorted(tags_point.items())))
    print('vector RUN directions:', dict(sorted(tags_run.items())))
    print('flags (+1) bits used:', dict(sorted(flagbits.items())))
    print('byte +2 bits 0-6 used:', dict(sorted(b2bits.items())))


if __name__ == '__main__':
    main()
