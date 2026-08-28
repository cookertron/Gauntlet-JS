#!/usr/bin/env python3
"""
hoardgate.py -- THE $32 KEY HOARD, $A7D5, and its drip at $A4DD.

A $32 cell is a pile of keys a dead player dropped.  $9413 writes a 3-byte
record at $5BE8 + 3*$84C0 -- the map CELL ADDRESS low, high, then the key
count -- and bumps $84C0.  Walking onto the cell searches those records for
it, queues the count into $84C1 (player 1) or $84C2 (player 2), and $A4DD's
head pays it out one key at a time.

    python tools/hoardgate.py          capture, write build/_hoard.json
    python tools/hoardgate.py show     ...and print each table

WHY IT NEEDED A TOOL.  The port already had the DROP ($9413) and the DRIP
($A4DD).  What it did not have was the SEARCH: doHoard() awarded the 100
points and cleared the cell without ever looking a record up, so the keys
were never paid.  Nothing caught it because $84C0 is 0 and $5BE8 is empty in
the captured state -- the arm was, in the file's own words, "code-read only".
This tool plants the records the game would have written and walks onto them.

THE SCENARIOS, and each tests something the others do not:

    plain2   one record, 2 keys        the ordinary case
    plain9   one record, 9 keys        a long drip, several passes
    full     9 keys, and the player is already carrying 9
                                       $A81D's limit bites.  NOTE $A4E7 DECs
                                       the queue BEFORE $A4E8 tests the limit,
                                       so a key that will not fit is LOST --
                                       the original throws it away and so must
                                       the port
    skip     two records, the FIRST for a different cell
                                       the search must walk past it
    dup      two records for the SAME cell, 4 then 7
                                       $A7E3's JR z leaves on the FIRST match,
                                       so 4 is paid and the 7 is lost.  The
                                       first count is 4 rather than 2 only so
                                       that this table differs from plain2's:
                                       a scenario whose correct output equals
                                       another's still discriminates against a
                                       last-match or summing implementation,
                                       but the matrix should not rely on that

THE B=0 FALL-THROUGH IS NOT TESTED because it cannot happen: no shipped
dungeon carries a $32 (all 307 built and counted), $9411 is one instruction
from $9428's INC, and $84C0 is written in only two places in the image --
that INC and $B3DA's clear at level start.  See doHoard()'s note.
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
MAP = 0x8000
PX, PY, KEYS_A, POTS, F11, HOARDQ = 0x8420, 0x8421, 0x8428, 0x8429, 0x842B, 0x84C1
SCORE, P14, NACT, AEND, COUNT = 0x8424, 0x8434, 0x8496, 0x8494, 0x84C0
RECS = 0x5BE8
START_X, START_Y = 40, 40           # cell (10,10)
CELL_R, CELL_C = 10, 11             # the $32, one step to the RIGHT
OTHER_R, OTHER_C = 5, 5             # a decoy record
PASSES = 40


def addr(r, c):
    return MAP + r * 32 + c


# name -> (starting keys, [(row, col, count), ...])
SCEN = [
    ('plain2', 0, [(CELL_R, CELL_C, 2)]),
    ('plain9', 0, [(CELL_R, CELL_C, 9)]),
    ('full',   9, [(CELL_R, CELL_C, 9)]),
    ('skip',   0, [(OTHER_R, OTHER_C, 5), (CELL_R, CELL_C, 3)]),
    ('dup',    0, [(CELL_R, CELL_C, 4), (CELL_R, CELL_C, 7)]),
]


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


def run(keys0, recs):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)                                   # settle
    m[NACT] = 0                                   # no actors in the way
    m[AEND], m[AEND + 1] = 0x00, 0x5C
    m[PX], m[PY] = START_X, START_Y
    m[KEYS_A] = keys0
    m[POTS] = 0
    m[MAP + CELL_R * 32 + CELL_C] = 0x32          # $9411 LD C,$32
    for i, (r, c, n) in enumerate(recs):          # $9413..$9426
        a = addr(r, c)
        m[RECS + 3 * i] = a & 0xFF
        m[RECS + 3 * i + 1] = a >> 8
        m[RECS + 3 * i + 2] = n
    m[COUNT] = len(recs)                          # $9428 INC (IY+$41)
    before = list(m[MAP:MAP + 1024])
    sel, bit = KM['D']                            # method 3, player 1 RIGHT
    h.ports.press(sel, keymask(bit))
    rows = []
    for _ in range(PASSES):
        one_pass(h)
        rows.append([m[KEYS_A], m[HOARDQ], m[F11],
                     (m[SCORE + 2] << 16) | (m[SCORE + 1] << 8) | m[SCORE],
                     m[MAP + CELL_R * 32 + CELL_C]])
    return before, rows, m[P14]


def main():
    out = {}
    for name, keys0, recs in SCEN:
        before, rows, p14 = run(keys0, recs)
        out[name] = {'keys0': keys0, 'p14': p14,
                     'recs': [[r, c, n] for r, c, n in recs],
                     'before': before, 'rows': rows,
                     'start': [START_X, START_Y],
                     'cell': [CELL_R, CELL_C], 'passes': PASSES}
        gained = rows[-1][0] - keys0
        print('%-8s start %d keys, records %s -> ended on %d keys (+%d), '
              'queue %d, cell $%02X'
              % (name, keys0, [list(r) for r in recs], rows[-1][0], gained,
                 rows[-1][1], rows[-1][4]))

    # the gate must be able to fail: the scenarios must not all agree
    sigs = {k: json.dumps(out[k]['rows']) for k in out}
    assert len(set(sigs.values())) == len(out), \
        'two scenarios produced identical tables -- the matrix tests nothing'
    path = os.path.join(ROOT, 'build', '_hoard.json')
    json.dump(out, open(path, 'w'))
    print('\nall %d scenarios distinct' % len(out))
    print('wrote', path)

    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        for k in out:
            print('\n=== %s ===' % k)
            print('  pass  keys  queue  f11  score   cell')
            for i, r in enumerate(out[k]['rows'][:16]):
                print('   %2d    %2d    %2d   $%02X  %06X   $%02X'
                      % (i + 1, r[0], r[1], r[2], r[3], r[4]))


if __name__ == '__main__':
    main()
