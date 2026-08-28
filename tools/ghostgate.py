#!/usr/bin/env python3
"""
ghostgate.py -- THE SYMBOL SHIFT WALK-THROUGH-WALLS CHEAT, $A84D.

All four move handlers end the same way.  Taking RIGHT, $A82F:

    $A82F  INC C / INC C / RES 7,C      ; the candidate x
    $A833  CALL $A924 / JR c,$A852      ; the LEASH -- straight to the undo
    $A838  BIT 1,C / RET z              ; the map is consulted every OTHER step
    $A83B  CALL $9A2D / CALL $A8E7
    $A841  JR c,$A84D                   ; blocked
    $A843  BIT 1,B / RET z
    $A846  CALL $9A3D / CALL $A8E7
    $A84C  RET nc                       ; not blocked -- the move stands
    $A84D  BIT 1,(IY+7)                 ; <-- SYMBOL SHIFT
    $A851  RET z                        ;     held -> RETURN, the move STANDS
    $A852  DEC C / DEC C / RES 7,C      ;     otherwise undo it
    $A856  LD (IX+$1D),0                ;     and drop the pending interaction
    $A85A  RES 3,E                      ;     and the walk animation bit

(IY+7) is $8486, the $7FFE half-row, and the game keeps the row RAW: every
reader is BIT n / JR nz / OR bit ($85B3 on), so a bit reads 0 when its key is
HELD.  `RET z` is therefore the SYM-HELD case and the blocked step survives.
The other three handlers are $A87B, $A8A9 and $A8D7, identical.

TWO THINGS THE CHEAT DOES NOT DO, both measured here:
  * it does not defeat the LEASH -- $A836 jumps straight to $A852 and never
    reaches $A84D, so two players still cannot separate;
  * it does not survive as a pending interaction being dropped: $A856 is
    skipped too, so an item under the wall corner is still taken.

    python tools/ghostgate.py          write build/_ghost.json
    python tools/ghostgate.py show     ...and print both traces

THE CONTROL IS THE POINT.  The tool runs the SAME walk twice, once with SYM
and once without, and ASSERTS the two traces differ.  A cheat that changed
nothing would otherwise pass every check written about it.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, IFF, TAPE_CALL_PC             # noqa: E402
from keyprobe import KEYS, keymask                             # noqa: E402
from sim_move import LOOP_TOP                                  # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
KM = {n: (s, b) for n, s, b in KEYS}
MAP = 0x8000
PX, PY = 0x8420, 0x8421
NACT, AEND = 0x8496, 0x8494
PASSES = 24
START_X, START_Y = 40, 40          # cell (10,10), the same seat headless uses
WALLS = [(10, 11), (10, 12), (10, 13)]     # a barrier to the RIGHT


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


def run(sym):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)                                  # settle
    m[NACT] = 0                                  # $8496 -- no actors at all,
    m[AEND], m[AEND + 1] = 0x00, 0x5C            # and the scan's end pointer
    m[PX], m[PY] = START_X, START_Y
    for r, c in WALLS:
        m[MAP + r * 32 + c] = 0x01               # 1..$10 is a wall to $A8E7
    before = list(m[MAP:MAP + 1024])
    sel, bit = KM['D']                           # method 3, player 1 RIGHT
    h.ports.press(sel, keymask(bit))
    if sym:
        s2, b2 = KM['SYM']
        h.ports.press(s2, keymask(b2))
    trace = []
    for _ in range(PASSES):
        one_pass(h)
        trace.append([m[PX], m[PY]])
    return before, trace


def main():
    before, plain = run(False)
    _, cheat = run(True)

    assert plain != cheat, (
        'holding SYMBOL SHIFT changed nothing -- the walk never reached a '
        'wall, so this gate would pass whatever the engine did')

    doc = {'before': before, 'plain': plain, 'cheat': cheat,
           'start': [START_X, START_Y], 'walls': [[r, c] for r, c in WALLS],
           'passes': PASSES}
    path = os.path.join(ROOT, 'build', '_ghost.json')
    json.dump(doc, open(path, 'w'))

    print('start (%d,%d), a wall barrier at cells %s'
          % (START_X, START_Y, ' '.join('(%d,%d)' % w for w in WALLS)))
    print('  without SYM: x runs %d -> %d  (blocked at the wall)'
          % (plain[0][0], plain[-1][0]))
    print('  with    SYM: x runs %d -> %d  (through it)'
          % (cheat[0][0], cheat[-1][0]))
    first = next(i for i in range(PASSES) if plain[i] != cheat[i])
    print('  the two traces first differ on pass %d: $%02X vs $%02X'
          % (first + 1, plain[first][0], cheat[first][0]))
    print('wrote', path)

    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        print('\n  pass   plain      cheat')
        for i in range(PASSES):
            print('   %2d   (%3d,%3d)  (%3d,%3d)%s'
                  % (i + 1, plain[i][0], plain[i][1], cheat[i][0], cheat[i][1],
                     '   <--' if plain[i] != cheat[i] else ''))


if __name__ == '__main__':
    main()
