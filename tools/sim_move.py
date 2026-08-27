#!/usr/bin/env python3
"""
sim_move.py -- drive the REAL Z80 from the saved live-level state, holding one
direction, and print (pass, x, y) per MAIN-LOOP PASS.

    python tools/sim_move.py right 24
    node   tools/headless.js --table right 24

Both print the same table with the same arguments, so the two outputs can be
diffed directly and kept as a regression test (manual 6.10).  The first
differing row names the pass to investigate.

=============================================================================
THE SAMPLER IS THE LOOP TOP $8503, NOT A FOUR-VIDEO-FRAME WINDOW
=============================================================================
This tool used to advance the machine by `run_frames(h, 4)` and read the
coordinates afterwards, on the strength of Q10's "one main-loop pass = four
video frames".  That measurement is right on average and WRONG as a sampler,
and it manufactured a two-row phantom in the 60-pass DOWN table that three
separate investigations then tried to explain as a missing game rule.

Measured here, on the real Z80, before changing anything:

  * $8503 is visited EXACTLY ONCE PER PASS.  Counting increments of the pass
    counter $8491 between consecutive visits gives {1: 97} over 97 passes --
    never 0, never 2.  So "step until PC == $8503" is a sound one-per-pass
    sampler by construction, whatever the pass costs in frames.
  * A pass is NOT four frames.  Over the same 97 passes the histogram of
    frames per pass is
        3.92:1  3.94:1  3.97:38  3.98:1  4.00:3  4.02:1  4.03:45  4.06:1
        4.96:1  4.97:4  5.03:1
    -- a mean of 4.0 with a tail out to 5.03.  Holding DOWN, pass 58 (the key
    at cell (3,31), map value $1F) costs 5.03 frames, so from row 59 onward
    every 4-frame window lands INSIDE the previous pass and the old table
    printed y=120 three times instead of twice.
  * The overrun is not the pickup: with the actors disabled no pass in the run
    exceeds 4.03 frames, and erasing the key cell leaves the long passes in
    place.

The engine's onePass() is one main-loop pass, so pass i on this side must be
pass i on that side; anchoring on a PC is what makes that true.  The frame
column is printed as well, because a pass over 4.5 frames is now a real,
watchable event rather than a rounding error.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC, FRAME_T   # noqa: E402
from keyprobe import KEYS, keymask                               # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
# player 1's direction keys, measured by tools/extract.py:
#   1=$01 up  Q=$02 down  S=$04 left  D=$08 right
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
"""THE BASELINE IS THE 48K/BEEPER BRANCH.

build/state_48k.pkl, not build/state_charsel.pkl.  The tape probes the
machine at boot ($CE50, LDIR'd to $61A8 and called at $C238) and stores the
answer in RAM $FFFD ($C242); $BEB9 CALL $BF21 then patches ten bytes and
picks ONE of the game's two complete sound drivers.  The old capture was
taken with the front end SKIPPED, so $FFFD held the loader stub's padding
byte $2A -- non-zero -- and every measurement this project made ran on the
128K/AY arm BY ACCIDENT.  build/state_48k.pkl is built by tools/boot48.py,
which restores the loader's real ORDER (block A to $8600, RUN THE PROBE,
then blocks B and C on top) so that $BF21 reads a $FFFD the game itself
wrote.  No branch byte is poked anywhere.

The two states are anchored at the SAME instant -- PC=$ABA1, ($8491)=$42,
dungeon 1, player at (12,8), health $1997, 63 actors -- so the opening
step_to_loop_top() below lands on the same pass top from either, and the
four direction tables are directly comparable.  MEASURED, and this is why
the switch is safe: `python tools/gate48.py points` runs the 48K arm against
its AY twin over 4 directions x {30,60,100,160} passes x {actors on, actors
off} = 2,800 rows and reports 0 mismatching.  The branch changes the CLOCK
and the ENTROPY, not the trajectory.
"""
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
LOOP_TOP = 0x8503                      # the main loop, $8503..$855C


def step_to_loop_top(h):
    """Run until PC reaches $8503 again.  Returns the T-states it took.

    A custom step loop, so it must handle the two cases the harness's own
    stepper does: the tape breakpoint and a HALT with interrupts enabled.
    """
    sim = h.sim
    regs = sim.registers
    opcodes = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0 = regs[T]
    n = 0
    while n < 8_000_000:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return regs[T] - t0
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            n += 1
            continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('no main-loop top in 8M instructions')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    # --frames adds the cost of each pass in video frames.  It is OFF by
    # default so that this table and `node tools/headless.js --table` can be
    # diffed line for line; the engine has no T-state clock and cannot print
    # that column.
    show_frames = '--frames' in sys.argv
    direction = args[0] if args else 'right'
    passes = int(args[1]) if len(args) > 1 else 24
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    sel, bit = KM[DIRKEY[direction]]
    h.ports.press(sel, keymask(bit))
    step_to_loop_top(h)                # align on the loop before sampling
    print('pass  x   y   blocked')
    for i in range(passes):
        dt = step_to_loop_top(h)
        m = h.memobj.m
        x, y = m[0x8420], m[0x8421]
        # (IX+$1D) is the pending-interaction slot; it is non-zero only on a
        # pass the interaction chain consumed, which is exactly the pass the
        # engine prints "interact $xx" on.
        pend = m[0x843D]
        note = '-' if not pend else f'interact ${pend:02x}'
        tail = f'   {dt/FRAME_T:.2f}f' if show_frames else ''
        print(f'{i+1:>4}{x:>4}{y:>4}   {note}{tail}')


if __name__ == '__main__':
    main()
