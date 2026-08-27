#!/usr/bin/env python3
"""
refute_actors.py -- independent re-measurement of the $5C00 actor-list report.

Everything here is a CAUSAL test against the running original: plant a known
state, run the real Z80, read the result.  No claim is taken from a reading.

MODES
    freeze      Plant ONE actor at a controlled offset from the camera, hold the
                player still, run 8 passes, report whether the record changed at
                all.  This is the off-screen-freeze gate, measured causally
                rather than observed: dy in [-3,39] and dx in [-3,63] update,
                everything else is completely frozen.
    rrate       Trace the two LD A,R consumers in the per-actor update.
                $AC25 (skip the chase re-decision) is ~50/50, but $AC4C
                ("turn when blocked") is NOT 3-in-4: it is only reachable when
                the $AC25 read was EVEN, and the path between them is a fixed
                57 or 59 M1 cycles, so R is odd at $AC4C in 125/128 samples and
                (R&3)==0 -- the "do not turn" case -- fires 1 time in 128.
    cadence     Movement rate in open ground, one actor, no obstacles.
                classes 0/1 ~1.09 units/pass, class $A0 ~1.71 (the extra turn at
                $AC1E), class $60 oscillates: it FLEES inside 12 units ($AC3C
                LD A,D / XOR 4 reverses the compass slot).
    selfpark    $AC73 parks the actor's own y at 0 before its self-scan.  That
                does NOT always hide it: with the candidate at y == 3 the
                operand $A991 writes to $A9B9 is 0, so the parked record matches
                itself and the move is refused.  One row of the world, but real.
    chase       $AD51 is a JR whose displacement at $AD52 is rewritten EVERY
                pass by $ABA1/$ABA4 (CALL $ADC7 / LD ($AD52),A).  Four targets:
                $24 chase P1, $19 chase P2, $2F nearest of the two, and $00 --
                $AD53, a RANDOM WALK (turn -1/0/+1 on a $B575 three-way split),
                which is not a chase at all.
    draw        The blit uses the actor's PRE-update position: BC is loaded at
                $ABD7 and the update at $A21E rewrites +0/+1 without touching
                it.  Reports how many blits land on the old square.
"""
import collections
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, A, B, C, D, E, H, L, F, R, IXh, IXl, TAPE_CALL_PC  # noqa: E402
from filmstrip import run_frames                                                            # noqa: E402

LIST, END, COUNT = 0x5C00, 0x8494, 0x8496
FRAME_T = 69888
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')


def boot():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def setlist(h, recs):
    for i, r in enumerate(recs):
        h.poke(LIST + 4 * i, *r)
    h.poke(COUNT, len(recs))
    e = LIST + 4 * len(recs)
    h.poke(END, e & 0xFF, e >> 8)


def steps(h, npass, hooks):
    """Run npass main-loop passes, calling hooks[pc](h) before each matching
    instruction.  Must handle the tape breakpoint and the HALT, or it hangs."""
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    for _ in range(npass):
        tgt = regs[T] + 4 * FRAME_T
        while regs[T] < tgt:
            pc = regs[PC]
            if pc == TAPE_CALL_PC:
                h._tape()
                continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt()
                continue
            fn = hooks.get(pc)
            if fn is not None:
                fn(h)
            ops[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)


# --------------------------------------------------------------------------
def mode_freeze():
    h = boot()
    m = h.memobj.m
    S0 = h.save_state()
    print('camera (%d,%d), player (%d,%d); one planted class-0 record, 8 passes,'
          % (m[0x848B], m[0x848C], m[0x8420], m[0x8421]))
    print('no keys held.  FROZEN means the four record bytes did not change.')
    for axis in (1, 0):
        print('\n  %s offset from the camera:' % 'xy'[axis])
        rng = range(-6, 48) if axis else range(-6, 72, 2)
        for d in rng:
            h.load_state(S0)
            x = (2 + (20 if axis else d)) & 0x7F
            y = (2 + (d if axis else 20)) & 0x7F
            setlist(h, [(x, y, 0x00, 0x00)])
            r0 = tuple(m[LIST:LIST + 4])
            for _ in range(8):
                run_frames(h, 4)
            r1 = tuple(m[LIST:LIST + 4])
            print('    d%s=%4d  (%3d,%3d)  %s' % ('xy'[axis], d, x, y,
                  'FROZEN' if r0 == r1 else 'updated'))
    print('\n  => the update hook $ABFF sits at $A21E, INSIDE the clip test at')
    print('     $A1DA, so an actor that fails the clip is never updated at all.')


def mode_rrate():
    h = boot()
    m = h.memobj.m
    h.poke(0x8420, 96, 56)
    h.poke(0x848B, 66)
    h.poke(0x848C, 38)          # park the player beside the horde
    s25, pairs, pending = [], [], [None]

    def at25(hh):
        pending[0] = hh.sim.registers[R]
        s25.append(hh.sim.registers[R])

    def at4c(hh):
        if pending[0] is not None:
            pairs.append((pending[0], hh.sim.registers[R]))
            pending[0] = None

    def atupd(hh):
        pending[0] = None

    steps(h, 40, {0xAC25: at25, 0xAC4C: at4c, 0xABFF: atupd})
    n = len(s25)
    b = collections.Counter(v & 1 for v in s25)
    print('$AC25  LD A,R / RRA / JR c   (skip the chase re-decision)')
    print('   %d samples   R&1 %s   P(skip) = %.3f' % (n, dict(b), b.get(1, 0) / max(1, n)))
    print('   R&15 %s' % dict(sorted(collections.Counter(v & 15 for v in s25).items())))
    print()
    print('$AC4C  LD A,R / AND 3 / JR z  (skip the turn when blocked)')
    print('   %d samples' % len(pairs))
    print('   R parity at $AC25 for those samples: %s   <- always EVEN, by construction'
          % dict(collections.Counter(a & 1 for a, _ in pairs)))
    print('   R($AC4C) - R($AC25): %s   <- a fixed, ODD path length'
          % dict(sorted(collections.Counter((y - x) & 0x7F for x, y in pairs).items(),
                        key=lambda kv: -kv[1])))
    h3 = collections.Counter(y & 3 for _, y in pairs)
    print('   R&3 at $AC4C: %s' % dict(sorted(h3.items())))
    print('   P((R&3)==0) = %.4f   -- the "3-in-4 take a turn" reading predicts 0.25;'
          % (h3.get(0, 0) / max(1, len(pairs))))
    print('   a blocked actor that re-decides turns essentially ALWAYS.')


def mode_cadence():
    for typ, label in ((0x00, 'class 0 ghost'), (0x20, 'class 1 grunt'),
                       (0x60, 'class 3 (non-directional)'), (0xA0, 'class 5 $A0')):
        h = boot()
        m = h.memobj.m
        h.poke(0x8420, 100, 64)                 # open ground: map rows 16..20 are $00
        h.poke(0x848B, 70)
        h.poke(0x848C, 46)
        setlist(h, [(100, 80, typ, 0x00)])
        ys = []
        for _ in range(14):
            run_frames(h, 4)
            ys.append(m[LIST + 1])
        # first pass at which it reaches the player's 7-box (y == 68)
        stop = ys.index(68) + 1 if 68 in ys else len(ys)
        print('  %-26s y/pass %s' % (label, ys))
        print('  %-26s %d units in %d passes = %.2f units/pass'
              % ('', 80 - min(ys), stop, (80 - min(ys)) / float(stop)))
    print('  (class 3 oscillates: $AC35 CP 12 / $AC3C LD A,D / XOR 4 -- it FLEES')
    print('   once |dx|+|dy| drops under 12.)')


def mode_selfpark():
    h = boot()
    base = h.save_state()
    r = h.sim.registers

    def probe(cx, cy):
        r[C], r[B] = cx, cy
        h.call(0xA97F, interrupts=False)
        return r[F] & 1
    print('$AC73 parks the actor\'s own y at 0, then $AC77 scans.  A record at')
    print('y == 0 is invisible to the scan only while the candidate y is not 3:')
    for cy in range(0, 9):
        h.load_state(base)
        setlist(h, [(20, 0, 0x10, 0)])
        print('   candidate (20,%d) -> parked record found: %s' % (cy, bool(probe(20, cy))))
    print('   ($A991 writes (cy-3)&$7F to $A9B9; at cy==3 that operand is 0 and')
    print('    the parked y==0 matches.  An actor moving to y==3 self-blocks.)')


def mode_chase():
    h = boot()
    TGT = {0x24: '$AD77 chase P1', 0x00: '$AD53 RANDOM WALK',
           0x19: '$AD6C chase P2', 0x2F: '$AD82 nearest of the two'}
    print('$AD51 is "JR d"; d at $AD52 is rewritten every pass by $ABA1/$ABA4.')
    print(' $842A $844A $8454.7 $842B.7 $844B.7 -> $AD52')
    seen = collections.Counter()
    for a in (0x00, 0x01):
        for b in (0x00, 0x01):
            for s54 in (0x00, 0x80):
                for s2b in (0x00, 0x80):
                    for s4b in (0x00, 0x80):
                        h.load_state(pickle.load(open(STATE, 'rb')))
                        h.poke(0x842A, a)
                        h.poke(0x844A, b)
                        h.poke(0x8454, s54)
                        h.poke(0x842B, s2b)
                        h.poke(0x844B, s4b)
                        h.call(0xADC7, interrupts=False)
                        v = h.sim.registers[A]
                        seen[v] += 1
                        print('    %02X    %02X     %d       %d       %d    -> $%02X  %s'
                              % (a, b, s54 >> 7, s2b >> 7, s4b >> 7, v, TGT.get(v, '?')))
    print('  targets reachable: %s' % {('$%02X' % k): v for k, v in seen.items()})
    h = boot()
    m = h.memobj.m
    live = []
    steps(h, 40, {0xAD51: lambda hh: live.append(m[0xAD52])})
    print('  in the captured single-player state, %d samples during play: %s'
          % (len(live), {('$%02X' % k): v for k, v in collections.Counter(live).items()}))
    print('  $AD53 turns the slot by -1/0/+1 on a $B575 three-way split ($55/$AA).')


def mode_draw():
    h = boot()
    m = h.memobj.m
    h.poke(0x8420, 96, 56)
    h.poke(0x848B, 66)
    h.poke(0x848C, 38)
    cur = [None]
    tally = collections.Counter()

    def a1da(hh):
        cur[0] = None

    def a221(hh):
        rg = hh.sim.registers
        ix = rg[IXl] + 256 * rg[IXh]
        if LIST <= ix < 0x5F00:
            cur[0] = {'rec': tuple(m[ix:ix + 4]), 'bc': (rg[C], rg[B]),
                      'cam': (m[0x848B], m[0x848C])}

    def a232(hh):
        if cur[0]:
            cur[0]['id'] = hh.sim.registers[A]

    def dd2(hh):
        c = cur[0]
        if not c or 'id' not in c:
            return
        rg = hh.sim.registers
        x, y, t, f = c['rec']
        cx, cy = c['cam']
        cc, bb = c['bc']
        a = rg[L] + 256 * rg[H] - 0xC000
        row = ((a >> 11) & 3) * 8 + ((a >> 5) & 7)
        col = a & 0x1F
        tally['pos_ok' if (col, row) == (((cc - cx) & 0x7E) >> 1, ((bb - cy) & 0x7E) >> 1)
              else 'pos_bad'] += 1
        ph = 0 if not (f & 0x40) else (1 if not (f & 0x80) else 2)
        tally['id_ok' if c['id'] == 0x40 + 24 * (t >> 5) + (t & 7) + 8 * ph else 'id_bad'] += 1
        tally['bc_is_record' if (cc, bb) == (x, y) else 'bc_is_STALE'] += 1
        cur[0] = None

    steps(h, 25, {0xA1DA: a1da, 0xA221: a221, 0xA232: a232, 0x9DD2: dd2})
    print('blits of $5C00 records, 25 passes beside the horde:')
    print('  destination == ((BC-cam)&$7E)>>1  : %d ok / %d wrong'
          % (tally['pos_ok'], tally['pos_bad']))
    print('  id == $40+24*class+dir+8*phase    : %d ok / %d wrong'
          % (tally['id_ok'], tally['id_bad']))
    print('  BC equalled the record at blit time: %d;  STALE (the actor moved'
          % tally['bc_is_record'])
    print('  during its own update, after BC was read at $ABD7): %d'
          % tally['bc_is_STALE'])
    print('  => an actor is drawn on the square it occupied BEFORE this pass.')


if __name__ == '__main__':
    mode = (sys.argv[1:] or ['freeze'])[0]
    fn = globals().get('mode_' + mode)
    if fn is None:
        print(__doc__)
    else:
        fn()
