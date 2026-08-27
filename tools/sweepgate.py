#!/usr/bin/env python3
"""
sweepgate.py -- THE $2F SWITCH AND $9967's WALL RE-TILING, $A7FC.

Standing on a $2F cell throws a switch that walks the WHOLE map once:

    $A802  EXX / LD C,0 / EXX      ; the replacement value, C'
    $A806  LD HL,$8000
    $A809  LD A,(HL)
    $A80A  CP $2F / JR nz / LD (HL),0        ; $2F -> 0
    $A810  CP $80 / CALL nc,$9967            ; >= $80 -> remove the wall
    $A815  INC HL / LD A,H / CP $84 / JR nz  ; $8000..$83FF, all 1024

$9967 removes a wall and BREAKS THE FOUR NEIGHBOURS' CONNECTION BITS towards
it -- the exact mirror of $99BA, which sets them:

    $9967  CALL $98BF        ; A = (HL) & $7F; Z if empty; C if A < $11
    $9973  LD (HL),A         ; C' (0 here) unless C' >= $3F, then 0
    $9975  RET nc            ; ONLY 1..$10 is a wall, and only a wall re-tiles
    up -> RES 2   right -> RES 3   down -> RES 0   left -> RES 1
    each gated by its own $98BF, each followed by $99B3 -- which gives a cell
    left with NO connections bit 4, the "joined to nothing" graphic.

WHY THIS TOOL EXISTS.  The port cleared bit-7 cells to 0 and skipped the
re-tiling, with a note saying $9967 "needs $99BA's auto-tiler".  Both halves
were stale: $99BA is placeWall() and $9967's body is what Dungeon.place()
already does at BUILD time.  Only the LIVE-map path was missing.  This tool
gates the live path against the original.

    python tools/sweepgate.py          plant, run $A7FC, write build/_sweep.json
    python tools/sweepgate.py show     ...and print the planted sites

THE PLANTED SITES cover every arm, including the ones that must NOT move:

    A (5,5)    $8F, all four neighbours walls   -> each loses its own bit
    B (9,9)    $84, up-neighbour is $04 ALONE   -> RES 2 empties it -> $10
    C (13,13)  $B6, masked $36 is NOT a wall    -> cleared, NO re-tiling, and
                                                   its wall neighbour survives
    D (17,17)  $81, every neighbour EMPTY       -> nothing to touch
    E (0,0)    $8F at the corner                -> up wraps to row 31, left
                                                   wraps to column 31
    F (31,20)  $8F on the last row              -> down wraps to row 0
    G          two $2F cells far apart          -> both cleared
    H          $36 / $20 / $11                  -> untouched

THE NON-VACUITY CHECK.  The tool also computes what the OLD naive port did
(clear to 0, no re-tiling) and ASSERTS it differs from the original's answer.
If those two ever agreed the planted map would be exercising nothing and this
gate would pass whatever the engine did.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                    # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
MAP = 0x8000
SWEEP = 0xA7FC


def cell(r, c):
    return MAP + r * 32 + c


# (row, col, value).  Order matters only in that later writes win.
PLANT = [
    # A -- a wall joined all four ways, every neighbour a wall
    (5, 5, 0x8F), (4, 5, 0x05), (5, 6, 0x0A), (6, 5, 0x03), (5, 4, 0x06),
    # B -- the up-neighbour's ONLY connection is the one being removed
    (9, 9, 0x84), (8, 9, 0x04),
    # C -- bit 7 set but masked $36: not a wall, so no re-tiling at all
    (13, 13, 0xB6), (12, 13, 0x05),
    # D -- a wall with nothing around it
    (17, 17, 0x81), (16, 17, 0x00), (17, 18, 0x00),
    (18, 17, 0x00), (17, 16, 0x00),
    # E -- the corner: up wraps to row 31, left wraps to column 31
    (0, 0, 0x8F), (31, 0, 0x05), (0, 31, 0x06), (0, 1, 0x0A), (1, 0, 0x03),
    # F -- the last row: down wraps to row 0
    (31, 20, 0x8F), (30, 20, 0x05), (0, 20, 0x03), (31, 19, 0x06),
    (31, 21, 0x0A),
    # G -- plain $2F switches, far apart
    (20, 3, 0x2F), (25, 28, 0x2F),
    # H -- must not move
    (22, 10, 0x36), (22, 12, 0x20), (22, 14, 0x11),
]


def naive(before):
    """What the port did BEFORE this work: clear, never re-tile."""
    out = list(before)
    for i, v in enumerate(out):
        if v == 0x2F or v >= 0x80:
            out[i] = 0
    return out


def main():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    for r, c, v in PLANT:
        m[cell(r, c)] = v
    before = list(m[MAP:MAP + 1024])

    h.call(SWEEP, limit=8_000_000)
    after = list(m[MAP:MAP + 1024])

    nv = naive(before)
    diff = [i for i in range(1024) if nv[i] != after[i]]
    assert diff, ('the naive sweep and the original agree on every cell -- '
                  'the planted map exercises no re-tiling and this gate '
                  'would pass whatever the engine did')

    doc = {'before': before, 'after': after,
           'naive_differs_at': diff,
           'plant': [[r, c, v] for r, c, v in PLANT]}
    path = os.path.join(ROOT, 'build', '_sweep.json')
    json.dump(doc, open(path, 'w'))

    moved = [i for i in range(1024) if before[i] != after[i]]
    print('%d of 1024 cells moved; the naive clear-without-re-tiling gets %d '
          'of them wrong' % (len(moved), len(diff)))
    print()
    print('  site          cell  before  after   naive')
    labels = {(5, 5): 'A wall', (4, 5): 'A up', (5, 6): 'A right',
              (6, 5): 'A down', (5, 4): 'A left',
              (9, 9): 'B wall', (8, 9): 'B isolate',
              (13, 13): 'C not-wall', (12, 13): 'C survivor',
              (17, 17): 'D lonely',
              (0, 0): 'E corner', (31, 0): 'E up-wrap', (0, 31): 'E left-wrap',
              (31, 20): 'F lastrow', (0, 20): 'F down-wrap',
              (20, 3): 'G $2F', (22, 10): 'H $36', (22, 12): 'H $20',
              (22, 14): 'H $11'}
    for (r, c), lab in labels.items():
        i = r * 32 + c
        mark = '   <-- naive wrong' if nv[i] != after[i] else ''
        print('  %-12s (%2d,%2d)   $%02X    $%02X     $%02X%s'
              % (lab, r, c, before[i], after[i], nv[i], mark))
    print()
    print('wrote', path)

    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        for r in range(32):
            row = ''.join('%02X' % after[r * 32 + c] for c in range(32))
            print('  r%2d %s' % (r, row))


if __name__ == '__main__':
    main()
