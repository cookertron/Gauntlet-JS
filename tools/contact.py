#!/usr/bin/env python3
"""
contact.py -- contact sheet of a memory range rendered as sprites, BOTH ways
(manual 5.1/A2: "the number of layouts equals the number of draw routines --
render every candidate block BOTH WAYS into one contact sheet and LOOK").

  row-major : `w` bytes per pixel row, rows consecutive  (what a blitter that
              draws a full 16-pixel row at a time wants)
  cell-major: each 8x8 character cell is 8 consecutive bytes

Usage:
  python tools/contact.py build/live_cs.bin out.png --base 0x5F00 --len 0x1080
                         --w 2 --h 16 [--layout row|cell|both] [--cols 24]
"""
import sys

from PIL import Image


def cells_row_major(data, w, h):
    stride = w * h
    out = []
    for o in range(0, len(data) - stride + 1, stride):
        img = Image.new('RGB', (w * 8, h), (0, 0, 0))
        px = img.load()
        for y in range(h):
            for b in range(w):
                v = data[o + y * w + b]
                for k in range(8):
                    if v & (0x80 >> k):
                        px[b * 8 + k, y] = (255, 255, 255)
        out.append(img)
    return out


def cells_cell_major(data, w, h):
    stride = w * h
    out = []
    rows = h // 8
    for o in range(0, len(data) - stride + 1, stride):
        img = Image.new('RGB', (w * 8, h), (0, 0, 0))
        px = img.load()
        for cy in range(rows):
            for cx in range(w):
                base = o + (cy * w + cx) * 8
                for y in range(8):
                    v = data[base + y]
                    for k in range(8):
                        if v & (0x80 >> k):
                            px[cx * 8 + k, cy * 8 + y] = (255, 255, 255)
        out.append(img)
    return out


def sheet(imgs, cols, label):
    if not imgs:
        return Image.new('RGB', (1, 1))
    cw, ch = imgs[0].size
    rows = (len(imgs) + cols - 1) // cols
    im = Image.new('RGB', (cols * (cw + 2), rows * (ch + 2) + 10), (30, 30, 40))
    for i, s in enumerate(imgs):
        im.paste(s, ((i % cols) * (cw + 2) + 1, (i // cols) * (ch + 2) + 1))
    return im


def main():
    a = sys.argv[1:]
    src, dst = a[0], a[1]
    del a[:2]
    base, ln, w, h, cols, layout = 0, None, 2, 16, 24, 'both'
    while a:
        if a[0] == '--base':
            base = int(a[1], 0); del a[:2]
        elif a[0] == '--len':
            ln = int(a[1], 0); del a[:2]
        elif a[0] == '--w':
            w = int(a[1]); del a[:2]
        elif a[0] == '--h':
            h = int(a[1]); del a[:2]
        elif a[0] == '--cols':
            cols = int(a[1]); del a[:2]
        elif a[0] == '--layout':
            layout = a[1]; del a[:2]
        else:
            del a[:1]
    data = open(src, 'rb').read()
    data = data[base:base + ln] if ln else data[base:]
    parts = []
    if layout in ('row', 'both'):
        parts.append(sheet(cells_row_major(data, w, h), cols, 'row'))
    if layout in ('cell', 'both'):
        parts.append(sheet(cells_cell_major(data, w, h), cols, 'cell'))
    total_h = sum(p.height for p in parts) + 8 * len(parts)
    im = Image.new('RGB', (max(p.width for p in parts), total_h), (10, 10, 15))
    y = 0
    for p in parts:
        im.paste(p, (0, y))
        y += p.height + 8
    im = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
    im.save(dst)
    print(f'wrote {dst}: {len(data)} bytes, {w*8}x{h} cells, layout={layout}')


if __name__ == '__main__':
    main()
