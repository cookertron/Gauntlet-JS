#!/usr/bin/env python3
"""
bankrepair.py -- restore STATIC PROGRAM DATA that the emulation scribbled on.

    python tools/bankrepair.py          report what differs from the tape
    python tools/bankrepair.py --write  ...and repair build/state_48k.pkl

WHY THIS EXISTS.

The sprite bank at $BE32..$BFFF is static: $B358's LDIR puts it there and
nothing in correct execution writes it again -- measured, 300 contended
passes, zero writes.  But the project's baseline state is a SNAPSHOT OF A
RUNNING MACHINE, and a running machine can scribble on itself.

It did.  The last record ends at $BFFF, immediately below the shadow screen
at $C000, and $9CD7 blits with `LD SP,source`.  With the wrong timing an
interrupt is accepted while SP is just above $C000 and the handler's own
PUSHes land INSIDE the record -- $A29F/$A2A0/$A2A1 writing the return
addresses $A2D2 and $B51D, which is what those bytes decode to.  Record 13 is
the COLON in "LEVEL : 2", and it reached the screen with its lower dot eaten:

    tape      00 00 03 80 06 C0 04 40 06 C0 03 80 00 00 00 00
    scribbled 00 00 03 80 06 C0 DA A2 80 D0 00 00 00 00 1D B5

A plain uncontended SkoolKit Simulator does this during the boot.  A
CMIOSimulator -- real 48K timing -- does not: booted contended, the record is
clean and stays clean.  build/state_48k.pkl predates the switch to contention
(see NOTES-engine.md, "ULA contention"), so it carries the damage.

WHY NOT JUST RE-BOOT.  `python tools/boot48.py` now produces a clean bank,
but regenerating the whole state also re-rolls the actor records at $5C00 --
the `LD A,R` coins -- and the tile sweep in extract.py is sensitive to where
the actors stand: closure measured 95.53% on the tuned state against 94.47%
on a fresh one.  Trading a 1% loss of tile fidelity for eight bytes is a bad
trade, so those eight bytes are repaired in place instead, FROM THE TAPE,
which is the authority for anything static.

THE TAPE IS THE PROOF, not this tool's opinion.  The bank is located by
searching the tape for its first 64 bytes and every differing byte is
reported.  If a byte differs that is NOT in the last record, that is worth
reading before writing anything -- it would mean something else scribbled
somewhere new.

`tools/introart.py` verifies every extracted icon against the tape, so this
class of damage cannot reach a shipped asset again.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                   # noqa: E402
from tzx import parse                                         # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
TAPE = os.path.join(ROOT, 'tape', 'Gauntlet - Side 1.tzx')
BANK, BANK_END = 0xBE32, 0xC000
LAST_RECORD = 0xBFDF          # $BE32 + 33*13


def main():
    write = '--write' in sys.argv[1:]
    st = pickle.load(open(STATE, 'rb'))
    h = Harness()
    h.load_state(st)
    m = h.memobj.m

    _, blocks = parse(TAPE)
    bodies = [b.data for b in blocks if b.data]
    probe = bytes(m[BANK:BANK + 64])          # the head of the bank is intact
    where = None
    for bi, body in enumerate(bodies):
        j = body.find(probe)
        if j >= 0:
            where = (bi, j)
            break
    if where is None:
        raise SystemExit('the sprite bank was not found on the tape -- the '
                         'head of $BE32 does not match any block, so this '
                         'tool cannot say what the bytes should be')
    bi, j = where
    tape = bodies[bi][j:j + (BANK_END - BANK)]
    print('sprite bank $%04X..$%04X found in tape block %d at offset %d'
          % (BANK, BANK_END - 1, bi, j))

    bad = [i for i in range(BANK_END - BANK) if m[BANK + i] != tape[i]]
    if not bad:
        print('the bank matches the tape byte for byte -- nothing to repair')
        return 0
    print('%d byte(s) differ from the tape:' % len(bad))
    outside = 0
    for i in bad:
        a = BANK + i
        tag = ''
        if not (LAST_RECORD <= a < BANK_END):
            tag = '   <-- OUTSIDE the last record, READ THIS BEFORE WRITING'
            outside += 1
        print('   $%04X  state $%02X  tape $%02X%s' % (a, m[a], tape[i], tag))
    if outside:
        print('\n%d byte(s) are outside $%04X..$%04X, which is not the damage '
              'this tool was written for.' % (outside, LAST_RECORD, BANK_END - 1))

    if not write:
        print('\n(report only -- pass --write to repair build/state_48k.pkl)')
        return 0

    for i in bad:
        m[BANK + i] = tape[i]
    pickle.dump(h.save_state(), open(STATE, 'wb'))
    print('\nrepaired %d byte(s) and rewrote %s' % (len(bad), STATE))
    return 0


if __name__ == '__main__':
    sys.exit(main())
