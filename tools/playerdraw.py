#!/usr/bin/env python3
"""playerdraw.py -- who supplies the PLAYER's sprite-record pointer?

Records a ring buffer of PCs and dumps the run that leads into the blit whose
destination is the player's own screen position."""
import os
import pickle
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, H, L, D, E, B, C, A,   # noqa: E402
                     IXh, IXl, TAPE_CALL_PC)
from keyprobe import KEYS, keymask                                    # noqa: E402
from filmstrip import run_frames                                      # noqa: E402
import adis                                                           # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
FRAME_T = 69888
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}


def main():
    direction = sys.argv[1] if len(sys.argv) > 1 else 'idle'
    back = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    run_frames(h, 32)

    m = h.memobj.m
    px_, py_, cx, cy = m[0x8420], m[0x8421], m[0x848B], m[0x848C]
    # the shadow-screen bitmap address of the player's top-left byte
    sx, sy = (px_ - cx) * 4, (py_ - cy) * 4
    want = 0xC000 | ((sy & 0xC0) << 5) | ((sy & 7) << 8) | ((sy & 0x38) << 2) | (sx >> 3)
    print(f'player=({px_},{py_}) cam=({cx},{cy}) -> screen ({sx},{sy}) '
          f'-> shadow addr ${want:04X}')

    ring = deque(maxlen=4000)
    regs, ops, mem, sim = h.regs, h.sim.opcodes, h.sim.memory, h.sim
    fd, ia = h.frame_duration, h.int_active
    target = regs[T] + 4 * FRAME_T
    hits = []
    while regs[T] < target:
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); continue
        ring.append((pc, regs[SP], regs[L] + 256 * regs[H], regs[C] + 256 * regs[B],
                     regs[IXl] + 256 * regs[IXh], regs[E] + 256 * regs[D], regs[A]))
        if pc == 0x9DD2 and (regs[L] + 256 * regs[H]) == want:
            hits.append(list(ring)[-back:])
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)

    print(f'{len(hits)} player draws this pass')
    if not hits:
        return
    img = bytearray(h.memobj.m)
    for pc, sp, hl, bc, ix, de, a in hits[0]:
        line = []
        adis.disasm(img, pc, 1, out=Fake(line))
        print(f'  SP=${sp:04X} HL=${hl:04X} BC=${bc:04X} DE=${de:04X} '
              f'IX=${ix:04X} A=${a:02X}   {line[0].rstrip()}')


class Fake:
    def __init__(self, sink):
        self.sink = sink

    def write(self, s):
        if s.strip():
            self.sink.append(s)

    def flush(self):
        pass


if __name__ == '__main__':
    main()
