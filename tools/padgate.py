#!/usr/bin/env python3
"""padgate.py -- THE TELEPORT PAD ($30), measured on the REAL Z80.

    python tools/padgate.py            # all four scenarios

Written for the play report "level 8, the character gets stuck beside a
teleporter and can rotate and fire but not move" (player 4,78 / cell 1,19 /
camera 2,60).  The first question a play report asks is not "how do I fix it"
but "DOES THE ORIGINAL DO THIS TOO?", and this tool is the answer.

=============================================================================
WHAT IT PLANTS AND WHY
=============================================================================
Dungeon 1 has no $30 cell and there is no way to reach a dungeon that does
without walking the tape, so the pad is PLANTED, cell by cell, the way
tools/doorgate.py plants a door.  The player starts at (12,8) = cell (3,2);
holding DOWN, the candidate y=10 has bit 1 SET, so $A88B's first probe steps
the map cursor one cell down to (3,3) -- $8063 -- and that is where the $30
goes.  A second pad at cell (8,6) = $80C8 gives $B246 somewhere to send him.

=============================================================================
THE FOUR SCENARIOS, AND WHAT EACH ONE SEPARATES
=============================================================================
1. CONTROL, no plant                     -- the player walks, nothing is armed.
2. ONE pad, hold DOWN                    -- he does not move.  This is the
   scenario an earlier write-up ran, and it is why the engine's `$30 pad ->
   stuck` note says "the original sticks too".
3. ONE pad, hold DOWN twice, THEN HOLD UP -- the experiment nobody ran, and
   the one that tells the two candidate models apart.  A permanent freeze and
   a per-pass arm/clear cycle produce the SAME table in scenario 2 and
   different tables here.
4. TWO pads, hold DOWN                   -- the level-8 case: $84AC is
   non-zero and $B195 actually teleports.

=============================================================================
THE COLUMNS
=============================================================================
$842E   the player's +$0E control byte.  bit 1 = "standing on a pad" ($A6B0
        SET 1), bit 2 = "in transit" ($B208 SET 2).  The high bits are the
        walk phase and they cycle; ignore them.
$84AC   THE PAD CENSUS COUNT, and it is rebuilt EVERY PASS:
          $A3DC  LD HL,$5BD0 / LD ($84AD),HL / $A3E2 LD (IY+$2D),0
        clears it just after the moves, and then the MAP BLITTER appends every
        $30 cell it actually DRAWS --  $9F74 CP $30 / CALL z,$B159, plus eight
        `CALL $B156` sites in the other blit loops.  $B159 does nothing unless
        a player has bit 1 set, and $B164 BIT 3,(IY+$2D) caps the list at 8.
        So $84AC is "how many teleport pads were on screen last pass", which
        is why it is 0 in dungeon 1 (no $30 cells at all) and why the pad you
        are standing on always counts.
$843D   the pending-interaction slot (IX+$1D).  It stays $30 across the armed
        pass AND the $B195 pass, because $A4FF jumps out before $A514 clears
        it; it is a SYMPTOM of the arm, not the thing that blocks the move.
$8436   (IX+$16), the transit countdown $B20C loads with 4.
$5BD0.. the census list itself, 3 bytes per pad: x, y, exit-direction mask.

=============================================================================
WHAT IT MEASURES  (this machine, build/state_48k.pkl)
=============================================================================
2. ONE pad, DOWN x12: the player never moves, and $842E bit 1 ALTERNATES
   1,0,1,0,... every pass.  $84AC is 1 on the armed passes, so $B195 does NOT
   bail at $B1A4; it runs $B246, which SKIPS the pad the player is standing on
   ($B27C JR z), leaves $84B0 = 0, and falls to $B216 POP IX / $B218
   RES 1,(IX+14).  Two passes per cycle, arm and clear.
3. ONE pad, DOWN x2 then UP: pass 3 moves to (12,6), pass 4 to (12,4).
   HE IS FREE.  The freeze is not a freeze; it is an inability to step ONTO
   the pad, and only while you hold that direction.
4. TWO pads, DOWN: pass 1 arms, PASS 2 TELEPORTS -- (12,8) -> (32,28), bit 2
   set, $8436 = 4, and the countdown runs 4,3,2,1 before $B221/$B225 clear
   both bits and he walks on.  Holding UP from there steps back onto the
   second pad and teleports him home.

Both exits from $B195 -- the teleport and the bail -- CLEAR bit 1.  There is
no path through $B195 that leaves it set, so the original can never be stuck
on a pad for more than one extra pass.

=============================================================================
THE PORT (web/gauntlet.html) DISAGREES ON 30 OF THESE ROWS
=============================================================================
`doTeleportPad()` sets `teleportArmed` and nothing inside a pass ever clears
it (the only two `= false` sites are the Game reset and startLevel), so
`} else if (this.teleportArmed){}` short-circuits the whole move routine for
the rest of the level.  Scenarios 2 and 4 both come out as "never moves", and
scenario 3 -- the discriminating one -- comes out as "never moves" too.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                   # noqa: E402
from keyprobe import KEYS, keymask                            # noqa: E402
from sim_move import step_to_loop_top, STATE, DIRKEY          # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}


def cell(col, row):
    """The map address of a cell -- $8000 + row*32 + col ($AE6D's arithmetic)."""
    return 0x8000 + (row & 31) * 32 + (col & 31)


PAD_A = cell(3, 3)      # straight below the player's start cell (3,2)
PAD_B = cell(8, 6)      # somewhere else on screen, so $B246 has a destination


def sample(h):
    m = h.memobj.m
    return (m[0x8420], m[0x8421], m[0x842E], m[0x84AC], m[0x843D], m[0x8436],
            list(m[0x5BD0:0x5BD9]))


def drive(plant, script, label):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    for addr, v in (plant or []):
        h.poke(addr, v)
    step_to_loop_top(h)                      # align before sampling
    print('\n--- %s ---' % label)
    print(' pass  dir     x   y  $842E b1 b2  $84AC $843D $8436  $5BD0..')
    i = 0
    for name, n in script:
        h.ports.release_all()
        if name:
            sel, bit = KM[DIRKEY[name]]
            h.ports.press(sel, keymask(bit))
        for _ in range(n):
            step_to_loop_top(h)
            i += 1
            x, y, e, ac, pend, ctr, lst = sample(h)
            print('%5d  %-6s%4d%4d   $%02X   %d  %d    %2d   $%02X   $%02X  %s'
                  % (i, name or 'none', x, y, e, (e >> 1) & 1, (e >> 2) & 1,
                     ac, pend, ctr, ' '.join('%02X' % b for b in lst)))
    return h


def main():
    print('PLANT: $30 at $%04X (cell 3,3) and $%04X (cell 8,6); '
          'the player starts at (12,8) = cell (3,2)' % (PAD_A, PAD_B))
    drive(None, [('down', 6)],
          '1. CONTROL: no pad, hold DOWN 6')
    drive([(PAD_A, 0x30)], [('down', 12)],
          '2. ONE pad, hold DOWN 12 -- bit 1 alternates, he never moves')
    drive([(PAD_A, 0x30)], [('down', 2), ('up', 8)],
          '3. ONE pad, DOWN 2 then UP 8 -- THE DISCRIMINATING RUN: he gets away')
    drive([(PAD_A, 0x30), (PAD_B, 0x30)], [('down', 10)],
          '4. TWO pads, hold DOWN 10 -- $84AC = 2 and $B195 TELEPORTS him')
    drive([(PAD_A, 0x30), (PAD_B, 0x30)], [('down', 2), ('up', 8)],
          '5. TWO pads, DOWN 2 then UP 8 -- and straight back again')


if __name__ == '__main__':
    main()
