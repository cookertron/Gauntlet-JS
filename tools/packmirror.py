#!/usr/bin/env python3
"""packmirror.py -- gate the two level >= 8 map mirrors, $9C06 and $9C69.

$91B5 stores a fresh random byte in $84C7; bit 1 turns on the horizontal
mirror and bit 0 the vertical one, so a level >= 8 comes out in one of four
orientations.  Here the byte is FORCED to each of the four values and the
original's own $97CB is compared against packdecode's mirror_h / mirror_v.
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


def run_nb(h, stop, limit=8_000_000):
    sim, regs, opcodes, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
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
    h = Harness()
    base = pickle.load(open(STATE, 'rb'))
    ok = tot = 0
    for pn, sub in ((2, 0), (3, 4), (7, 2), (12, 5), (20, 1), (31, 6)):
        pack = PD.load_pack(pn)
        lens = PD.sub_lengths(pack)
        starts = [PD.HDR]
        for x in lens:
            starts.append(starts[-1] + x)
        body = pack[starts[sub]:]
        buf = bytearray(PD.FIRST_COPY + PD.SECOND_COPY)
        k = min(len(body), len(buf))
        buf[:k] = body[:k]
        buf[1] &= ~0x04                       # switch off the random $36 pass
        for c7 in (0, 1, 2, 3):
            h.load_state(base)
            m = h.memobj.m
            m[0xC000:0xC000 + len(buf)] = bytes(buf)
            m[0x8403] = 12                    # a level >= 8
            m[0x84C7] = c7
            r = h.regs
            r[8], r[9] = 0xC0, 0x00           # IX = $C000
            sp = r[12]
            for s in reversed(h.SENTINELS):
                sp = (sp - 2) & 0xFFFF
                m[sp] = s & 0xFF
                m[sp + 1] = s >> 8
            r[12] = sp
            r[PC] = 0x97CB
            run_nb(h, {0x9896})
            h.regs[PC] = 0x9899               # step over $9BB7's random objects
            run_nb(h, set(h.SENTINELS))
            grid = bytes(m[0x8000:0x8400])
            player = (m[0x8492], m[0x8493])
            nact = m[0x8496]
            actors = [tuple(m[0x5C00 + 4 * i:0x5C00 + 4 * i + 3])
                      for i in range(nact)]

            mp, info = PD.expand(buf, 0)
            if c7 & 2:
                PD.mirror_h(mp)
            if c7 & 1:
                PD.mirror_v(mp)
            same = sum(1 for i in range(1024) if grid[i] == mp.cell[i])
            # $9C3D..$9C68 mirrors the ACTOR LIST as well as the player start,
            # so compare it here -- this is what caught the omission.
            ok_a = ([a[:3] for a in actors] == [tuple(a[:3]) for a in mp.actors])
            tot += 1
            good = same == 1024 and player == mp.player and ok_a
            ok += good
            print('pack %2d sub %d  $84C7=%d (%-10s): %d/1024  player %s/%s  '
                  '%d actors, list %s  %s' %
                  (pn, sub, c7,
                   {0: 'none', 1: 'vertical', 2: 'horizontal',
                    3: 'both'}[c7], same, player, mp.player, len(actors),
                   'OK' if ok_a else 'MISMATCH',
                   'OK' if good else 'MISMATCH'))
    print('\n%d/%d orientations reproduced' % (ok, tot))


if __name__ == '__main__':
    main()
