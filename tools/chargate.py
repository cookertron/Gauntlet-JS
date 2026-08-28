#!/usr/bin/env python3
"""
chargate.py -- the CHARACTER TABLE, measured.

Block A's picker writes ONE byte per player and everything else about a
character follows from it:

    $FFFF   player 1's character index 0..3   ($C42C, from $C7FD)
    $FFFE   player 2's                        ($C4E3 two players / $C449 one)

    0 WARRIOR  Thor      red     1 VALKYRIE Thyra   cyan
    2 WIZARD   Merlin    yellow  3 ELF      Questor green

proved in tools/menugate.py by driving the picker and looking at the screen.

At boot $BE53/$BE64 index a FOUR-entry table at $BF19 with it (`$BEE5`) and
write the pair into the player block:

    $8433 / $8453   the character TAG = 8*index -- the shot's state base and
                    the key to $B890's panel name and ink
    $8435 / $8455   ONE PACKED STATS BYTE, four 2-bit fields.  Each field is
                    extracted by its own consumer, and that is the evidence
                    for the field layout -- no field is named by guesswork:

      bits 1:0 SHOT    $9115   A = stats AND 3        (+2 if inventory bit 3)
                              -> $7D64 + 2*idx, TWO bytes: actor tier damage
                                 and generator damage
      bits 3:2 FIGHT   $A964   A = (stats>>1) AND 6   (+4 if inventory bit 5)
                              -> $7D70 + idx, ONE byte t; damage = t>>1, and
                                 +1 when t is odd and pass counter bit 1 is set
      bits 5:4 MAGIC   $A544   A = (stats>>2) AND 12  (+4 if inventory bit 2)
                              -> $7D1C + 4*idx, THREE bytes -> $84A3/$84A4/$84A5
      bits 7:6 ARMOUR  $AB6F   A = (stats>>3) AND $18 (+$10 if inventory bit 0)
                              -> $7D34 + idx, SIX bytes LDIRed to $8437..$843C
                                 (the contact-damage row)

and $BEE5 also LDIRs the character's $420-byte SPRITE SET out of the master
bank at $5F00 + $420*index.

Usage:
    python tools/chargate.py            boot the game once per index and read
                                        the live bytes back
    python tools/chargate.py tables     the four stat tables off the tape only
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import boot48                                                   # noqa: E402
from harness import R                                           # noqa: E402

NAME = {0: 'WARRIOR (Thor, red)', 1: 'VALKYRIE (Thyra, cyan)',
        2: 'WIZARD (Merlin, yellow)', 3: 'ELF (Questor, green)'}
INK = {0: 0x42, 1: 0x45, 2: 0x46, 3: 0x44}
P1, P2 = 0x8420, 0x8440
SHOT_T, FIGHT_T, MAGIC_T, ARM_T = 0x7D64, 0x7D70, 0x7D1C, 0x7D34


def fields(stats):
    return (stats & 3, (stats >> 2) & 3, (stats >> 4) & 3, (stats >> 6) & 3)


def tables(mem):
    print('THE FOUR STAT TABLES (off the tape, unmoved by the relocation)')
    print(f'  $7D64 SHOT   (2-byte records, idx = shot + 2*invbit3):')
    for i in range(6):
        a = SHOT_T + 2 * i
        print(f'     [{i}] ${a:04X}  actor tier damage ${mem[a]:02X}   '
              f'generator damage ${mem[a+1]:02X}')
    print(f'  $7D70 FIGHT  (1 byte, idx = 2*fight + 4*invbit5):')
    row = ' '.join(f'{mem[FIGHT_T+i]:02X}' for i in range(12))
    print(f'     {row}      even entries only: ' +
          ' '.join(f'[{i}]={mem[FIGHT_T+i]}' for i in range(0, 12, 2)))
    print(f'  $7D1C MAGIC  (4-byte records, idx = magic + invbit2):')
    for i in range(5):
        a = MAGIC_T + 4 * i
        print(f'     [{i}] ${a:04X}  ' + ' '.join(f'{mem[a+j]:02X}' for j in range(4)))
    print(f'  $7D34 ARMOUR (6 used of an 8-byte stride, '
          f'idx = armour + 2*invbit0):')
    for i in range(6):
        a = ARM_T + 8 * i
        print(f'     [{i}] ${a:04X}  ' + ' '.join(f'{mem[a+j]:02X}' for j in range(6)))


def derive(mem, stats, inv):
    shot, fight, magic, arm = fields(stats)
    si = shot + 2 * ((inv >> 3) & 1)
    fi = 2 * fight + 4 * ((inv >> 5) & 1)
    mi = magic + ((inv >> 2) & 1)
    ai = arm + 2 * (inv & 1)
    t = mem[FIGHT_T + fi]
    return {
        'shot': (shot, si, mem[SHOT_T + 2 * si], mem[SHOT_T + 2 * si + 1]),
        'fight': (fight, fi, t, t >> 1, t & 1),
        'magic': (magic, mi, [mem[MAGIC_T + 4 * mi + j] for j in range(3)]),
        'armour': (arm, ai, [mem[ARM_T + 8 * ai + j] for j in range(6)]),
    }


def one(idx, verbose=False):
    h = boot48.make('48k', verbose=False)
    h.poke(0xFFFF, idx)
    h.poke(0xFFFE, (idx + 1) & 3)
    boot48.drive(h, verbose=False)
    m = h.memobj.m
    return h, m


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'tables':
        import mkimage
        mem, _ = mkimage.build('full')
        tables(mem)
        return
    import mkimage
    static, _ = mkimage.build('full')
    tables(static)
    print()
    print('MEASURED IN LIVE RAM, one boot per index '
          '($FFFF poked, $FFFE = index+1)')
    print()
    for idx in range(4):
        h, m = one(idx)
        tag, stats = m[0x8433], m[0x8435]
        inv = m[0x8434]
        d = derive(m, stats, inv)
        row = [m[0x8437 + i] for i in range(6)]
        print(f'  $FFFF = {idx}  {NAME[idx]}')
        print(f'     $8433 tag    ${tag:02X}   (= 8*{idx}: '
              f'{"OK" if tag == 8 * idx else "MISMATCH"}), '
              f'panel ink ${INK[idx]:02X}')
        print(f'     $8435 stats  ${stats:02X}   shot {d["shot"][0]}  '
              f'fight {d["fight"][0]}  magic {d["magic"][0]}  '
              f'armour {d["armour"][0]}   (inventory $8434 = ${inv:02X})')
        print(f'     $8437..$843C ' + ' '.join(f'{b:02X}' for b in row) +
              '   armour row [' + str(d['armour'][1]) + '] predicted ' +
              ' '.join(f'{b:02X}' for b in d['armour'][2]) +
              ('  OK' if row == d['armour'][2] else '  MISMATCH'))
        print(f'     shot dmg     actor ${d["shot"][2]:02X} tiers, '
              f'generator {d["shot"][3]}')
        print(f'     melee        $7D70[{d["fight"][1]}] = {d["fight"][2]} -> '
              f'{d["fight"][3]}' +
              (' or ' + str(d['fight'][3] + 1) + ' (alternating)'
               if d['fight'][4] else ' flat'))
        print(f'     magic        $7D1C[{d["magic"][1]}] = ' +
              ' '.join(f'{b:02X}' for b in d['magic'][2]))
        p2 = m[0x8455]
        print(f'     player 2 ($FFFE={(idx+1)&3}) $8453=${m[0x8453]:02X} '
              f'$8455=${p2:02X}')
        print()


if __name__ == '__main__':
    main()
