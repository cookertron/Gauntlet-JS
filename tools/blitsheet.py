#!/usr/bin/env python3
"""
blitsheet.py -- render EVERY shadow-screen draw of one main-loop pass, built
purely from the blitter's write values, as a labelled contact sheet.  Manual G7:
look at it.

Usage: python tools/blitsheet.py [--dir idle|right|left|up|down] [--passes 4]
                                 [--out build/blitsheet_idle.png]
"""
import os
import pickle
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness                                          # noqa: E402
from keyprobe import KEYS, keymask                                    # noqa: E402
from filmstrip import run_frames                                      # noqa: E402
import blitwatch as BW                                                # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}


def main():
    args = sys.argv[1:]
    direction, passes, out, warm = 'idle', 4, None, 32
    while args:
        if args[0] == '--dir':
            direction = args[1]; del args[:2]
        elif args[0] == '--passes':
            passes = int(args[1]); del args[:2]
        elif args[0] == '--out':
            out = args[1]; del args[:2]
        elif args[0] == '--warm':
            warm = int(args[1]); del args[:2]
        elif args[0] == '--state':
            globals()['STATE'] = args[1]; del args[:2]
        else:
            del args[:1]
    out = out or os.path.join(ROOT, 'build', f'blitsheet_{direction}.png')

    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    run_frames(h, warm)

    rows = []
    for p in range(passes):
        h.memobj.watch(0xC000, 0xDB00)
        entries = []
        BW.run_pass(h, entries, 4)
        h.memobj.unwatch()
        log = h.memobj.log
        m = h.memobj.m
        px_, py_, cx, cy = m[BW.P_X], m[BW.P_Y], m[BW.CAM_X], m[BW.CAM_Y]
        draws = [BW.describe(d) for d in BW.group_draws(log)]
        tiles = []
        for d in draws:
            grid, cols, rws = BW.sprite_from_writes(d)
            idx = d['bmp'][0][0]
            pops = [e for e in entries if e[0] <= idx and e[1] in (0x9DEC, 0x9E61)]
            sp = pops[-1][2] if pops else 0
            tiles.append((d, grid, sp))
        rows.append((p, (px_, py_), (cx, cy), tiles))
        print(f'pass {p}: player=({px_},{py_}) cam=({cx},{cy}) '
              f'-> expect x={(px_-cx)*4} y={(py_-cy)*4}   {len(tiles)} draws')
        for d, grid, sp in tiles:
            print(f"   ({d['x0']:3d},{d['y0']:3d}) {d['w_bytes']}x{d['h_rows']}"
                  f"  SP=${sp:04X}  attr={[f'{w[3]:02X}' for w in d['attr']]}")

    scale = 3
    cw, ch = 16 * scale + 8, 16 * scale + 26
    cols_n = max(len(r[3]) for r in rows)
    sheet = Image.new('RGB', (cols_n * cw + 4, len(rows) * ch + 4), (12, 12, 18))
    dr = ImageDraw.Draw(sheet)
    for i, (p, ply, cam, tiles) in enumerate(rows):
        for j, (d, grid, sp) in enumerate(tiles):
            im = BW.render_grid(grid, scale=scale)
            x0, y0 = 4 + j * cw, 4 + i * ch
            sheet.paste(im, (x0, y0))
            lbl = f"{d['x0']},{d['y0']}"
            dr.text((x0, y0 + 16 * scale + 1), lbl, fill=(200, 200, 200))
            dr.text((x0, y0 + 16 * scale + 11), f'{sp:04X}', fill=(120, 200, 255))
            if d['x0'] == (ply[0] - cam[0]) * 4 and d['y0'] == (ply[1] - cam[1]) * 4:
                dr.rectangle([x0 - 2, y0 - 2, x0 + 16 * scale + 1, y0 + 16 * scale + 1],
                             outline=(255, 60, 60))
    sheet.save(out)
    print(f'wrote {out}  ({sheet.width}x{sheet.height})')


if __name__ == '__main__':
    main()
