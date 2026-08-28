#!/usr/bin/env python3
"""
introart.py -- the LEVEL-ENTRY SCREEN's strings and icons.

$8B27 draws it once per level ($B38E, immediately before $B391 CALL $8503).
It is six strings out of the length-prefixed table at $7E21 and eight object
icons, all in the HUD font and the game's own 33-byte sprite records, so
nothing new has to be invented -- only located.

    python tools/introart.py           write build/intro.json
    python tools/introart.py show      ...and print what it found

WHAT IS EXTRACTED

  strings   the six the screen can print, as FONT CODES (the table is not
            ASCII: it stores codes the plotter indexes straight into the
            font).  $8A08 centres each one by (32 - length) / 2 added to the
            LOW byte of the address alone, so a line cannot cross a third
            boundary.  The rows come from the DE each caller passes, which is
            an offset from $C000:
              $0040 -> row  2   "FIND THE  POTION"     $7EDD, on $847D bit 4
              $0800 -> row  8   "YOU HAVE FOUND A"     $7EBD, treasure only
              $0840 -> row 10   "TREASURE  ROOM"       $7ECE, treasure only
              $1000 -> row 16   "SHOTS NOW STUN"       $7EFD, $847E bit 4
                                "SHOTS NOW HURT"       $7F0C, bit 5 alone
              $1020 -> row 17   "OTHER  PLAYERS"       $7EEE, either bit
            $8B4F BIT 4 is tested FIRST, so with both bits set STUN wins --
            measured, the two pictures are identical.

  digits    $8B7E LD A,($8403) / CALL $8A84 -- the LEVEL NUMBER, drawn right
            after the icons from the column DE was left on (6 + 8*2 = 22).
            $8A84 divides by 100 and 10 by repeated subtraction and pushes
            each digit through the same $8AB0, so the digits are SPRITES from
            $BE32 + 33*d, not font glyphs, and a level under 10 draws ONE
            digit with no leading zero.

  icons     eight slots from the table at $8BAC, drawn only when the level is
            NOT a treasure room.  $8AB0 turns an entry into $BE32 + 33*index
            and blits it with $9DD2 at attribute $47; $8AAB steps TWO columns
            per slot from column 6 of row 8.  A zero entry draws nothing and
            just advances.

  attr      $8B84 floods the whole shadow attribute file with $47 -- bright
            white on black, over the HUD as well as the playfield.
"""
import base64
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                   # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')

STRINGS = {                       # name: (address, character row)
    'potion':   (0x7EDD, 2),
    'found':    (0x7EBD, 8),
    'treasure': (0x7ECE, 10),
    'stun':     (0x7EFD, 16),
    'hurt':     (0x7F0C, 16),
    'others':   (0x7EEE, 17),
}
ICON_TABLE = 0x8BAC               # eight entries
ICON_BASE = 0xBE32
ICON_ROW, ICON_COL, ICON_STEP = 8, 6, 2
ATTR = 0x47


TAPE = os.path.join(ROOT, 'tape', 'Gauntlet - Side 1.tzx')


def verify_against_tape(icons):
    """Every icon's 32 data bytes must appear verbatim in a tape block.

    This is the check that would have caught the corrupt colon the day it
    appeared, instead of it reaching the screen and being taken for a fault
    on the tape."""
    if not os.path.exists(TAPE):
        print('  (tape not present -- the art was NOT verified)')
        return
    from tzx import parse                                    # noqa: E402
    _, blocks = parse(TAPE)
    bodies = [b.data for b in blocks if b.data]
    bad = [k for k, v in sorted(icons.items(), key=lambda kv: int(kv[0]))
           if not any(base64.b64decode(v['frame']) in body for body in bodies)]
    if bad:
        raise SystemExit(
            'ICON(S) ' + ', '.join(bad) + ' ARE NOT ON THE TAPE.'
            '  These records are static art; if they do not match the tape,'
            ' the state they were read from has been scribbled on -- see the'
            ' note above verify_against_tape().  Regenerate with'
            ' `python tools/boot48.py` (the harness contends by default now)'
            ' and extract again.')
    print('  all %d icons verified byte for byte against %s'
          % (len(icons), os.path.basename(TAPE)))


def main():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m

    strings = {}
    for name, (addr, row) in STRINGS.items():
        n = m[addr]
        strings[name] = {'addr': addr, 'row': row, 'len': n,
                         'codes': list(m[addr + 1:addr + 1 + n])}

    table = list(m[ICON_TABLE:ICON_TABLE + 8])
    # 0..9 are the DIGITS $8A84 draws the level number with -- it converts by
    # repeated subtraction of 100, 10 and 1 and sends each digit through the
    # SAME $8AB0, so the level number is drawn in sprites, not in the font,
    # starting at the column the eight icon slots left DE on (column 22).
    icons = {}
    for idx in sorted({t for t in table if t} | set(range(10))):
        p = ICON_BASE + 33 * idx
        icons[str(idx)] = {'addr': p, 'attr': m[p],
                           'frame': base64.b64encode(
                               bytes(m[p + 1:p + 33])).decode()}

    # ---- THE ART MUST BE THE TAPE'S ART ------------------------------
    # These records are STATIC program data: $B358's LDIR puts them at $BE32
    # and nothing in correct execution writes them again (measured: 300
    # contended passes, zero writes into $BFDF..$BFFF).  But they are read
    # out of a RUNNING state, and a running state can be scribbled on.
    #
    # It was.  Icon 13 -- the colon in 'LEVEL : 2' -- came out corrupt, and
    # it was OUR EMULATION that did it, not the tape.  The last record ends
    # at $BFFF, immediately below the shadow screen, and $9CD7 blits with
    # `LD SP,source`; with the wrong timing an interrupt is accepted while SP
    # is just above $C000 and the handler's own PUSHes land inside the record
    # ($A29F/$A2A0/$A2A1 writing the return addresses $A2D2 and $B51D).  A
    # plain uncontended Simulator does that during the boot; a CONTENDED one
    # -- real 48K timing -- does not, and the tape holds a clean colon.
    verify_against_tape(icons)

    doc = {'strings': strings, 'table': table, 'icons': icons,
           'icon_row': ICON_ROW, 'icon_col': ICON_COL, 'icon_step': ICON_STEP,
           'num_col': ICON_COL + 8 * ICON_STEP,      # where $8A84 starts
           'attr': ATTR}
    path = os.path.join(ROOT, 'build', 'intro.json')
    json.dump(doc, open(path, 'w'))

    for name, s in strings.items():
        txt = ''.join(chr(c) if 32 <= c < 127 else '.' for c in s['codes'])
        print('%-9s $%04X row %2d len %2d  %r' % (name, s['addr'], s['row'],
                                                  s['len'], txt))
    print('icon table $8BAC: %s' % ' '.join('%02X' % t for t in table))
    for k, v in icons.items():
        px = sum(bin(b).count('1') for b in base64.b64decode(v['frame']))
        print('  icon %2s at $%04X  attr $%02X  %3d pixels'
              % (k, v['addr'], v['attr'], px))
    print('wrote', path)

    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        for k, v in icons.items():
            b = base64.b64decode(v['frame'])
            print('\n--- icon %s ---' % k)
            for r in range(16):
                row = (b[r * 2] << 8) | b[r * 2 + 1]
                print('  ' + ''.join('#' if row & (1 << (15 - i)) else '.'
                                     for i in range(16)))


if __name__ == '__main__':
    main()
