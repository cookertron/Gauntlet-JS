#!/usr/bin/env python3
"""
telegate.py -- THE TELEPORT-PAD DIFFERENTIAL, carrying the ORIGINAL's own
answer to the level-8 "stuck beside a teleporter" play report.

    python tools/telegate.py all        every check below
    python tools/telegate.py lone       the lone-pad stick, per pass
    python tools/telegate.py pair       the two-pad teleport, per pass
    python tools/telegate.py enum       8 pad placements x 4 directions
    python tools/telegate.py gate       the BIT 1 phase table
    python tools/telegate.py window     where a destination pad must be

This tool drives the REAL Z80 only; it never loads the engine.  Every number
it prints was measured before it was written down, and the expectations below
are those measurements, not a design.

=============================================================================
WHAT THE ORIGINAL ACTUALLY DOES WITH A $30 TELEPORT PAD
=============================================================================
The chain, read off the machine:

    $A8FC  CP $20 / ... / $A912 CP $33 / JR c,$A919
                              -- $2F..$32 RECORD, they do not block
    $A919  (IX+$1E/$1F) := the cell, (IX+$1D) := $30, carry CLEAR
    $A61A  a non-zero (IX+$1D) REFUSES the commit at $A620
    $A65D -> $A673 CP $30 / JR z,$A6B0
    $A6B0  SET 1,(IX+14) / RET          -- and NOTHING else

    $A4FF  BIT 1,(IX+14) / $A503 JP nz,$B195
           -- so the NEXT pass's whole player move is REPLACED by the
              teleport state machine, before $A514 clears the pending slot

    $B195  BIT 1,(IX+14) / RET z
    $B19A  BIT 2,(IX+14) / JR nz,$B21D        -- already in flight
    $B1A0  LD A,($84AC) / OR A / JR z,$B218   -- no pads on screen
    $B1A8  CALL $B246                         -- score the pads
    $B1B3  (IY+$31) == 0 -> $B216/$B218       -- no destination
    $B218  RES 1,(IX+14) / RET                -- ABORT: the flag is CLEARED
    $B1FF  (IX+$1D):=E, (IX):=C, (IX+1):=B, SET 2,(IX+14), (IX+$16):=4,
           sfx 11                             -- the teleport COMMITS
    $B21D  DEC (IX+$16) / RET nz ... RES 2 / RES 1   -- 4 more frozen passes

$84AC is NOT a constant 0.  It is the number of teleport pads DRAWN this pass,
and it is rebuilt from scratch every pass:

    $A3DC  LD HL,$5BD0 / LD ($84AD),HL / LD (IY+$2D),0    -- right after the
           player move, BEFORE the map draws at $850F..$8518
    $B156  CP $30 / RET nz                    -- the map-draw hook, called
           from eight sites in $9EFC..$A1D9 with HL = the map cell
    $B159  BIT 1,(IY-$51) / BIT 1,(IY-$31)    -- only while a teleport is
           PENDING for player 1 or player 2
    $B164  BIT 3,(IY+$2D) / RET nz            -- at most 8 entries
    $B179  (HL):=x, (HL+1):=y, (HL+2):=$B2B6's free-neighbour mask
    $B181  OR A / JR z,$B18D                  -- a pad with no free neighbour
                                                 is NOT counted
    $B18A  INC (IY+$2D)                       -- i.e. $84AC

$B246 then scores each entry by $8469[|dx|/4] + $8469[|dy|/4], and $8469 is a
table of SQUARES -- squared cell distance.  `$B27C JR z` throws away a score
of zero, which is the pad the player is standing against, so a pad can never
be its own destination.  Nothing else on screen means no destination.

MEASURED CONSEQUENCE, and it is the answer to the play report:

  * The original DOES stick -- for ever -- but only on the axis whose probe
    hits the pad, and only while no OTHER pad is on screen.  400 passes
    holding LEFT into a lone pad leaves the player on exactly one square.
  * (IX+14) bit 1 ALTERNATES set/clear with period 2, because $B218 clears it
    on every teleport-machine pass.  It is not a latch.
  * The other three directions move on the very next pass, at full speed.
  * With a second pad ON SCREEN the teleport fires: 1 refused pass, then the
    jump, then 4 frozen passes ((IX+$16) 4..1), then normal movement.
  * At the REPORTED (4,78) the vertical BIT 1 gate is CLOSED (y mod 4 == 2),
    so up and down commit whatever is planted around him.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                   # noqa: E402
from keyprobe import KEYS, keymask                            # noqa: E402
from sim_move import step_to_loop_top, DIRKEY, STATE          # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
P = 0x8420                       # player 1's block
DIRS = ('up', 'down', 'left', 'right')

# the play report's own numbers
RX, RY, RCAMX, RCAMY = 4, 78, 2, 60

_fails = []


def check(label, got, want):
    ok = got == want
    print('  %-58s %s' % (label, 'OK' if ok else 'FAIL'))
    if not ok:
        print('      got  %r' % (got,))
        print('      want %r' % (want,))
        _fails.append(label)
    return ok


def cell(cx, cy):
    return 0x8000 + (cy & 31) * 32 + (cx & 31)


def setup(x=RX, y=RY, camx=RCAMX, camy=RCAMY, pads=(), grid=None, level=8):
    """A live in-play state with a chosen map, player square and camera.

    The map is the only thing the movement gates read besides the player block
    and the actor list, so replacing $8000..$83FF wholesale is a faithful way
    to put the original on a chosen dungeon geometry.  The actor list is
    emptied so that $A97F cannot be mistaken for the map or the pad.
    """
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    m[0x8000:0x8400] = bytes(grid if grid is not None else bytes(1024))
    m[0x8403] = level
    m[P], m[P + 1] = x, y
    m[0x848B], m[0x848C] = camx, camy
    m[0x8496] = 0                                  # no actors
    m[0x8494], m[0x8495] = 0x00, 0x5C
    m[P + 8] = 9                                   # keys: doors are not the story
    m[P + 0x0E] = 0
    m[P + 0x1D] = 0
    m[0x84AC] = 0
    m[0x84AD], m[0x84AE] = 0xD0, 0x5B
    for c in pads:
        m[cell(*c)] = 0x30
    return h, m


def hold(h, m, direction, n):
    h.ports.release_all()
    if direction:
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    rows = []
    for _ in range(n):
        step_to_loop_top(h)
        rows.append((m[P], m[P + 1], m[P + 0x1D], m[P + 0x0E], m[P + 0x16],
                     m[0x84AC], m[P + 0x0D]))
    return rows


# --------------------------------------------------------------------------
def cmd_lone(npass=8, show=True):
    """A LONE pad west of the reported square: the original sticks for ever."""
    h, m = setup(pads=[(0, 19)])
    step_to_loop_top(h)
    rows = hold(h, m, 'left', npass)
    if show:
        print('# ORIGINAL, lone $30 at cell (0,19), player (4,78), HOLD LEFT')
        print('  pass   x   y  cell   $1D  (IX+14)  (IX+$16)  $84AC')
        for i, r in enumerate(rows):
            print('  %4d %3d %3d (%2d,%2d)  $%02X   $%02X       %3d      %2d'
                  % (i + 1, r[0], r[1], r[0] >> 2, r[1] >> 2, r[2], r[3],
                     r[4], r[5]))
    check('lone pad: the player never leaves (4,78)',
          sorted(set((r[0], r[1]) for r in rows)), [(4, 78)])
    check('lone pad: (IX+$1D) is $30 on every pass',
          sorted(set(r[2] for r in rows)), [0x30])
    check('lone pad: (IX+14) bit 1 ALTERNATES, it does not latch',
          [(r[3] >> 1) & 1 for r in rows], [(i + 1) % 2 for i in range(npass)])
    check('lone pad: $84AC alternates 1,0 -- the pad is drawn, then not',
          [r[5] for r in rows], [1 - i % 2 for i in range(npass)])
    check('lone pad: (IX+$16) stays 0 -- no teleport is ever in flight',
          sorted(set(r[4] for r in rows)), [0])


def cmd_pair(npass=8, show=True):
    """A SECOND pad on screen: the original teleports, it does not stick."""
    h, m = setup(pads=[(0, 19), (6, 19)])
    step_to_loop_top(h)
    rows = hold(h, m, 'left', npass)
    if show:
        print('# ORIGINAL, $30 at (0,19) AND (6,19), player (4,78), HOLD LEFT')
        print('  pass   x   y  cell   $1D  (IX+14)  (IX+$16)  $84AC')
        for i, r in enumerate(rows):
            print('  %4d %3d %3d (%2d,%2d)  $%02X   $%02X       %3d      %2d'
                  % (i + 1, r[0], r[1], r[0] >> 2, r[1] >> 2, r[2], r[3],
                     r[4], r[5]))
    check('pair: pass 1 is refused with (IX+$1D) = $30',
          (rows[0][0], rows[0][1], rows[0][2]), (4, 78, 0x30))
    check('pair: pass 2 COMMITS the jump to cell (5,19), beside the far pad',
          (rows[1][0], rows[1][1]), (20, 76))
    check('pair: (IX+14) bit 2 is set in flight',
          [(r[3] >> 2) & 1 for r in rows[1:5]], [1, 1, 1, 1])
    check('pair: (IX+$16) counts 4,3,2,1 then the flags clear',
          [r[4] for r in rows[:6]], [0, 4, 3, 2, 1, 0])
    check('pair: normal movement resumes on pass 7',
          (rows[6][0], rows[6][1]), (18, 76))


def cmd_enum(npass=14):
    """D4: one pad in each of the eight neighbours x each of four directions."""
    cx, cy = RX >> 2, RY >> 2
    neigh = [('W', (cx - 1, cy)), ('E', (cx + 1, cy)),
             ('N', (cx, cy - 1)), ('S', (cx, cy + 1)),
             ('NW', (cx - 1, cy - 1)), ('NE', (cx + 1, cy - 1)),
             ('SW', (cx - 1, cy + 1)), ('SE', (cx + 1, cy + 1)),
             ('-', None)]
    print('# ORIGINAL at the reported (4,78): which moves does a pad refuse?')
    print('  pad  cell      up     down   left   right')
    refusing = {}
    for name, pc in neigh:
        row = []
        for d in DIRS:
            h, m = setup(pads=([pc] if pc else []))
            step_to_loop_top(h)
            rows = hold(h, m, d, npass)
            moved = (rows[-1][0], rows[-1][1]) != (RX, RY)
            row.append('move' if moved else 'STUCK')
        refusing[name] = tuple(row)
        print('  %-4s %-9s %-6s %-6s %-6s %-6s'
              % (name, str(pc) if pc else '', *row))
    # Measured: at y mod 4 == 2 the vertical BIT 1 gate is shut, so up and
    # down commit whatever is planted; only the pad the horizontal probe
    # reaches can refuse, and the second probe is DOWN-and-right.
    check('up and down always move at (4,78), whatever is planted',
          sorted(set(r[0] for r in refusing.values()) |
                 set(r[1] for r in refusing.values())), ['move'])
    check('W and SW refuse LEFT; E and SE refuse RIGHT; nothing else refuses',
          {k: v for k, v in refusing.items() if 'STUCK' in v},
          {'W': ('move', 'move', 'STUCK', 'move'),
           'E': ('move', 'move', 'move', 'STUCK'),
           'SW': ('move', 'move', 'STUCK', 'move'),
           'SE': ('move', 'move', 'move', 'STUCK')})


def cmd_gate(npass=14):
    """The BIT 1 gate, per sub-cell phase.  This is why (4,78) cannot lock."""
    print('# ORIGINAL: which directions a neighbouring pad can refuse, by the')
    print('# sub-cell phase of the player.  $A82F/$A85D BIT 1,C and')
    print('# $A88B/$A8B9 BIT 1,B consult the map only every other 2-unit step.')
    print('  (x%4,y%4)  at        refused')
    table = {}
    for px in range(4):
        for py in range(4):
            x, y = 4 + px, 76 + py
            cx, cy = x >> 2, y >> 2
            refused = []
            for d, pc in (('left', (cx - 1, cy)), ('right', (cx + 1, cy)),
                          ('up', (cx, cy - 1)), ('down', (cx, cy + 1))):
                h, m = setup(x=x, y=y, pads=[pc])
                step_to_loop_top(h)
                rows = hold(h, m, d, npass)
                if (rows[-1][0], rows[-1][1]) == (x, y):
                    refused.append(d)
            table[(px, py)] = tuple(refused)
            print('  (%d,%d)      (%3d,%3d)  %s'
                  % (px, py, x, y, ','.join(refused) or 'none'))
    check('horizontal is gated by x mod 4 in {0,1}',
          sorted(set(k[0] for k, v in table.items() if 'left' in v)), [0, 1])
    check('vertical is gated by y mod 4 in {0,1}',
          sorted(set(k[1] for k, v in table.items() if 'up' in v)), [0, 1])
    check('the reported y mod 4 == 2 refuses NO vertical move',
          [v for k, v in table.items() if k[1] == 2],
          [('left', 'right'), ('left', 'right'), (), ()])


def cmd_window(npass=4):
    """WHERE the second pad has to be.  It is the visible playfield, exactly."""
    print('# ORIGINAL: sweep a second pad and ask whether the teleport fires.')
    print('# camera (2,60) -> the playfield is 16x10 cells at (0..15,15..24).')

    def probe(second):
        h, m = setup(pads=[(0, 19), second])
        sel, bit = KM[DIRKEY['left']]
        h.ports.press(sel, keymask(bit))
        step_to_loop_top(h)
        tp = False
        for _ in range(npass):
            step_to_loop_top(h)
            tp = tp or bool(m[P + 0x0E] & 4) or (m[P], m[P + 1]) != (RX, RY)
        return tp

    cols = [c for c in range(1, 32) if probe((c, 19))]
    rows = [r for r in range(32) if probe((8, r))]
    print('  columns that work, on row 19: %s' % cols)
    print('  rows that work, in column 8 : %s' % rows)
    check('a destination pad must be in the visible columns 1..15',
          cols, list(range(1, 16)))
    check('a destination pad must be in the visible rows 15..24',
          rows, list(range(15, 25)))


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    todo = ([cmd_lone, cmd_pair, cmd_enum, cmd_gate, cmd_window]
            if which == 'all' else
            [{'lone': cmd_lone, 'pair': cmd_pair, 'enum': cmd_enum,
              'gate': cmd_gate, 'window': cmd_window}[which]])
    for fn in todo:
        print()
        fn()
    print()
    print('%d failing check(s)' % len(_fails))
    return 1 if _fails else 0


if __name__ == '__main__':
    sys.exit(main())
