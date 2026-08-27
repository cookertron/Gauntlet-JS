#!/usr/bin/env python3
"""packstate.py -- every byte $97CB writes, grouped by region and by writer.

Tells a port exactly what state a level build produces besides the 32x32 map.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, TAPE_CALL_PC   # noqa: E402
import packdecode as PD                         # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')

REGIONS = [
    (0x5B20, 0x5B60, 'monster-shot ring'),
    (0x5B60, 0x5B90, '$9B0E scratch'),
    (0x5C00, 0x5F00, 'ACTOR LIST (4-byte records)'),
    (0x7B00, 0x7C00, 'sprite pointer table ($9AEF)'),
    (0x8000, 0x8400, 'THE 32x32 MAP'),
    (0x8400, 0x8500, 'globals / players'),
    (0xB500, 0xB600, 'SELF-MODIFIED operands in $B58C'),
    (0xE000, 0xF000, 'object bank'),
    (0xF000, 0x10000, 'object bank / tile attrs ($9AC9)'),
]


def region(a):
    for lo, hi, name in REGIONS:
        if lo <= a < hi:
            return name
    return 'other'


def run_nb(h, stop, limit=8_000_000):
    regs, opcodes, mem = h.sim.registers, h.sim.opcodes, h.sim.memory
    n = 0
    while n < limit:
        pc = regs[PC]
        if pc in stop:
            return n
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        if mem[pc] == 0x76:
            regs[PC] = (pc + 1) & 0xFFFF
            n += 1
            continue
        opcodes[mem[pc]]()
        n += 1
    return n


def main():
    pn = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    sub = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    pack = PD.load_pack(pn)
    lens = PD.sub_lengths(pack)
    starts = [PD.HDR]
    for x in lens:
        starts.append(starts[-1] + x)
    body = pack[starts[sub]:]
    m = h.memobj.m
    m[0xC000:0xC000 + min(len(body), 0x1000)] = body[:0x1000]
    m[0x8403] = 1
    r = h.regs
    r[8], r[9] = 0xC0, 0x00
    sp = r[12]
    for s in reversed(h.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        m[sp] = s & 0xFF
        m[sp + 1] = s >> 8
    r[12] = sp
    r[PC] = 0x97CB
    h.memobj.watch(0x4000, 0x10000)
    run_nb(h, set(h.SENTINELS))
    h.memobj.unwatch()

    byreg = {}
    for pc, a, v in h.memobj.log:
        k = region(a)
        d = byreg.setdefault(k, {'n': 0, 'lo': 0xFFFF, 'hi': 0, 'pcs': set()})
        d['n'] += 1
        d['lo'] = min(d['lo'], a)
        d['hi'] = max(d['hi'], a)
        d['pcs'].add(pc)
    print('pack %d sub %d: $97CB wrote %d bytes' % (pn, sub, len(h.memobj.log)))
    for name in sorted(byreg, key=lambda k: byreg[k]['lo']):
        d = byreg[name]
        print('  %-34s $%04X..$%04X  %6d writes  by %s' %
              (name, d['lo'], d['hi'], d['n'],
               ' '.join('$%04X' % p for p in sorted(d['pcs'])[:8])))
    print()
    print('actor list head:', ' '.join('%02X' % b for b in m[0x5C00:0x5C20]))
    print('$8492/$8493 player start = (%d,%d)  $8494 list end = $%04X  '
          '$8496 count = %d' % (m[0x8492], m[0x8493],
                                m[0x8494] | (m[0x8495] << 8), m[0x8496]))


if __name__ == '__main__':
    main()
