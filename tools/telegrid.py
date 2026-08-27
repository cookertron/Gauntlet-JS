#!/usr/bin/env python3
"""
telegrid.py -- THE MOVE-GATE NEIGHBOURHOOD TABLE, printed from BOTH sides.

    python tools/telegrid.py z80  PACK SUB C7 X0 Y0 NX NY PASSES CAMX CAMY CTR
    node   tools/headless.js --telegrid PACK SUB C7 X0 Y0 NX NY PASSES CAMX CAMY CTR
    python tools/telegrid.py diff PACK SUB C7 [X0 Y0 NX NY PASSES ...]
    python tools/telegrid.py pads PACK SUB C7

Both sides take the SAME eleven arguments and print the SAME rows (manual
6.10), so the two outputs diff line for line and the pair can be kept as a
regression test.  `diff` runs both, prints the mismatching pairs and then the
SHAPE of the disagreement region.

(`tools/telegate.py` is the one-sided companion: it drives the real Z80 through
a handful of hand-planted teleport scenarios.  This tool is the two-sided
differential over a whole neighbourhood of a REAL level-8 dungeon.)

=============================================================================
WHY A TABLE AND NOT A POSITION
=============================================================================
A play report named ONE position -- level 8, player (4,78), beside a teleport
pad, "can rotate and fire but not move".  Manual D4 says that where two
predicates read the same data you enumerate the disagreements exhaustively
rather than chasing the one you were handed.  So this walks EVERY player
position on the 2-unit grid the player actually occupies (his move is
`INC C / INC C`, so his coordinates are always even) across a window several
cells wide in each direction -- INCLUDING ACROSS THE WRAP AT COLUMN 0/31 --
holds each of the four directions for PASSES main-loop passes from a clean
player block, and prints the whole trajectory.  What comes out is not "is this
position stuck", it is the SHAPE of the region that is, and the shape names the
rule.

=============================================================================
THE MAP IS DECODED ON BOTH SIDES, NEVER CAPTURED
=============================================================================
Both sides build the dungeon from the same tape record with

    flags bit 2 cleared          $9B5F's "one random $36 survives" is LD A,R
    level 1 (no $9BB7 objects)   $9BB7's extra objects are LD A,R too
    the mirrors applied by hand  $9C06 / $9C69, selected by C7

which is exactly the recipe `python tools/packgate.py all` scores 307/307 with,
so the two grids are identical by a test that already exists.  The FNV-1a
checksum of the 1024 cells goes in the HEADER line, and `diff` compares the
headers first: a decoder disagreement fails loudly up there instead of turning
into a phantom movement bug in the body.

WHICH DUNGEON.  The play report's three numbers -- level 8, player (4,78),
cell (1,19) reading $00 -- pick out exactly ONE of the 307 x 4 orientations.
Sweeping every record for "cell (1,19) is free AND the player standing on it
can reach a $30 through one of $A82F/$A85D's probes" returns pack 29 sub 1
with $84C7 = 3, and nothing else: a $30 at cell (2,19) with cell (2,20) free
below it, which is what lets $A82F's second, down-and-right probe pass.  That
is the default here.

=============================================================================
THE PER-TRIAL RESET
=============================================================================
The Z80 side loads build/state_48k.pkl, pokes the decoded grid over
$8000..$83FF, sets $8403 = 8, empties the actor list, settles the camera by
running the loop with no key held, and uses that as the base state for every
trial.  Per trial it restores that state and pokes only

    $8420/$8421  x, y                    $842D  0   the compass slot
    $842E  0   the walk phase and BOTH   $8436  0   the (IX+$16) countdown
           teleport bits ($A6B0's SET    $843D  0   the pending slot
           1, $B208's SET 2)             $8491  the pass counter
    $848B/$848C  the camera

and holds one direction key through the game's own $A4DD.  The camera is a
single settled value shared by the whole window and PASSED AS AN ARGUMENT to
both sides, because the camera is not a move gate: it enters only through
$B156, which registers a $30 pad in $5BD0 only while that tile is actually
being DRAWN.  That is the one place the camera can change the answer, and it
is stated here rather than hidden.

The trajectory token is `x,y:pend/tel`:
    pend   (IX+$1D) = $843D, the pending-interaction slot at the end of the pass
    tel    $842E AND 6 -- bit 1 is $A6B0's teleport arm, bit 2 is $B208's
           "teleport in flight".  Printing the byte rather than a boolean is
           what put the original finding IN the table instead of behind it:
           the engine used to model bit 1 as a boolean of its own and have no
           bit 2 at all, and 120 of these 1600 rows disagreed, every one of
           them with the Z80 mid-teleport.  Both bits now live in animCtl,
           exactly as they do in $842E, and the table closes to 0 of 1600.

THE TIE-BREAK IS FED IN, NOT MODELLED.  $B1B9 CALL $B575 ends `LD A,R / SUB L`,
so WHICH of several equally-near pads $B195 lands on is a draw off the refresh
register and no port can reproduce it.  The Z80 side stops at $B1CC -- the
`LD C,(IX)` that reads the chosen record -- and takes (IX - $5B00)/4; that list
is passed to headless.js as a 14th argument and consumed by the engine's
padPickOverride hook, one entry per DECISION.  Same shape as p2gate.py's
video-clock feed.  Without it the tied rows legitimately diverge and the table
reads 5 of 256 on the pack-5 window instead of 0.
"""
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import packdecode as PD                                          # noqa: E402
from harness import Harness, PC, T, IFF, IXh, IXl, TAPE_CALL_PC  # noqa: E402
from keyprobe import KEYS, keymask                               # noqa: E402
from sim_move import step_to_loop_top, DIRKEY, STATE             # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRS = (('U', 'up'), ('D', 'down'), ('L', 'left'), ('R', 'right'))
HEADLESS = os.path.join(HERE, 'headless.js')

# the player block, IX = $8420
P_X, P_Y = 0x8420, 0x8421
P_SLOT = 0x842D          # (IX+13) the compass slot
P_CTL = 0x842E           # (IX+14) walk phase, teleport bits 1 and 2
P_CTR16 = 0x8436         # (IX+$16) the exit / teleport dissolve countdown
P_PEND = 0x843D          # (IX+$1D) the pending-interaction slot
MAP = 0x8000
LEVEL = 0x8403
NACT = 0x8496            # (IY+$17) the actor count
ATAIL = 0x8494           # $5C00 + 4*count
SPAWNBASE = 0x84A0       # (IY+$21) $AA19's spawn threshold
PASSCTR = 0x8491
CAMX, CAMY = 0x848B, 0x848C
PADCOUNT = 0x84AC        # (IY+$2D) how many $30 pads $B156 registered
PADLIST = 0x5BD0         # four bytes each: x, y, free-neighbour mask, unused


# --------------------------------------------------------------------------
def build_grid(pack, sub, c7):
    """The 1024 cells, from the tape record alone.  `pack` is 1-based."""
    p = PD.load_pack(pack)
    lens = PD.sub_lengths(p)
    starts = [PD.HDR]
    for n in lens:
        starts.append(starts[-1] + n)
    body = p[starts[sub]:starts[sub] + lens[sub]]
    buf = bytearray(PD.FIRST_COPY + PD.SECOND_COPY)
    buf[:min(len(body), len(buf))] = body[:len(buf)]
    buf[1] &= ~0x04                       # $9B5F is an LD A,R draw: off
    mp, _info = PD.expand(buf, 0)
    if c7 & 2:
        PD.mirror_h(mp)                   # $98A2 -> $9C06
    if c7 & 1:
        PD.mirror_v(mp)                   # $98A5 -> $9C69
    return bytes(mp.cell), mp


def fnv1a(b):
    h = 0x811C9DC5
    for v in b:
        h = ((h ^ v) * 0x01000193) & 0xFFFFFFFF
    return h


def clear_actors(m):
    """EMPTY THE ACTOR LIST AND STOP IT REFILLING.

    MEASURED, and the reason the first version of this table printed 98
    phantom disagreements (manual G1: verify the measuring code before the
    measured code).  Zeroing $8496 once is NOT enough on a level >= 8: this
    dungeon has $20..$2E generator cells, `$8506 CALL $A9C2` sweeps them EVERY
    pass, and $AA1D's draw spawns fresh actors straight back into the list.
    With the list refilled, $A5F0 CALL $A97F -- the THIRD gate -- fires on the
    first pass of a trial and eats it, and every row then reads one pass behind
    the port for a reason that has nothing to do with teleporters.

    $AA19/$AA22 is `E = $84A0 - ((count>>3) AND 15) ; roll = rand ; JR nc` --
    it spawns only while roll < E -- so $84A0 = 0 makes E = 0 and no draw can
    ever be below it.  The port's generatorRoll() computes the same E from
    `spawnBase`, so setting it to 0 on both sides is ONE clamp, not two
    different ones, and it suppresses SPAWNING, not any rule this table
    measures.
    """
    m[NACT] = 0                                  # $8496 (IY+$17)
    m[ATAIL], m[ATAIL + 1] = 0x00, 0x5C          # $8494 = $5C00
    m[SPAWNBASE] = 0                             # $84A0 -> $AA22 never spawns


def settle(grid, x, y, settle_passes=60):
    """A base state with the grid in place, no actors and the camera converged
    on (x, y).  Returns (harness, saved state, camx, camy, pass counter)."""
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    m[MAP:MAP + 0x400] = grid
    m[LEVEL] = 8
    clear_actors(m)
    m[P_X], m[P_Y] = x, y
    h.ports.release_all()
    for _ in range(settle_passes):
        step_to_loop_top(h)
    clear_actors(m)                              # the settle can spawn: redo it
    return h, h.save_state(), m[CAMX], m[CAMY], m[PASSCTR]


def step_watch_pick(h, picks):
    """step_to_loop_top, but also record the ORIGINAL'S OWN TIE-BREAK.

    $B1B9 CALL $B575 ends `LD A,R / SUB L`, so which of several equally-near
    pads $B195 chooses is a draw off the refresh register and NO port can
    reproduce it.  What a port CAN be asked to reproduce is the rule, so this
    stops at $B1CC -- `LD C,(IX)`, the instruction that reads the chosen
    record -- and takes (IX - $5B00)/4, which is the index the machine drew.
    The list is handed to headless.js as its 14th argument and consumed by the
    engine's padPickOverride hook, the same shape as p2gate.py's clock feed.
    Confirmed on an instruction boundary: $B1CC is the target of $B1B7's
    `JR z` and of $B1C2's, and disassembling forward from $B195 lands on it.
    """
    sim = h.sim
    regs = sim.registers
    opcodes = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < 8_000_000:
        pc = regs[PC]
        if n and pc == 0x8503:
            return
        if pc == 0xB1CC:
            picks.append(((regs[IXh] << 8 | regs[IXl]) - 0x5B00) // 4)
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


def z80_rows(x0, y0, nx, ny, passes, camx, camy, ctr, base):
    h, st = base
    out = []
    picks = []
    for j in range(ny):
        y = (y0 + 2 * j) & 0x7F
        for i in range(nx):
            x = (x0 + 2 * i) & 0x7F
            for ch, name in DIRS:
                h.load_state(st)
                m = h.memobj.m
                clear_actors(m)
                m[P_X], m[P_Y] = x, y
                m[P_SLOT] = 0
                m[P_CTL] = 0                     # both teleport bits down
                m[P_CTR16] = 0
                m[P_PEND] = 0
                m[PASSCTR] = ctr & 0xFF
                m[CAMX], m[CAMY] = camx, camy
                sel, bit = KM[DIRKEY[name]]
                h.ports.press(sel, keymask(bit))
                toks = []
                for _ in range(passes):
                    step_watch_pick(h, picks)
                    toks.append('%d,%d:%02x/%x' % (m[P_X], m[P_Y], m[P_PEND],
                                                   m[P_CTL] & 6))
                out.append('%4d%4d %s  %s' % (x, y, ch, ' '.join(toks)))
    return out, picks


def header(pack, sub, c7, grid, x0, nx, y0, ny, passes, camx, camy, ctr):
    return ('# telegrid pack=%d sub=%d c7=%d grid=%08x x0=%d nx=%d y0=%d '
            'ny=%d passes=%d cam=%d,%d ctr=%d'
            % (pack, sub, c7, fnv1a(grid), x0, nx, y0, ny, passes,
               camx, camy, ctr))


BANNER = '#   x   y d  trajectory: x,y:pend/tel per pass'


def js_rows(argv):
    cmd = ['node', HEADLESS, '--telegrid'] + [str(a) for a in argv]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        raise RuntimeError('headless.js failed:\n' +
                           r.stdout[-3000:] + r.stderr[-3000:])
    lines = r.stdout.splitlines()
    k = next(i for i, l in enumerate(lines) if l.startswith('# telegrid'))
    return lines[k], lines[k + 2:]


# --------------------------------------------------------------------------
def shape(zr, jr):
    """Name the disagreement region rather than listing it."""
    bad = 0
    bydir, firstpass, ztel, ring = {}, {}, {}, []
    touched = 0        # trials whose Z80 trajectory enters the teleport chain
    off_chain = 0      # ...that disagree WITHOUT entering it
    for a, b in zip(zr, jr):
        # the row is '%4d%4d %s  %s': x 0..3, y 4..7, space, DIR at 9, then
        # two spaces, so the trajectory tokens start at column 12.
        head, ztoks = a[:12], a[12:].split()
        jtoks = b[12:].split()
        d = head[9]
        # THE ARMING RING: every trial whose FIRST pass records a $30 and
        # comes out of $A6B0 with bit 1 up.
        if ztoks and ztoks[0].endswith('/2') and ':30' in ztoks[0]:
            ring.append((int(head[:4]), int(head[4:8]), d))
        chain = any(not t.endswith('/0') for t in ztoks)
        if chain:
            touched += 1
        if a == b:
            continue
        if not chain:
            off_chain += 1
        bad += 1
        bydir[d] = bydir.get(d, 0) + 1
        k = next(i for i in range(min(len(ztoks), len(jtoks)))
                 if ztoks[i] != jtoks[i])
        firstpass[k + 1] = firstpass.get(k + 1, 0) + 1
        ztel[ztoks[k].rsplit('/', 1)[1]] = \
            ztel.get(ztoks[k].rsplit('/', 1)[1], 0) + 1
    return bad, bydir, firstpass, ztel, ring, touched, off_chain


def main():
    argv = sys.argv[1:]
    mode = argv[0] if argv else 'diff'
    d = [29, 1, 3, 120, 64, 20, 20, 6, -1, -1, -1]
    v = list(d)
    for i, a in enumerate(argv[1:]):
        v[i] = int(a)
    pack, sub, c7, x0, y0, nx, ny, passes, camx, camy, ctr = v

    grid, mp = build_grid(pack, sub, c7)

    if mode == 'pads':
        pads = [(k % 32, k // 32) for k, val in enumerate(grid)
                if (val & 0x7F) == 0x30]
        print('pack %d sub %d c7 %d: grid=%08x  player start %s'
              % (pack, sub, c7, fnv1a(grid), mp.player))
        print('%d teleport pads: %s' % (len(pads), pads))
        return 0

    base = None
    if camx < 0 or camy < 0 or ctr < 0:
        h, st, scx, scy, sctr = settle(grid, (x0 + nx) & 0x7F,
                                       (y0 + ny) & 0x7F)
        base = (h, st)
        camx = scx if camx < 0 else camx
        camy = scy if camy < 0 else camy
        ctr = sctr if ctr < 0 else ctr
    hdr = header(pack, sub, c7, grid, x0, nx, y0, ny, passes, camx, camy, ctr)
    jsargs = [pack, sub, c7, x0, y0, nx, ny, passes, camx, camy, ctr]

    if mode == 'js':
        jh, jr = js_rows(jsargs)
        print(jh)
        print(BANNER)
        for r in jr:
            print(r)
        return 0

    if base is None:
        h, st, _a, _b, _c = settle(grid, (x0 + nx) & 0x7F, (y0 + ny) & 0x7F)
        base = (h, st)
    zr, picks = z80_rows(x0, y0, nx, ny, passes, camx, camy, ctr, base)

    if mode == 'z80':
        print(hdr)
        print(BANNER)
        for r in zr:
            print(r)
        return 0

    jh, jr = js_rows(jsargs + [','.join(str(k) for k in picks)])
    print(hdr)
    print('# port  ' + jh[2:])
    if jh != hdr:
        print('!! HEADER MISMATCH -- the two sides did not build the same '
              'dungeon; the body below is meaningless')
    print(BANNER)
    n = 0
    for a, b in zip(zr, jr):
        if a != b:
            n += 1
            if n <= 24:
                print('Z80  ' + a)
                print('port ' + b)
    if len(zr) != len(jr):
        print('!! ROW COUNT %d vs %d' % (len(zr), len(jr)))
    bad, bydir, firstpass, ztel, ring, touched, off = shape(zr, jr)
    print('%d of %d rows disagree  (%d positions x 4 directions)'
          % (bad, len(zr), len(zr) // 4))
    print('# --- THE SHAPE ---')
    print('# %d rows enter the teleport chain on the Z80 ($842E bit 1 or 2 up '
          'on some pass)' % touched)
    print('# %d rows disagree WITHOUT ever entering it  <- must be 0: every '
          'other rule already agrees' % off)
    print('# by direction: ' + '  '.join('%s %d' % (k, bydir.get(k, 0))
                                         for k in 'UDLR'))
    print('# first divergent pass: ' +
          '  '.join('%d:%d' % kv for kv in sorted(firstpass.items())))
    print('# $842E AND 6 in the Z80 token that diverges: ' +
          '  '.join('%s:%d' % kv for kv in sorted(ztel.items())))
    print('# THE ARMING RING -- %d (position, direction) pairs whose FIRST '
          'pass records $30 and comes out of $A6B0 with bit 1 up:' % len(ring))
    for x, y, dd in ring:
        print('#   %3d,%3d %s  cell (%d,%d)' % (x, y, dd, x >> 2, y >> 2))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
