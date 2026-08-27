#!/usr/bin/env python3
"""
spritetrap2.py -- trap the 16-wide blitter, and for every call work out
whether the destination is the one that tracks the PLAYER, using the measured
origin  screen_x = (x-cam_x)*4 - 8,  screen_y = (y-cam_y)*4.
Prints the source address of the player's own blit, live, per pass.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, SP, H, L, TAPE_CALL_PC  # noqa: E402
from keyprobe import KEYS, keymask                               # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
FRAME_T = 69888
BLIT16 = 0x9DEC
P_X, P_Y, CAM_X, CAM_Y = 0x8420, 0x8421, 0x848B, 0x848C
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}


def scr(base, x, y):
    return base | ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2) | (x >> 3)


def run(h, frames, hits):
    target = h.regs[T] + frames * FRAME_T
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    while regs[T] < target:
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); continue
        if pc == BLIT16:
            hits.append((regs[SP], (regs[H] << 8) | regs[L],
                         mem[P_X], mem[P_Y], mem[CAM_X], mem[CAM_Y],
                         bytes(mem[regs[SP]:regs[SP] + 40])))
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)


def bits(b):
    return format(b, '08b').replace('0', '.').replace('1', '#')


def main():
    direction = sys.argv[1] if len(sys.argv) > 1 else 'right'
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    h = Harness()
    h.load_state(pickle.load(open(os.path.join(ROOT, 'build', 'state_charsel.pkl'), 'rb')))
    mem = h.memobj.m
    print('player', mem[P_X], mem[P_Y], 'cam', mem[CAM_X], mem[CAM_Y])
    if direction != 'none':
        sel, bit = KM[DIRKEY[direction].upper()]
        h.ports.press(sel, keymask(bit))
    found = {}
    for p in range(passes):
        hits = []
        run(h, 4, hits)
        px, py, cx, cy = mem[P_X], mem[P_Y], mem[CAM_X], mem[CAM_Y]
        sx = (px - cx) * 4 - 8
        sy = (py - cy) * 4
        want = scr(0xC000, sx & 0xFF, sy & 0xFF)
        match = [hh for hh in hits if hh[1] == want]
        others = sorted(set(hh[0] for hh in hits))
        print(f'pass {p:2d} p=({px},{py}) cam=({cx},{cy}) scr=({sx},{sy}) want=${want:04X} '
              f'-> {[hex(m[0]) for m in match]}   all_src={[hex(o) for o in others]}')
        for m in match:
            found.setdefault(m[0], m[6])
    print()
    for a, b in sorted(found.items()):
        print(f'=== PLAYER SRC ${a:04X} at 2x16 row-major:')
        for r in range(16):
            print('   ' + bits(b[r * 2]) + bits(b[r * 2 + 1]))
        print()


if __name__ == '__main__':
    main()
