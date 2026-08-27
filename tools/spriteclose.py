#!/usr/bin/env python3
"""
spriteclose.py -- the closure test for the player sprite format.

Claim under test: a sprite record is 33 bytes -- 1 attribute byte then 16 rows
of 2 bytes, row-major, LEFT byte first -- and the 16x16 the blitter leaves in
the shadow screen is those 32 bytes verbatim (the blit is opaque, LD (HL),E).

The test captures the shadow bitmap at the blitter's EXIT ($9E49) and compares
it byte for byte with mem[src .. src+32], and compares the 2x2 shadow attribute
cells with mem[src-1].  Anything less than 100% means the geometry is wrong.

It also de-duplicates on the INNER 16x16 (not on the 32x32 grab, whose margin
carries background) to get the honest frame count per direction.
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from spritegrab import (capture, inner16, render, bbox, MARGIN)     # noqa: E402

BUILD = os.path.join(ROOT, 'build')


def main():
    passes = 32
    if '--passes' in sys.argv:
        passes = int(sys.argv[sys.argv.index('--passes') + 1])
    char = 3
    if '--char' in sys.argv:
        char = int(sys.argv[sys.argv.index('--char') + 1])

    ok = bad = 0
    attr_ok = attr_bad = 0
    rows = []
    for direction in (None, 'up', 'down', 'left', 'right'):
        name = direction or 'idle'
        recs = capture(direction, passes, char=char)
        uniq = {}
        for r in recs:
            if r['after'] is None:
                continue
            got = inner16(r['after'])
            want = r['srcbytes']                     # mem[src .. src+32]
            if got == want:
                ok += 1
            else:
                bad += 1
                if bad < 4:
                    print(f'  MISMATCH {name} src=${r["src"]:04X}\n'
                          f'    screen {got.hex()}\n    source {want.hex()}')
            # the record's attribute byte is at src-1; the blitter puts C into
            # all four cells of the 2x2
            cells = r['sattr']          # 4x4 cells; the 16x16 is rows/cols 1..2
            quad = [cells[5], cells[6], cells[9], cells[10]]
            if all(q == r['attr'] for q in quad) and r['attr'] == r['recattr']:
                attr_ok += 1
            else:
                attr_bad += 1
            u = uniq.setdefault(got, {'n': 0, 'r': r, 'src': set()})
            u['n'] += 1
            u['src'].add(r['src'])
        order = sorted(uniq.values(), key=lambda u: -u['n'])
        print(f'{name:>6}: {len(recs)} blits, {len(order)} distinct 16x16, '
              f'records ' + ' '.join(f'${s-1:04X}' for u in order
                                     for s in sorted(u['src'])))
        for u in order:
            bb = bbox(u['r']['after'])
            print(f'         n={u["n"]:3} record=${min(u["src"])-1:04X} '
                  f'attr=${u["r"]["attr"]:02X} '
                  f'bbox in the 16x16 = '
                  f'({bb[0]-MARGIN},{bb[1]-MARGIN})-({bb[2]-MARGIN},{bb[3]-MARGIN}) '
                  f'= {bb[2]-bb[0]+1}x{bb[3]-bb[1]+1} px')
        rows.append((name, [render(u['r']['after'], u['r']['sattr'],
                                   box=(MARGIN, MARGIN, 16, 16)) for u in order]))

    print(f'\nCLOSURE: screen 16x16 == mem[src..src+32]   {ok} of {ok+bad} '
          f'({100.0*ok/max(1,ok+bad):.2f}%)')
    print(f'         shadow 2x2 attrs == C == mem[src-1]  {attr_ok} of '
          f'{attr_ok+attr_bad} ({100.0*attr_ok/max(1,attr_ok+attr_bad):.2f}%)')

    cols = max(len(r[1]) for r in rows)
    cw = rows[0][1][0].width + 6
    ch = rows[0][1][0].height + 6
    sheet = Image.new('RGB', (cols * cw, len(rows) * ch), (25, 25, 35))
    for i, (name, ims) in enumerate(rows):
        for j, im in enumerate(ims):
            sheet.paste(im, (j * cw + 3, i * ch + 3))
    out = os.path.join(BUILD, f'player_c{char}_sheet.png')
    sheet.save(out)
    print('rows top to bottom: ' + ', '.join(r[0] for r in rows))
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
