#!/usr/bin/env python3
"""
playercompare.py -- put the ORIGINAL's real display and the PORT's canvas side
by side over the player's 16x16, and count the differing pixels.

This is the cross-language gate for the sprite: tools/playersprite.py proves the
DECODE against the original's own shadow-screen writes, and this proves the
whole pipeline -- decode, JSON, build, renderer -- against the original's REAL
display, with a picture to look at (manual G7).

Both crops are taken at the address each side computed for itself:
  * the original at the destination its blitter was actually handed (trapped at
    $9DD2), which is the only non-circular choice;
  * the port at ((x-cam)&$7E)>>1*8, its own origin.

Why "right" needs the trapped destination and the others do not: one harness
pass is four video frames, and that window does not begin at the game's main
loop top, so at the end of it the player's coordinate in RAM is one 2-unit step
ahead of the position the pass actually drew.  Holding right with the camera
still, that is exactly 8 pixels -- which is where the phantom "-8 draw origin"
in the old notes came from.  Holding down the same lag lands on y, and once the
camera starts tracking it vanishes entirely, because then (coord - cam) is
constant.  Sample at the blit and the offset is zero.

Usage:  python tools/playercompare.py [--char 3] [--passes 8]
Writes build/player_vs_original.png.
"""
import os
import pickle
import re
import subprocess
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, SP, H, L, TAPE_CALL_PC   # noqa: E402
from keyprobe import KEYS, keymask                                # noqa: E402
import fixchar                                                    # noqa: E402
import playersprite as ps                                         # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
FRAME_T = 69888
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')


def one_pass(h, box):
    """Advance one pass, remembering the LAST player blit's destination."""
    target = h.regs[T] + 4 * FRAME_T
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    while regs[T] < target:
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        if pc == ps.BLIT16 and 0x5F00 <= regs[SP] < 0x6320:
            box[:] = [regs[H] << 8 | regs[L], regs[SP]]
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)


def main():
    args = sys.argv[1:]
    char, passes = 3, 8
    while args:
        if args[0] == '--char':
            char = int(args[1]); del args[:2]
        elif args[0] == '--passes':
            passes = int(args[1]); del args[:2]
        else:
            del args[:1]

    rows, worst = [], 0
    for d in ('idle', 'up', 'down', 'left', 'right'):
        h = Harness()
        h.load_state(pickle.load(open(STATE, 'rb')))
        fixchar.fix(h.memobj.m, char, 0)
        if d != 'idle':
            sel, bit = KM[DIRKEY[d]]
            h.ports.press(sel, keymask(bit))
        box = [0, 0]
        for _ in range(passes):
            one_pass(h, box)
        ox, oy = ps.unscr(box[0])
        rec = (box[1] - 1 - 0x5F00) // ps.REC

        dump = os.path.join(ROOT, 'build', f'_cmp_{d}.bin')
        open(dump, 'wb').write(bytes(h.memobj.m))
        png = os.path.join(ROOT, 'build', f'_cmp_{d}.png')
        subprocess.run([sys.executable, os.path.join(HERE, 'screen.py'),
                        dump, png], check=True, capture_output=True)
        orig = Image.open(png).convert('RGB').resize((256, 192), Image.NEAREST)

        ppm = os.path.join(ROOT, 'build', f'_eng_{d}.ppm')
        r = subprocess.run(['node', os.path.join(HERE, 'render_shot.js'),
                            ppm, d, str(passes)],
                           check=True, capture_output=True, text=True)
        eng = Image.open(ppm).convert('RGB')
        gx, gy, gcx, gcy = map(int, re.search(
            r'player at \((\d+),(\d+)\) cam \((\d+),(\d+)\)', r.stdout).groups())
        ex = (((gx - gcx) & 0x7E) >> 1) * 8
        ey = (((gy - gcy) & 0x7E) >> 1) * 8

        a = orig.crop((ox, oy, ox + 16, oy + 16))
        b = eng.crop((ex, ey, ex + 16, ey + 16))
        diff = sum(1 for p, q in zip(a.get_flattened_data(),
                                     b.get_flattened_data()) if p != q)
        worst = max(worst, diff)
        print(f'{d:>6}: record {rec:2d}  original blitted to ({ox},{oy}), '
              f'port drew at ({ex},{ey})  ->  {diff} differing pixels of 256')
        rows.append((f'{d} rec{rec}', a, b, diff))

    Z = 6
    im = Image.new('RGB', (2 * 16 * Z + 100, len(rows) * (16 * Z + 8) + 14),
                   (16, 16, 22))
    dr = ImageDraw.Draw(im)
    dr.text((100, 3), 'ORIGINAL', fill=(200, 220, 255))
    dr.text((100 + 16 * Z + 6, 3), 'PORT', fill=(200, 220, 255))
    for i, (name, a, b, diff) in enumerate(rows):
        y = 14 + i * (16 * Z + 8)
        dr.text((4, y + 16 * Z // 2 - 8), name, fill=(200, 220, 255))
        dr.text((4, y + 16 * Z // 2 + 2), f'{diff} px diff',
                fill=(140, 240, 140) if diff == 0 else (250, 140, 140))
        im.paste(a.resize((16 * Z, 16 * Z), Image.NEAREST), (100, y))
        im.paste(b.resize((16 * Z, 16 * Z), Image.NEAREST),
                 (100 + 16 * Z + 6, y))
    out = os.path.join(ROOT, 'build', 'player_vs_original.png')
    im.save(out)
    print(f'\nwrote {out}   worst row: {worst} differing pixels of 256')
    if worst:
        sys.exit('COMPARISON FAILED')


if __name__ == '__main__':
    main()
