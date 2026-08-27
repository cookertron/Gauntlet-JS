#!/usr/bin/env python3
"""
spritebank.py -- contact-sheet a sprite bank at the TRUE record geometry.

Recovered from the draw path at $9F79 and confirmed against the blitter's own
writes:

    a sprite record is 33 ($21) bytes
      +0        attribute byte  (loaded into C by LD C,(HL) at $9F83, then
                                 stored to the 2x2 attribute block at $9DDD)
      +1..+32   bitmap, 2 bytes per pixel row, 16 rows, ROW-MAJOR, top row
                first, LEFT byte first  (POP DE -> E=left, D=right)

Every earlier decode failed because it ignored the 1-byte attribute header and
used a 32-byte stride, so each successive record slid one byte further out of
phase -- which is exactly what "noise that becomes clean in the tail" looks
like when the tail happens to re-align.

Usage:
    python tools/spritebank.py 0x5F00 0x6F80 build/bank_5F00.png [--cols 16]
"""
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PAL_DIM = [(0, 0, 0), (0, 0, 0xD7), (0xD7, 0, 0), (0xD7, 0, 0xD7),
           (0, 0xD7, 0), (0, 0xD7, 0xD7), (0xD7, 0xD7, 0), (0xD7, 0xD7, 0xD7)]
PAL_BRIGHT = [(0, 0, 0), (0, 0, 0xFF), (0xFF, 0, 0), (0xFF, 0, 0xFF),
              (0, 0xFF, 0), (0, 0xFF, 0xFF), (0xFF, 0xFF, 0), (0xFF, 0xFF, 0xFF)]
REC = 33


def render_record(data, scale=3, use_attr=True):
    """data = 33 bytes."""
    at = data[0]
    pal = PAL_BRIGHT if (at & 0x40) else PAL_DIM
    ink = pal[at & 7] if use_attr else (255, 214, 0)
    paper = pal[(at >> 3) & 7] if use_attr else (18, 18, 26)
    im = Image.new('RGB', (16 * scale, 16 * scale), paper)
    px = im.load()
    for r in range(16):
        for c in range(2):
            v = data[1 + r * 2 + c]
            for b in range(8):
                col = ink if (v & (0x80 >> b)) else paper
                for sy in range(scale):
                    for sx in range(scale):
                        px[(c * 8 + b) * scale + sx, r * scale + sy] = col
    return im


def sheet(mem, lo, hi, cols=16, scale=3, use_attr=True, label=True):
    n = (hi - lo) // REC
    rows = (n + cols - 1) // cols
    cw = 16 * scale + 6
    chh = 16 * scale + (12 if label else 4)
    im = Image.new('RGB', (cols * cw + 4, rows * chh + 4), (10, 10, 16))
    dr = ImageDraw.Draw(im)
    for i in range(n):
        a = lo + i * REC
        tile = render_record(mem[a:a + REC], scale, use_attr)
        x, y = 4 + (i % cols) * cw, 4 + (i // cols) * chh
        im.paste(tile, (x, y))
        if label:
            dr.text((x, y + 16 * scale), f'{a:04X}', fill=(150, 170, 200))
    return im


def main():
    args = sys.argv[1:]
    cols, scale, noattr = 16, 3, False
    while '--cols' in args:
        i = args.index('--cols'); cols = int(args[i + 1]); del args[i:i + 2]
    while '--scale' in args:
        i = args.index('--scale'); scale = int(args[i + 1]); del args[i:i + 2]
    if '--noattr' in args:
        args.remove('--noattr'); noattr = True
    img = os.path.join(ROOT, 'build', 'live_cs.bin')
    if '--image' in args:
        i = args.index('--image'); img = args[i + 1]; del args[i:i + 2]
    lo, hi, out = int(args[0], 0), int(args[1], 0), args[2]
    mem = bytearray(open(img, 'rb').read())
    im = sheet(mem, lo, hi, cols, scale, not noattr)
    im.save(out)
    print(f'wrote {out} ({im.width}x{im.height}) '
          f'{(hi - lo) // REC} records of {REC} bytes from ${lo:04X}')


if __name__ == '__main__':
    main()
