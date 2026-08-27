#!/usr/bin/env python3
"""packlook.py -- LOOK at a decoded level.

Builds one sub-block through the ORIGINAL, runs the main loop a few passes so
its own tile blitter paints the shadow screen, dumps that to a PNG, and prints
the same level as ASCII straight out of packdecode.  If the decode were wrong
the two would not be the same maze.

    python tools/packlook.py 5 0
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, TAPE_CALL_PC   # noqa: E402
import packdecode as PD                         # noqa: E402
import screen                                   # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LOOP_TOP = 0x8503

GLYPH = {}
for v in range(1, 0x11):
    GLYPH[v] = '#'
GLYPH[0x00] = '.'
GLYPH[0x11] = 'D'
GLYPH[0x12] = 'd'
for v in range(0x13, 0x1F):
    GLYPH[v] = 'o'
GLYPH[0x1F] = 'k'
for v in range(0x20, 0x3F):
    GLYPH[v] = '*'
GLYPH[0x36] = 'X'


def glyph(v):
    if v & 0x80:
        return '#'
    return GLYPH.get(v, '?')


def ascii_map(mp, player=None, actors=()):
    grid = [[glyph(mp.cell[r * 32 + c]) for c in range(32)] for r in range(32)]
    for (x, y, _t) in actors:
        grid[(y // 4) & 31][(x // 4) & 31] = 'm'
    if player:
        grid[(player[1] // 4) & 31][(player[0] // 4) & 31] = '@'
    return '\n'.join(''.join(row) for row in grid)


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
        if mem[pc] == 0x76 and regs[26]:
            h._fast_halt()
            n += 1
            continue
        opcodes[mem[pc]]()
        if regs[26] and regs[25] % h.frame_duration < h.int_active:
            h.sim.accept_interrupt(regs, mem, pc)
        n += 1
    return n


def main():
    pn = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    sub = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    pack = PD.load_pack(pn)
    lens = PD.sub_lengths(pack)
    starts = [PD.HDR]
    for x in lens:
        starts.append(starts[-1] + x)
    body = pack[starts[sub]:]
    buf = bytearray(PD.FIRST_COPY + PD.SECOND_COPY)
    k = min(len(body), len(buf))
    buf[:k] = body[:k]

    m = h.memobj.m
    m[0xC000:0xC000 + len(buf)] = bytes(buf)
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
    run_nb(h, set(h.SENTINELS))
    # put the players where the record says, as $B43E..$B446 does
    px, py = m[0x8492], m[0x8493]
    m[0x8420], m[0x8421] = px, py
    m[0x8440], m[0x8441] = px, py
    m[0x8496] = m[0x8496]                    # actor count as built
    # back into the main loop for a few passes so the blitter runs
    r[PC] = LOOP_TOP
    for _ in range(3):
        run_nb(h, {LOOP_TOP})
        r[PC] = LOOP_TOP + 1
        run_nb(h, {LOOP_TOP})
    out = os.path.join(ROOT, 'build', 'pack_%d_%d.png' % (pn, sub))
    img = screen.render(bytes(m), base=0x4000, attr_base=0x5800)
    img.resize((768, 576), 0).save(out)
    mp, info = PD.expand(buf, 0)
    print(ascii_map(mp, mp.player, mp.actors))
    print('\nplayer %s, %d actors, flags $%02X' %
          (mp.player, len(mp.actors), info['flags']))
    print('wrote', out)


if __name__ == '__main__':
    main()
