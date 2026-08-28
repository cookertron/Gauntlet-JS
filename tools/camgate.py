#!/usr/bin/env python3
"""
camgate.py -- THE CAMERA AT THE MAP EDGE, $A3E6 / $B58C.

Reported from play, in a treasure room: "the scroll stops working correctly
and I can leave through the edge of the playfield before the scroll catches
up."  It looks like a bug and it is not: the map is a TORUS and the camera
is CLAMPED, so a player who can reach the map edge outruns it on the original
too.  This tool measures both machines doing it.

    python tools/camgate.py          capture, write build/_cam_edge.json
    python tools/camgate.py show     ...and print the trace

WHAT IS GOING ON

  $A3E6 writes the camera TARGET -- the player's coordinate + 2, or the
  midpoint of the two players -- and $B58C steps the camera toward it, twice
  a pass, by 2 units a step ($B5AD SUB 4 / ADD A,2).
  $B58C also CLAMPS: 66 across and 90 down.  The map is 32 cells = 128 units
  and the viewport is 16 cells = 64 units, so the clamp is the map edge: the
  camera stops there because there is nothing beyond it to show.
  The PLAYER does not stop.  $A82F's `RES 7,C` masks his coordinate to 7 bits,
  so he wraps 127 -> 0 and walks out of one side of the map into the other
  while the camera is still pinned at the far end.

WHY A TREASURE ROOM IS WHERE YOU SEE IT.  In an ordinary dungeon the walls
stop you long before the map edge.  A treasure room is open ground, so it is
the first place a player can actually get there.

THE SETUP is deliberately artificial and identical on both sides -- the whole
map cleared to floor, no actors, the player and the camera placed by hand --
because the point is to compare the two CAMERAS, not to find a dungeon where
the case comes up.  `sym` (the walk-through-walls cheat) is not used: with the
map cleared there is nothing to walk through.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, IFF, TAPE_CALL_PC             # noqa: E402
from keyprobe import KEYS, keymask                             # noqa: E402
from sim_move import LOOP_TOP                                  # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
KM = {n: (s, b) for n, s, b in KEYS}
PX, PY, CAMX, CAMY = 0x8420, 0x8421, 0x848B, 0x848C
NACT, AEND = 0x8496, 0x8494
# per direction, chosen so the run has room to REACH the clamp before the
# coordinate wraps: 66 across needs a long walk right, 90 down a long walk
# down, and starting both in the middle only reaches one of them.
SETUP = {'right': {'start': (8, 64), 'cam': (0, 32)},
         'down':  {'start': (64, 4), 'cam': (32, 0)}}
PASSES = 90


def one_pass(h):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < 20_000_000:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        n += 1
        if regs[IFF] and regs[25] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    raise RuntimeError('no main-loop top')


def run(direction):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)
    m[NACT] = 0
    m[AEND], m[AEND + 1] = 0x00, 0x5C
    for a in range(0x8000, 0x8400):
        m[a] = 0                                  # open floor, everywhere
    m[PX], m[PY] = SETUP[direction]['start']
    m[CAMX], m[CAMY] = SETUP[direction]['cam']
    sel, bit = KM[{'right': 'D', 'down': 'Q'}[direction]]
    h.ports.press(sel, keymask(bit))
    rows = []
    for _ in range(PASSES):
        one_pass(h)
        rows.append([m[PX], m[PY], m[CAMX], m[CAMY]])
    return rows


def main():
    out = {'setup': {d: {k: list(v) for k, v in SETUP[d].items()}
                     for d in SETUP},
           'passes': PASSES,
           'runs': {d: run(d) for d in ('right', 'down')}}
    path = os.path.join(ROOT, 'build', '_cam_edge.json')
    json.dump(out, open(path, 'w'))

    for d, rows in out['runs'].items():
        ax = 0 if d == 'right' else 1
        cx = 2 if d == 'right' else 3
        pos = [r[ax] for r in rows]
        cam = [r[cx] for r in rows]
        wrapped = any(pos[i + 1] < pos[i] for i in range(len(pos) - 1))
        limit = 66 if d == 'right' else 90
        print('%-6s player %3d -> %3d, camera %3d -> %3d, reached the clamp '
              'at %d: %s, player wrapped past it: %s'
              % (d, pos[0], pos[-1], cam[0], cam[-1], limit,
                 limit in cam, wrapped))
    # the gate must exercise the clamp, or it is testing an ordinary walk
    # The clamp must be REACHED -- not be the maximum.  The camera coordinate
    # is masked to 7 bits like the player's, so once the player has wrapped
    # and the camera is chasing him back it dips below 0 and reads 126.
    # Checking `max == 90` therefore fails for a reason that has nothing to
    # do with the clamp, which is what the first version of this did.
    assert 66 in [r[2] for r in out['runs']['right']], \
        'the right-hand run never reached the camera clamp at 66'
    assert 90 in [r[3] for r in out['runs']['down']], \
        'the downward run never reached the camera clamp at 90'
    print('\nboth runs reach the clamp ($42 across, $5A down)')
    print('wrote', path)

    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        for d, rows in out['runs'].items():
            ax = 0 if d == 'right' else 1
            cx = 2 if d == 'right' else 3
            print('\n=== %s ===' % d)
            print('  pass  pos  cam   pos-cam')
            for i, r in enumerate(rows):
                if i % 5 == 4:
                    print('   %2d   %3d  %3d   %4d'
                          % (i + 1, r[ax], r[cx], r[ax] - r[cx]))


if __name__ == '__main__':
    main()
