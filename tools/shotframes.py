#!/usr/bin/env python3
"""
shotframes.py -- decode the SHOT sprite records into build/shot_frames.json.

The shot blitter picks its record with arithmetic, not with the $7B00 pointer
table every other sprite in the game goes through -- so this decoder is a
transcription of $8DBF..$8DF8 and nothing else:

    $8DBF  LD A,(IX+2)                  the shot's STATE byte
    $8DC9  CP 8 / JR nc                 state < 8 -> $8DCD LD A,($8491) / AND 7
    $8DD2  ADD A,A / ADD A,A / ADD A,A  idx*8, and the CARRY out of it matters
    $8DD5  LD L,A / LD H,0
    $8DDA  JR nc,$8DE9                  no carry -> the ordinary bank
    $8DDC  INC H / LD A,L / OR A / JP nz,$8EB7    idx*8 >= 256 and not exactly
    $8DE2  LD HL,$DD58 / LD B,0         256 -> the 8-wide OPAQUE blitter;
                                        exactly 256 -> the EXPLOSION record
    $8DE9  ADD HL,HL / ADD HL,HL        HL = idx*32
    $8DEB  LD A,H / ADD A,A / ADD A,$D0 / LD H,A
    $8DF0  BIT 7,L / JR z / INC H       (idx&7) >= 4 -> the next page
    $8DF5  SET 7,L / LD B,$FF
    $8DF9  LD C,(HL) / INC HL / LD SP,HL      +0 ATTRIBUTE, +1.. the rows

so   addr = $D000 + 256*(2*(idx>>3) + ((idx&7)>=4)) + (((idx&7)*32) | $80)
which for the four legal shot tags $00/$08/$10/$18 gives the four banks
$D080 / $D280 / $D480 / $D680 with attributes $42 / $45 / $46 / $44.

The record is then TWELVE rows of two bytes (twelve POP DE between $8DFD and
$8EB0), not the sixteen the $9DD2 sprite blitter reads -- so a shot record is
25 bytes of the 32-byte slot and the last seven are never read.

Those addresses are inside the SHADOW SCREEN.  They survive because $B4FF's
per-pass clear only wipes the low $80 bytes of each page $D000..$D7FF, and the
sprite banks live in the top halves -- the game stashes them in the shadow
screen's HUD rows, which the clear steps over and the display never shows.

    python tools/shotframes.py            # from build/live_cs.bin
"""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# $BF19's four legal character tags, plus $90 -- the MONSTER shot's, which
# $8FC5 writes as `facing | $90` and which draws a directional fireball in
# bright yellow ($46) rather than a character-coloured arrow.
TAGS = (0x00, 0x08, 0x10, 0x18, 0x90)
ROWS = 12                                 # twelve POP DE, $8E37..$8EA8
EXPLODE = 0xDD58                          # $8DE2 -- state exactly $20


def record_addr(idx):
    """$8DD2..$8DF5, and it is NOT idx * 32.

    $8DD2 is three ADD A,A on an 8-BIT register and only the LAST carry
    survives into H ($8DD8 LD H,0 / JR nc / INC H), after which $8DE9 shifts
    HL twice more.  For the four character tags idx never exceeds $1F, the
    dropped carries are all zero and idx * 32 is the same answer -- which is
    why the earlier transcription here was never wrong in practice.  It is
    wrong for the MONSTER tag $90: idx * 32 says $F480, which is noise, and
    the machine says $D480, which is a fireball.
    """
    a = idx & 0xFF
    c = 0
    for _ in range(3):                       # $8DD2 ADD A,A x3
        c = 1 if (a & 0x80) else 0
        a = (a << 1) & 0xFF
    hl = (((c << 8) | a) * 4) & 0xFFFF       # $8DD5 LD L,A .. $8DEA ADD HL,HL
    h = (((hl >> 8) * 2) + 0xD0) & 0xFF      # $8DEB LD A,H / ADD A,A / ADD $D0
    lo = hl & 0xFF
    if lo & 0x80:                            # $8DF0 BIT 7,L / INC H
        h = (h + 1) & 0xFF
    return (h << 8) | (lo | 0x80)            # $8DF5 SET 7,L


def decode(mem, addr):
    rec = mem[addr:addr + 1 + 2 * ROWS]
    return rec[0], base64.b64encode(bytes(rec[1:])).decode()


def main():
    img = os.path.join(ROOT, 'build', 'live_cs.bin')
    if len(sys.argv) > 1:
        img = sys.argv[1]
    mem = bytearray(open(img, 'rb').read())

    out = {
        '_source': f'{os.path.basename(img)}, addresses from $8DE9..$8DF8',
        '_geometry': {'rows': ROWS, 'bytes_per_row': 2, 'width': 16,
                      'first_screen_row': 2},
        '_note': ('12 source rows land on 10 screen rows: $8E8C and $8E9E are '
                  'INC C where every sibling is INC D, so source rows 7+8 '
                  'share a screen row and 9+10 share the next.'),
        'banks': {},
    }
    # $8EB7 -- the CLASS-3 fireball, which does not use the record table at
    # all.  For its indices ($21..$24, from $90A1's (flags>>4 & 3) + $21) the
    # three ADD A,A at $8DD2 leave a CARRY, and $8DDD LD A,L / OR A / JP nz
    # sends it to $8EB7: HL*4 + $D958, i.e. $DD78, $DD98, $DDB8, $DDD8.
    # Three sizes of a growing fireball in BRIGHT GREEN ($44) and a fourth
    # record that is empty.  $DD58 is the explosion the same page holds.
    out['fireball'] = []
    for a in (0xDD78, 0xDD98, 0xDDB8, 0xDDD8):
        ink, frame = decode(mem, a)
        out['fireball'].append({'addr': a, 'ink': ink, 'frame': frame})
    print('fireball: ' + ' '.join('$%04X ink $%02X' % (f['addr'], f['ink'])
                                  for f in out['fireball']))

    for tag in TAGS:
        inks, frames, addrs = [], [], []
        for slot in range(8):
            a = record_addr(tag | slot)
            ink, bits = decode(mem, a)
            inks.append(ink)
            frames.append(bits)
            addrs.append('$%04X' % a)
        out['banks'][str(tag)] = {
            'tag': tag, 'ink': inks[0], 'inks': inks,
            'addrs': addrs, 'frames': frames,     # index = compass slot 0..7
            'uniform_ink': len(set(inks)) == 1,
        }
        print(f'tag ${tag:02X}: {addrs[0]}..{addrs[-1]}  ink ${inks[0]:02X}  '
              f'uniform={len(set(inks)) == 1}')

    ink, bits = decode(mem, EXPLODE)
    out['explode'] = {'addr': '$%04X' % EXPLODE, 'ink': ink, 'frame': bits}
    print(f'explosion $DD58: ink ${ink:02X}')

    dst = os.path.join(ROOT, 'build', 'shot_frames.json')
    json.dump(out, open(dst, 'w'), indent=1)
    print(f'wrote {dst}')


if __name__ == '__main__':
    main()
