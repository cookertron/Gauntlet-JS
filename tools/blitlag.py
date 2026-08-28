#!/usr/bin/env python3
"""blitlag.py -- is the sprite drawn at the player's CURRENT coordinate, or at
the one he had at the start of the pass?  Records the player's coordinate at
the moment the blitter is entered, alongside the destination it is entered
with, and the coordinate at the end of the pass."""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, H, L, IXh, IXl,        # noqa: E402
                     TAPE_CALL_PC)
from keyprobe import KEYS, keymask                                    # noqa: E402
from filmstrip import run_frames                                      # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
FRAME_T = 69888
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}


def xy(addr):
    o = (addr - 0xC000) & 0x1FFF
    y = ((o & 0x1800) >> 5) | ((o & 0x0700) >> 8) | ((o & 0x38 << 2) >> 2)
    y = ((o & 0x1800) >> 5) | ((o & 0x0700) >> 8) | ((o & 0x00E0) >> 2)
    return (o & 0x1F) * 8, y


def main():
    direction = sys.argv[1] if len(sys.argv) > 1 else 'right'
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    state = sys.argv[3] if len(sys.argv) > 3 else os.path.join(
        ROOT, 'build', 'state_elf.pkl')
    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    if direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    run_frames(h, 32)
    regs, ops, mem, sim = h.regs, h.sim.opcodes, h.sim.memory, h.sim
    m = h.memobj.m
    fd, ia = h.frame_duration, h.int_active
    print(f'{"pass":>4} {"at draw":>10} {"cam@draw":>9} {"dest":>10} '
          f'{"(p-c)*4":>9} {"delta":>6}  SP     {"end of pass":>11}')
    for p in range(passes):
        target = regs[T] + 4 * FRAME_T
        rows = []
        while regs[T] < target:
            pc = regs[PC]
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape(); continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt(); continue
            # $A243 JP $9DD2 is the PLAYER/actor draw call site; IX=$A246 is
            # its return continuation, which identifies it uniquely.
            if pc == 0x9DD2 and (regs[IXl] + 256 * regs[IXh]) == 0xA246:
                rows.append((m[0x8420], m[0x8421], m[0x848B], m[0x848C],
                             regs[L] + 256 * regs[H], regs[SP]))
            ops[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
        for px, py, cx, cy, hl, sp in rows[:1]:
            dx, dy = xy(hl)
            ex, ey = (px - cx) * 4, (py - cy) * 4
            print(f'{p:4d} {f"({px},{py})":>10} {f"({cx},{cy})":>9} '
                  f'{f"({dx},{dy})":>10} {f"({ex},{ey})":>9} '
                  f'{f"({dx-ex},{dy-ey})":>6}  ${sp:04X}  '
                  f'{f"({m[0x8420]},{m[0x8421]})":>11}')


if __name__ == '__main__':
    main()
