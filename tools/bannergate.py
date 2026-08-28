#!/usr/bin/env python3
"""
bannergate.py -- THE MESSAGE BANNER, $891C / $8A28.

$84B9 is the message code and $8543 calls $891C once a pass.  The port already
reproduces the STATE -- which code is raised and the pause it arms -- but not
the DRAWING.  This tool captures what the original actually paints, so the
drawing can be gated on pixels rather than on intention.

    python tools/bannergate.py           capture, write build/_banner.json
    python tools/bannergate.py show      ...and print each panel as ASCII

WHAT WAS MEASURED (all of it by running the thing, not by reading it):

  $8935  LD A,($84B9) / OR A / RET z      code 0 is "no message"
  $893B  CALL $8A28                       the PANEL, on every non-zero code
  $8943  BIT 7,A / JR nz,$898B            bit 7 -> the thief's message
  $8947  AND $3F / JR nz,$8964            any of bits 0..5 -> an item message
         otherwise ($40 alone) -> $894B, the TREASURE PAY-OUT, which also
         calls $BA01 to silence the sound and then $899F twice, once per
         player block ($8420 with DE=$00E0, $8440 with DE=$0840)

  $8A28  fills the SHADOW attribute file at $D8C7 -- 18 bytes x 7 rows,
         stride $20 -- with $57, then draws a border.  $D8C7 is offset $C7 in
         the attribute file, i.e. CHARACTER ROW 6, COLUMN 7, and the panel is
         18 x 7 characters = 144 x 56 pixels.

  The panel reaches the MAIN screen through $9CD7's ordinary blit at $8550,
  so it is captured from $4000/$5800 AFTER the pass, which is what the player
  actually sees.  Reading the shadow instead shows nothing: the playfield
  redraw has already overwritten it by the top of the next pass.

  MEASURED codes: $01, $02, $04, $08, $10, $20 are the six item messages,
  $81 is the thief, $40 is the treasure pay-out.  Every one of them paints,
  and every one paints a DIFFERENT rectangle -- which is what makes this a
  test of the strings and not just of the panel.
"""
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

# $8A28's own numbers, converted once: $D8C7 - $D800 = $C7 = row 6, col 7
ROW0, COL0, COLS, ROWS = 6, 7, 18, 7
CODES = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x81, 0x40]


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


def panel(m):
    """The 18 x 7 character rectangle: pixels then attributes."""
    px = []
    for r in range(ROW0 * 8, ROW0 * 8 + ROWS * 8):
        px.append([m[scr(c * 8, r)] for c in range(COL0, COL0 + COLS)])
    at = []
    for r in range(ROWS):
        base = 0x5800 + (ROW0 + r) * 32 + COL0
        at.append([m[base + c] for c in range(COLS)])
    return px, at


def capture():
    out = {}
    for code in CODES:
        h = Harness()
        h.load_state(pickle.load(open(STATE, 'rb')))
        m = h.memobj.m
        one_pass(h)                       # settle
        before_px, _ = panel(m)
        m[0x84B9] = code                  # raise the message
        one_pass(h)
        px, at = panel(m)
        out['%02X' % code] = {
            'code': code, 'px': px, 'at': at,
            # $84B9 is consumed by the same pass, and $847D bit 2 is the pause
            'b9_after': m[0x84B9], 'f847D': m[0x847D],
            'changed': sum(1 for a, b in zip(sum(before_px, []), sum(px, []))
                           if a != b),
        }
    return out


def show(doc):
    for k in sorted(doc, key=lambda s: doc[s]['code']):
        d = doc[k]
        print('\n=== code $%s -- %d of %d bytes changed, $84B9 after = %02X ==='
              % (k, d['changed'], COLS * ROWS * 8, d['b9_after']))
        for row in d['px']:
            line = ''.join(''.join('#' if b & (0x80 >> i) else '.'
                                   for i in range(8)) for b in row)
            if line.strip('.'):
                print('  ' + line)


def main():
    doc = capture()
    path = os.path.join(ROOT, 'build', '_banner.json')
    json.dump(doc, open(path, 'w'))
    for k in sorted(doc, key=lambda s: doc[s]['code']):
        d = doc[k]
        ink = sorted({a for row in d['at'] for a in row})
        print('code $%s: %4d pixel bytes differ, attributes %s'
              % (k, d['changed'], ' '.join('$%02X' % a for a in ink)))
    # the panels must not be identical, or the gate is testing the border
    sigs = {k: json.dumps(doc[k]['px']) for k in doc}
    assert len(set(sigs.values())) == len(doc), \
        'two codes painted the same panel -- the gate would not see the text'
    print('\nall %d panels are distinct' % len(doc))
    print('wrote', path)
    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        show(doc)


if __name__ == '__main__':
    main()
