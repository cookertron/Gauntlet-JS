#!/usr/bin/env python3
"""
telecensus.py -- FREEZE THE ORIGINAL'S OWN ANSWER for the teleport chain.

    python tools/telecensus.py            rebuild build/telecensus.json
    python tools/telecensus.py --quick    a small sweep, for a smoke test
    node   tools/headless.js              replays it against the engine

This tool drives the REAL Z80 only.  It writes build/telecensus.json, which
tools/headless.js then reads and replays against the shipped engine -- the
same shape as build/levelcheck.json and the level closure test.  Nothing here
ever calls the code under test, so the expected values are measurements.

=============================================================================
WHY THIS EXISTS -- THE PLAY REPORT, AND WHAT SETTLED IT
=============================================================================
A play report said: level 8, player (4,78), cell (1,19) = $00, camera (2,60),
"the character gets stuck and can't move to an adjoining space; can rotate and
fire but not move".  The engine carried a PROVISIONAL note claiming the
original sticks too and that the port was therefore faithful.  It does not,
and it was not.  Driven here, on level 8 dungeons the game built for itself
through its own $B3D0 off the tape:

  * stepping onto a $30 costs ONE refused pass -- $A919 records, $A61A refuses
    the commit, $A65D -> $A673 -> $A6B0 SET 1,(IX+14).  That pass is faithful
    and the port always reproduced it.
  * the NEXT pass, $A4FF BIT 1,(IX+14) / $A503 JP nz,$B195 hands the whole
    move to the teleport machine, and $B195 ALWAYS resolves it.  With a second
    pad drawn it TELEPORTS ($B1FF), freezes four passes ($B20C) and walks on;
    with only the pad you are standing against drawn, $B246 discards its zero
    score ($B27C) and $B216/$B218 RES 1 clears the arm.  There is no path on
    the real machine that leaves the arm up.
  * so the original refuses you only IN THE PAD'S DIRECTION and only on
    alternate passes.  The port latched the flag for the rest of the level and
    refused all four directions for ever, which is the report verbatim.

The frozen tables below are what a fix has to keep reproducing.

=============================================================================
WHAT IS FROZEN, AND WHERE EACH NUMBER COMES FROM
=============================================================================
"census"  1452 observations of $84AC and every byte of $5BD0, over three real
          tape dungeons x a 22 x 22 sweep of camera positions.  Taken by
          pinning bit 1 of PLAYER 2's $844E -- which opens $B159's gate
          without arming player 1 and therefore without running $B195 at all,
          so the draw is observed and nothing else moves.  This is the table
          that closes "what IS $84AC", the question the old note got wrong.

"chain"   per-pass trajectories through the whole state machine for a set of
          scenarios including the reported square, with x, y, $843D, $842E,
          $8436 and $84AC on every pass, plus the original's own tie-break
          picks read at $B1CC.  A lone-pad scenario (every pad but one erased
          from a real dungeon) carries the BAIL arm, which is the genuine
          single-axis stall and must survive any fix.

"squares" the 17 bytes at $8469 that $B246 scores with, dumped from live RAM.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import packdecode as PD                                          # noqa: E402
from harness import Harness, PC, T, IFF, IXh, IXl, TAPE_CALL_PC  # noqa: E402
from keyprobe import KEYS, keymask                               # noqa: E402
from sim_move import step_to_loop_top, DIRKEY, STATE             # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}

P_X, P_Y = 0x8420, 0x8421
P_SLOT, P_CTL, P_CTR16, P_PEND = 0x842D, 0x842E, 0x8436, 0x843D
P2_CTL = 0x844E              # player 2's (IX+14): $B159's SECOND gate
MAP, LEVEL = 0x8000, 0x8403
NACT, ATAIL, SPAWNBASE = 0x8496, 0x8494, 0x84A0
PASSCTR = 0x8491
CAMX, CAMY = 0x848B, 0x848C
CAMTX, CAMTY = 0x848D, 0x848E
PADCOUNT, PADLIST = 0x84AC, 0x5BD0
SQTAB = 0x8469
LOOP_TOP = 0x8503

# the three dungeons the census sweep uses.  (pack, sub, c7), pack 1-based.
#   6/6/1   32 pads on a lattice -- the probe that pins the draw window
#   29/1/3  a pad at cell (2,19), i.e. the play report's own neighbour
#   2/5/1   the six-pad dungeon whose pad list the port's own level-8 draw
#           also produces: (31,8) (30,12) (2,20) (1,24) (20,30) (23,31)
CENSUS_MAPS = ((6, 6, 1), (29, 1, 3), (2, 5, 1))


def build_grid(pack, sub, c7):
    p = PD.load_pack(pack)
    lens = PD.sub_lengths(p)
    starts = [PD.HDR]
    for n in lens:
        starts.append(starts[-1] + n)
    body = p[starts[sub]:starts[sub] + lens[sub]]
    buf = bytearray(PD.FIRST_COPY + PD.SECOND_COPY)
    buf[:min(len(body), len(buf))] = body[:len(buf)]
    buf[1] &= ~0x04                       # $9B5F is an LD A,R draw: off
    mp, _ = PD.expand(buf, 0)
    if c7 & 2:
        PD.mirror_h(mp)                   # $98A2 -> $9C06
    if c7 & 1:
        PD.mirror_v(mp)                   # $98A5 -> $9C69
    return bytes(mp.cell)


def fnv1a(b):
    h = 0x811C9DC5
    for v in b:
        h = ((h ^ v) * 0x01000193) & 0xFFFFFFFF
    return h


def clear_actors(m):
    """$8496 = 0 is NOT enough on a level >= 8: $8506 CALL $A9C2 sweeps the
    $20..$2E generator cells every pass and $AA1D's draw refills the list.
    $84A0 = 0 makes $AA19's E zero so $AA22's `roll >= E` refuses every draw.
    The engine's generatorRoll() computes the same E from `spawnBase`, so this
    is ONE clamp on both sides and it suppresses spawning, not any rule."""
    m[NACT] = 0
    m[ATAIL], m[ATAIL + 1] = 0x00, 0x5C
    m[SPAWNBASE] = 0


def fresh(grid):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    assert m[LEVEL] == 1, 'the baseline state is not level 1'
    m[MAP:MAP + 0x400] = grid
    m[LEVEL] = 8
    clear_actors(m)
    return h


def settle(h, x, y, passes=140):
    """Let the game's own $B58C converge the camera on (x, y)."""
    m = h.memobj.m
    h.ports.release_all()
    m[P_X], m[P_Y] = x, y
    last = None
    for _ in range(passes):
        step_to_loop_top(h)
        cam = (m[CAMX], m[CAMY])
        if cam == last:
            break
        last = cam
    clear_actors(m)
    return (m[CAMX], m[CAMY])


def step_pick(h, picks):
    """One $8503 -> $8503 pass, stopping at $B1CC to read the tie-break.

    $B1B9 CALL $B575 ends `LD A,R / SUB L`, so WHICH of several equally-near
    pads the machine lands on is a draw off the refresh register.  $B1CC is
    `LD C,(IX)` -- the instruction that reads the chosen record -- so
    (IX - $5B00)/4 IS the index it drew.  It is recorded rather than modelled.
    """
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < 8_000_000:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return
        if pc == 0xB1CC:
            picks.append(((regs[IXh] << 8 | regs[IXl]) - 0x5B00) // 4)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('no main-loop top in 8M instructions')


# --------------------------------------------------------------------------
def census(step=6):
    """$84AC and $5BD0 as a function of (dungeon, camera).

    The camera is POKED and then one pass is run; $850C CALL $B58C steps it
    up to 2 units toward its target BEFORE $850F..$8518 paint, so the value
    recorded here is the post-step one -- the camera the draw actually used.
    """
    out = []
    for (pack, sub, c7) in CENSUS_MAPS:
        grid = build_grid(pack, sub, c7)
        h = fresh(grid)
        m = h.memobj.m
        settle(h, 60, 60)
        base = h.save_state()
        for camy in range(0, 128, step):
            for camx in range(0, 128, step):
                h.load_state(base)
                clear_actors(m)
                m[P_X], m[P_Y] = 60, 60
                m[CAMX], m[CAMY] = camx, camy
                # $B159's gate is "SOME player has bit 1 up".  Pinning PLAYER
                # 2's opens it without arming player 1, so $A4FF never jumps
                # and the draw is observed with nothing else running.
                m[P2_CTL] |= 2
                step_to_loop_top(h)
                n = m[PADCOUNT]
                recs = [[m[PADLIST + 4 * i], m[PADLIST + 4 * i + 1],
                         m[PADLIST + 4 * i + 2]] for i in range(n)]
                out.append(dict(pack=pack, sub=sub, c7=c7,
                                cam=[m[CAMX], m[CAMY]], px=60, py=60,
                                n=n, recs=recs))
        print('  census %d/%d/%d done (%d rows)' % (pack, sub, c7, len(out)))
    return out


# scenarios: (name, pack, sub, c7, x, y, dir, passes, erase-all-pads-but,
#             {cell: value} planted)
SCENARIOS = (
    # THE PLAY REPORT'S OWN SQUARE.  Pack 29 sub 1 c7=3 puts a $30 at cell
    # (2,19) with cell (1,19) reading $00, and the camera the game settles on
    # for a player at (4,78) is (2,60) -- the report's third number, which
    # nobody arranged.  Holding RIGHT is the only horizontal approach that
    # arms: y = 78 gives ny = 76/80 with bit 1 clear, so neither vertical
    # handler even probes the map.
    ('report-right',   29, 1, 3,  4, 78, 'right', 10, None, None),
    ('report-left',    29, 1, 3,  4, 78, 'left',  10, None, None),
    ('report-up',      29, 1, 3,  4, 78, 'up',    10, None, None),
    ('report-down',    29, 1, 3,  4, 78, 'down',  10, None, None),
    # THE BAIL ARM -- the genuine quirk.  Every pad but the one at (2,19) is
    # erased, so $B246 sees only the pad the player is standing against, whose
    # score is 0, and $B27C throws it away: $84B0 comes back 0, $B216/$B218
    # clear the arm and the player is refused AGAIN next pass.  The result is
    # a single-axis stall with $842E bit 1 alternating -- which is what the
    # old note measured and mis-read as "the original sticks too".
    ('lone-right',     29, 1, 3,  4, 78, 'right', 12, (2, 19), None),
    ('lone-up',        29, 1, 3,  4, 78, 'up',    12, (2, 19), None),
    ('lone-down',      29, 1, 3,  4, 78, 'down',  12, (2, 19), None),
    # a second, independent dungeon, and one whose pad list the port's own
    # level-8 draw also produces.
    ('sixpad-down',     2, 5, 1,  4, 78, 'down',  14, None, None),
    ('sixpad-up',       2, 5, 1,  4, 78, 'up',    14, None, None),
    # the 32-pad lattice: a dungeon where a teleport always has partners.
    ('lattice-down',    6, 6, 1,  4, 74, 'down',  12, None, None),
    # $B232 SUB $36 / JP $A687 -- TELEPORTING ONTO AN EXIT EXITS THE LEVEL,
    # and it works only because $A4FF's JP bypassed $A514, so the memo $B1FF
    # wrote survives all four transit passes.  MEASURED: the flight from
    # (4,78) lands on cell (3,24), so a $36 planted there makes pend read $36
    # the whole way; on the release pass (IX+11) bit 6 sets and (IX+$16)
    # becomes $18, and 24 passes later the level changes.  A port that clears
    # `pend` per pass during the flight silently loses this.
    # 29 passes and not more ON PURPOSE: the arm, the four transit passes and
    # the whole of $A693's $18 countdown are $B232's business, and they are
    # what is compared.  On pass 30 $8537 CALL $94AE hands over to the NEXT
    # LEVEL -- on the Z80 the player reappears at (12,116) and $8403 changes
    # -- which is the level BUILD, gated by the 307-record closure test and by
    # tools/levelgate.py, not by this table.  Comparing it here would compare
    # two level starts under the name of a teleport.
    ('exit36',         29, 1, 3,  4, 78, 'right', 29, None, {(3, 24): 0x36}),
)


def chain():
    out = []
    for (name, pack, sub, c7, x, y, d, passes, keep, plant) in SCENARIOS:
        grid = bytearray(build_grid(pack, sub, c7))
        if keep is not None:
            for i in range(1024):
                if grid[i] == 0x30 and (i & 31, i >> 5) != keep:
                    grid[i] = 0
        for (c, r), v in (plant or {}).items():
            grid[r * 32 + c] = v
        h = fresh(bytes(grid))
        m = h.memobj.m
        cam = settle(h, x, y)
        clear_actors(m)
        m[P_X], m[P_Y] = x, y
        m[P_SLOT] = 0
        m[P_CTL] = 0
        m[P_CTR16] = 0
        m[P_PEND] = 0
        sel, bit = KM[DIRKEY[d]]
        h.ports.press(sel, keymask(bit))
        picks, rows = [], []
        for _ in range(passes):
            step_pick(h, picks)
            rows.append([m[P_X], m[P_Y], m[P_PEND], m[P_CTL] & 6,
                         m[P_CTR16], m[PADCOUNT], m[0x842B] & 0x40])
        h.ports.release_all()
        out.append(dict(name=name, pack=pack, sub=sub, c7=c7,
                        grid=fnv1a(bytes(grid)),
                        x=x, y=y, dir=d, cam=list(cam), passes=passes,
                        keep=list(keep) if keep else None,
                        plant=[[c, r, v] for (c, r), v in (plant or {}).items()],
                        picks=picks, rows=rows))
        print('  chain %-14s %s' % (name, rows[:3]))
    return out


def main():
    quick = '--quick' in sys.argv
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    squares = list(h.memobj.m[SQTAB:SQTAB + 17])
    del h
    doc = dict(
        note='frozen by tools/telecensus.py from the REAL Z80; replayed by '
             'node tools/headless.js.  Never regenerate from the engine.',
        squares=squares,
        census=census(12 if quick else 6),
        chain=chain(),
    )
    path = os.path.join(ROOT, 'build', 'telecensus.json')
    json.dump(doc, open(path, 'w'))
    print('wrote %s: %d census rows, %d chain scenarios'
          % (path, len(doc['census']), len(doc['chain'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
