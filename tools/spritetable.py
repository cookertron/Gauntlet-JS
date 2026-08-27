#!/usr/bin/env python3
"""
spritetable.py -- the sprite POINTER TABLE at $7B00 and the 33-byte record.

Read, not guessed, from $A231 (reached by tracing back from the blitter entry):

    $A231  EX AF,AF'          ; A' = sprite index, 1-based
    $A232  DEC A
    $A233  ADD A,A            ; *2, carry = index >= 129
    $A234  LD L,A / LD H,$7B
    $A237  JR nc,$A23A / INC H        ; table is $7B00..$7CFF, 256 entries
    $A23A  LD A,(HL) / INC L / LD H,(HL) / LD L,A   ; HL = sprite RECORD
    $A23E  LD C,(HL)          ; record +0  = the ATTRIBUTE byte
    $A23F  INC HL             ; record +1.. = 32 bytes of pixels
    $A240  DI / LD SP,HL / EX DE,HL / JP $9DD2

So a sprite record is 33 bytes: 1 attribute + 16 rows x 2 bytes, row-major,
left byte first (the blitter writes E then D, E always to the lower column).
$21 = 33 and $42 = 66 -- exactly the strides the animation dispatcher at $A31A
adds to its frame pointers before storing them into table slots $7B5C/$7B5E.

Usage:  python tools/spritetable.py [image.bin] [--out build/sprites.png]
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PAL_DIM = [(0, 0, 0), (0, 0, 0xD7), (0xD7, 0, 0), (0xD7, 0, 0xD7),
           (0, 0xD7, 0), (0, 0xD7, 0xD7), (0xD7, 0xD7, 0), (0xD7, 0xD7, 0xD7)]
PAL_BRIGHT = [(0, 0, 0), (0, 0, 0xFF), (0xFF, 0, 0), (0xFF, 0, 0xFF),
              (0, 0xFF, 0), (0, 0xFF, 0xFF), (0xFF, 0xFF, 0), (0xFF, 0xFF, 0xFF)]
TABLE = 0x7B00
NENT = 256


def record(mem, addr):
    return mem[addr], bytes(mem[addr + 1:addr + 33])


def render(attr, bm, scale=4, colour=True):
    im = Image.new('RGB', (16 * scale, 16 * scale))
    px = im.load()
    pal = PAL_BRIGHT if (attr & 0x40) else PAL_DIM
    ink, paper = (pal[attr & 7], pal[(attr >> 3) & 7]) if colour \
        else ((255, 255, 0), (0, 0, 0))
    for r in range(16):
        for b in range(2):
            v = bm[r * 2 + b]
            for k in range(8):
                c = ink if (v & (0x80 >> k)) else paper
                for sy in range(scale):
                    for sx in range(scale):
                        px[(b * 8 + k) * scale + sx, r * scale + sy] = c
    return im


def entropy_score(bm):
    """Cheap 'is this noise?' measure: fraction of horizontal bit transitions.
    Real 16x16 artwork runs about 0.10-0.25; uniform noise sits near 0.5."""
    t = n = 0
    for r in range(16):
        v = (bm[r * 2] << 8) | bm[r * 2 + 1]
        for k in range(15):
            t += ((v >> k) & 1) != ((v >> (k + 1)) & 1)
            n += 1
    return t / n


def main():
    args = sys.argv[1:]
    img = os.path.join(ROOT, 'build', 'live_cs.bin')
    out = os.path.join(ROOT, 'build', 'sprites.png')
    colour = True
    if args and not args[0].startswith('--'):
        img = args[0]; del args[:1]
    if '--out' in args:
        i = args.index('--out'); out = args[i + 1]; del args[i:i + 2]
    if '--mono' in args:
        args.remove('--mono'); colour = False
    mem = bytearray(open(img, 'rb').read())

    ptrs = [(mem[TABLE + 2 * i] | (mem[TABLE + 2 * i + 1] << 8))
            for i in range(NENT)]
    ims = []
    print('idx  ptr    attr  noise  bytes')
    for i, p in enumerate(ptrs):
        if p < 0x4000 or p > 0xFFDE:
            ims.append(None)
            continue
        attr, bm = record(mem, p)
        e = entropy_score(bm)
        ims.append(render(attr, bm, colour=colour))
        if i < 24 or 40 <= i < 60:
            print(f'{i+1:3}  ${p:04X}  ${attr:02X}  {e:.2f}   {bm[:8].hex()}')
    cols = 16
    rows = (len(ims) + cols - 1) // cols
    cw = 16 * 4 + 4
    sheet = Image.new('RGB', (cols * cw, rows * cw), (40, 40, 60))
    for i, im in enumerate(ims):
        if im is not None:
            sheet.paste(im, ((i % cols) * cw + 2, (i // cols) * cw + 2))
    sheet.save(out)
    print(f'wrote {out}  ({len([i for i in ims if i])} records, index 1..{NENT})')


if __name__ == '__main__':
    main()
