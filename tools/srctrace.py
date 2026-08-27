#!/usr/bin/env python3
"""srctrace.py -- what code sets SP for the player's sprite blit?

Keeps a ring buffer of the last N PCs and dumps it when the 16x16 blitter is
entered with a destination that matches the player.
"""
import os
import pickle
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, H as RH, L as RL, C as RC,
                     TAPE_CALL_PC)                                # noqa: E402
from keyprobe import KEYS, keymask                                # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
FRAME_T = 69888
BLIT16_IN = 0x9DD2
BANK_LO, BANK_HI = 0x5F00, 0x6F80


def main():
    depth = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    target = regs[T] + 4 * FRAME_T
    ring = deque(maxlen=depth)
    hit = None
    while regs[T] < target:
        pc = regs[PC]
        if pc == BLIT16_IN and BANK_LO <= regs[SP] < BANK_HI:
            hit = (list(ring), regs[SP], regs[RC],
                   regs[RL] + 256 * regs[RH])
            break
        ring.append((pc, regs[SP]))
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    if hit is None:
        print('no player blit seen')
        return
    ring, sp, c, hl = hit
    print(f'player blit: dst=${hl:04X}  src=SP=${sp:04X}  C=${c:02X}')
    print('preceding PCs (oldest first), with SP at each:')
    for pc, s in ring:
        print(f'   ${pc:04X}  SP=${s:04X}')


if __name__ == '__main__':
    main()
