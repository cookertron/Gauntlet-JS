#!/usr/bin/env python3
"""
shotcompare.py -- the ORIGINAL's real display against the PORT's canvas, over
a shot in flight.  Same shape as tools/playercompare.py, and the same gate:
count the differing pixels and ATTRIBUTE every differing cell in writing.

    python tools/shotcompare.py [dir] [passes]
    python tools/shotcompare.py all

The original is driven with the ELF character installed the way the boot would
install it (tools/shotgate.py's elf(), i.e. the $BF19 pair plus the game's own
$AB6F), FIRE held, and sampled at the main-loop top $8503 -- so the display
file has already been copied from the shadow screen by $8550 CALL $9CD7 and
carries the shot the pass drew.  The port is rendered headlessly at the same
pass through tools/render_shot.js.

WHAT IS EXPECTED TO DIFFER, and it is not the shot:
  * the player's own 2x2 cell block.  The original in this captured state
    draws 1,056 bytes of ROM there -- the $FFFF boot bug NOTES-engine.md
    documents -- and the port draws the repaired elf.
  * three whole cells of panel/edge, (0,0) and (25,0)/(26,0), which differ
    with or without a shot and are a pre-existing tile-capture difference.
The SHOT's own cells must differ by ZERO pixels.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from PIL import Image                                   # noqa: E402
import shotgate as SG                                   # noqa: E402
from screen import PAL_DIM, PAL_BRIGHT                  # noqa: E402

ROWS = 20                                # the playfield; 20..23 are the HUD


def decode(mem, base=0x4000, attrs=0x5800, rows=ROWS):
    """The display-file transform, straight out of the manual's 3.2."""
    im = Image.new('RGB', (256, rows * 8))
    px = im.load()
    for y in range(rows * 8):
        a = base | ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2)
        for xb in range(32):
            at = mem[attrs + (y >> 3) * 32 + xb]
            pal = PAL_BRIGHT if at & 0x40 else PAL_DIM
            ink, paper = pal[at & 7], pal[(at >> 3) & 7]
            b = mem[a + xb]
            for i in range(8):
                px[xb * 8 + i, y] = ink if b & (0x80 >> i) else paper
    return im


def one(direction='right', n=4, fire=True, save=True):
    h = SG.boot()
    SG.elf(h, 3)
    k = SG.DIRKEY[direction]
    if k:
        SG.press(h, k)
    SG.step_to(h, {SG.LOOP_TOP})
    if fire:
        SG.press(h, 'Z')
    for _ in range(n):
        SG.step_to(h, {SG.LOOP_TOP})
    m = h.memobj.m
    sx, sy, st = m[0x8430], m[0x8431], m[0x8432]
    camx, camy = m[0x848B], m[0x848C]
    orig = decode(m)

    ppm = os.path.join(ROOT, 'build', '_shotcmp.ppm')
    cmd = f'node tools/render_shot.js {ppm} {direction} {n}'
    if fire:
        cmd += ' --fire'
    subprocess.run(cmd, shell=True, cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    port = Image.open(ppm).crop((0, 0, 256, ROWS * 8))

    pa, pb = orig.load(), port.load()
    cells = {}
    for y in range(ROWS * 8):
        for x in range(256):
            if pa[x, y] != pb[x, y]:
                cells[(x // 8, y // 8)] = cells.get((x // 8, y // 8), 0) + 1

    # the cells the shot occupies: $B557's transform, 2 columns x 2 rows
    scol = ((sx - camx) & 0x7E) >> 1
    srow = ((sy - camy) & 0x7E) >> 1
    shot_cells = {(scol + a, srow + b) for a in (0, 1) for b in (0, 1)}
    pcol = ((m[0x8420] - camx) & 0x7E) >> 1
    prow = ((m[0x8421] - camy) & 0x7E) >> 1
    player_cells = {(pcol + a, prow + b) for a in (0, 1) for b in (0, 1)}

    in_shot = sum(v for c, v in cells.items() if c in shot_cells)
    in_player = sum(v for c, v in cells.items() if c in player_cells)
    other = {c: v for c, v in cells.items()
             if c not in shot_cells and c not in player_cells}
    print(f'  {direction:<6} pass {n:>2}  shot ({sx},{sy}) state ${st:02X} '
          f'cells {sorted(shot_cells)}')
    print(f'         differing pixels: SHOT {in_shot}   player {in_player} '
          f'(the $FFFF boot bug)   elsewhere {sum(other.values())} in '
          f'{sorted(other)}')
    if save:
        both = Image.new('RGB', (256, ROWS * 8 * 2 + 4), (40, 40, 40))
        both.paste(orig, (0, 0))
        both.paste(port, (0, ROWS * 8 + 4))
        dst = os.path.join(ROOT, 'build', f'shot_vs_original_{direction}.png')
        both.resize((768, (ROWS * 8 * 2 + 4) * 3), Image.NEAREST).save(dst)
        print(f'         wrote {dst}')
    return in_shot


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'right'
    if what == 'all':
        bad = 0
        print('# original vs port, ELF character, FIRE held')
        for d in ('right', 'left', 'up', 'down'):
            for n in (2, 3, 4):
                bad += one(d, n, save=(n == 3))
        print(f'\nTOTAL SHOT PIXELS DIFFERING: {bad}')
        # and the same frame with no shot at all, to show which cells were
        # already differing before shooting was ported
        print('\n# the SAME frame with no fire held, for attribution:')
        one('right', 4, fire=False, save=False)
        sys.exit(1 if bad else 0)
    one(what, int(sys.argv[2]) if len(sys.argv) > 2 else 4)


if __name__ == '__main__':
    main()
