#!/usr/bin/env python3
"""
screen.py -- render the Spectrum display file out of a memory image (manual
phase 6, ROUTE A: straight out of our own harness, so there is no scale search,
no grid fit and no colour classifier).

    addr = $4000 | ((y AND $C0) shl 5) | ((y AND $07) shl 8)
                 | ((y AND $38) shl 2) | (x shr 3)
    attrs = $5800 + row*32 + col       (linear, unlike the bitmap)
    attribute byte: bit7 FLASH, bit6 BRIGHT, bits5-3 PAPER, bits2-0 INK

Palette: non-bright components are 0xD7, bright 0xFF (manual A3 -- which of the
two a given game uses is a MEASUREMENT, made by scanning the attribute
constants the game writes; both are provided here and the renderer honours the
BRIGHT bit per cell).

Usage:
    python tools/screen.py build/live_play.bin out.png [--scale 3] [--attrs]
"""
import os
import sys

from PIL import Image

PAL_DIM = [(0, 0, 0), (0, 0, 0xD7), (0xD7, 0, 0), (0xD7, 0, 0xD7),
           (0, 0xD7, 0), (0, 0xD7, 0xD7), (0xD7, 0xD7, 0), (0xD7, 0xD7, 0xD7)]
PAL_BRIGHT = [(0, 0, 0), (0, 0, 0xFF), (0xFF, 0, 0), (0xFF, 0, 0xFF),
              (0, 0xFF, 0), (0, 0xFF, 0xFF), (0xFF, 0xFF, 0), (0xFF, 0xFF, 0xFF)]


def scr_addr(x, y):
    return 0x4000 | ((y & 0xC0) << 5) | ((y & 0x07) << 8) | ((y & 0x38) << 2) | (x >> 3)


def render(mem, base=0x4000, attr_base=0x5800, flash_phase=0):
    img = Image.new('RGB', (256, 192))
    px = img.load()
    for y in range(192):
        row = y >> 3
        for xb in range(32):
            a = base + (((y & 0xC0) << 5) | ((y & 0x07) << 8) | ((y & 0x38) << 2) | xb)
            bits = mem[a]
            at = mem[attr_base + row * 32 + xb]
            pal = PAL_BRIGHT if (at & 0x40) else PAL_DIM
            ink = pal[at & 7]
            paper = pal[(at >> 3) & 7]
            if (at & 0x80) and flash_phase:
                ink, paper = paper, ink
            for b in range(8):
                px[xb * 8 + b, y] = ink if (bits & (0x80 >> b)) else paper
    return img


def render_attrs(mem, attr_base=0x5800):
    """A 32x24 image of just the attribute file -- the primary diff for an
    attribute-driven game (manual 3.3(ii))."""
    img = Image.new('RGB', (32, 24))
    px = img.load()
    for row in range(24):
        for col in range(32):
            at = mem[attr_base + row * 32 + col]
            pal = PAL_BRIGHT if (at & 0x40) else PAL_DIM
            px[col, row] = pal[at & 7]
    return img


def main():
    args = sys.argv[1:]
    scale = 3
    attrs = False
    base = 0x4000
    if '--scale' in args:
        i = args.index('--scale'); scale = int(args[i + 1]); del args[i:i + 2]
    if '--attrs' in args:
        args.remove('--attrs'); attrs = True
    if '--base' in args:
        i = args.index('--base'); base = int(args[i + 1], 0); del args[i:i + 2]
    src, dst = args[0], args[1]
    mem = open(src, 'rb').read()
    img = render_attrs(mem, base + 0x1800) if attrs else render(mem, base, base + 0x1800)
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    img.save(dst)
    print(f'wrote {dst} ({img.width}x{img.height})')


if __name__ == '__main__':
    main()
