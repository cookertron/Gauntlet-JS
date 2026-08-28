#!/usr/bin/env python3
"""
actorsheet.py -- render every sprite an ACTOR RECORD can select, so that the
type byte's top three bits can be identified by LOOKING rather than by argument.

The id arithmetic is read from $ACF5..$AD13, the tail of the per-actor update:

    A = (type & $E0)          ; RRA -> t*16, RRA -> t*8, ADD -> t*24  (t = type>>5)
    A = A + (type & 7) + $40  ; direction slot, base id 64
    BIT 6,(IX+3) / JR z       ; <-- jumps past BOTH additions
    ADD A,8
    BIT 7,(IX+3) / JR z
    ADD A,8

        id    = $40 + 24*(type >> 5) + (type & 7) + 8*phase
        phase = 0 if flags bit 6 clear, else 1 if bit 7 clear, else 2

VERIFIED against the live game: trapping the pointer lookup at $A232/$A25D
whenever the record being drawn is in the $5C00 list gives 253/253 agreement
over 180 driven passes (see the docstring of tools/listwatch.py for the method).

The layout that falls out is uniform: 24 records per class, eight classes, and
classes 6 and 7 are the two PLAYERS' character sets at $5F00 and $6320 -- which
is the corroboration that the actor and player sprite schemes are one scheme.

    python tools/actorsheet.py [build/live_cs.bin] [build/actorsheet.png]
"""
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from sprite33 import sprite_addr, decode          # noqa: E402

PAL = [(0, 0, 0), (0, 0, 215), (215, 0, 0), (215, 0, 215),
       (0, 215, 0), (0, 215, 215), (215, 215, 0), (215, 215, 215),
       (0, 0, 0), (0, 0, 255), (255, 0, 0), (255, 0, 255),
       (0, 255, 0), (0, 255, 255), (255, 255, 0), (255, 255, 255)]


def actor_sprite_id(type_byte, flags):
    phase = 0 if not (flags & 0x40) else (2 if (flags & 0x80) else 1)
    return (0x40 + 24 * (type_byte >> 5) + (type_byte & 7) + 8 * phase) & 0xFF


def main():
    img_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(HERE), 'build', 'live_cs.bin')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(HERE), 'build', 'actorsheet.png')
    mem = open(img_path, 'rb').read()

    S = 3
    CELL = 16 * S + 4
    sheet = Image.new('RGB', (4 + 24 * CELL, 8 * (CELL + 14) + 4), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for cls in range(8):
        d.text((2, 2 + cls * (CELL + 14)),
               f'class {cls}  (type ${cls << 5:02X}..)  base id {0x40 + 24 * cls}',
               fill=(255, 255, 0))
        for slot in range(8):
            for ph in range(3):
                sid = (0x40 + 24 * cls + slot + 8 * ph) & 0xFF
                base = sprite_addr(mem, sid)
                x0 = 2 + (slot * 3 + ph) * CELL
                y0 = 2 + cls * (CELL + 14) + 12
                if base == 0 or base + 33 > len(mem):
                    d.text((x0, y0), '--', fill=(255, 0, 0))
                    continue
                attr, rows = decode(mem, base)
                bright = 8 if attr & 0x40 else 0
                ink = PAL[(attr & 7) + bright]
                paper = PAL[((attr >> 3) & 7) + bright]
                for r in range(16):
                    for c in range(16):
                        d.rectangle([x0 + c * S, y0 + r * S,
                                     x0 + c * S + S - 1, y0 + r * S + S - 1],
                                    fill=ink if rows[r][c] else paper)
                d.text((x0, y0 - 11), str(sid), fill=(200, 200, 200))
    sheet.save(out)
    print(f'wrote {out}')
    for cls in range(8):
        ptrs = [sprite_addr(mem, (0x40 + 24 * cls + i) & 0xFF) for i in range(24)]
        print(f'  class {cls}: {len(set(ptrs))} distinct records, first ${ptrs[0]:04X}')


if __name__ == '__main__':
    main()
