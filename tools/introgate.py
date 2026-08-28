#!/usr/bin/env python3
"""
introgate.py -- THE LEVEL-ENTRY SCREEN, $8B27.

$B38E CALL $8B27 sits immediately before $B391 CALL $8503, so this runs ONCE
per level, between building the dungeon and starting the main loop.  It is the
screen the player reads before every level.

    python tools/introgate.py            capture, write build/_intro.json
    python tools/introgate.py show       ...and print each variant as ASCII

HOW IT IS REACHED, and what draws what:

  $B382  CALL $B3D0    which zeroes $847D/$847E and then CLEARS THE SHADOW
                       BITMAP $C000..$CFFF -- the top SIXTEEN character rows
                       only; $D000..$D7FF (rows 16..23) keeps the HUD panel
  $B38E  CALL $8B27

  $8B27  BIT 4,(IY-2) / JR z,$8B37       $847D bit 4
  $8B2D    DE=$0040  IX=$7EDD  "FIND THE  POTION"        -> char row 2
  $8B37  LD A,($847E) / AND $30 / JR z,$8B5C   the STUN/HURT bits
  $8B3E    DE=$1020  IX=$7EEE  "OTHER  PLAYERS"          -> char row 17
  $8B48    DE=$1000  IX=$7EFD  "SHOTS NOW STUN"          -> char row 16
           ...or $7F0C "SHOTS NOW HURT" when bit 4 is clear ($8B4F BIT 4)
  $8B5C  BIT 6,(IY-1) / JR nz,$8BB4       a TREASURE ROOM
  $8BB4    DE=$0800  IX=$7EBD  "YOU HAVE FOUND A"        -> char row 8
  $8BBE    DE=$0840  IX=$7ECE  "TREASURE  ROOM"          -> char row 10
  $8B62  otherwise: EIGHT objective icons from the table at $8BAC, drawn from
         DE=$C806 (char row 8, column 6) by $8AA4 / $8AAB
  $8B84  the tail, on every path: SET 5,(IY-2) (the LONG pause), CALL $BBA7,
         flood the shadow ATTRIBUTE file $D800..$DAFF with $47, SET 2,(IY-2)
         (the pause request) and CALL $9CD7, which blits and blocks.

  $8A08 is the centring printer: HL = $C000 + DE, then the start column is
  (32 - length)/2 added to L ALONE, so a string is centred on the 32-column
  screen and cannot cross a third boundary.

THE CAPTURE.  The shadow is cleared the way $B3D0 clears it and $8B27 is then
called on its own, which is what the game does; the picture is read back out
of the shadow, because that is what $9CD7 puts on the display.  Calling $8B27
WITHOUT the clear shows the previous playfield underneath -- the first attempt
here did exactly that and looked like garbage.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, SP, PC                          # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')

# ($847D, $847E, label) -- the three independent switches
# ($847D, $847E, label) plus an optional LEVEL, because $8B7E draws the level
# number in sprites and $8A84 emits one, two or three digits with no leading
# zero -- a matrix at one level cannot see that at all.
VARIANTS = [
    (0x00, 0x00, 'plain'),
    (0x00, 0x00, 'lvl7', 7),
    (0x00, 0x00, 'lvl9', 9),
    (0x00, 0x00, 'lvl10', 10),
    (0x00, 0x00, 'lvl42', 42),
    (0x00, 0x00, 'lvl99', 99),
    (0x00, 0x00, 'lvl100', 100),
    (0x00, 0x00, 'lvl137', 137),
    (0x10, 0x00, 'potion'),
    (0x00, 0x10, 'stun'),
    (0x00, 0x20, 'hurt'),
    (0x00, 0x30, 'stun+hurt'),
    (0x10, 0x10, 'potion+stun'),
    (0x00, 0x40, 'treasure'),
    (0x10, 0x40, 'potion+treasure'),
    (0x00, 0x50, 'treasure+stun'),
]


def shadow_addr(x, y):
    return ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2) | (x >> 3)


def capture(d7d, d7e, level=None):
    """Step $8B27 to $9CD7 and read the shadow.

    NOT h.call(): $8B84's tail ends in CALL $9CD7, whose first instruction is
    HALT, and h.call runs with interrupts disabled -- so it spins there to the
    step limit and the routine's own writes are the only thing that survives.
    Stopping AT $9CD7 is the right place anyway: everything the screen is has
    been drawn by then, and $9CD7 only blits it to $4000 and blocks."""
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    # $B3E4's clear: the shadow BITMAP's first $1000 bytes only
    for a in range(0xC000, 0xD000):
        m[a] = 0
    m[0x847D] = d7d
    m[0x847E] = d7e
    if level is not None:
        m[0x8403] = level
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    regs[PC] = 0x8B27
    sp = (regs[SP] - 2) & 0xFFFF
    mem[sp] = 0x00
    mem[sp + 1] = 0x00                       # a sentinel return of $0000
    regs[SP] = sp
    n = 0
    while n < 3_000_000:
        pc = regs[PC]
        if pc == 0x9CD7 or pc < 0x4000 or mem[pc] == 0x76:
            break
        ops[mem[pc]]()
        n += 1
    else:
        raise RuntimeError('$8B27 did not reach $9CD7')
    return (list(m[0xC000:0xD800]), list(m[0xD800:0xDB00]))


def main():
    out = {}
    for v in VARIANTS:
        d7d, d7e, label = v[0], v[1], v[2]
        level = v[3] if len(v) > 3 else None
        px, at = capture(d7d, d7e, level)
        out[label] = {'f847D': d7d, 'f847E': d7e, 'level': level,
                      'px': px, 'at': at}
        rows = sorted({y // 8 for y in range(192)
                       if any(px[shadow_addr(c * 8, y)] for c in range(32))})
        print('%-16s $847D=%02X $847E=%02X lvl=%-4s ink rows %s  attrs %s'
              % (label, d7d, d7e, level, rows,
                 ' '.join('$%02X' % a for a in sorted(set(at))[:4])))
    path = os.path.join(ROOT, 'build', '_intro.json')
    json.dump(out, open(path, 'w'))
    sigs = {k: json.dumps(out[k]['px']) for k in out}
    print('\n%d variants, %d distinct pictures'
          % (len(out), len(set(sigs.values()))))
    print('wrote', path)
    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        for k in out:
            print('\n=== %s ===' % k)
            px = out[k]['px']
            for y in range(192):
                line = ''.join('#' if px[shadow_addr(c * 8, y)] & (0x80 >> b)
                               else '.' for c in range(32) for b in range(8))
                if line.strip('.'):
                    print('  ' + line)


if __name__ == '__main__':
    main()
