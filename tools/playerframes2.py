#!/usr/bin/env python3
"""
playerframes2.py -- the player's animation frames, rebuilt from the BLITTER'S
OWN WRITES (never read back out of memory, never decoded by guesswork).

For each direction it single-steps the real Z80, traps the player's own draw
(the call site at $A243, identified by its return continuation IX=$A246),
records every (address,value) the 16x16 blitter writes into the shadow screen,
inverts the display-file address to (x,y) and rebuilds the bitmap.

It also prints, per frame:
  * the sprite-record address (SP-1 at the first POP)
  * the record index within the character's 32-record set  (33 bytes each)
  * pose = index % 8, phase = index // 8
  * the attribute byte, both as stored at record+0 and as actually written

Usage:  python tools/playerframes2.py [--state build/state_elf.pkl]
                                      [--passes 24] [--out build/player_from_writes.png]
"""
import os
import pickle
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, H, L, B, C,            # noqa: E402
                     IXh, IXl, TAPE_CALL_PC)
from keyprobe import KEYS, keymask                                    # noqa: E402
from filmstrip import run_frames                                      # noqa: E402
from spritebank import PAL_DIM, PAL_BRIGHT                            # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
FRAME_T = 69888
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
BANK = 0x5F00
REC = 33
PLAYER_RET = 0xA246          # IX at the player/actor draw call site $A243


def bmp_xy(addr):
    o = (addr - 0xC000) & 0x1FFF
    return (o & 0x1F), (((o & 0x1800) >> 5) | ((o & 0x0700) >> 8)
                        | ((o & 0x00E0) >> 2))


def capture(state, direction, passes):
    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    if direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    run_frames(h, 32)
    regs, ops, mem, sim = h.regs, h.sim.opcodes, h.sim.memory, h.sim
    m = h.memobj.m
    fd, ia = h.frame_duration, h.int_active
    out = {}
    order = []
    for _ in range(passes):
        h.memobj.watch(0xC000, 0xDB00)
        log = h.memobj.log
        marks = []
        target = regs[T] + 4 * FRAME_T
        while regs[T] < target:
            pc = regs[PC]
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape(); continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt(); continue
            if pc == 0x9DD2 and (regs[IXl] + 256 * regs[IXh]) == PLAYER_RET:
                # the player's OWN draw: destination must be his measured
                # screen position, (x-cam_x)*4 , (y-cam_y)*4
                sx, sy = (m[0x8420] - m[0x848B]) * 4, (m[0x8421] - m[0x848C]) * 4
                want = (0xC000 | ((sy & 0xC0) << 5) | ((sy & 7) << 8)
                        | ((sy & 0x38) << 2) | (sx >> 3))
                if (regs[L] + 256 * regs[H]) == want:
                    marks.append([len(log), None, None])
            elif pc == 0x9DEC and marks and marks[-1][1] is None:
                marks[-1][1] = regs[SP]
            elif pc == 0x9E49 and marks and marks[-1][2] is None:
                marks[-1][2] = len(log)
            ops[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
        h.memobj.unwatch()
        for k, (i0, sp, i1) in enumerate(marks):
            if sp is None or i1 is None:
                continue
            chunk = log[i0:i1]   # $9E49 JP (IX) is after the last write
            bmp = [w for w in chunk if w[1] < 0xD800]
            att = [w for w in chunk if w[1] >= 0xD800]
            if len(bmp) != 32:
                continue
            grid = [[0] * 2 for _ in range(16)]
            cols = sorted({bmp_xy(w[1])[0] for w in bmp})
            rows = sorted({bmp_xy(w[1])[1] for w in bmp})
            for _pc, a, v in bmp:
                cx, cy = bmp_xy(a)
                grid[rows.index(cy)][cols.index(cx)] = v
            data = bytes(v for r in grid for v in r)
            rec = sp - 1
            key = (data, rec)
            if key not in out:
                out[key] = {'n': 0, 'rec': rec, 'attr_written': att[0][2],
                            'attr_stored': m[rec], 'data': data,
                            'idx': (rec - BANK) // REC}
                order.append(key)
            out[key]['n'] += 1
    return [out[k] for k in order]


def draw(fr, scale=4):
    at = fr['attr_written']
    pal = PAL_BRIGHT if (at & 0x40) else PAL_DIM
    ink, paper = pal[at & 7], pal[(at >> 3) & 7]
    im = Image.new('RGB', (16 * scale, 16 * scale), paper)
    px = im.load()
    for r in range(16):
        for c in range(2):
            v = fr['data'][r * 2 + c]
            for b in range(8):
                if v & (0x80 >> b):
                    for sy in range(scale):
                        for sx in range(scale):
                            px[(c * 8 + b) * scale + sx, r * scale + sy] = ink
    return im


def main():
    args = sys.argv[1:]
    state = os.path.join(ROOT, 'build', 'state_elf.pkl')
    passes = 24
    out = os.path.join(ROOT, 'build', 'player_from_writes.png')
    while args:
        if args[0] == '--state':
            state = args[1]; del args[:2]
        elif args[0] == '--passes':
            passes = int(args[1]); del args[:2]
        elif args[0] == '--out':
            out = args[1]; del args[:2]
        else:
            del args[:1]

    rows = []
    for direction in ('idle', 'up', 'down', 'left', 'right'):
        frames = capture(state, direction, passes)
        print(f'{direction:>6}: {len(frames)} distinct frames over {passes} passes')
        for f in frames:
            print(f"        rec ${f['rec']:04X}  idx {f['idx']:2d} "
                  f"(pose {f['idx'] % 8}, phase {f['idx'] // 8})  "
                  f"attr stored ${f['attr_stored']:02X} written ${f['attr_written']:02X}"
                  f"  x{f['n']}")
        rows.append((direction, frames))

    scale = 4
    cw, chh = 16 * scale + 8, 16 * scale + 24
    ncols = max(len(f) for _, f in rows)
    im = Image.new('RGB', (70 + ncols * cw, len(rows) * chh + 6), (12, 12, 18))
    d = ImageDraw.Draw(im)
    for i, (name, frames) in enumerate(rows):
        d.text((6, 6 + i * chh + 24), name, fill=(230, 230, 230))
        for j, f in enumerate(frames):
            x, y = 70 + j * cw, 6 + i * chh
            im.paste(draw(f, scale), (x, y))
            d.text((x, y + 16 * scale + 1), f"{f['rec']:04X}", fill=(140, 180, 230))
            d.text((x, y + 16 * scale + 11), f"i{f['idx']} p{f['idx'] % 8}",
                   fill=(120, 150, 120))
    im.save(out)
    print(f'wrote {out} ({im.width}x{im.height})')


if __name__ == '__main__':
    main()
