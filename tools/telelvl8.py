#!/usr/bin/env python3
"""telelvl8.py -- THE TELEPORT PLAY REPORT, MEASURED ON A REAL LEVEL 8.

    python tools/telelvl8.py cache     build LEVEL 8 on the real Z80 and cache it
    python tools/telelvl8.py report    the play report's own square, per pass
    python tools/telelvl8.py escape    approach the pad, RELEASE, press the other way
    python tools/telelvl8.py pads      every $30 pad of this level, all four sides
    python tools/telelvl8.py exit36    teleporting ONTO an exit ends the level
    python tools/telelvl8.py all       all of the above

=============================================================================
WHY THIS TOOL EXISTS SEPARATELY FROM tools/telegate.py AND tools/padgate.py
=============================================================================
Those two plant a `$30` into DUNGEON 1, because build/state_48k.pkl is on
dungeon 1 and dungeon 1 has no teleport pad.  The play report is about LEVEL
8, and manual phase 16 says a play report tells you WHERE TO POINT THE
SIMULATOR -- so the simulator has to be pointed at level 8, not at level 1
with the level-8 furniture carried over by hand.

This tool drives the original's OWN level start ($B3D0 -> $9175 -> $97CB) with
the entropy register seeded to 5, and the pack draw that comes out has

    cell (1,19) = $00        <-- the play report's own square, free
    cell (2,20) = $30        <-- a teleport pad, down-and-right of it
    cell (1,24) = $30        and four more at (31,8) (30,12) (20,30) (23,31)

so the reported position (4,78) = cell (1,19), camera (2,60), is reproduced on
a map the GAME built, with pads the TAPE carries.  Nothing is planted except
in the `exit36` check, which says so.

Sampler: tools/sim_move.py's step_to_loop_top ($8503), one sample per pass.

=============================================================================
WHAT IT MEASURES  (this machine, build/state_48k.pkl -> $B3D0 with R=5)
=============================================================================
`report`  holding DOWN from (4,78): he walks to (4,92), ARMS on the pad at
          cell (1,24) on pass 10 ($842E bit 1, $843D = $30, $84AC = 2), and on
          pass 11 he is TELEPORTED to (8,84) = cell (2,21), the south
          neighbour of the OTHER pad.  Four transit passes ($8436 = 4,3,2,1
          with bit 2 set), released on pass 15, WALKING AGAIN on pass 16.

`escape`  arm on pass 10, teleport on 11, released on 15, re-arm on 16 from
          the far side of the second pad, teleported back on 17, released on
          21, and on pass 22 he walks UP.  The player is never held for more
          than five passes.

`pads`    all six pads x four approaches (ten of the twenty-four start squares
          are free): the BIT 1 phase string is `01111100000000` in every one,
          i.e. THE ARM IS FIVE PASSES LONG -- one to arm plus the four of
          $B20C's transit timer -- and the player MOVES in every one.

`exit36`  the same level with the destination cell (2,21) made a $36: the
          pending slot holds $36 for the whole flight (because $A4FF's JP
          bypasses $A514), $B232 fires on the release pass, (IX+11) bit 6 and
          (IX+$16) = $18 start the exit walk, and 24 passes later $8403 goes
          8 -> 9.  ANY PORT THAT CLEARS `pend` DURING THE FLIGHT LOSES THIS.

THE HEADLINE: there is no input, and no pad on this level, that leaves the
original's teleport arm set for a sixth pass.  "The original sticks too" is
false on level 8.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC                                  # noqa: E402
from sim_move import step_to_loop_top, KM, DIRKEY                # noqa: E402
from keyprobe import keymask                                     # noqa: E402
import packbuild                                                 # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
CACHE = os.path.join(ROOT, 'build', '_lvl8_r005_live.pkl')
GRID = os.path.join(ROOT, 'build', '_r005_lvl8.bin')
SEED_R = 5

PX, PY = 0x8420, 0x8421          # (IX)   (IX+1)
F11, CTL, XIT, PEND = 0x842B, 0x842E, 0x8436, 0x843D
CAMX, CAMY, LVL = 0x848B, 0x848C, 0x8403
PADN = 0x84AC                    # (IY+$2D) the pad census, rebuilt every pass
TIE = 0x84B0                     # (IY+$31) $B246's tie count

REPORT = (4, 78)                 # the play report's own x,y
REPORT_CAM = (2, 60)


# --------------------------------------------------------------------------
def cache(force=False):
    """Build LEVEL 8 through the original's own $B3D0 and cache the machine."""
    if os.path.exists(CACHE) and not force:
        return pickle.load(open(CACHE, 'rb'))
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    h.regs[15] = SEED_R                       # R -- the entropy $B575 folds in
    pc, sp = h.regs[PC], h.regs[12]
    packbuild.build(h, 8, rewind=True, watch=False)
    h.regs[PC], h.regs[12] = pc, sp           # back into the live main loop
    m = h.memobj.m
    assert m[LVL] == 8, 'not on level 8: $8403 = %d' % m[LVL]
    g = bytes(m[0x8000:0x8400])
    assert g[19 * 32 + 1] == 0x00, 'cell (1,19) is not free'
    assert g[20 * 32 + 2] == 0x30, 'cell (2,20) is not a $30'
    open(GRID, 'wb').write(g)
    st = h.save_state()
    pickle.dump(st, open(CACHE, 'wb'))
    print('LEVEL 8 built on the real Z80 ($8403 = %d), pads at %s'
          % (m[LVL], pads_of(g)))
    return st


def pads_of(g):
    return [(i % 32, i // 32) for i, b in enumerate(g) if b == 0x30]


def machine(st=None):
    h = Harness()
    h.load_state(st or cache())
    return h


def hold(h, d):
    h.ports.release_all()
    if d:
        sel, bit = KM[DIRKEY[d]]
        h.ports.press(sel, keymask(bit))


def park(h, x, y, cx=None, cy=None):
    """Put him on a square and let the camera settle without letting him walk."""
    cx = (x - 2) if cx is None else cx
    cy = (y - 18) if cy is None else cy
    for a, v in ((PX, x), (PY, y), (CAMX, max(0, cx)), (CAMY, max(0, cy))):
        h.poke(a, v)
    hold(h, None)
    step_to_loop_top(h)
    for _ in range(6):
        step_to_loop_top(h)
        h.poke(PX, x)
        h.poke(PY, y)


HDR = ('pass    x   y  cell    $842E b1 b2  pend  $8436  $84AC $84B0   pads '
       '(x,y,mask x4)')


def row(h, i):
    m = h.memobj.m
    pads = ' '.join('%02X' % b for b in m[0x5BD0:0x5BD8])
    return ('%4d  %3d %3d  (%2d,%2d)  %02X    %d  %d   %02X    %02X      %d     %d   %s'
            % (i, m[PX], m[PY], m[PX] >> 2, m[PY] >> 2, m[CTL],
               (m[CTL] >> 1) & 1, (m[CTL] >> 2) & 1, m[PEND], m[XIT],
               m[PADN], m[TIE], pads))


# --------------------------------------------------------------------------
def cmd_report(n=18):
    st = cache()
    h = machine(st)
    m = h.memobj.m
    g = bytes(m[0x8000:0x8400])
    print('THE PLAY REPORT, ON THE REAL Z80, ON LEVEL 8')
    print('  $8403 = %d   cell(1,19) = $%02X   cell(2,20) = $%02X   '
          'cell(1,24) = $%02X'
          % (m[LVL], g[19 * 32 + 1], g[20 * 32 + 2], g[24 * 32 + 1]))
    park(h, *REPORT, *REPORT_CAM)
    print('  parked at the reported (%d,%d), camera (%d,%d); holding DOWN'
          % (m[PX], m[PY], m[CAMX], m[CAMY]))
    print(HDR)
    hold(h, 'down')
    for i in range(n):
        step_to_loop_top(h)
        print(row(h, i + 1))
    print('EXPECT: arm on pass 10, TELEPORT to (8,84) on 11, transit 11..14,')
    print('        bit 1 CLEAR on 15, and MOVING AGAIN on 16.')


def cmd_escape(n1=10, n2=12):
    h = machine()
    park(h, *REPORT, *REPORT_CAM)
    print('APPROACH DOWN for %d passes, then RELEASE and press UP for %d'
          % (n1, n2))
    print(HDR)
    hold(h, 'down')
    for i in range(n1):
        step_to_loop_top(h)
        print(row(h, i + 1))
    print('  --- key released, UP pressed ---')
    hold(h, 'up')
    for i in range(n2):
        step_to_loop_top(h)
        print(row(h, n1 + i + 1))
    print('EXPECT: he is teleported back, released on pass 21, and WALKS UP on 22.')


FROM = {'down': (0, -1), 'up': (0, 1), 'right': (-1, 0), 'left': (1, 0)}


def cmd_pads(n=14):
    st = cache()
    g = bytes(st[0][0x8000:0x8400])
    print('EVERY $30 PAD OF THIS LEVEL 8, APPROACHED FROM ALL FOUR SIDES')
    print('pads:', pads_of(g))
    print()
    print('%-10s %-6s %-10s %-16s %-16s %-6s %s'
          % ('pad', 'from', 'start', 'BIT 1 per pass', '$84AC per pass',
             'moved', 'longest BIT 1 run'))
    worst = 0
    for p in pads_of(g):
        c, r = p
        for d in ('down', 'up', 'right', 'left'):
            dc, dr = FROM[d]
            x, y = ((c + dc) * 4) & 0x7F, ((r + dr) * 4) & 0x7F
            if g[(y >> 2) * 32 + (x >> 2)] != 0:
                print('%-10s %-6s (the start square is not free)' % (str(p), d))
                continue
            h = machine(st)
            m = h.memobj.m
            park(h, x, y)
            hold(h, d)
            b1, census, pos = [], [], []
            for _ in range(n):
                step_to_loop_top(h)
                b1.append((m[CTL] >> 1) & 1)
                census.append(m[PADN])
                pos.append((m[PX], m[PY]))
            run = mx = 0
            for v in b1:
                run = run + 1 if v else 0
                mx = max(mx, run)
            worst = max(worst, mx)
            print('%-10s %-6s (%3d,%3d)  %-16s %-16s %-6s %d'
                  % (str(p), d, x, y, ''.join(map(str, b1)),
                     ''.join('%X' % v for v in census),
                     'YES' if len(set(pos)) > 1 else 'no', mx))
    print()
    print('LONGEST UNBROKEN RUN OF BIT 1 ANYWHERE: %d passes' % worst)
    assert worst == 5, 'the arm is not five passes long: %d' % worst
    print('  == 5, the arm pass plus $B20C\'s four transit passes.  The')
    print('  original NEVER holds the player for a sixth pass.')


def cmd_exit36(n=42):
    st = cache()
    h = machine(st)
    m = h.memobj.m
    h.poke(0x8000 + 21 * 32 + 2, 0x36)        # PLANTED: the flight's landing cell
    park(h, *REPORT, *REPORT_CAM)
    print('THE SAME LEVEL 8 WITH ONE CELL PLANTED: (2,21) := $36, an EXIT.')
    print('  pass    x   y  cell    $842E  pend  $8436  (IX+11)  $8403')
    hold(h, 'down')
    for i in range(n):
        step_to_loop_top(h)
        print('  %4d  %3d %3d  (%2d,%2d)   %02X    %02X    %02X      %02X       %d'
              % (i + 1, m[PX], m[PY], m[PX] >> 2, m[PY] >> 2, m[CTL], m[PEND],
                 m[XIT], m[F11], m[LVL]))
        if m[LVL] != 8:
            print('  --- $B232 FIRED: the level ended, $8403 = %d ---' % m[LVL])
            return
    raise AssertionError('the level never ended -- $B232 did not fire')


def cmd_all():
    for f in (cmd_report, cmd_escape, cmd_pads, cmd_exit36):
        print('=' * 76)
        f()
        print()


if __name__ == '__main__':
    c = sys.argv[1] if len(sys.argv) > 1 else 'all'
    {'cache': lambda: cache(force=True), 'report': cmd_report,
     'escape': cmd_escape, 'pads': cmd_pads, 'exit36': cmd_exit36,
     'all': cmd_all}[c]()
