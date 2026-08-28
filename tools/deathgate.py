#!/usr/bin/env python3
"""
deathgate.py -- drive the REAL Z80 through DEATH and print the numbers a port
must reproduce.  Same shape as actorgate.py / doorgate.py: it never loads the
engine, it only asks the original.

    python tools/deathgate.py all
    python tools/deathgate.py seq        pass-by-pass, death -> game over
    python tools/deathgate.py routes     poke vs monster contact vs the drain
    python tools/deathgate.py drop       the drop value against the key count
    python tools/deathgate.py rejoin     the 2-player death/rejoin memory diff
    python tools/deathgate.py restart    the 1-player game over -> new level
    python tools/deathgate.py keys       which key sets which direction bit
    python tools/deathgate.py stack      the Q19 stack-imbalance scan

THE TEST.  $8540 CALL $93C2 runs ONCE PER PASS, last but one in the main loop
(after the actors, the draw, the HUD, the exit walk, the cull and the generator
sweep).  $93CD is entered for player 1 and FALLEN INTO for player 2:

    $93CD  BIT 7,(IX+$14) / RET nz          already dead
    $93D2  LD A,(IX+2) / OR (IX+3) / RET nz  DEAD only when the packed-BCD
                                             health is exactly $0000

Poking health to $0000 is verified here to reach the identical path, with the
identical writes, as a monster contact ($A627 -> $B852, clamped) and as the
drain ($B6E4 -> $B852) -- see `routes`.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, SP, FRAME_T, TAPE_CALL_PC   # noqa: E402
from keyprobe import KEYS, keymask                                   # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LOOP_TOP = 0x8503
# measured from $862E (player 1) and $8657 (player 2), control method 3
P1 = dict(up='1', down='Q', left='S', right='D', fire='Z', shift='CAPS')
P2 = dict(up='8', down='I', left='K', right='L', fire='M', shift='SPACE')


def boot():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def press(h, name):
    sel, bit = KM[name]
    h.ports.press(sel, keymask(bit))


def run_until_pc(h, targets, limit=20_000_000):
    """Step until PC is in `targets`; returns (pc, tstates, steps)."""
    if isinstance(targets, int):
        targets = (targets,)
    targets = set(targets)
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0, n = regs[T], 0
    while n < limit:
        pc = regs[PC]
        if n and pc in targets:
            return (pc, regs[T] - t0, n)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return (None, regs[T] - t0, n)


def pass_top(h):
    return run_until_pc(h, LOOP_TOP)


AREAS = [('map $8000-$83FF', 0x8000, 0x8400),
         ('globals $8400-$84D0', 0x8400, 0x84D0),
         ('actors $5C00-$5F00', 0x5C00, 0x5F00),
         ('lists $5B00-$5C00', 0x5B00, 0x5C00),
         ('hiscore $7F1B-$7F80', 0x7F1B, 0x7F80),
         ('map buffer $5F00-$6F80', 0x5F00, 0x6F80),
         ('pack $C000-$D010', 0xC000, 0xD010)]
NAMES = {}
for _i in range(32):
    NAMES[0x8420 + _i] = 'p1+%02X' % _i
    NAMES[0x8440 + _i] = 'p2+%02X' % _i


def show_diff(before, after, maxrows=30):
    for name, lo, hi in AREAS:
        rows = [(a, before[a], after[a]) for a in range(lo, hi) if before[a] != after[a]]
        print('  --- %-24s %d bytes changed' % (name, len(rows)))
        for a, b, c in rows[:maxrows]:
            print('        $%04X  %02X -> %02X   %s' % (a, b, c, NAMES.get(a, '')))
        if len(rows) > maxrows:
            print('        ... %d more' % (len(rows) - maxrows))


# ---------------------------------------------------------------- seq
def cmd_seq():
    h = boot(); m = h.memobj.m
    pass_top(h)
    h.poke(0x8422, 0x00, 0x00)
    print('pass ctr  hp   +0B +0E +14 +17 +18 +19  $847D  where')
    for i in range(3):
        pc, dt, n = run_until_pc(h, (LOOP_TOP, 0x855C))
        print('%4d %4d %02X%02X  %02X  %02X  %02X  %02X  %02X  %02X   %02X    %s  (%.2f frames)'
              % (i + 1, m[0x8491], m[0x8422], m[0x8423], m[0x842B], m[0x842E], m[0x8434],
                 m[0x8437], m[0x8438], m[0x8439], m[0x847D],
                 'MAIN LOOP RETURNS' if pc == 0x855C else 'pass end', dt / FRAME_T))
        if pc == 0x855C:
            break
    pc, dt, n = run_until_pc(h, 0xB3B9)
    print('$B39B spin ends at ctr %d -> $B3B9 game over (both $8434 & $8454 bit 7)' % m[0x8491])
    print('high-score record $7F2B: %s   $84C6=%d'
          % (' '.join('%02X' % b for b in m[0x7F2B:0x7F33]), m[0x84C6]))


# ---------------------------------------------------------------- routes
def cmd_routes():
    WATCH = (0x93CD, 0x93D9, 0x942B, 0xA627, 0xB852, 0xB6E4)

    def drive(label, setup, keys=(), maxpass=40):
        h = boot(); m = h.memobj.m
        pass_top(h)
        setup(h)
        for k in keys:
            press(h, k)
        for i in range(maxpass):
            sim = h.sim
            regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
            fd, ia = h.frame_duration, h.int_active
            n, seen = 0, []
            while n < 400000:
                pc = regs[PC]
                if n and pc == LOOP_TOP:
                    break
                if pc in WATCH:
                    seen.append(pc)
                if mem[pc] == 0x76 and regs[IFF]:
                    h._fast_halt(); n += 1; continue
                opcodes[mem[pc]]()
                if regs[IFF] and regs[T] % fd < ia:
                    sim.accept_interrupt(regs, mem, pc)
                n += 1
            if 0x93D9 in seen:
                cell = 0x8000 + (m[0x8421] >> 2) * 32 + (m[0x8420] >> 2)
                print('  %-9s death on pass %2d (ctr %d) at (%d,%d); cell $%04X=$%02X; path %s'
                      % (label, i + 1, m[0x8491], m[0x8420], m[0x8421], cell, m[cell],
                         ' '.join('$%04X' % p for p in seen)))
                return
        print('  %-9s NO DEATH in %d passes' % (label, maxpass))

    drive('poke', lambda h: h.poke(0x8422, 0x00, 0x00), maxpass=3)
    drive('contact', lambda h: h.poke(0x8422, 0x00, 0x10), keys=('Q',))
    drive('drain', lambda h: h.poke(0x8422, 0x00, 0x01))


# ---------------------------------------------------------------- drop
def cmd_drop():
    print('  keys  cell   $84C0  $5BE8 record')
    for nk in (0, 1, 2, 3, 9):
        h = boot(); m = h.memobj.m
        pass_top(h)
        h.poke(0x8428, nk)
        h.poke(0x8422, 0x00, 0x00)
        pass_top(h)
        print('  %4d  $%02X    %d      %s' % (nk, m[0x8043], m[0x84C0],
              ' '.join('%02X' % b for b in m[0x5BE8:0x5BEB])))
    print('  ($22 = a class-0 tier-2 GENERATOR, $1F = a key, $32 = a hoard)')


# ---------------------------------------------------------------- rejoin
def two_player(h):
    """Player 2 joins the live level with his own fire key -- the game's own
    join path, not a poke."""
    pass_top(h)
    press(h, P2['fire'])
    pass_top(h); pass_top(h)
    h.ports.release_all()


def cmd_rejoin():
    h = boot(); m = h.memobj.m
    two_player(h)
    pass_top(h)
    h.poke(0x8424, 0x00, 0x12, 0x34); h.poke(0x8428, 3); h.poke(0x8429, 2)
    h.poke(0x8434, 0x22)
    before = bytes(m)
    h.poke(0x8422, 0x00, 0x00)
    for _ in range(4):
        pass_top(h)
    press(h, P1['shift'])                       # commit the name
    pass_top(h); pass_top(h)
    h.ports.release_all()
    press(h, P1['fire'])                        # rejoin
    for _ in range(8):
        pass_top(h)
    h.ports.release_all()
    print('  p1 %s' % ' '.join('%02X' % b for b in m[0x8420:0x8440]))
    show_diff(before, bytes(m))


# ---------------------------------------------------------------- restart
def cmd_restart():
    h = boot(); m = h.memobj.m
    pass_top(h)
    before = bytes(m)
    h.poke(0x8422, 0x00, 0x00)
    run_until_pc(h, 0xB3B9)
    press(h, P1['shift'])
    run_until_pc(h, 0xB47B)
    h.ports.release_all()
    press(h, P1['fire'])
    for _ in range(8):
        pc, dt, n = run_until_pc(h, (0xB4AD, 0xB3D0, LOOP_TOP), limit=20_000_000)
        if pc == 0xB3D0:
            print('  level setup entered')
        if pc == LOOP_TOP:
            print('  live again after %.1f frames of reload' % (dt / FRAME_T))
            break
    h.ports.release_all()
    show_diff(before, bytes(m), maxrows=8)


# ---------------------------------------------------------------- keys
def cmd_keys():
    h = boot()
    pass_top(h)
    base = pickle.dumps(h.save_state())
    print('  key    $8427 (p1)  $8447 (p2)')
    for name, sel, bit in KEYS:
        h.load_state(pickle.loads(base))
        press(h, name)
        pass_top(h)
        a, b = h.memobj.m[0x8427], h.memobj.m[0x8447]
        if a or b:
            print('  %-6s   $%02X         $%02X' % (name, a, b))
    print('  bit 0 up, 1 down, 2 left, 3 right, 4 FIRE, 5 shift')


# ---------------------------------------------------------------- stack
CALLS = {0xCD, 0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC,
         0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF}
RETS = {0xC9, 0xC0, 0xC8, 0xD0, 0xD8, 0xE0, 0xE8, 0xF0, 0xF8}


def cmd_stack():
    """Manual Q19 / family C1: a RET that returns past an open frame has had a
    caller's return address discarded.  RETI/RETN and the ISR's own frame are
    tracked, or every interrupt shows up as a false discard."""
    def scan(h, ninstr, tag):
        sim = h.sim
        regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
        fd, ia = h.frame_duration, h.int_active
        shadow, found, n = [], {}, 0
        while n < ninstr:
            pc = regs[PC]
            op = mem[pc]
            sp0 = regs[SP]
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape(); n += 1; continue
            if op == 0x76 and regs[IFF]:
                h._fast_halt(); shadow.append((-1, regs[SP])); n += 1; continue
            opcodes[op]()
            sp1 = regs[SP]
            if op in CALLS and sp1 == (sp0 - 2) & 0xFFFF:
                shadow.append((regs[PC], sp1))
            elif ((op in RETS or (op == 0xED and mem[(pc + 1) & 0xFFFF] in (0x4D, 0x45)))
                    and sp1 == (sp0 + 2) & 0xFFFF):
                k, d = 0, []
                while shadow and shadow[-1][1] < sp0:
                    d.append(shadow.pop()[0]); k += 1
                if shadow and shadow[-1][1] == sp0:
                    shadow.pop()
                    if k:
                        found[(pc, k, tuple(d))] = found.get((pc, k, tuple(d)), 0) + 1
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
                shadow.append((-1, regs[SP]))
            n += 1
        print('  %-10s %7d instructions, %d discard sites' % (tag, n, len(found)))
        for (pc, k, d), c in sorted(found.items(), key=lambda kv: -kv[1]):
            print('     RET $%04X discarded %d frame(s) %s x%d'
                  % (pc, k, ' '.join('$%04X' % a for a in d), c))

    h = boot(); pass_top(h); scan(h, 400_000, 'idle')
    h = boot(); pass_top(h); press(h, P1['down']); scan(h, 400_000, 'walking')
    h = boot(); pass_top(h); h.poke(0x8422, 0, 0); scan(h, 400_000, 'death')


CMDS = {'seq': cmd_seq, 'routes': cmd_routes, 'drop': cmd_drop, 'rejoin': cmd_rejoin,
        'restart': cmd_restart, 'keys': cmd_keys, 'stack': cmd_stack}


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    names = list(CMDS) if what == 'all' else [what]
    for nm in names:
        print('=== %s ===' % nm)
        CMDS[nm]()
        print()


if __name__ == '__main__':
    main()
