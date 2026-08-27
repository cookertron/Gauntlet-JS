#!/usr/bin/env python3
"""
spritegrab.py -- capture the player sprite VERBATIM out of the shadow screen at
the instant the blitter finishes drawing it.

Why this works and the earlier captures did not.  The 16x16 blitter at $9DD2
writes with LD (HL),E -- OPAQUE, no OR and no mask -- so between the last write
of the sprite and the moment the background is restored underneath it, the
shadow bitmap at the destination IS the sprite's own bytes.  Earlier captures
sampled at the end of a main-loop pass, by which time the restore had already
happened, and sampled (real AND NOT shadow) at a position derived from the
player's coordinate rather than from the blitter's own HL.

This tool breaks on the blitter's ENTRY (PC == $9DD2, recording HL/SP/C), runs
on to its EXIT (PC == $9E49, the JP (IX)) and grabs the shadow bitmap and the
shadow attributes right there.  The grab is 32x32 pixels centred on the 16x16
destination, so a sprite that is taller or wider than 16x16 is visible rather
than cropped.

Usage:  python tools/spritegrab.py [--passes 40]
"""
import base64
import json
import os
import pickle
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, H as RH, L as RL, C as RC,
                     IXh, IXl, TAPE_CALL_PC)                      # noqa: E402
from keyprobe import KEYS, keymask                                # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
BUILD = os.path.join(ROOT, 'build')
P_X, P_Y, P_DIR, CAM_X, CAM_Y = 0x8420, 0x8421, 0x8427, 0x848B, 0x848C
SHADOW, SHADOW_ATTR = 0xC000, 0xD800
REAL, REAL_ATTR = 0x4000, 0x5800
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
FRAME_T = 69888

BLIT16_IN, BLIT16_OUT = 0x9DD2, 0x9E49
BANK_LO, BANK_HI = 0x5F00, 0x6F80          # the LDIR'd graphics bank

PAL_DIM = [(0, 0, 0), (0, 0, 0xD7), (0xD7, 0, 0), (0xD7, 0, 0xD7),
           (0, 0xD7, 0), (0, 0xD7, 0xD7), (0xD7, 0xD7, 0), (0xD7, 0xD7, 0xD7)]
PAL_BRIGHT = [(0, 0, 0), (0, 0, 0xFF), (0xFF, 0, 0), (0xFF, 0, 0xFF),
              (0, 0xFF, 0), (0, 0xFF, 0xFF), (0xFF, 0xFF, 0), (0xFF, 0xFF, 0xFF)]

MARGIN = 8                                  # pixels of context on every side
GW, GH = 16 + 2 * MARGIN, 16 + 2 * MARGIN   # 32 x 32 grab


def scr(base, x, y):
    return base | ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2) | (x >> 3)


def scr_xy(addr):
    o = addr & 0x1FFF
    y = (((o >> 11) & 3) << 6) | (((o >> 5) & 7) << 3) | ((o >> 8) & 7)
    return (o & 31) * 8, y


def grab_block(m, base, x0, y0, w=GW, h=GH):
    """w x h pixels as h rows of w/8 bytes, or None if it leaves the screen."""
    if x0 < 0 or y0 < 0 or x0 + w > 256 or y0 + h > 192:
        return None
    return bytes(m[scr(base, x0 + b * 8, y0 + r)]
                 for r in range(h) for b in range(w // 8))


def grab_attrs(m, base, x0, y0, w=GW, h=GH):
    if x0 < 0 or y0 < 0 or x0 + w > 256 or y0 + h > 192:
        return None
    return bytes(m[base + ((y0 // 8) + r) * 32 + (x0 // 8) + c]
                 for r in range(h // 8) for c in range(w // 8))


def render(bm, attrs, w=GW, h=GH, scale=6, box=None):
    """Render a grab with its real attribute colours; `box` outlines the
    blitter's own 16x16 destination inside the wider grab."""
    im = Image.new('RGB', (w * scale, h * scale))
    px = im.load()
    bw = w // 8
    for r in range(h):
        for b in range(bw):
            v = bm[r * bw + b]
            at = attrs[(r // 8) * bw + b]
            pal = PAL_BRIGHT if (at & 0x40) else PAL_DIM
            ink, paper = pal[at & 7], pal[(at >> 3) & 7]
            for k in range(8):
                col = ink if (v & (0x80 >> k)) else paper
                x = b * 8 + k
                for sy in range(scale):
                    for sx in range(scale):
                        px[x * scale + sx, r * scale + sy] = col
    if box:
        bx, by, bw2, bh2 = box
        for x in range(bx, bx + bw2):
            for e, y in ((0, by), (0, by + bh2 - 1)):
                px[x * scale, y * scale] = (255, 0, 255)
        for y in range(by, by + bh2):
            for e, x in ((0, bx), (0, bx + bw2 - 1)):
                px[x * scale, y * scale] = (255, 0, 255)
    return im


def inner16(bm, w=GW):
    """The blitter's own 16x16 destination out of the wider grab: 32 bytes,
    row-major, 2 bytes per row -- directly comparable with the source bytes."""
    bw = w // 8
    return bytes(bm[(MARGIN + r) * bw + (MARGIN // 8) + b]
                 for r in range(16) for b in range(2))


def bbox(bm, w=GW, h=GH):
    bw = w // 8
    xs, ys = [], []
    for r in range(h):
        for b in range(bw):
            v = bm[r * bw + b]
            for k in range(8):
                if v & (0x80 >> k):
                    xs.append(b * 8 + k)
                    ys.append(r)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# The character-set repair.  Read from the boot code in build/image.bin:
#
#   $BE17  LD HL,$C000 / LD DE,$5F00 / LD BC,$1080 / LDIR   ; 128 records, the
#          FOUR characters' sprite sets, $420 (32 records) each
#   $BE53  LD A,($FFFF) / LD DE,$C000 / CALL $BEE5          ; player 1's choice
#   $BE64  LD A,($FFFE) / LD DE,$C420 / CALL $BEE5          ; player 2's choice
#   $BE74  LD DE,$5F00 / POP HL / LD BC,$0840 / LDIR        ; both sets back
#          over the head of the bank
#   $BEE5  LD E,A / SUB A / LD HL,$5F00 / LD IX,$BF19 / LD BC,$420
#   $BEF2  CP E / JR z,$BEFD / INC A / ADD HL,BC / INC IX / INC IX / JR $BEF2
#   $BEFD  POP DE / LDIR / LD A,(IX) / LD C,(IX+1) / RET
#
# so $FFFE/$FFFF hold the two players' character indices, 0..3.  In the harness
# they are never set -- they are still $2A, left over from the BASIC block that
# the $FF00 stub was cut out of -- so $BEE5 adds $420 forty-two times and the
# source wraps to $5F00 + 42*$420 = $10C40 -> $0C40, IN THE ROM.  Every player
# sprite the game then draws is 32 bytes of 48K ROM.  That is the whole of the
# "speckled blob".
CHAR_BASE = 0x5F00
CHAR_SET = 0x420                      # 32 records of 33 bytes
P2_BASE = 0x6320                      # $5F00 + $420
STATS = {0: (0x00, 0x8E), 1: (0x08, 0xD8), 2: (0x10, 0x32), 3: (0x18, 0x64)}
PRISTINE = None                       # the un-clobbered 128-record bank


def pristine_bank():
    """The bank as $BE20's LDIR leaves it, before $BE7B overwrites its head."""
    global PRISTINE
    if PRISTINE is None:
        PRISTINE = bytes(Harness().memobj.m[0xC000:0xC000 + 0x1080])
    return PRISTINE


def repair(h, c1=3, c2=3, stats=True):
    """Redo the $BEE5/$BE7B copy with a VALID character index."""
    b = pristine_bank()
    m = h.memobj.m
    m[CHAR_BASE:CHAR_BASE + CHAR_SET] = b[c1 * CHAR_SET:(c1 + 1) * CHAR_SET]
    m[P2_BASE:P2_BASE + CHAR_SET] = b[c2 * CHAR_SET:(c2 + 1) * CHAR_SET]
    if stats:
        m[0x8433], m[0x8435] = STATS[c1]
        m[0x8453], m[0x8455] = STATS[c2]
    m[0xFFFF], m[0xFFFE] = c1, c2


def capture(direction, passes, char=None, verbose=False):
    """Run `passes` main-loop passes; on every player blit, grab the shadow
    screen at the blitter's exit.  Returns a list of records."""
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if char is not None:
        repair(h, char, char)
    if direction:
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))

    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    m = h.memobj.m
    fd, ia = h.frame_duration, h.int_active
    target = regs[T] + passes * 4 * FRAME_T

    out = []
    pending = None
    while regs[T] < target:
        pc = regs[PC]
        if pc == BLIT16_IN:
            hl = regs[RL] + 256 * regs[RH]
            src = regs[SP]
            if BANK_LO <= src < BANK_HI and (hl & 0xE000) == 0xC000:
                dx, dy = scr_xy(hl)
                pending = dict(dst=hl, dx=dx, dy=dy, src=src, attr=regs[RC],
                               px=m[P_X], py=m[P_Y], cx=m[CAM_X], cy=m[CAM_Y],
                               dir=m[P_DIR],
                               before=grab_block(m, SHADOW, dx - MARGIN, dy - MARGIN))
        elif pc == BLIT16_OUT and pending is not None:
            dx, dy = pending['dx'], pending['dy']
            pending['after'] = grab_block(m, SHADOW, dx - MARGIN, dy - MARGIN)
            pending['sattr'] = grab_attrs(m, SHADOW_ATTR, dx - MARGIN, dy - MARGIN)
            pending['rattr'] = grab_attrs(m, REAL_ATTR, dx - MARGIN, dy - MARGIN)
            pending['real'] = grab_block(m, REAL, dx - MARGIN, dy - MARGIN)
            pending['srcbytes'] = bytes(m[pending['src']:pending['src'] + 32])
            pending['recattr'] = m[pending['src'] - 1]
            pending['sp_end'] = regs[SP]
            pending['ix'] = regs[IXl] + 256 * regs[IXh]
            out.append(pending)
            pending = None
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    return out


def main():
    passes = 40
    char = None
    tag = ''
    if '--passes' in sys.argv:
        passes = int(sys.argv[sys.argv.index('--passes') + 1])
    if '--char' in sys.argv:
        char = int(sys.argv[sys.argv.index('--char') + 1])
        tag = f'c{char}_'

    summary = {}
    for direction in (None, 'up', 'down', 'left', 'right'):
        name = tag + (direction or 'idle')
        recs = capture(direction, passes, char=char)
        # de-duplicate on the drawn bitmap
        uniq = {}
        for r in recs:
            if r['after'] is None:
                continue
            k = r['after']
            u = uniq.setdefault(k, {'n': 0, 'rec': r, 'srcs': {}, 'attrs': {}})
            u['n'] += 1
            u['srcs'][r['src']] = u['srcs'].get(r['src'], 0) + 1
            u['attrs'][r['attr']] = u['attrs'].get(r['attr'], 0) + 1
        order = sorted(uniq.values(), key=lambda u: -u['n'])
        print(f'\n=== {name}: {len(recs)} player blits over {passes} passes, '
              f'{len(order)} distinct 32x32 grabs')
        ims = []
        for i, u in enumerate(order):
            r = u['rec']
            bb = bbox(u['rec']['after'])
            srcs = ' '.join(f'${s:04X}x{c}' for s, c in sorted(u['srcs'].items()))
            at = r['sattr']
            sameas_src = (u['rec']['after'] is not None)
            print(f'  frame {i}: n={u["n"]:3}  src={srcs}  attrC=' +
                  ' '.join(f'${a:02X}' for a in u['attrs']) +
                  f'  bbox(in 32x32)={bb}  dst=({r["dx"]},{r["dy"]}) '
                  f'player=({r["px"]},{r["py"]}) cam=({r["cx"]},{r["cy"]}) '
                  f'dir={r["dir"]} SPend=${r["sp_end"]:04X} IX=${r["ix"]:04X}')
            print(f'           shadow attrs 4x4 = ' +
                  ' '.join(f'{at[q*4:q*4+4].hex()}' for q in range(4)) +
                  f'   real attrs = ' +
                  ' '.join(f'{r["rattr"][q*4:q*4+4].hex()}' for q in range(4)))
            im = render(u['rec']['after'], u['rec']['sattr'],
                        box=(MARGIN, MARGIN, 16, 16))
            im.save(os.path.join(BUILD, f'grab_{name}_{i}.png'))
            ims.append(im)
        if ims:
            cw = ims[0].width + 6
            sheet = Image.new('RGB', (cw * len(ims), ims[0].height + 6), (30, 30, 40))
            for j, im in enumerate(ims):
                sheet.paste(im, (j * cw + 3, 3))
            sheet.save(os.path.join(BUILD, f'grabsheet_{name}.png'))
        summary[name] = [
            {'n': u['n'], 'src': sorted(u['srcs']),
             'attr': sorted(u['attrs']),
             'bbox': bbox(u['rec']['after']),
             'grab32': base64.b64encode(u['rec']['after']).decode(),
             'sprite16': inner16(u['rec']['after']).hex(),
             'srcbytes': u['rec']['srcbytes'].hex()}
            for u in order]
    json.dump(summary, open(os.path.join(BUILD, 'spritegrab.json'), 'w'), indent=1)
    print('\nwrote build/spritegrab.json and build/grab_*.png')


if __name__ == '__main__':
    main()
