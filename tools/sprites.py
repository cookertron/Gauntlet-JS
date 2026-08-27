#!/usr/bin/env python3
"""
sprites.py -- recover the player's sprite SET (per direction, per animation
frame) by instrumenting the blitter rather than by isolating one frame.

The earlier extraction took (real screen AND NOT shadow screen) at the player's
computed position.  That yields ONE frame -- whatever he happened to look like
at that instant -- with no direction and no animation, which is why he did not
look right.

The player is drawn by the same blitter as everything else ($9DEC, which uses
SP as a fast data pointer), into the SHADOW screen, before the shadow is
blitted to the real screen and the background restored underneath him.  So the
way to get the real artwork is to record (SP, HL) at $9DEC and keep the ones
whose destination is the player's own screen position.

Manual D9 applies to what we do with them: frame selection is a RULE, often
positional rather than temporal, and it must be read out of the draw routine
rather than assumed to be a counter.

Usage:  python tools/sprites.py [--passes 40]
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, H, L, TAPE_CALL_PC)   # noqa: E402
from keyprobe import KEYS, keymask                                   # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
FRAME_T = 69888
SHADOW_BMP = 0xC000
P_X, P_Y, CAM_X, CAM_Y = 0x8420, 0x8421, 0x848B, 0x848C
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}


def addr_to_xy(a, base=SHADOW_BMP):
    """Inverse of the display-file formula (manual 3.2)."""
    o = a - base
    third = (o >> 11) & 3
    line = (o >> 8) & 7
    row = (o >> 5) & 7
    col = o & 31
    return col * 8, third * 64 + row * 8 + line


def capture(direction, passes):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if direction:
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    m = h.memobj.m
    fd, ia = h.frame_duration, h.int_active
    found = {}          # source -> count, for blits landing on the player
    end = regs[T] + passes * 4 * FRAME_T
    while regs[T] < end:
        pc = regs[PC]
        if pc == 0x9DEC:
            dst = (regs[H] << 8) | regs[L]
            if SHADOW_BMP <= dst < SHADOW_BMP + 0x1800:
                dx, dy = addr_to_xy(dst)
                psx = (m[P_X] - m[CAM_X]) * 4
                psy = (m[P_Y] - m[CAM_Y]) * 4
                # the blit starts at the sprite's top-left; allow the row
                # within the 16-pixel cell
                if abs(dx - psx) <= 8 and 0 <= (dy - psy) < 16:
                    src = regs[SP]
                    found[src] = found.get(src, 0) + 1
        if pc == TAPE_CALL_PC:
            h._tape(); continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    return h, found


def main():
    passes = 40
    if '--passes' in sys.argv:
        passes = int(sys.argv[sys.argv.index('--passes') + 1])

    out = {}
    for direction in ('right', 'left', 'up', 'down', None):
        h, found = capture(direction, passes)
        name = direction or 'idle'
        srcs = sorted(found, key=lambda s: -found[s])
        print(f'{name:>6}: {len(srcs)} distinct sprite sources  ' +
              ' '.join(f'${s:04X}x{found[s]}' for s in srcs[:8]))
        m = h.memobj.m
        out[name] = []
        for s in srcs:
            # each POP consumes 2 bytes; a 16x16 cell is 16 rows x 2 bytes.
            # The blitter walks the source FORWARD from SP.
            out[name].append({'src': f'${s:04X}', 'hits': found[s],
                              'bitmap': list(m[s:s + 32])})
    path = os.path.join(ROOT, 'build', 'player_sprites.json')
    json.dump(out, open(path, 'w'), indent=1)
    print(f'wrote {path}')


if __name__ == '__main__':
    main()
