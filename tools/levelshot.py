#!/usr/bin/env python3
"""
levelshot.py -- drive the ORIGINAL into a later dungeon and put its screen
beside the PORT's.

    python tools/levelshot.py 2 200 1    dungeon 2, 200 passes, holding UP

It drives the real Z80 through $B3D0 for the wanted dungeon (tape load, the
pack's own stub, $91D7, $9175, $97CB and the placement), calls $8B27 the way
$B38E does, enters the main loop at $8503 and holds a key, then renders
$4000..$5AFF; then it runs `node tools/render_shot.js --level N` with the same
direction and the same number of passes and stacks the two.

WHAT IT DOES AND DOES NOT SHOW -- stated plainly, because the picture is not
what you would first expect.  The ORIGINAL's playfield is redrawn strictly
INCREMENTALLY: $9EFC paints map tiles into the shadow screen only for the
columns and rows the camera has just uncovered, $B4FF clears the shadow
playfield again every pass, and $A08B/$A159 blit only the uncovered strips to
$4000.  A fresh level therefore keeps $8B27's "LEVEL : n" banner over
everything the camera has not scrolled past, and neither screen ever holds a
whole static picture of the dungeon.  So this tool shows:

  * that the original really reaches dungeon N through the whole chain (the
    banner and the panel are its own), and where its ACTORS are;
  * the port's rendering of the same dungeon underneath.

It is NOT a pixel comparison of the map, and it should not be read as one.
THE MAP COMPARISON IS THE CLOSURE TEST -- tools/levelgate.py dumps the grid
the original leaves at $8000..$83FF and `node tools/headless.js` checks all
307 of them cell for cell.  For a picture of the original's own blitter on a
dungeon, build/pack_5_0.png (from the pack-format work) is the one to look at.
"""
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, TAPE_CALL_PC              # noqa: E402
import packbuild                                           # noqa: E402
import screen as screenmod                                 # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')


def main():
    level = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    key = sys.argv[3] if len(sys.argv) > 3 else 'D'
    DIRNAME = {'D': 'right', 'S': 'left', '1': 'up', 'Q': 'down'}

    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    first = True
    for lv in range(1, level + 1):
        packbuild.build(h, lv, rewind=first, watch=False)
        first = False
    # $B3D0 returned to the harness sentinel, so enter the main loop by hand
    # ($B381's own `CALL $8503`) and let the game paint its own screen.  The
    # loop top is visited exactly once per pass.
    r = h.regs
    m = h.memobj.m
    sp = r[12]
    for s in reversed(h.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        m[sp] = s & 0xFF
        m[sp + 1] = s >> 8
    r[12] = sp
    # $B38E CALL $8B27 -- the full repaint that stands between $B3D0 and the
    # main loop.  Without it the playfield is whatever the LOAD SCREEN left
    # there, because the per-pass blitter only redraws the columns and rows
    # the camera has just uncovered.
    h.call(0x8B27)
    sp = r[12]
    for s in reversed(h.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        m[sp] = s & 0xFF
        m[sp + 1] = s >> 8
    r[12] = sp
    r[PC] = 0x8503
    # The original's playfield is redrawn INCREMENTALLY, only for the columns
    # and rows the camera has just uncovered, so a level whose camera never
    # moves keeps whatever the level-intro screen ($8B27's "LEVEL : n") left
    # there.  Hold a direction -- control method 3, so D is right for player 1
    # -- and let the camera travel, which is what makes it paint.  The port is
    # driven with the same key for the same number of passes.
    from keyprobe import KEYS, keymask
    km = {n: (s, b) for n, s, b in KEYS}
    sel, bit = km[key.upper()]
    h.ports.press(sel, keymask(bit))
    for _ in range(passes):
        h.run_until((0x8503,), limit=4_000_000)
    mem = bytes(h.memobj.m[0:0x10000])
    orig_png = os.path.join(ROOT, 'build', '_lvl%d_orig.png' % level)
    screenmod.render(mem, base=0x4000, attr_base=0x5800).save(orig_png)

    ppm = os.path.join(ROOT, 'build', '_lvl%d_port.ppm' % level)
    subprocess.run(['node', os.path.join(HERE, 'render_shot.js'), ppm,
                    DIRNAME.get(key.upper(), 'none'), str(passes),
                    '--level', str(level)], cwd=ROOT, check=True)

    from PIL import Image
    a = Image.open(orig_png).convert('RGB')
    b = Image.open(ppm).convert('RGB')
    w, hh = b.size
    a = a.resize((w, hh), Image.NEAREST)
    out = Image.new('RGB', (w, hh * 2))
    out.paste(a, (0, 0))
    out.paste(b, (0, hh))
    out = out.resize((w * 2, hh * 4), Image.NEAREST)
    path = os.path.join(ROOT, 'build', 'level_%d_vs_original.png' % level)
    out.save(path)
    print('wrote', path, '-- ORIGINAL on top, PORT below')


if __name__ == '__main__':
    main()
