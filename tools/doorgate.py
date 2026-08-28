#!/usr/bin/env python3
"""
doorgate.py -- drive the REAL Z80 through a door opening and print the map,
pass by pass, so the port's $95AB transcription can be gated cell for cell.

    python tools/doorgate.py            the dungeon's own door run, opened
    python tools/doorgate.py plant      a synthetic cross of door cells

Nothing here loads the engine.  It prints the ORIGINAL's answers; the engine
is driven through the same scenario by tools/headless.js and compared.

WHY THIS EXISTS.  $A6D4 -- the door interaction -- does NOT clear the cell.
It spends a key, seeds two cursors at $849A/$849C and sets $849E = $FD; the
erasing is done afterwards by $8506 CALL $95AB, which walks both cursors out
of the opened cell and clears every adjacent door cell it can reach.  So the
observable behaviour is spread over several passes and over several cells, and
"the door opens" is not one write.  MEASURED here rather than reasoned about.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC          # noqa: E402
from keyprobe import KEYS, keymask                             # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LOOP_TOP = 0x8503


def looptop(h):
    sim = h.sim
    regs, opc, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < 8_000_000:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opc[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError


def run(plant, passes=8, keys=3, direction='down', start=None, window=None):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    m[0x8496] = 0                      # actors off: they only add noise here
    m[0x8428] = keys                   # (IX+8) -- enough keys to open it
    for (c, r), v in plant.items():
        m[0x8000 + r * 32 + c] = v
    if start:
        m[0x8420], m[0x8421] = start
    sel, bit = KM[DIRKEY[direction]]
    h.ports.press(sel, keymask(bit))
    looptop(h)
    out = []
    for i in range(passes):
        looptop(h)
        cells = {(c, r): m[0x8000 + r * 32 + c] for (c, r) in window}
        out.append(dict(pass_=i + 1, ctr=m[0x8491], x=m[0x8420], y=m[0x8421],
                        keys=m[0x8428], slot=m[0x843D], state=m[0x849E],
                        curA=(m[0x849A], m[0x849B]), curB=(m[0x849C], m[0x849D]),
                        cells=cells))
    return out


def show(rows, window):
    print('pass ctr   x   y keys slot $849E  cells ' +
          ' '.join(f'({c},{r})' for c, r in window))
    for d in rows:
        cs = ' '.join(f'  ${d["cells"][k]:02X} ' for k in window)
        print(f'{d["pass_"]:>4}{d["ctr"]:>4}{d["x"]:>4}{d["y"]:>4}{d["keys"]:>5}'
              f'   ${d["slot"]:02X}   ${d["state"]:02X}        {cs}')


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'plant'
    if what == 'plant':
        # a horizontal run of six door cells with one hanging below, so both
        # cursors have somewhere to go and the vertical branch is exercised
        row, col = 10, 3
        plant = {(col + i, row): 0x11 for i in range(4)}
        plant[(col, row + 1)] = 0x11
        window = [(col - 1, row), (col, row), (col + 1, row), (col + 2, row),
                  (col + 3, row), (col, row + 1)]
        rows = run(plant, passes=8, start=(col * 4, (row - 3) * 4),
                   window=window)
        show(rows, window)
    else:
        # the dungeon's own vertical door: the single $12 in column 9
        m = None
        h = Harness(); h.load_state(pickle.load(open(STATE, 'rb')))
        grid = h.memobj.m
        found = [(c, r) for r in range(32) for c in range(32)
                 if grid[0x8000 + r * 32 + c] in (0x11, 0x12)]
        print('door cells in dungeon 1:', found)


if __name__ == '__main__':
    main()
