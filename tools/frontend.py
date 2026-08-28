#!/usr/bin/env python3
"""
frontend.py -- drive BLOCK A, the front end nobody in this project had ever
run, and read out what it leaves in RAM.

The loader calls it once (`$FF0F CALL $C1F2`) and block C then lands on top of
it, so the only things block A can hand to the game are the bytes ABOVE
$FB76.  There are five, and this tool watches all of them:

    $FFFB   player 2's control method   ($C51D, from $C808)
    $FFFC   player 1's control method   ($C508, from $C808)  -- $8560 dispatches
    $FFFD   the SOUND BRANCH            ($C242, from the $7FFD paging probe)
    $FFFE   player 2's character index  ($C449 one player / $C4E3 two)
    $FFFF   player 1's character index  ($C42C, from $C7FD)

$FFFE and $FFFF were the two long-standing open items: tools/fixchar.py had to
REPAIR the sprite bank because the captured state holds the loader stub's
padding $2A in both.  The code that fills them, read out of block A:

    $C426  CALL $C75B              the character picker; it leaves the choice
    $C429  LD A,($C7FD)            in $C7FD
    $C42C  LD ($FFFF),A            <- PLAYER 1
    $C432  LD C,A / INC A / AND 3 / LD ($C7FD),A
    $C439  LD A,($C7FF) / DEC A / JR nz,$C44F        two players?
    $C43F  LD A,R / AND 3          ONE PLAYER: player 2's character is a
    $C443  CP C / JR nz,$C449      REFRESH-REGISTER DRAW, forced not to equal
    $C446  INC A / AND 3           player 1's
    $C449  LD ($FFFE),A            <- PLAYER 2
    $C44F  ... "PLAYER TWO CHOOSE" ... $C4DD CALL $C75B / $C4E3 LD ($FFFE),A

so in a ONE-PLAYER game $FFFE is unreproducible in the Q18 sense and is
merely "not player 1's character".

Usage:
    python tools/frontend.py             drive it with SPACE held
    python tools/frontend.py --keys ...  hold other keys as well
    python tools/frontend.py --shot N    also write build/frontend_N.png
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import PC, T, IFF, FRAME_T, R                          # noqa: E402
from probe48 import stage_a                                         # noqa: E402
from keyprobe import KEYS, keymask                                  # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
WATCH_LO, WATCH_HI = 0xFFFB, 0x10000
CHARNAME = {0: 'WARRIOR (red)', 1: 'VALKYRIE (cyan)', 2: 'WIZARD (yellow)',
            3: 'ELF (green)'}
CTRL = {0: 'method 0', 1: 'method 1', 2: 'method 2', 3: 'method 3'}

# every PC in block A that writes one of the five bytes
WRITERS = {0xC242: '$FFFD  sound branch, from the $7FFD probe',
           0xC42C: '$FFFF  player 1 character, from $C7FD',
           0xC449: '$FFFE  player 2 character, ONE player: LD A,R & 3',
           0xC4E3: '$FFFE  player 2 character, TWO players: from $C7FD',
           0xC508: '$FFFC  player 1 control method, from $C808',
           0xC51D: '$FFFB  player 2 control method, from $C808'}


def run(h, limit, targets=(), tap=None, period=8 * 69888, trace=None):
    """Step block A.  It sets IM 2 with I=$FD and a $EEEE vector of its own
    ($C27C..$C29B), so interrupts are left ON.

    `tap` is the key that is pressed and released alternately every `period`
    T-states.  A HELD key is not enough to drive this front end: $C224 and
    $C4F5/$C555 wait for SPACE to be PRESSED while $C86C
    (`BIT 0,(IX+7) / JR z`) waits for it to be RELEASED, so anything held for
    ever deadlocks in one or the other.  Measured: SPACE held from $C1F2 gets
    as far as $C870 and stops there for good.
    """
    targets = set(targets)
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    phase = -1
    while n < limit:
        pc = regs[PC]
        if n and pc in targets:
            return ('target', n)
        if trace is not None and pc in trace:
            trace[pc] = trace[pc] + 1 if trace[pc] else 1
        if tap is not None:
            ph = (regs[T] // period) & 1
            if ph != phase:
                phase = ph
                h.ports.release_all()
                if ph:
                    sel, bit = KM[tap]
                    h.ports.press(sel, keymask(bit))
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return ('limit', n)


def enumerate_p2():
    """$C43F LD A,R / AND 3 / CP C / JR nz / INC A / AND 3, over every R.

    Manual: ENUMERATE a formula's reachable outputs under its guard rather
    than arguing about them.
    """
    print('$C43F, player 2\'s character in a ONE-player game, over all 256 R:')
    for c in range(4):
        hist = {}
        for r in range(256):
            a = r & 3
            if a == c:
                a = (a + 1) & 3
            hist[a] = hist.get(a, 0) + 1
        print(f'   player 1 = {c} {CHARNAME[c]:<18} -> ' +
              '  '.join(f'{k}:{v/256:.2f}' for k, v in sorted(hist.items())))
    print('   so it is never player 1\'s character, and (p1+1)&3 is twice as')
    print('   likely as either of the other two.  The DRAW itself is the')
    print('   refresh register -- Q18\'s class, not reproducible.')


def tapn(h, key, times, frames=6):
    """Press and release `key` `times` times, `frames` video frames each."""
    for _ in range(times):
        sel, bit = KM[key]
        h.ports.press(sel, keymask(bit))
        _advance(h, frames)
        h.ports.release_all()
        _advance(h, frames)


def _advance(h, frames):
    t0 = h.regs[T]
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    while regs[T] - t0 < frames * 69888:
        pc = regs[PC]
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)


def sweep(limit):
    """Drive the character picker EXPLICITLY.  Its keys, read out of $C7BF:

        $C7BF  BIT 0,(IX+7)   SPACE  -> select
        $C7C4  BIT 4,(IX+3)   key 5  -> next character  (skipping $C7FE)
        $C7E0  BIT 2,(IX+4)   key 8  -> previous

    so pressing 5 k times before SPACE should land on character k.
    """
    print('\nDRIVING THE CHARACTER PICKER EXPLICITLY (key 5 steps, SPACE picks)')
    for k in range(4):
        h = stage_a()
        h.regs[12] = (h.regs[12] - 2) & 0xFFFF      # push the loader's own
        h.memobj.m[h.regs[12]] = 0x12               # return address $FF12
        h.memobj.m[h.regs[12] + 1] = 0xFF
        h.memobj.watch(WATCH_LO, WATCH_HI)
        # SPACE past "STOP TAPE AND PRESS SPACE" and the title pages
        reason, n = run(h, limit, targets=(0xC426,), tap='SPACE')
        if reason != 'target':
            print(f'   k={k}: never reached $C426'); continue
        tapn(h, '5', k)
        reason, n = run(h, limit, targets=(0xC429,), tap='SPACE')
        reason2, n2 = run(h, limit, targets=(0xFF12,), tap='SPACE')
        h.memobj.unwatch()
        m = h.memobj.m
        print(f'   5 pressed {k}x -> $FFFF={m[0xFFFF]} '
              f'({CHARNAME.get(m[0xFFFF], "?"):<18}) $FFFE={m[0xFFFE]} '
              f'({CHARNAME.get(m[0xFFFE], "?"):<18}) $FFFC={m[0xFFFC]} '
              f'$FFFB={m[0xFFFB]} $FFFD={m[0xFFFD]}   '
              f'{"returned to the loader at $FF12" if reason2 == "target" else "stopped $%04X" % h.pc()}')


def main():
    args = sys.argv[1:]
    keys = ['SPACE']
    limit = 30_000_000
    if '--keys' in args:
        keys = args[args.index('--keys') + 1].split(',')
    if '--limit' in args:
        limit = int(args[args.index('--limit') + 1], 0)

    h = stage_a()
    print(f'block A at $C1F2, tapping {keys[0]} on and off every 8 frames')
    print('  before: ' + ' '.join(f'(${a:04X})=${h.memobj.m[a]:02X}'
                                  for a in range(WATCH_LO, 0x10000)))
    h.memobj.watch(WATCH_LO, WATCH_HI)
    reason, n = run(h, limit, tap=keys[0])
    h.memobj.unwatch()
    print(f'  ran {n} instructions ({h.regs[T]/FRAME_T:.1f} video frames), '
          f'stopped: {reason}, PC=${h.pc():04X}')
    print(f'  {len(h.memobj.log)} writes to $FFFB..$FFFF:')
    for pc, a, v in h.memobj.log:
        print(f'    (${a:04X}) <- ${v:02X}   from PC=${pc:04X}   '
              f'{WRITERS.get(pc, "")}')
    m = h.memobj.m
    print('  after:  ' + ' '.join(f'(${a:04X})=${m[a]:02X}'
                                  for a in range(WATCH_LO, 0x10000)))
    print()
    print(f'  $FFFF player 1 character = {m[0xFFFF]} '
          f'{CHARNAME.get(m[0xFFFF], "OUT OF RANGE")}')
    print(f'  $FFFE player 2 character = {m[0xFFFE]} '
          f'{CHARNAME.get(m[0xFFFE], "OUT OF RANGE")}')
    print(f'  $FFFC player 1 control   = {m[0xFFFC]}')
    print(f'  $FFFB player 2 control   = {m[0xFFFB]}')
    print(f'  $FFFD sound branch       = {m[0xFFFD]} '
          f'({"48K / BEEPER" if m[0xFFFD] == 0 else "128K / AY"})')
    print()
    enumerate_p2()
    if '--sweep' in args:
        sweep(limit)
    return h


if __name__ == '__main__':
    main()
