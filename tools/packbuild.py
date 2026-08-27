#!/usr/bin/env python3
"""packbuild.py -- drive the ORIGINAL through a full level build and capture
   * the selected record's bytes (as assembled at $C000 by $91D7),
   * the resulting 32x32 map at $8000,
   * the actor list at $5C00 and the player start ($8492/$8493),
   * every PC that writes into $8000..$83FF.

Usage:  python tools/packbuild.py [level] [--pack N] [--sub A,B,C]
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, TAPE_CALL_PC   # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
BUILD_ENTRY = 0xB3D0


def run_nb(h, stop, limit=6_000_000):
    """Step with interrupts off (the build is a DI section anyway)."""
    sim = h.sim
    regs = sim.registers
    opcodes = sim.opcodes
    mem = sim.memory
    n = 0
    while n < limit:
        pc = regs[PC]
        if pc in stop:
            return ('target', n)
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
    return ('limit', n)


def build(h, level=1, rewind=True, watch=True):
    m = h.memobj.m
    if rewind:
        h.deck.rewind()
    m[0x8403] = level
    if level < 8:
        # $9184: bit 7 of $84CC clear -> load the $80 pack and use record 0;
        # set -> skip the load and let the LEVEL NUMBER pick the record.
        m[0x84CC] = 0x00 if level == 1 else 0x80
        m[0x84CD] = 0x80
    r = h.regs
    sp = r[12]
    for s in reversed(h.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        m[sp] = s & 0xFF
        m[sp + 1] = s >> 8
    r[12] = sp
    r[PC] = BUILD_ENTRY
    if watch:
        h.memobj.watch(0x8000, 0x8400)
    reason, n = run_nb(h, set(h.SENTINELS))
    if watch:
        h.memobj.unwatch()
    return reason, n


def main():
    args = sys.argv[1:]
    level = int(args[0]) if args and not args[0].startswith('--') else 1
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    reason, n = build(h, level)
    m = h.memobj.m
    print('build level %d: %s in %d instrs, deck log %s' %
          (level, reason, n, h.deck.log))
    # writers into the map
    byp = {}
    for pc, a, v in h.memobj.log:
        byp[pc] = byp.get(pc, 0) + 1
    print('writers into $8000-$83FF:')
    for pc, c in sorted(byp.items(), key=lambda kv: -kv[1]):
        print('   $%04X  %d' % (pc, c))
    print('player start ($8492,$8493) = (%d,%d)' % (m[0x8492], m[0x8493]))
    print('actor count $8496 =', m[0x8496])
    grid = bytes(m[0x8000:0x8400])
    vals = {}
    for b in grid:
        vals[b] = vals.get(b, 0) + 1
    print('map value histogram:', {'$%02X' % k: v for k, v in sorted(vals.items())})
    open(os.path.join(ROOT, 'build', 'map_lvl%d.bin' % level), 'wb').write(grid)
    open(os.path.join(ROOT, 'build', 'recs_lvl%d.bin' % level), 'wb').write(
        bytes(m[0xC000:0xC700]))
    print('wrote build/map_lvl%d.bin and build/recs_lvl%d.bin' % (level, level))
    for row in range(32):
        print('%2d ' % row + ' '.join('%02X' % b for b in grid[row * 32:row * 32 + 32]))


if __name__ == '__main__':
    main()
