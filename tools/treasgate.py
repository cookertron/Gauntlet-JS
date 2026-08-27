#!/usr/bin/env python3
"""
treasgate.py -- THE TREASURE ROOM'S PAY-OUT, $A748 / $891C / $899F.

Reported from play: "once the timer runs out there's a dialog box in the
middle of the playing field showing the points gained -- the number of
treasure chests x 100 if the player makes it to the exit, and zero if not."
That is three separate arms and the port had none of them, though it drew
the panel and even filled in the number:

    $A748  BIT 6,(IY-1) / (IX+12) = DAA((IX+12)+1)
           a $13 CHEST bumps the counter, in BCD, in a treasure room only.
           $969A starts it at ZERO on entering one and $A697 makes the exit
           skip its usual bump, so inside a treasure room (IX+12) is not a
           level number -- it is how many chests you are carrying.

    $891C  BIT 6,(IY-1) / BIT 7,(IY-2) / ... / SET 6,(IY+$3A)
           in a treasure room, once the LEVEL IS OVER, raise $84B9 bit 6.
           The timer expiring is what ends the room ($8B22 SET 7), and $8543
           runs before $8556's test, so the panel goes up on that same pass.

    $899F  BIT 7,(IX+$14) / RET nz          not in the game, nothing to pay
           LD A,(IX+12)                     the chests
           BIT 6,(IX+11) / JR nz / SUB A    ...unless he is not EXITING
           LD D,A / LD E,0 / CALL $B807     score += A00 in BCD, i.e. x100

    python tools/treasgate.py          capture, write build/_treasure.json
    python tools/treasgate.py show     ...and print each table

THE SCENARIOS.  $847E bit 6 is set by hand rather than by building a real
treasure room: the arms under test read that bit and nothing else, and the
room the two machines BUILD differs anyway (the record is drawn through the
`LD A,R` substitute -- see selectAndBuild).  Planting the bit tests the arms;
it would be a mistake to let an entropy divergence decide whether they run.

    chest-tre     three $13 chests, treasure mode   -> counter 0,1,2,3
    chest-plain   the same three, NOT treasure mode -> counter never moves
    payout-exit   five chests banked, EXITING, level over -> +500
    payout-miss   the same five, NOT exiting        -> +0

Each ends its level the way the GAME does -- `payout-exit` walks onto a $36
and lets the exit sequence run, `payout-miss` lets the timer expire.  Setting
$847D bit 7 by hand instead is tempting and wrong: the original tests it at
$8556, the BOTTOM of the loop, so a pass that begins with it set still runs
$8543's banner, while the port tests it at the top of onePass() and does not.
In play the bit is always set mid-pass and the two agree; pre-setting it
compares a situation neither machine reaches.
Note also that $84B9 is raised and CLEARED inside a single pass ($8931 sets
it, $899A clears it), so it cannot be sampled at the loop top -- the durable
observable is the SCORE.
$8AE3's `AND $40` is why `payout-exit` cannot use the timer: an EXITING
player does not tick it.
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
PX, PY, F11, LVLOWN = 0x8420, 0x8421, 0x842B, 0x842C
SCORE, CODE = 0x8424, 0x84B9
F847D, F847E = 0x847D, 0x847E
TIMER, TDIV = 0x84B6, 0x84B7
NACT, AEND = 0x8496, 0x8494
START = (40, 40)                       # cell (10,10)
CHESTS = [(10, 11), (10, 12), (10, 13)]
PASSES = 12

# name -> (treasure?, chests planted, levelOwn, exiting?, expire the timer?)
SCEN = [
    ('chest-tre',   True,  True,  0, False, False),
    ('chest-plain', False, True,  0, False, False),
    ('payout-exit',  True,  False, 5, True,  True),
    ('payout-miss',  True,  False, 5, False, True),
    ('payout-plain', False, False, 5, True,  True),
]


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


def run(treasure, chests, lvlown, exiting, expire):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)
    m[NACT] = 0
    m[AEND], m[AEND + 1] = 0x00, 0x5C
    m[PX], m[PY] = START
    m[LVLOWN] = lvlown
    m[SCORE] = m[SCORE + 1] = m[SCORE + 2] = 0
    m[CODE] = 0
    if treasure:
        m[F847E] |= 0x40                       # $91AB SET 6,(IY-1)
        # ...and give the room a FULL clock.  $84B6 in the captured state is
        # whatever it is, and treasure mode makes $8527 start ticking it: left
        # alone it expired on the first pass and ended the level, which put
        # the walk runs into an entropy-built NEXT dungeon where nothing can
        # be compared.  $915E's own reload is BCD $20..$30 with $84B7 = 12.
        m[TIMER], m[TDIV] = 0x30, 12
    else:
        m[F847E] &= ~0x40 & 0xFF
    if exiting:
        m[F11] |= 0x40                         # $A687 SET 6,(IX+11)
    if chests:
        for r, c in CHESTS:
            m[MAP + r * 32 + c] = 0x13
    if expire:
        # THE ARM IN ISOLATION.  Ending the level for real drags the whole
        # transition in, and the next level is built through the `LD A,R`
        # substitute -- so from the transition on the two machines are in
        # different dungeons and nothing can be compared.  $891C is called
        # once a pass from $8543 and reads only $847D/$847E/(IX+11)/(IX+12),
        # so calling it with those planted tests exactly the three arms and
        # nothing else.
        m[F847D] |= 0x80                       # $8B22 SET 7 -- the level is over
    before = list(m[MAP:MAP + 1024])
    if chests:
        sel, bit = KM['D']
        h.ports.press(sel, keymask(bit))       # walk RIGHT over them
    if expire:
        before = list(m[MAP:MAP + 1024])
        s0 = (m[SCORE + 2] << 16) | (m[SCORE + 1] << 8) | m[SCORE]
        h.call(0x891C, limit=8_000_000)        # $8543's own call
        s1 = (m[SCORE + 2] << 16) | (m[SCORE + 1] << 8) | m[SCORE]
        return before, [[m[LVLOWN], m[CODE], m[F847D], s1, m[PX]]], s1 - s0
    rows = []
    lvl0 = m[0x8403]
    for _ in range(PASSES):
        one_pass(h)
        rows.append([m[LVLOWN], m[CODE], m[F847D],
                     (m[SCORE + 2] << 16) | (m[SCORE + 1] << 8) | m[SCORE],
                     m[PX]])
        # STOP AT THE TRANSITION.  Once $8403 moves, the next level has been
        # BUILT -- and its record is drawn through the `LD A,R` substitute, so
        # from here the two machines are in different dungeons and nothing
        # after this row can be compared.  The pay-out has already landed in
        # the score by then, which is the row that matters.
        if m[0x8403] != lvl0:
            break
    return before, rows, None


def main():
    out = {}
    for name, tre, ch, lo, ex, exp in SCEN:
        before, rows, paid = run(tre, ch, lo, ex, exp)
        out[name] = {'treasure': tre, 'chests': ch, 'levelOwn': lo,
                     'exiting': ex, 'expire': exp, 'start': list(START),
                     'chestCells': [list(c) for c in CHESTS],
                     'paid': paid, 'arm': bool(exp),
                     'before': before, 'rows': rows, 'passes': PASSES}
        print('%-12s counter %d -> %d, score %06X%s'
              % (name, lo, rows[-1][0], rows[-1][3],
                 '' if paid is None else ('   PAID %d' % paid)))

    # the matrix has to discriminate, or it is testing nothing
    assert out['chest-tre']['rows'][-1][0] != out['chest-plain']['rows'][-1][0], \
        'the chest counter moved the same with and without treasure mode'
    assert out['payout-exit']['rows'][-1][3] != out['payout-miss']['rows'][-1][3], \
        'exiting and not exiting paid the same score'
    path = os.path.join(ROOT, 'build', '_treasure.json')
    json.dump(out, open(path, 'w'))
    print('\nwrote', path)

    if len(sys.argv) > 1 and sys.argv[1] == 'show':
        for k, v in out.items():
            print('\n=== %s ===' % k)
            print('  pass  (IX+12)  $84B9  $847D   score   x')
            for i, r in enumerate(v['rows'][:10]):
                print('   %2d      %2d     $%02X    $%02X   %06X  %3d'
                      % (i + 1, r[0], r[1], r[2], r[3], r[4]))


if __name__ == '__main__':
    main()
