#!/usr/bin/env python3
"""
harvest.py -- recover a WHOLE level by walking the camera over it and unioning
the in-view object lists.

Why this and not a decode of the tape pack: the pack is a variable-length
record list expanded into the shadow screen, and its encoding is not yet
closed.  The manual's Q7 says exactly what to do in that case -- "FOR ANYTHING
PACKED, THE PRIMARY PATH IS TO READ THE LEVELS OUT OF THE LIVE BUFFER in the
simulator... It always works and it costs an afternoon."  The game keeps only
the objects near the camera in its scan list at $5C00 (count at $8496), so the
whole level is the union of those lists over every camera position.

Positions are reached by POKING the player's coordinates ($8420/$8421) and
letting the game rebuild its own view -- manual 6.8, constructing scenarios by
poking rather than by playing.

Usage:  python tools/harvest.py [--step 16] [--settle 3] [--out build/level0.json]
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                   # noqa: E402
from filmstrip import run_frames                              # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
P_X, P_Y = 0x8420, 0x8421
OBJ_BASE, OBJ_COUNT = 0x5C00, 0x8496
CAM_X = 0x848B


def main():
    args = sys.argv[1:]
    step = 16
    settle = 3
    out = os.path.join(ROOT, 'build', 'level0.json')
    while args:
        if args[0] == '--step':
            step = int(args[1], 0); del args[:2]
        elif args[0] == '--settle':
            settle = int(args[1], 0); del args[:2]
        elif args[0] == '--out':
            out = args[1]; del args[:2]
        else:
            del args[:1]

    base = pickle.load(open(STATE, 'rb'))
    h = Harness()
    seen = {}
    visited = 0
    for py in range(0, 256, step):
        for px in range(0, 256, step):
            h.load_state(base)
            h.poke(P_X, px)
            h.poke(P_Y, py)
            run_frames(h, 4 * settle)
            m = h.memobj.m
            n = m[OBJ_COUNT]
            visited += 1
            for i in range(n):
                a = OBJ_BASE + i * 4
                x, y, t, f = m[a], m[a + 1], m[a + 2], m[a + 3]
                seen.setdefault((x, y), (t, f))
        print(f'  y={py:3d}  cumulative objects: {len(seen)}')

    objs = [[x, y, t, f] for (x, y), (t, f) in sorted(seen.items())]
    types = {}
    for _, _, t, _ in objs:
        types[t] = types.get(t, 0) + 1
    payload = {
        'source': 'union of the in-view object lists at $5C00 over a poked '
                  'camera sweep; see tools/harvest.py',
        'grid_step': step,
        'camera_positions': visited,
        'count': len(objs),
        'fields': ['x', 'y', 'type', 'flags'],
        'objects': objs,
        'type_histogram': {f'0x{k:02X}': v for k, v in sorted(types.items())},
    }
    json.dump(payload, open(out, 'w'), indent=1)
    print(f'\n{len(objs)} distinct objects from {visited} camera positions')
    print('type histogram: ' +
          ' '.join(f'${k:02X}:{v}' for k, v in sorted(types.items())))
    xs = [o[0] for o in objs]
    ys = [o[1] for o in objs]
    print(f'extent: x {min(xs)}..{max(xs)}   y {min(ys)}..{max(ys)}')
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
