#!/usr/bin/env python3
"""
maptiles.py -- the MAP TILES, decoded from the game's own 33-byte sprite
records instead of scraped off the screen.

WHY THIS EXISTS.  build/assets.json's tiles were sampled from the original's
shadow screen: 17 map values, because only 17 ever came into view in dungeon
one.  With all 307 dungeons now buildable, 63 distinct non-zero map values
occur and the other 46 rendered as nothing -- on 33 of the 307 dungeons more
than half the furniture was invisible.

WHAT THE MACHINE ACTUALLY DOES ($9F6C..$9F8A, the map draw):

    $9F6C  LD A,(HL) / OR A / JR z          the CELL VALUE; 0 draws nothing
    $9F7A  DEC A / ADD A,A / LD L,A / LD H,$7B    <- the SAME $7B00 pointer
    $9F7F  LD A,(HL) / INC L / LD H,(HL) / LD L,A      table the player and
    $9F83  LD C,(HL) / INC HL / LD SP,HL / JP $9DD2    the monsters use

so a map tile IS a 33-byte record: +0 the attribute for a 2x2 character-cell
block, +1..+32 sixteen rows of two bytes.  Note `DEC A / ADD A,A` with no
AND: the index is 8-bit, so cell value v and v+$80 land on the SAME entry --
which is why the "second wall graphic" bit 7 draws identically.
    id = ((v - 1) & $7F) + 1

AND THE TABLE IS REBUILT PER LEVEL ($9AB9, called from $97CB at $97DF):

    $9AB9  colour = (IX+2) bits 3-5 ; C = $7D9C[colour]
    $9AC9  stamp C into byte 0 of EIGHTY records from $F524, stride $21
    $9AD1  ... and of THREE more at $E2D6
    $9AD9  bank = (IX+1) bits 3-5 ; base = $F524 + bank*$210
    $9AEF  $7B00[0..14] := base+$21 .. base+$21*15 ; $7B00[15] := base

so the sixteen WALL ids $01..$10 come out of one of five 16-record banks
chosen by the record's own flags, ids $01..$0F taking base+$21*id and id $10
taking base itself, and every one of them wears the level's colour scheme.
Everything else keeps whatever the boot left in $7B00 and its own attribute.

CROSS-CHECK, and it is the point of the whole file: the 17 tiles already in
build/assets.json were sampled off the ORIGINAL'S SHADOW SCREEN by an
unrelated tool.  Fifteen of the sixteen are byte-identical to the record this
scheme picks; the odd one out is $13, which is the ANIMATED tile ($A31A
repoints ids $2F/$30 and $13's neighbours every few frames), so the capture
caught one of its frames.  `node tools/headless.js` asserts that agreement.

Usage:  python tools/maptiles.py
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
OUT = os.path.join(ROOT, 'build', 'map_tiles.json')

WALL_BASE = 0xF524          # $9AC9 / $9AE1
BANK_STRIDE = 0x210         # 16 records of $21
REC = 0x21
SCHEMES = 0x7D9C            # eight tile attribute bytes
COLOURED = 0xE2D6           # the three records $9AD1 also stamps
PTRS = 0x7B00


def main():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m

    schemes = list(m[SCHEMES:SCHEMES + 8])

    # the five wall banks, 16 records each.  Index 0 of a bank is map id $10
    # (the isolated wall) and 1..15 are the connectivity values.
    banks = []
    for b in range(5):
        base = WALL_BASE + b * BANK_STRIDE
        banks.append([list(m[base + i * REC + 1: base + i * REC + 33])
                      for i in range(16)])

    # which ids the three colour-stamped records at $E2D6 belong to
    coloured = {}
    static = {}
    for v in range(1, 0x80):
        p = PTRS + (v - 1) * 2
        addr = m[p] | (m[p + 1] << 8)
        if addr == 0:
            continue
        if v <= 0x10:
            continue                       # rebuilt per level by $9AB9
        if COLOURED <= addr < COLOURED + 3 * REC:
            coloured[v] = (addr - COLOURED) // REC
        static[v] = dict(attr=m[addr], bitmap=list(m[addr + 1:addr + 33]))

    colrecs = [list(m[COLOURED + i * REC + 1: COLOURED + i * REC + 33])
               for i in range(3)]

    # ---- $A31A's SPARKLE ------------------------------------------------
    # The TREASURE tile ($13, the chest) is ANIMATED: on every even pass
    # ($A33B LD A,($8491) / AND 1 / RET nz) $A341..$A36E rewrites part of its
    # bitmap from a master copy, ANDing each byte with a random mask:
    #
    #   $A341  LD HL,$E199 / LD DE,$FF74 / LD B,5   / CALL $A356
    #   $A34C  INC DE x4 / INC HL x4 / LD B,3       / fall into $A356
    #   $A356  CALL $B575 / OR $F0 / LD C,A / LD A,(DE) / AND C / LD (HL),A
    #          CALL $B575 / OR $0F / LD C,A / LD A,(DE) / AND C / LD (HL),A
    #          DJNZ
    #
    # $E18C is the $13 record, so $E199 is data byte 12 and $E1A7 byte 26.
    # The two masks are why only the MIDDLE of each row twinkles: `OR $F0`
    # keeps the high byte's top nibble (pixels 0..3) and `OR $0F` the low
    # byte's bottom nibble (pixels 12..15), so pixels 4..11 flicker and the
    # chest's outline stays put.
    # The bitmap in `static` above is whatever the capture froze mid-sparkle;
    # THIS is the master it is drawn from.
    SPARKLE_ID, SPARKLE_DST, SPARKLE_SRC = 0x13, 0xE199, 0xFF74
    rec = m[PTRS + (SPARKLE_ID - 1) * 2] | (m[PTRS + (SPARKLE_ID - 1) * 2 + 1] << 8)
    off = SPARKLE_DST - (rec + 1)                     # 12
    sparkle = dict(id=SPARKLE_ID, blocks=[
        dict(dst=off,      src=list(m[SPARKLE_SRC:SPARKLE_SRC + 10])),
        dict(dst=off + 14, src=list(m[SPARKLE_SRC + 14:SPARKLE_SRC + 20]))])
    assert off == 12, 'the $13 record moved; $A341 addresses need re-reading'

    data = dict(schemes=schemes, banks=banks, coloured=colrecs, sparkle=sparkle,
                coloured_ids={str(k): v for k, v in coloured.items()},
                static={str(k): v for k, v in static.items()},
                note=('map tile = a 33-byte sprite record reached through '
                      '$7B00 with id = ((cell-1) & $7F) + 1 ($9F7A).  Ids '
                      '$01..$10 are rebuilt per level by $9AB9 from bank '
                      '$F524 + flags(3-5)*$210 and wear attribute '
                      '$7D9C[byte2 bits 3-5]; the three records at $E2D6 '
                      'wear it too; everything else is static.'))
    json.dump(data, open(OUT, 'w'), separators=(',', ':'))
    print('%d static ids, 5 wall banks of 16, %d colour-stamped ids %s'
          % (len(static), len(coloured), sorted(coloured)))
    print('attribute schemes $7D9C:', ' '.join('%02X' % s for s in schemes))
    print('wrote', OUT)


if __name__ == '__main__':
    main()
