#!/usr/bin/env python3
"""
filmstrip.py -- render consecutive frames side by side and LOOK at them
(manual G7: "for anything visual, render a filmstrip of consecutive frames and
LOOK at it -- thirty lines of code that settles arguments histograms cannot").

Usage:
    python tools/filmstrip.py out.png [--state build/state_charsel.pkl]
                              [--keys Z:20,D:60] [--frames 12] [--every 6]
"""
import os
import pickle
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC      # noqa: E402
from keyprobe import KEYS, keymask                          # noqa: E402
import screen                                               # noqa: E402

KEYMAP = {name: (sel, bit) for name, sel, bit in KEYS}
FRAME_T = 69888


def run_frames(h, n):
    """Advance n video frames (the game HALTs twice per pass, so frames are
    the natural unit)."""
    target = h.regs[T] + n * FRAME_T
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    while regs[T] < target:
        pc = regs[PC]
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
    args = sys.argv[1:]
    out = args[0]
    del args[:1]
    state = os.path.join(ROOT, 'build', 'state_charsel.pkl')
    keys = []
    frames = 12
    every = 6
    cols = 4
    while args:
        if args[0] == '--state':
            state = args[1]; del args[:2]
        elif args[0] == '--keys':
            keys = [k for k in args[1].split(',') if k]; del args[:2]
        elif args[0] == '--frames':
            frames = int(args[1]); del args[:2]
        elif args[0] == '--every':
            every = int(args[1]); del args[:2]
        elif args[0] == '--cols':
            cols = int(args[1]); del args[:2]
        else:
            del args[:1]

    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    for k in keys:
        name, _, _n = k.partition(':')
        sel, bit = KEYMAP[name.upper()]
        h.ports.press(sel, keymask(bit))

    shots = []
    for i in range(frames):
        run_frames(h, every)
        shots.append(screen.render(h.memobj.m))
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * 258, rows * 194), (40, 40, 40))
    for i, im in enumerate(shots):
        sheet.paste(im, ((i % cols) * 258 + 1, (i // cols) * 194 + 1))
    sheet.save(out)
    print(f'wrote {out}  ({len(shots)} frames, {every} video frames apart, '
          f'keys={keys or "none"})')


if __name__ == '__main__':
    main()
