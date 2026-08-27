#!/usr/bin/env python3
"""
sparklegate.py -- $A31A's TREASURE SPARKLE, the animated chest.

The $13 tile is not a still picture.  On every EVEN pass $A31A rebuilds part
of its bitmap from a master copy through a random mask:

    $A33B  LD A,($8491) / AND 1 / RET nz      even passes only
    $A341  LD HL,$E199 / LD DE,$FF74 / LD B,5 / CALL $A356
    $A34C  INC DE x4 / INC HL x4 / LD B,3     / falls into $A356
    $A356  CALL $B575 / OR $F0 / LD C,A / LD A,(DE) / AND C / LD (HL),A
           CALL $B575 / OR $0F / LD C,A / LD A,(DE) / AND C / LD (HL),A
           DJNZ $A356

$E18C is the $13 record, so the blocks are data bytes 12..21 and 26..31 --
rows 6..10 and 13..15 of the chest.

WHAT CAN BE COMPARED, and what cannot.  The masks come from $B575, `LD A,R`,
so the port cannot reproduce the SEQUENCE and does not try.  What it must
reproduce is the SHAPE of the animation, and that is fully determined:

    ever-set    = the master's own bits.  A bit not in the master can never
                  appear, whatever the mask.
    always-set  = the bits the mask cannot clear -- `OR $F0` pins the high
                  byte's top nibble and `OR $0F` the low byte's bottom
                  nibble, so those survive every draw.
    flickering  = ever-set AND NOT always-set.

Those three masks are what this tool measures on the original, over enough
passes for the draws to have covered everything, and what tools/headless.js
requires of the engine.  A still picture fails all three.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, IFF, TAPE_CALL_PC             # noqa: E402
from sim_move import LOOP_TOP                                  # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
PTRS, TILE = 0x7B00, 0x13
PASSES = 120


def one_pass(h):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < 20_000_000:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        n += 1
        if regs[IFF] and regs[25] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    raise RuntimeError('no main-loop top')


def main():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    p = PTRS + (TILE - 1) * 2
    rec = m[p] | (m[p + 1] << 8)
    data = rec + 1
    print('the $%02X tile record is at $%04X, data $%04X' % (TILE, rec, data))

    always = [0xFF] * 32
    ever = [0x00] * 32
    moved_on_odd = 0
    prev = list(m[data:data + 32])
    for _ in range(PASSES):
        # $A31A runs at $8503, the TOP, and reads $8491 as it stands there;
        # $9CFB increments it at $8550, the BOTTOM.  Sampling the counter
        # after the pass therefore reads the NEXT pass's value and inverts
        # the parity -- the first run of this tool reported 59 changes "on an
        # odd pass" for that reason alone.
        parity = m[0x8491] & 1
        one_pass(h)
        cur = list(m[data:data + 32])
        for i in range(32):
            always[i] &= cur[i]
            ever[i] |= cur[i]
        if cur != prev and parity:
            moved_on_odd += 1
        prev = cur
    flick = [(ever[i] & ~always[i]) & 0xFF for i in range(32)]

    doc = {'tile': TILE, 'passes': PASSES, 'always': always, 'ever': ever,
           'flicker': flick, 'moved_on_odd': moved_on_odd}
    path = os.path.join(ROOT, 'build', '_sparkle.json')
    json.dump(doc, open(path, 'w'))

    live = [i for i in range(32) if flick[i]]
    print('%d passes: bytes that flicker: %s' % (PASSES, live))
    for i in live:
        print('   byte %2d  always $%02X  ever $%02X  flicker $%02X'
              % (i, always[i], ever[i], flick[i]))
    print('changes seen on an ODD pass: %d (must be 0)' % moved_on_odd)
    assert live, 'nothing flickered -- the sparkle never ran, so this gate ' \
                 'would pass a still picture'
    assert moved_on_odd == 0, '$A33B says even passes only'
    print('wrote', path)


if __name__ == '__main__':
    main()
