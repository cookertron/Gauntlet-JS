#!/usr/bin/env python3
"""packtrace.py -- watch the dungeon-pack load and the code the pack itself runs.

$9203 ends with  PUSH HL(=$C000) / RET  after zeroing $C000..$C005, so the
freshly loaded pack is ENTERED as code at $C000 (six NOPs, then the pack's own
depacker).  This tool drives that on the real Z80 and reports where the
depacker writes.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC   # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')


def run_traced(h, stop_pcs, limit=4_000_000):
    """Step with interrupts off, recording the PC histogram and the ranges."""
    sim = h.sim
    regs = sim.registers
    opcodes = sim.opcodes
    mem = sim.memory
    hist = {}
    n = 0
    while n < limit:
        pc = regs[PC]
        if pc in stop_pcs:
            return ('target', n, hist)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        hist[pc] = hist.get(pc, 0) + 1
        opcodes[mem[pc]]()
        n += 1
    return ('limit', n, hist)


def main():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    h.deck.rewind()
    m = h.memobj.m
    m[0x84CC] &= 0x7F                    # force the tape load
    m[0x84CD] = 0x80                     # (IY+$4E) expected flag
    m[0x8403] = 1                        # level 1

    before6F = bytes(m[0x6F80:0x6F80 + 0x45A])
    # sentinel-based call of the loader
    r = h.regs
    sp = r[12]
    for s in reversed(h.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        m[sp] = s & 0xFF
        m[sp + 1] = s >> 8
    r[12] = sp
    r[PC] = 0x9203
    reason, n, hist = run_traced(h, set(h.SENTINELS))
    print('reason', reason, 'steps', n, 'PC %04X' % h.pc())
    print('deck log:', h.deck.log)
    lo = min(a for a in hist if a >= 0x4000)
    print('distinct PCs:', len(hist))
    # group the PC histogram into ranges
    addrs = sorted(hist)
    runs = []
    start = prev = addrs[0]
    for a in addrs[1:]:
        if a - prev > 24:
            runs.append((start, prev))
            start = a
        prev = a
    runs.append((start, prev))
    print('executed ranges:')
    for a, b in runs:
        tot = sum(hist[x] for x in addrs if a <= x <= b)
        print('  $%04X-$%04X  %d instrs' % (a, b, tot))

    after6F = bytes(m[0x6F80:0x6F80 + 0x45A])
    print('6F80 block changed:', after6F != before6F)
    print('$C000..$C020:', ' '.join('%02X' % b for b in m[0xC000:0xC020]))
    print('$6F80..$6FA0:', ' '.join('%02X' % b for b in m[0x6F80:0x6FA0]))
    nz = max(i for i in range(0x6F80, 0x7400) if m[i]) if any(m[0x6F80:0x7400]) else 0
    print('last non-zero at/after $6F80: $%04X' % nz)
    print('$DDD8..$DE00:', ' '.join('%02X' % b for b in m[0xDDD8:0xDE00]))
    nz2 = [i for i in range(0xDDD8, 0xE000) if m[i]]
    print('non-zero in $DDD8..$DFFF:', len(nz2))


if __name__ == '__main__':
    main()
