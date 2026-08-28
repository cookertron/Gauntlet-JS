#!/usr/bin/env python3
"""
bannerart.py -- the MESSAGE BANNER's panel art and its strings.

$8A28 paints the panel and $8964/$898B/$894B pick the text.  Rather than
transcribe the border plotter -- which is a two-loop affair writing into the
shadow attribute file and then a frame -- the border is EXTRACTED, the same
way every other piece of this game's artwork is: the original is made to draw
it and the result is read back off the screen.

    python tools/bannerart.py            write build/banner.json
    python tools/bannerart.py show       ...and print what it found

WHAT IS EXTRACTED

  geometry   $8A28 fills $D8C7 for 18 bytes x 7 rows, stride $20.  $D8C7 is
             offset $C7 in the attribute file, so the panel is at CHARACTER
             ROW 6, COLUMN 7 and is 18 x 7 characters.  Attribute $57.

  border     the cells the text never occupies: row 0, row 6, and columns 0
             and 17 of rows 1..5.  MEASURED IDENTICAL across all eight
             message codes, which is what says it is a frame and not part of
             a message.

  strings    decoded from the rendered panels against the HUD FONT, which the
             banner shares -- the glyphs are byte-identical, and that is the
             measurement that let this be ported at all.  The first line is
             the CHARACTER NAME of the player the message is about and is not
             stored here; the engine draws it from the same place the HUD
             does.

  The $40 (treasure pay-out) line carries a NUMBER that depends on the hoard,
  so its text is stored with the digits blanked and the engine fills them in.
"""
import base64
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, IFF, TAPE_CALL_PC          # noqa: E402
from sim_move import LOOP_TOP                               # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
ROW0, COL0, COLS, ROWS = 6, 7, 18, 7
ATTR = 0x57
CODES = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x81]


def scr(x, y, base=0x4000):
    return base | ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2) | (x >> 3)


def one_pass(h):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < 20_000_000:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        n += 1
        if regs[IFF] and regs[25] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    raise RuntimeError('no main-loop top')


def render(code):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)
    m[0x84B9] = code
    one_pass(h)
    return [[m[scr(c * 8, ROW0 * 8 + y)] for c in range(COL0, COL0 + COLS)]
            for y in range(ROWS * 8)]


def cell(px, cr, cc):
    return [px[cr * 8 + y][cc] for y in range(8)]


def main():
    panels = {c: render(c) for c in CODES}

    # --- the FONT, read back out of the built asset so the decode uses the
    #     same glyphs the engine will draw with ---------------------------
    hud = json.load(open(os.path.join(ROOT, 'build', 'hud_font.json'),
                         encoding='utf-8'))
    glyphs = {int(k): list(base64.b64decode(v))
              for k, v in hud['font']['glyphs'].items()}
    by_bits = {tuple(v): k for k, v in glyphs.items()}

    # --- the BORDER: every cell no message ever writes into ---------------
    frame = {}
    for cr in range(ROWS):
        for cc in range(COLS):
            if not (cr in (0, ROWS - 1) or cc in (0, COLS - 1)):
                continue
            seen = {tuple(cell(panels[c], cr, cc)) for c in CODES}
            assert len(seen) == 1, \
                'border cell (%d,%d) is not constant across the codes' % (cr, cc)
            frame['%d,%d' % (cr, cc)] = base64.b64encode(
                bytes(cell(panels[CODES[0]], cr, cc))).decode()

    # --- the STRINGS, decoded against that font ---------------------------
    texts = {}
    for c in CODES:
        lines = []
        for cr in range(1, ROWS - 1):
            s = ''
            for cc in range(1, COLS - 1):
                b = tuple(cell(panels[c], cr, cc))
                # BLANK FIRST.  This font has SEVERAL all-zero glyphs (32 and
                # 64 among them), so testing the table first decodes every
                # space as whichever of them the dict happened to keep --
                # the first run of this tool reported ELF padded with '@'.
                if not any(b):
                    s += ' '
                elif b in by_bits:
                    s += chr(by_bits[b])
                else:
                    s += chr(0)      # a glyph the HUD font does not have
            lines.append(s.rstrip())
        texts['%02X' % c] = lines

    unknown = sum(l.count('\x00') for v in texts.values() for l in v)
    doc = {'row': ROW0, 'col': COL0, 'cols': COLS, 'rows': ROWS,
           'attr': ATTR, 'frame': frame, 'texts': texts,
           '_note': 'strings decoded against the HUD font; the banner shares it'}
    path = os.path.join(ROOT, 'build', 'banner.json')
    json.dump(doc, open(path, 'w'))
    for c in CODES:
        print('$%02X  %s' % (c, ' | '.join(repr(l) for l in texts['%02X' % c]
                                           if l.strip())))
    print('\n%d border cells, %d glyphs the HUD font could not name'
          % (len(frame), unknown))
    print('wrote', path)


if __name__ == '__main__':
    main()
