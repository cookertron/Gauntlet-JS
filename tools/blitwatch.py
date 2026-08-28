#!/usr/bin/env python3
"""
blitwatch.py -- reconstruct the player's sprite from the blitter's OWN WRITES.

Rather than guessing the sprite geometry from the graphics bank, this steps the
real Z80 one instruction at a time with harness.Mem.watch() over the SHADOW
screen ($C000-$D7FF bitmap, $D800-$DAFF attributes), groups the writes into
individual draws by the blitter at $9DD2..$9EA0, converts every destination back
to (x,y) with the inverse display-file formula, and rebuilds the bitmap from the
WRITE VALUES -- never from memory afterwards.

It also snapshots SP/HL/BC at each blitter entry, so the written byte sequence
can be compared against the source bytes and the source order/stride settled.

NOTE: SkoolKit's Simulator binds self.memory into its opcode partials at
construction, so wrapping/replacing sim.memory after the fact logs almost
nothing.  Mem.watch() is the only correct hook.

Usage:
    python tools/blitwatch.py [--dir right|left|up|down|idle] [--passes 8]
                              [--png build/blit_idle.png] [--dump out.json]
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, H, L, B, C,            # noqa: E402
                     IXh, IXl, TAPE_CALL_PC)
from keyprobe import KEYS, keymask                                    # noqa: E402
from filmstrip import run_frames                                      # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
P_X, P_Y, P_DIR = 0x8420, 0x8421, 0x8427
CAM_X, CAM_Y = 0x848B, 0x848C
FRAME_T = 69888
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}

# every blitter variant, from the disassembly:
#   $9DD2  16-wide: 2x2 attribute block then 16 rows x 2 bytes, JP (IX)
#   $9E4B   8-wide: 1x2 attribute block then 16 rows x 1 byte
BLIT_LO, BLIT_HI = 0x9DD2, 0x9EA0
ENTRIES = (0x9DD2, 0x9DEC, 0x9E22, 0x9E4B, 0x9E61, 0x9E88)
HEADS = (0x9DDD, 0x9E56)               # the first attribute write of each draw
SHADOW_BMP, SHADOW_ATTR = 0xC000, 0xD800


def bmp_xy(addr):
    """Inverse Spectrum display-file address -> (x_byte_column, y_pixel_row)."""
    o = (addr - SHADOW_BMP) & 0x1FFF
    y = ((o & 0x1800) >> 5) | ((o & 0x0700) >> 8) | ((o & 0x00E0) >> 2)
    return (o & 0x1F), y


def attr_rc(addr):
    o = addr - SHADOW_ATTR
    return (o // 32), (o % 32)


def run_pass(h, entries, n=4):
    """One main-loop pass (4 video frames -- battery Q10), single-stepped so
    the blitter entry registers can be snapshotted."""
    target = h.regs[T] + n * FRAME_T
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    log = h.memobj.log
    fd, ia = h.frame_duration, h.int_active
    while regs[T] < target:
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); continue
        if pc in ENTRIES:
            entries.append((len(log), pc, regs[SP], regs[L] + 256 * regs[H],
                            regs[C] + 256 * regs[B],
                            regs[IXl] + 256 * regs[IXh]))
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)


def group_draws(log):
    """A draw begins at a HEAD (the first attribute write) and runs until the
    next head or the first write from outside the blitter."""
    draws = []
    cur = None
    for i, (pc, addr, val) in enumerate(log):
        if not (BLIT_LO <= pc < BLIT_HI):
            cur = None
            continue
        if pc in HEADS:
            cur = []
            draws.append(cur)
        if cur is None:
            continue
        cur.append((i, pc, addr, val))
    return [d for d in draws if any(w[2] < SHADOW_ATTR for w in d)]


def describe(draw):
    bmp = [w for w in draw if w[2] < SHADOW_ATTR]
    att = [w for w in draw if w[2] >= SHADOW_ATTR]
    cols = sorted({bmp_xy(w[2])[0] for w in bmp})
    rows = sorted({bmp_xy(w[2])[1] for w in bmp})
    return {
        'i0': draw[0][0],
        'n_bmp': len(bmp), 'n_attr': len(att),
        'x0': cols[0] * 8, 'x1': cols[-1] * 8 + 7,
        'y0': rows[0], 'y1': rows[-1],
        'w_bytes': len(cols), 'h_rows': len(rows),
        'first_pc': bmp[0][1],
        'bmp': bmp, 'attr': att,
    }


def sprite_from_writes(d):
    """Build the bitmap from the WRITE VALUES.  Later writes win (they don't
    overlap in practice; assert if they do)."""
    cols = sorted({bmp_xy(w[2])[0] for w in d['bmp']})
    rows = sorted({bmp_xy(w[2])[1] for w in d['bmp']})
    ci = {c: i for i, c in enumerate(cols)}
    ri = {r: i for i, r in enumerate(rows)}
    grid = [[None] * len(cols) for _ in rows]
    for _, pc, addr, val in d['bmp']:
        cx, cy = bmp_xy(addr)
        grid[ri[cy]][ci[cx]] = val
    return grid, cols, rows


def render_grid(grid, scale=6, ink=(255, 214, 0), paper=(20, 20, 28)):
    from PIL import Image
    hgt = len(grid)
    wid = len(grid[0]) * 8
    im = Image.new('RGB', (wid * scale, hgt * scale), (60, 0, 0))
    px = im.load()
    for r, row in enumerate(grid):
        for c, v in enumerate(row):
            if v is None:
                continue
            for b in range(8):
                col = ink if (v & (0x80 >> b)) else paper
                for sy in range(scale):
                    for sx in range(scale):
                        px[(c * 8 + b) * scale + sx, r * scale + sy] = col
    return im


def main():
    args = sys.argv[1:]
    direction, passes, png, dump, warm = 'idle', 8, None, None, 32
    while args:
        if args[0] == '--dir':
            direction = args[1]; del args[:2]
        elif args[0] == '--passes':
            passes = int(args[1]); del args[:2]
        elif args[0] == '--png':
            png = args[1]; del args[:2]
        elif args[0] == '--dump':
            dump = args[1]; del args[:2]
        elif args[0] == '--warm':
            warm = int(args[1]); del args[:2]
        elif args[0] == '--state':
            globals()['STATE'] = args[1]; del args[:2]
        else:
            del args[:1]

    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    run_frames(h, warm)

    results = []
    for p in range(passes):
        h.memobj.watch(0xC000, 0xDB00)
        entries = []
        run_pass(h, entries, 4)
        h.memobj.unwatch()
        log = h.memobj.log
        m = h.memobj.m
        px_, py_, cx, cy = m[P_X], m[P_Y], m[CAM_X], m[CAM_Y]
        # MEASURED: the shadow-screen draw origin is exactly (x-cam_x)*4,
        # (y-cam_y)*4 -- no offset on either axis.  (The earlier "-8 on x"
        # came from matching against the wrong draw.)
        wx, wy = (px_ - cx) * 4, (py_ - cy) * 4
        draws = [describe(d) for d in group_draws(log)]
        hit = None
        for d in draws:
            if d['x0'] == wx and d['y0'] == wy:
                hit = d
        print(f"--- pass {p}: player=({px_},{py_}) dir={m[P_DIR]} cam=({cx},{cy})"
              f"  expect ({wx},{wy})   {len(draws)} draws / {len(log)} writes")
        for d in draws:
            mark = ' <== PLAYER' if d is hit else ''
            print(f"    x{d['x0']:4d}..{d['x1']:3d}  y{d['y0']:4d}..{d['y1']:3d}"
                  f"  {d['w_bytes']}bytes x {d['h_rows']}rows  bmp={d['n_bmp']:3d}"
                  f" attr={d['n_attr']:2d}  head=${d['bmp'][0][1]:04X}{mark}")
        if hit is not None:
            report(hit, entries, m, p, png, results, direction)
    if dump:
        json.dump(results, open(dump, 'w'), indent=1)
        print(f'wrote {dump}')


def report(d, entries, m, p, png, results, direction):
    grid, cols, rows = sprite_from_writes(d)
    # order of the writes
    seq = [(bmp_xy(w[2])[1], bmp_xy(w[2])[0]) for w in d['bmp']]
    rows_order = []
    for y, x in seq:
        if not rows_order or rows_order[-1] != y:
            rows_order.append(y)
    cols_first = seq[0][1], seq[1][1] if len(seq) > 1 else None
    # SP at the first POP of this draw
    idx = d['bmp'][0][0]
    ent = [e for e in entries if e[0] <= idx]
    firstpop = [e for e in ent if e[1] in (0x9DEC, 0x9E61)]
    sp = firstpop[-1][2] if firstpop else None
    hl = [e for e in ent if e[1] in (0x9DD2, 0x9E4B)]
    print(f"    -> {d['n_bmp']} bitmap bytes, {d['n_attr']} attribute bytes")
    print(f"    -> bounding box x {d['x0']}..{d['x1']} ({d['w_bytes']} bytes wide),"
          f" y {d['y0']}..{d['y1']} ({d['h_rows']} pixel rows)")
    print(f"    -> row order: {rows_order[:6]} ... {rows_order[-3:]}   "
          f"({'TOP-DOWN' if rows_order[1] > rows_order[0] else 'BOTTOM-UP'})")
    print(f"    -> within a row, first two columns written: {cols_first}")
    if hl:
        print(f"    -> entry HL=${hl[-1][3]:04X}  BC=${hl[-1][4]:04X}  IX=${hl[-1][5]:04X}")
    if sp is not None:
        src = bytes(m[sp:sp + d['n_bmp']])
        written = bytes(w[3] for w in d['bmp'])
        print(f"    -> SP at first POP = ${sp:04X}")
        print(f"       source  {' '.join(f'{b:02X}' for b in src[:16])}")
        print(f"       written {' '.join(f'{b:02X}' for b in written[:16])}")
        print(f"       write-ORDER equals source order: {src == written}"
              f"   (odd rows write D then E -- see $9DF1)")
        flat = bytes(v for row in grid for v in row)
        print(f"       POSITIONAL (row-major, left byte first) == source: "
              f"{flat == src}")
        print(f"       record start = SP-1 = ${sp - 1:04X}, "
              f"attribute byte there = ${m[sp - 1]:02X} "
              f"(attribute actually written = ${d['attr'][0][3]:02X})")
    if d['attr']:
        print("    -> attrs: " + ' '.join(
            f'${w[2]:04X}(r{attr_rc(w[2])[0]},c{attr_rc(w[2])[1]})={w[3]:02X}'
            for w in d['attr']))
    results.append({
        'pass': p, 'dir': direction, 'x0': d['x0'], 'y0': d['y0'],
        'w_bytes': d['w_bytes'], 'h_rows': d['h_rows'], 'n_bmp': d['n_bmp'],
        'sp': sp, 'rows_order': rows_order,
        'bytes': [w[3] for w in d['bmp']],
        'grid': [[(-1 if v is None else v) for v in r] for r in grid],
        'attrs': [[w[2], w[3]] for w in d['attr']],
    })
    if png:
        im = render_grid(grid)
        out = png if png.endswith('.png') else png + '.png'
        out = out.replace('.png', f'_{p}.png')
        im.save(out)
        print(f'    -> wrote {out}')


if __name__ == '__main__':
    main()
