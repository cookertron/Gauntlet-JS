#!/usr/bin/env python3
"""
mapvalues.py -- what every map cell value MEANS, measured on the real Z80.

Three behavioural tables, none of them read off the artwork:

  python tools/mapvalues.py walk    the PLAYER's cell test $A8E7..$A918:
                                    plant each value in a free cell, call it,
                                    report carry (blocked) and the pending
                                    interaction it records, under four
                                    different key/item/stat combinations.
  python tools/mapvalues.py touch   the INTERACTION dispatcher $A65D: set the
                                    pending type + cell, call it, and diff the
                                    whole of $5800..$85FF afterwards.  This is
                                    what actually names each value.
  python tools/mapvalues.py shoot   the SHOT's own ladder $8F54..$8FAD, a
                                    SECOND and completely separate predicate
                                    over the same values.  carry = the shot
                                    stops here.
  python tools/mapvalues.py map     dump the live 32x32 grid and count values.
  python tools/mapvalues.py all     all four.

Everything drives tools/harness.py from build/state_charsel.pkl; the engine is
never loaded.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, F                                 # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
CELL = 0x8000 + 5 * 32 + 5          # a free cell inside the opening room
P1 = 0x8420

# player-block bytes worth naming in the diff (offsets from $8420)
NAME = {
    0x8420: 'x', 0x8421: 'y', 0x8422: 'health.hi', 0x8423: 'health.lo',
    0x8424: 'score.hi', 0x8425: 'score.md', 0x8426: 'score.lo',
    0x8427: 'dirbits', 0x8428: 'KEYS', 0x8429: 'POTIONS',
    0x842A: '+10 invis-timer', 0x842B: '+11 hud-dirty', 0x842C: '+12 NEXT LEVEL',
    0x842D: '+13 compass', 0x842E: '+14 anim', 0x842F: '+15 sprite-base',
    0x8434: '+20 ITEM BITS', 0x8435: '+21 stats', 0x8437: '+23 hurt.tier0',
    0x8438: '+24 hurt.tier1', 0x8439: '+25 hurt.tier2',
    0x843D: '+29 pend.type', 0x843E: '+30 pend.lo', 0x843F: '+31 pend.hi',
    0x849A: 'door.cursorA', 0x849C: 'door.cursorB', 0x849E: 'door.state',
    0x84A3: 'spell.damage', 0x84B9: 'item.announce',
}


def fresh():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def label(a):
    return NAME.get(a, '$%04X' % a)


def walk():
    print('=== $A8E7 the player cell test: carry = BLOCKED ===')
    base = pickle.load(open(STATE, 'rb'))
    h = Harness()
    cases = [(0, 0x00, 0x20, 'keys=0  items=$00 stats=$20 (the saved elf)'),
             (9, 0x00, 0x20, 'keys=9  items=$00 stats=$20'),
             (0, 0x20, 0x20, 'keys=0  items=bit5 stats=$20'),
             (0, 0x00, 0x28, 'keys=0  items=$00 stats bit3 set')]
    for keys, i14, i15, what in cases:
        out = []
        for v in range(0x100):
            h.load_state(base)
            h.poke(CELL, v)
            h.poke(0x8428, keys)
            h.poke(0x8434, i14)
            h.poke(0x8435, i15)
            h.poke(0x843D, 0)
            h.call(0xA8E7, regs={'HL': CELL, 'IX': P1})
            out.append((h.regs[F] & 1, h.memobj.m[0x843D]))
        print('  ' + what)
        prev, start = None, 0
        for i, r in enumerate(out + [None]):
            if r != prev:
                if prev is not None:
                    rng = ('$%02X' % start) if i - 1 == start else ('$%02X..$%02X' % (start, i - 1))
                    print('     %-11s carry=%d  records %s'
                          % (rng, prev[0], ('$%02X' % prev[1]) if prev[1] else '-'))
                prev, start = r, i


def touch():
    print('=== $A65D the interaction dispatcher: what each value DOES ===')
    base = pickle.load(open(STATE, 'rb'))
    h = Harness()
    for v in list(range(0x11, 0x33)) + [0x36, 0x37, 0x38, 0x40]:
        h.load_state(base)
        m = h.memobj.m
        h.poke(CELL, v)
        h.poke(0x8428, 9)                    # keys, so doors are affordable
        h.poke(0x8429, 0)
        h.poke(0x8491, 0x43)                 # odd pass: the generator arm runs
        typ = 0x12 if v in (0x11, 0x12) else v
        h.poke(0x843D, typ)
        h.poke(0x843E, CELL & 0xFF)
        h.poke(0x843F, CELL >> 8)
        snap = bytes(m[0x5800:0x8600])
        h.call(0xA65D, regs={'IX': P1, 'A': typ, 'BC': 0x1010})
        d = ['%s=$%02X->$%02X' % (label(0x5800 + i), a, b)
             for i, (a, b) in enumerate(zip(snap, m[0x5800:0x8600])) if a != b]
        print('  $%02X  %s' % (v, ', '.join(d) if d else '(nothing)'))


def shoot():
    print('=== $8F54 the SHOT ladder: carry = the shot STOPS here ===')
    base = pickle.load(open(STATE, 'rb'))
    h = Harness()
    for v in range(0x00, 0x40):
        h.load_state(base)
        m = h.memobj.m
        h.poke(CELL, v)
        snap = bytes(m[0x5800:0x8600])
        # $8F51's CALL $AE6D is skipped: enter at the LD A,(HL) with HL set.
        # IX = $8430 is the shot's frame (IX-12..-10 is the owner's score).
        h.call(0x8F54, regs={'HL': CELL, 'IX': 0x8430})
        d = ['%s=$%02X->$%02X' % (label(0x5800 + i), a, b)
             for i, (a, b) in enumerate(zip(snap, m[0x5800:0x8600])) if a != b]
        print('  $%02X  carry=%d  cell now $%02X   %s'
              % (v, h.regs[F] & 1, m[CELL], ', '.join(d) if d else ''))


def dumpmap():
    import collections
    h = fresh()
    m = h.memobj.m
    print('=== the live 32x32 grid at $8000 ===')
    print('     ' + ''.join('%2X ' % c for c in range(32)))
    for r in range(32):
        print('%2X:  ' % r + ''.join('%02X ' % m[0x8000 + r * 32 + c] for c in range(32)))
    cnt = collections.Counter(bytes(m[0x8000:0x8400]))
    print()
    for v in sorted(cnt):
        print('  $%02X  x%d' % (v, cnt[v]))


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    for name, fn in (('walk', walk), ('touch', touch), ('shoot', shoot), ('map', dumpmap)):
        if what in (name, 'all'):
            fn()
            print()


if __name__ == '__main__':
    main()
