#!/usr/bin/env python3
"""
hudfont.py -- pull the HUD's own 8x8 FONT and its live counter state out of the
original, into build/hud_font.json, which tools/build.py inlines.

Why a separate tool rather than another arm of tools/extract.py: extract.py
sweeps 64 camera positions to sample tiles and takes minutes; none of that is
needed here, and the HUD assets are read straight out of the image.

WHAT IS RECOVERED, AND FROM WHERE
---------------------------------
font      $77B0.  The glyph plotter is $B6C0 and it states the layout outright:

            $B6C0  EXX / SUB $20 / ADD A,A / LD L,A / LD H,0 / ADD HL,HL x2
            $B6C9  LD BC,$77B0 / ADD HL,BC          => glyph = $77B0+(code-32)*8
            $B6CD  LD C,D / LD B,8
            $B6D0  LD A,(HL) / LD (DE),A / INC HL / INC D / DJNZ
            $B6D6  LD D,C / INC E                   => 8 rows, then next COLUMN

          INC D steps one PIXEL ROW inside a character cell and INC E steps one
          character COLUMN, so a glyph is 8 rows of one byte and the routine
          writes NO attribute -- the cell keeps whatever the panel painter left.

fields    the screen addresses the HUD prints into, decoded to (row, col) with
          the standard display-file arithmetic.  $B713 LD DE,$50C9 is player
          1's field origin and $B722 LD DE,$50DA is player 2's; $B74A SUB 8
          moves left 8 columns for the score.  $B7B8 ADD A,E / ADD A,$16 is the
          icon step, which carries the low byte out of row 22 into row 23.

counters  the live values at level start, read from the player block at $8420
          and the globals: health/score are packed BCD, keys/potions plain.

panel     $B5E8 paints ONE player's 15-column half of rows 20-23, and which of
          the two pictures it paints is (IX+$14) bit 7:

            $B5EA  BIT 7,(IX+$14) / JR z,$B5FE
            $B5F1  CALL $B864     -> 4 rows x 15 codes from $7DB8   NOT PLAYING
                   C=0 CALL $B66F -> rows 20,21,22 attribute 0 and the ISR ink
                                     operand ($A2CE/$A2D6) := 0, i.e. the logo
                                     COLOUR-CYCLES
                   C=6 JP $B687   -> row 23 attribute 6
            $B5FE  CALL $B87A     -> 3 rows x 15 codes from $7DF4   PLAYING
                   CALL $B694     -> row 23 blanked with 15 spaces
            $B607  CALL $B890     -> the name string and its ink from (IX+$13)
            $B60A  A = ((len XOR 15) + 1) >> 1   the centring offset, row 20
            $B61C  CALL $B66C     -> rows 20,21,22 x 15 cells filled with the ink
            $B624  the six POWER-ICON cells, cols 0,1,2 and 12,13,14 of row 20,
                   from bits 0..5 of (IX+$14) -- $46 $43 $44 $42 $47 $45 when set

          $B624's arm CANNOT FIRE.  $B5E8 is entered only through $948C, whose
          last three instructions are LD A,(IX+$14) / AND $80 / LD (IX+$14),A,
          so C is 0 or $80 at $B630 and six RR C never produce a carry.  The
          six cells are always attribute 0.  Recorded here as the table plus
          that fact; the port paints them black.
"""
import base64
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                    # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
FONT = 0x77B0            # $B6C9 LD BC,$77B0
# $B6C1 SUB $20 sets the first code.  The LAST one is NOT $7F: player 2's
# panel can print VALKYRIE, and $7E29's entry is nine codes $81..$89 (a
# condensed wordmark drawn as glyphs, exactly as WIZARD is $29..$2F).  The
# table closes on itself: $77B0 + ($89-$20+1)*8 = $7B00, which is where the
# sprite POINTER table starts, and $20..$89 is precisely the set of codes the
# two panel arts ($7DB8, $7DF4) and the four name strings between them use.
FIRST, LAST = 0x20, 0x89
P1 = 0x8420


def rowcol(addr):
    """Spectrum display-file address -> (character row, column)."""
    hi, lo = addr >> 8, addr & 0xFF
    third = (hi >> 3) & 3
    row = third * 8 + ((lo >> 5) & 7)
    return row, lo & 0x1F


def main():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m

    glyphs = {}
    for code in range(FIRST, LAST + 1):
        g = bytes(m[FONT + (code - FIRST) * 8: FONT + (code - FIRST) * 8 + 8])
        glyphs[code] = base64.b64encode(g).decode()

    # $B713 / $B722 -- the two field origins, and $B74A's SUB 8 for the score
    p1_row, p1_col = rowcol(0x50C9)
    p2_row, p2_col = rowcol(0x50DA)
    # $B7B8: E' = E' + A + $16, so the icon low byte walks out of row 22's
    # column band into row 23.  Decode both ends rather than asserting them.
    key1_row, key1_col = rowcol(0x50C9 + 1 + 0x16)        # the FIRST key
    pot1_row, pot1_col = rowcol(0x50C9 + (0x10 - 1) + 0x16)   # the FIRST potion

    out = {
        '_source': ('read out of live RAM by tools/hudfont.py; font $77B0, '
                    'plotter $B6C0, field origins $50C9/$50DA'),
        'font': {'base': FONT, 'first': FIRST, 'last': LAST,
                 'height': 8, 'glyphs': glyphs},
        'fields': {
            # row 22: score cols 1..6, health cols 9..12 (p1)
            'score': {'row': p1_row, 'col': p1_col - 8, 'digits': 6,
                      'bytes': 3, 'addr': '$8424'},
            'health': {'row': p1_row, 'col': p1_col, 'digits': 4,
                       'bytes': 2, 'addr': '$8422'},
            # row 23: key i at col (key1_col + i - 1), potion i at
            # (pot1_col - i + 1) -- $B79B does A = $10 - potions, so the
            # potions walk LEFTWARDS from column 14.
            'keys': {'row': key1_row, 'col': key1_col, 'char': 0x21, 'attr': 6},
            'potions': {'row': pot1_row, 'col': pot1_col, 'char': 0x22,
                        'attr': 5},
            'p2_col_offset': p2_col - p1_col,
        },
        'state': {
            # the player block, $8420, 32-byte stride
            'health': (m[P1 + 2] << 8) | m[P1 + 3],       # packed BCD
            'score': (m[P1 + 4] << 16) | (m[P1 + 5] << 8) | m[P1 + 6],
            'keys': m[P1 + 8], 'potions': m[P1 + 9],
            'timer': m[P1 + 0x0A], 'flags': m[P1 + 0x0B],
            'level': m[P1 + 0x0C],
            'p14': m[P1 + 0x14], 'p15': m[P1 + 0x15],
            # the two clocks the drain runs off: $8497 counts VIDEO FRAMES in
            # the ISR ($A2A2 INC (IY+$18)) and $849F holds the next expected
            # value of ($8497 & $C0) ($B6DA/$B6E6).
            'frame_ctr': m[0x8497], 'drain_phase': m[0x849F],
            'hurry': m[0x84B8],
            # $84A0, the generator spawn threshold base = 50 + dungeon/2
            'spawn_base': m[0x84A0], 'dungeon': m[0x8403],
        },
        # $B5E8's two panel pictures and the name table -- see the docstring
        'panel': {
            'wait': [list(m[0x7DB8 + r * 15: 0x7DB8 + (r + 1) * 15])
                     for r in range(4)],          # $B864 LD BC,$0F04
            'play': [list(m[0x7DF4 + r * 15: 0x7DF4 + (r + 1) * 15])
                     for r in range(3)],          # $B87A LD BC,$0F03
            # $B890, keyed on (IX+$13): 0 -> $7E21, 8 -> $7E29, 16 -> $7E33,
            # anything else -> $7E3B.  Length byte first.
            'names': [{'tag': tag, 'ink': ink,
                       'codes': list(m[a + 1: a + 1 + m[a]])}
                      for tag, a, ink in ((0x00, 0x7E21, 0x42),
                                          (0x08, 0x7E29, 0x45),
                                          (0x10, 0x7E33, 0x46),
                                          (None, 0x7E3B, 0x44))],
            # $B624's six cells; unreachable (see the docstring) but recorded
            'icon_cols': [0, 1, 2, 12, 13, 14],
            'icon_attrs': [0x46, 0x43, 0x44, 0x42, 0x47, 0x45],
            'wait_row23_attr': 6,                 # $B5F9 LD C,6
        },
        # $7CFE, generator value -> spawned actor state byte (class<<5|tier<<3)
        'gen_state_table': list(m[0x7CFE:0x7D0D]),
        # $7D70, the melee damage table indexed by $A964
        'melee_damage': list(m[0x7D70:0x7D80]),
    }
    path = os.path.join(ROOT, 'build', 'hud_font.json')
    json.dump(out, open(path, 'w'), indent=1)
    print(f'font: {len(glyphs)} glyphs of 8 bytes from ${FONT:04X}')
    print(f'p1 field origin $50C9 -> row {p1_row} col {p1_col}; '
          f'p2 $50DA -> row {p2_row} col {p2_col} (+{p2_col-p1_col})')
    print(f'first key icon -> row {key1_row} col {key1_col}; '
          f'first potion -> row {pot1_row} col {pot1_col}')
    print(f'state: health ${out["state"]["health"]:04X} '
          f'score {out["state"]["score"]:06X} keys {out["state"]["keys"]} '
          f'potions {out["state"]["potions"]} '
          f'$8497=${out["state"]["frame_ctr"]:02X} '
          f'$849F=${out["state"]["drain_phase"]:02X}')
    print(f'wrote {path}')


if __name__ == '__main__':
    main()
