#!/usr/bin/env python3
"""blitprobe.py -- where does the sprite draw actually WRITE?  Histogram every
write above $4000 by (writer PC -> destination region) for N main-loop passes.

Uses harness.Mem.watch(), NOT a wrapper: SkoolKit's Simulator binds
self.memory into its opcode partials at construction, so replacing
sim.memory afterwards silently logs nothing (measured: 6 writes/pass)."""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                   # noqa: E402
from keyprobe import KEYS, keymask                            # noqa: E402
from filmstrip import run_frames                              # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}


def region(a):
    if 0x4000 <= a < 0x5800:
        return 'REAL_BMP'
    if 0x5800 <= a < 0x5B00:
        return 'REAL_ATTR'
    if 0xC000 <= a < 0xD800:
        return 'SHDW_BMP'
    if 0xD800 <= a < 0xDB00:
        return 'SHDW_ATTR'
    return f'{a >> 12:X}xxx'


def main():
    direction = sys.argv[1] if len(sys.argv) > 1 else 'idle'
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    run_frames(h, 32)
    h.memobj.watch(0x4000, 0x10000)
    run_frames(h, 4 * passes)
    h.memobj.unwatch()
    log = h.memobj.log
    hist = {}
    for pc, a, v in log:
        k = (pc, region(a))
        hist[k] = hist.get(k, 0) + 1
    print(f'{len(log)} writes above $4000 in {passes} pass(es) ({direction})')
    for (pc, r), n in sorted(hist.items(), key=lambda kv: -kv[1])[:45]:
        print(f'  ${pc:04X}  {r:<10} {n}')


if __name__ == '__main__':
    main()
