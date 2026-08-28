#!/usr/bin/env python3
"""
actorframes.py -- decode the MONSTER sprite records into build/actor_frames.json
for the port, exactly the way tools/playersprite.py does it for the player.

The id an actor is drawn with is computed by the game itself at $ACF5..$AD13:

    $ACF5  LD A,E / AND $E0 / RRA / LD C,A / RRA / ADD A,C / LD C,A
    $ACFD  LD A,E / AND 7 / OR $40 / ADD A,C          ; $40 + 24*class + facing
    $AD03  BIT 6,(IX+3) / JR z / ADD A,8
    $AD0B  BIT 7,(IX+3) / JR z / ADD A,8              ; phase 0,1,0,2

        id = $40 + 24*class + facing + 8*phase

VERIFIED, not derived: trapping $AD13 (where A holds the id and IX still points
at the record) over 40 driven passes with the player warped into the horde gave
512 draws and 512 agreements -- see the report.  Each class therefore owns 24
records = 8 compass facings x 3 walk phases, ids $40..$CF for classes 0..5.
Classes 6 and 7 (bases 208 and 232) are the two PLAYERS' character sets, which
is where the id space closes at 255.

A record is 33 bytes, reached through the 255-entry pointer table at $7B00
indexed (id-1)*2:  +0 attribute, +1..+32 = 16 rows x 2 bytes, left byte first.
Same format as the player's, same blitter ($9DD2), and it is OPAQUE.

    python tools/actorframes.py            # from build/live_cs.bin
"""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PTR_TABLE = 0x7B00
RECORD = 33
CLASSES = 6                      # 0..5; 6 and 7 are the player banks


def record_ptr(mem, sid):
    """$A231 DEC A / ADD A,A / LD L,A / LD H,$7B"""
    a = PTR_TABLE + ((sid - 1) * 2 & 0x1FF)
    return mem[a] | (mem[a + 1] << 8)


def main():
    img = os.path.join(ROOT, 'build', 'live_cs.bin')
    if len(sys.argv) > 1:
        img = sys.argv[1]
    mem = bytearray(open(img, 'rb').read())

    out = {
        '_source': f'{os.path.basename(img)}, records via the $7B00 pointer table',
        '_id_formula': '0x40 + 24*class + facing + 8*phase   ($ACF5..$AD13)',
        '_geometry': {'bytes_per_record': RECORD, 'rows': 16, 'bytes_per_row': 2},
        '_verified': '512/512 draws agreed with the id the game handed $AD13',
        'classes': {},
    }
    for cls in range(CLASSES):
        frames, inks, ptrs = [], [], []
        for phase in range(3):
            for facing in range(8):
                sid = 0x40 + 24 * cls + facing + 8 * phase
                p = record_ptr(mem, sid)
                ptrs.append(p)
                rec = mem[p:p + RECORD]
                inks.append(rec[0])
                frames.append(base64.b64encode(bytes(rec[1:])).decode())
        out['classes'][str(cls)] = {
            'base_id': 0x40 + 24 * cls,
            'ink': inks[0],
            'inks': inks,
            'uniform_ink': len(set(inks)) == 1,
            # index = facing + 8*phase, the same order the id formula uses
            'frames': frames,
        }
        span = max(ptrs) + RECORD - min(ptrs)
        print(f'class {cls}: ids ${0x40+24*cls:02X}..${0x40+24*cls+23:02X}  '
              f'ptrs ${min(ptrs):04X}..${max(ptrs):04X} span {span} bytes  '
              f'ink ${inks[0]:02X}  uniform={len(set(inks)) == 1}')

    dst = os.path.join(ROOT, 'build', 'actor_frames.json')
    json.dump(out, open(dst, 'w'), indent=1)
    print(f'wrote {dst}')


if __name__ == '__main__':
    main()
