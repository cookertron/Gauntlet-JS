#!/usr/bin/env python3
"""
bankbrute.py -- brute-force the Gauntlet sprite bank layout and RANK the
hypotheses with a speckle metric, then write contact sheets to LOOK at.

Usage:  python tools/bankbrute.py
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from speckle import score_grid                                   # noqa: E402

IMG = os.path.join(ROOT, 'build', 'image.bin')
LIVE = os.path.join(ROOT, 'build', 'trap_live.bin')


# ---------------------------------------------------------------- decoders --
def dec(data, off, w, h, layout='row', rev=False, swap=False):
    """Return an h x (w*8) grid of 0/1."""
    n = w * h
    if off < 0 or off + n > len(data):
        return None
    blk = bytearray(data[off:off + n])
    if swap:
        for i in range(0, n - 1, 2):
            blk[i], blk[i + 1] = blk[i + 1], blk[i]
    g = [[0] * (w * 8) for _ in range(h)]
    if layout == 'row':
        for y in range(h):
            for b in range(w):
                v = blk[y * w + b]
                for k in range(8):
                    g[y][b * 8 + k] = (v >> (7 - k)) & 1
    elif layout == 'cell':
        rows = h // 8
        for cy in range(rows):
            for cx in range(w):
                base = (cy * w + cx) * 8
                for y in range(8):
                    v = blk[base + y]
                    for k in range(8):
                        g[cy * 8 + y][cx * 8 + k] = (v >> (7 - k)) & 1
    elif layout == 'interleave2':
        # two streams: even bytes = left column stream, odd = right column
        half = n // 2
        for y in range(h):
            if y >= half:
                break
            for b in range(w):
                v = blk[b * half + y] if b * half + y < n else 0
                for k in range(8):
                    g[y][b * 8 + k] = (v >> (7 - k)) & 1
    elif layout == 'halves':
        # 2 stacked 8-row halves stored consecutively as separate blocks
        for y in range(h):
            for b in range(w):
                blkidx = (y // 8) * (w * 8) + (y % 8) * w + b
                v = blk[blkidx] if blkidx < n else 0
                for k in range(8):
                    g[y][b * 8 + k] = (v >> (7 - k)) & 1
    if rev:
        g = g[::-1]
    return g


def sheet(grids, cols, scale=3, pad=2, bg=(24, 24, 32)):
    grids = [g for g in grids if g]
    if not grids:
        return Image.new('RGB', (8, 8), bg)
    gh, gw = len(grids[0]), len(grids[0][0])
    rows = (len(grids) + cols - 1) // cols
    im = Image.new('RGB', (cols * (gw + pad) + pad, rows * (gh + pad) + pad), bg)
    px = im.load()
    for i, g in enumerate(grids):
        ox = (i % cols) * (gw + pad) + pad
        oy = (i // cols) * (gh + pad) + pad
        for y in range(len(g)):
            for x in range(len(g[0])):
                if g[y][x]:
                    px[ox + x, oy + y] = (255, 255, 255)
                else:
                    px[ox + x, oy + y] = (0, 0, 0)
    return im.resize((im.width * scale, im.height * scale), Image.NEAREST)


def rank(data, base, count, hyps):
    out = []
    for name, off0, stride, w, h, layout, rev, swap in hyps:
        sc, grids = [], []
        for j in range(count):
            g = dec(data, base + off0 + j * stride, w, h, layout, rev, swap)
            if not g:
                continue
            grids.append(g)
            s = score_grid(g)
            if s and s['setpx'] > 16:
                sc.append(s['speckle'])
        if not sc:
            continue
        sc.sort()
        med = sc[len(sc) // 2]
        out.append((med, name, grids))
    out.sort(key=lambda r: r[0])
    return out


def main():
    img = open(IMG, 'rb').read()
    live = open(LIVE, 'rb').read()
    BANK = 0xC000                      # pristine 4-character bank in image.bin

    # every hypothesis the brief asked for, plus the winner
    hyps = [
        # name, first-byte offset, stride, w, h, layout, reversed, byteswap
        ('33-stride +1  2x16 row-major  [WINNER]', 1, 33, 2, 16, 'row', False, False),
        ('33-stride +0  2x16 row-major', 0, 33, 2, 16, 'row', False, False),
        ('33-stride +1  2x16 row REVERSED', 1, 33, 2, 16, 'row', True, False),
        ('33-stride +1  2x16 row BYTESWAP', 1, 33, 2, 16, 'row', False, True),
        ('33-stride +1  2x16 cell-major', 1, 33, 2, 16, 'cell', False, False),
        ('33-stride +1  2x16 halves', 1, 33, 2, 16, 'halves', False, False),
        ('33-stride +1  2x16 interleaved', 1, 33, 2, 16, 'interleave2', False, False),
        ('66-stride +1  2x16 row-major (even frames only)', 1, 66, 2, 16, 'row', False, False),
        ('66-stride +$43 2x33 row-major', 0x43, 66, 2, 33, 'row', False, False),
        ('66-stride +$43 3x22 row-major', 0x43, 66, 3, 22, 'row', False, False),
        ('66-stride +$43 6x11 row-major', 0x43, 66, 6, 11, 'row', False, False),
        ('66-stride +$43 4x16 (masked)', 0x43, 66, 4, 16, 'row', False, False),
        ('32-stride +0  2x16 row-major (naive)', 0, 32, 2, 16, 'row', False, False),
        ('32-stride +0  2x16 cell-major (naive)', 0, 32, 2, 16, 'cell', False, False),
        ('33-stride +1  1x32 row-major', 1, 33, 1, 32, 'row', False, False),
        ('33-stride +1  4x8 row-major', 1, 33, 4, 8, 'row', False, False),
        ('66-stride +$43 2x24 row-major', 0x43, 66, 2, 24, 'row', False, False),
        ('66-stride +$43 2x21 row-major', 0x43, 66, 2, 21, 'row', False, False),
        ('66-stride +$43 2x8  row-major', 0x43, 66, 2, 8, 'row', False, False),
    ]
    res = rank(img, BANK, 32, hyps)
    print('=== HYPOTHESIS RANKING, char 0 sprite set ($C000..$C41F of build/image.bin)')
    print('    lower speckle = more art-like\n')
    for med, name, _ in res:
        print(f'  {med:6.3f}   {name}')

    # baselines -- does the metric actually separate art from noise?
    print('\n=== METRIC BASELINES (median speckle over 32 blocks)')
    bl = [
        ('KNOWN-GOOD  chest/HUD art  live $E18D, 33-stride+0 2x16', live, 0xE18D, 0, 33, 2, 16),
        ('KNOWN-GOOD  pristine bank  image $C000, 33-stride+1 2x16', img, 0xC000, 1, 33, 2, 16),
        ('KNOWN-GOOD  intact tail    live  $6740, 33-stride+1 2x16', live, 0x6740, 1, 33, 2, 16),
        ('CLAIMED-GOOD tail          live  $6A00, 32-stride+0 2x16', live, 0x6A00, 0, 32, 2, 16),
        ('CORRUPT     live bank      live  $5F00, 33-stride+1 2x16', live, 0x5F00, 1, 33, 2, 16),
        ('KNOWN-NOISE actor list     live  $5C00, 32-stride+0 2x16', live, 0x5C00, 0, 32, 2, 16),
        ('KNOWN-NOISE Z80 code       live  $8800, 32-stride+0 2x16', live, 0x8800, 0, 32, 2, 16),
    ]
    for name, d, base, off0, stride, w, h in bl:
        r = rank(d, base, 32, [(name, off0, stride, w, h, 'row', False, False)])
        if r:
            print(f'  {r[0][0]:6.3f}   {name}')

    # contact sheets for the top hypotheses + the losers, so we can LOOK
    outdir = os.path.join(ROOT, 'build')
    written = []
    for med, name, grids in res[:3] + res[-3:]:
        tag = (name.split('[')[0].strip().replace(' ', '_').replace('/', '')
               .replace('+', 'p').replace('$', '').replace('(', '').replace(')', ''))
        p = os.path.join(outdir, f'bank_{tag}.png')
        sheet(grids, 8, scale=3).save(p)
        written.append((med, p))
    # the full pristine bank: all 4 characters, 32 frames each
    allg = [dec(img, BANK + 1 + j * 33, 2, 16, 'row') for j in range(128)]
    p = os.path.join(outdir, 'bank_all4_chars.png')
    sheet(allg, 16, scale=3).save(p)
    written.append((0, p))
    print('\n=== WROTE')
    for med, p in written:
        print('  ', p)


if __name__ == '__main__':
    main()
