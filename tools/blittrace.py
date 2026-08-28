#!/usr/bin/env python3
"""
blittrace.py -- log every entry into the three blitters during main-loop passes,
with destination, source and attribute byte, alongside the player's coordinates.

Blitters (read from build/live_cs.bin, boundaries confirmed by disassembling
from the JP (IX) that ends each):
    $9DD2  16 px wide x 16 rows, 2x2 attribute cells, ends $9E49  JP (IX)
    $9E4B   8 px wide x 16 rows, 2x1 attribute cells, ends $9E9E  JP (IX)
    $9EA0  16 px wide x  8 rows, 1x2 attribute cells, ends ~$9F1x
All three compute the attribute address as $D8 + ((H>>3) AND 3), so they can
only target the SHADOW screen at $C000/$D800.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, H as RH, L as RL, C as RC,
                     TAPE_CALL_PC)                                # noqa: E402
from keyprobe import KEYS, keymask                                # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
P_X, P_Y, P_DIR, CAM_X, CAM_Y = 0x8420, 0x8421, 0x8427, 0x848B, 0x848C
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
FRAME_T = 69888

BLIT = {0x9DD2: ('16x16', 0x9E49), 0x9E4B: ('8x16', 0x9E9E)}


def scr_xy(addr):
    """Bitmap address -> (x, y) inside its 6144-byte screen."""
    o = addr & 0x1FFF
    y = (((o >> 11) & 3) << 6) | (((o >> 5) & 7) << 3) | ((o >> 8) & 7)
    x = (o & 31) * 8
    return x, y


def trace_pass(h, log):
    """One main-loop pass = 4 video frames.  Records blitter entries."""
    target = h.regs[T] + 4 * FRAME_T
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    m = h.memobj.m
    fd, ia = h.frame_duration, h.int_active
    while regs[T] < target:
        pc = regs[PC]
        if pc in BLIT:
            hl = regs[RL] + 256 * regs[RH]
            log.append((pc, hl, regs[SP], regs[RC],
                        m[P_X], m[P_Y], m[CAM_X], m[CAM_Y]))
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)


def main():
    direction = sys.argv[1] if len(sys.argv) > 1 else None
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if direction and direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    for p in range(passes):
        log = []
        trace_pass(h, log)
        m = h.memobj.m
        px, py, cx, cy = m[P_X], m[P_Y], m[CAM_X], m[CAM_Y]
        exp = ((px - cx) * 4 - 8, (py - cy) * 4)
        print(f'--- pass {p}: player=({px},{py}) cam=({cx},{cy}) '
              f'dir={m[P_DIR]} expected sprite at {exp}   {len(log)} blits')
        for pc, hl, sp, c, ppx, ppy, pcx, pcy in log:
            x, y = scr_xy(hl)
            mark = '  <== PLAYER?' if abs(x - exp[0]) <= 8 and abs(y - exp[1]) <= 8 else ''
            print(f'    ${pc:04X} {BLIT[pc][0]:5} dst=${hl:04X} ({x:3},{y:3}) '
                  f'src=${sp:04X} attr=${c:02X}{mark}')


if __name__ == '__main__':
    main()
