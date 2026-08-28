#!/usr/bin/env python3
"""
actormodel.py -- a REFERENCE IMPLEMENTATION of the actor update, and the
differential test that earned it.

    python tools/actormodel.py --verify                 # down, from the save
    python tools/actormodel.py --verify --key D --poke 86,60 --passes 200
    python tools/actormodel.py --coin                   # does the RNG matter?

The routine being modelled is `$ABFF..$AD1B`, which the game installs as a
CALL at `$A21E` (patched by `$ABB4`) inside the *draw* routine `$A1DA`, so an
actor is updated only on the passes on which it is drawn.  Everything here is
read from `build/live_cs.bin` and then checked against the running Z80: the
differential in `--verify` compares the whole 4-byte record after the update.

    key      passes  updates  matching  mismatching
    -        200     2368     2368      0
    Q down   200     3536     3536      0
    1 up     200     1051     1051      0
    S left   200     1368     1368      0
    D right  200     5124     5124      0
                     -----    -----     -
                     13447    13447     0

with the branch census `--verify` prints.  Two inputs are supplied by the
harness as an ORACLE and are NOT modelled, because they cannot be: `$AC25`
and `$AC4C` both do `LD A,R` and use the Z80's refresh register as a coin.
See `--coin`.
"""
import os
import pickle
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

FRAME_T = 69888
CENSUS = Counter()

# --- addresses ------------------------------------------------------------
ACTORS, COUNT, TAIL = 0x5C00, 0x8496, 0x8494
PASSC = 0x8491                     # (IY+$12)
GFLAGS = 0x847E                    # (IY-1): bit2 animation tick, bit3 magic
DIRMASK = 0x8418                   # 8 masks: bit0 up bit1 down bit2 L bit3 R
DEFLECT = 0x8460                   # 9 bytes, the wall-follow table
P1, P2 = 0x8420, 0x8440


def norm(a):
    """$AD37: fold a 7-bit wrapped difference into a signed byte [-64,63]."""
    if a < 0x80:
        return a if a < 0x40 else a ^ 0x80
    return a if a >= 0xC0 else a ^ 0x80


def choose_dir(m, cx, cy):
    """$AD77 -> $AD1C -> $AD9B.  Returns (compass 0..7, Manhattan distance).
    0 N, 1 NE, 2 E, 3 SE, 4 S, 5 SW, 6 W, 7 NW -- the same eight slots the
    player's frame table uses."""
    px, py = m[P1], m[P1 + 1]
    L = norm((cx - px) & 0xFF)
    H = norm((cy - py) & 0xFF)
    dx = L if L < 0x80 else 256 - L
    dy = H if H < 0x80 else 256 - H
    dist = (dx + dy) & 0xFF
    if H == 0:
        d = 2 if (L & 0x80) else 6
    elif L == 0:
        d = 0 if not (H & 0x80) else 4
    elif L & 0x80:
        d = 3 if (H & 0x80) else 1
    else:
        d = 5 if (H & 0x80) else 7
    return d, dist


def probe(m, x, y, face):
    """$ADF8.  Step 2 units per set bit of the mask, then test up to FOUR map
    cells -- the actor's 2x2 straddle -- and report blocked if ANY is
    non-zero.  Actors are stopped by every non-zero map value; they have no
    door rule.  The pointer walk wraps the 32x32 grid on both axes."""
    mask = m[DIRMASK + (face & 7)]
    B, C = y, x
    if mask & 1:
        B = (B - 2) & 0x7F
    if mask & 2:
        B = (B + 2) & 0x7F
    if mask & 4:
        C = (C - 2) & 0x7F
    if mask & 8:
        C = (C + 2) & 0x7F
    base = ((B & 0x7C) << 3) + ((C & 0x7C) >> 2)          # $AE6D
    H, L = 0x80 + (base >> 8), base & 0xFF
    if m[(H << 8) | L]:
        return True, C, B
    if C & 3:
        L2 = (L + 1) & 0xFF
        if (L2 & 0x1F) == 0:
            L2 = (L2 - 0x20) & 0xFF
        if m[(H << 8) | L2]:
            return True, C, B
        L = L2
    if not (B & 3):
        return False, C, B
    t = L + 0x20
    if t > 0xFF:
        L = t & 0xFF
        H = (H + 1) & 0xFF
        if ((H - 4) & 0xFF) == 0x80:
            H = 0x80
    else:
        L = t
    if m[(H << 8) | L]:
        return True, C, B
    if not (C & 3):
        return False, C, B
    L2 = (L - 1) & 0xFF
    if ((~L2) & 0x1F) == 0:
        L2 = (L2 + 0x20) & 0xFF
    return (m[(H << 8) | L2] != 0), C, B


def touch(m, C, B):
    """$AEA0: does the candidate square overlap a live player (7x7)?"""
    if not (m[0x842B] & 0x80):
        if (((m[P1] + 3 - C) & 0x7F) < 7 and ((m[P1 + 1] + 3 - B) & 0x7F) < 7):
            return True
    if m[0x844B] & 0x80:
        return False
    return (((m[P2] + 3 - C) & 0x7F) < 7 and ((m[P2 + 1] + 3 - B) & 0x7F) < 7)


def scan(m, C, B):
    """$A97F, the same 7x7 actor scan the player's move goes through.  Note
    the ASYMMETRY: x is compared modulo 128, y is a plain 8-bit subtract."""
    c = (C - 3) & 0x7F
    ycmp = (B - 3) & 0x7F
    for k in range(m[COUNT]):
        a = ACTORS + 4 * k
        if ((m[a] - c) & 0x78) == 0 and ((m[a] - c) & 0x7F) != 7:
            if 0 <= m[a + 1] - ycmp < 7:
                return True
    return False


def update(m, ix, r1, r2):
    """$ABFF..$AD13 for one actor at `ix`.  r1/r2 are the two `LD A,R` coins.
    Returns the new 4-byte record, or None for a branch outside this model
    (deletion, the ranged attack, the magic sweep)."""
    x, y, st, fl = m[ix], m[ix + 1], m[ix + 2], m[ix + 3]
    if m[GFLAGS] & 0x08:
        CENSUS['magic sweep $AF5D (unmodelled)'] += 1
        return None
    E = st
    afacing = st & 7
    if not ((((ix & 0xFF) >> 2) ^ m[PASSC]) & 1):     # $AC0E round robin
        if E < 0xA0:
            CENSUS['not this actor\'s pass'] += 1
            return _tail(m, x, y, E, fl)
        if not (m[PASSC] & 2):
            CENSUS['not this actor\'s pass (class>=5)'] += 1
            return _tail(m, x, y, E, fl)
        CENSUS['class>=5 extra turn'] += 1
    else:
        CENSUS['this actor\'s pass'] += 1
    if not (r1 & 1):                                  # $AC25 coin
        CENSUS['re-aimed at the player'] += 1
        D, dist = choose_dir(m, x, y)
        if (E & 0xE0) == 0x60 and dist < 12:
            D ^= 4                                    # $AC3C class 3 flees
        E &= 0xF8
        afacing = D                                   # A' at $AC45
        if (fl & 0x20) and (r2 & 3):                  # blocked last time
            D = m[DEFLECT + D + (1 if (fl & 0x10) else 0)]
            CENSUS['deflected by the $8460 wall-follow table'] += 1
        E |= D
    else:
        CENSUS['kept its facing (coin)'] += 1
    blocked, C, B = probe(m, x, y, E & 7)
    CENSUS['map probe blocked' if blocked else 'map probe free'] += 1
    if not blocked:
        if touch(m, C, B):
            CENSUS['touched a player ($AEA0, unmodelled)'] += 1
            return None
        save = m[ix + 1]
        m[ix + 1] = 0                                 # $AC73 hide self
        hit = scan(m, C, B)
        m[ix + 1] = save
        CENSUS['actor scan blocked it' if hit else 'MOVED'] += 1
        if not hit:
            return _anim(m, C, B, E, fl & ~0x20)
    fl = (fl ^ 0x10 if fl & 0x20 else fl) | 0x20      # $AC93
    return _anim(m, x, y, (E & 0xF8) | (afacing & 7), fl)


def _anim(m, x, y, E, fl):
    if (E & 0xE0) == 0x80:                            # $ACB0 class 4 blinks
        CENSUS['class 4 blink'] += 1
        if fl >= 0xC0:
            fl ^= 8
        if fl & 0x20:
            fl &= 0xF7
    return _tail(m, x, y, E, fl)


def _tail(m, x, y, E, fl):
    if 0x40 <= (E & 0xE0) < 0x80:
        CENSUS['class 2/3 ranged attack $AF8F (unmodelled)'] += 1
        return None
    if m[GFLAGS] & 0x04:                              # $ACCE animation tick
        if (E & 0xE0) in (0x00, 0x60) or not (fl & 0x20):
            d = (fl + 0x40) & 0xFF
            if d & 3:
                d = (d - 1) & 0xFF
                CENSUS['animate + countdown'] += 1
            else:
                CENSUS['animate'] += 1
            fl = d
    return (x & 0xFF, y & 0xFF, E & 0xFF, fl & 0xFF)


def sprite_id(state, flags):
    """$ACF5..$AD13.  ids 64..255 = 8 classes x 8 facings x 3 phases; classes
    6 and 7 are the two players (bases 208 and 232)."""
    b6, b7 = (flags >> 6) & 1, (flags >> 7) & 1
    ph = 0 if not b6 else (2 if b7 else 1)
    return 64 + 24 * (state >> 5) + (state & 7) + 8 * ph


# ------------------------------------------------------------------ driver
def _drive(key, poke, passes, hook):
    from harness import (Harness, PC, T, IFF, R, A, IXh, IXl,
                         TAPE_CALL_PC)
    from keyprobe import KEYS, keymask
    km = {n: (s, b) for n, s, b in KEYS}
    h = Harness()
    h.load_state(pickle.load(open(os.path.join(ROOT, 'build',
                                               'state_charsel.pkl'), 'rb')))
    if poke:
        h.memobj.m[P1], h.memobj.m[P1 + 1] = poke
    if key and key != '-':
        sel, bit = km[key.upper()]
        h.ports.press(sel, keymask(bit))
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    m = h.memobj.m
    pend = None
    for p in range(passes):
        end = regs[T] + 4 * FRAME_T
        while regs[T] < end:
            pc = regs[PC]
            if pc == TAPE_CALL_PC:
                h._tape()
                continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt()
                continue
            if pc == 0xAC00:
                ix = regs[IXl] + 256 * regs[IXh]
                pend = [ix, 0, 0, (m[ix], m[ix + 1], m[ix + 2], m[ix + 3]), p]
            elif pc == 0xAC27 and pend:       # just after LD A,R at $AC25
                pend[1] = regs[A]
            elif pc == 0xAC4E and pend:       # just after LD A,R at $AC4C
                pend[2] = regs[A]
            elif pc == 0xAD13 and pend:
                hook(m, pend)
                pend = None
            ops[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
    return h


def verify(key, poke, passes):
    stats = [0, 0, 0, []]

    def hook(m, pend):
        ix, r1, r2, snap, p = pend
        got = (m[ix], m[ix + 1], m[ix + 2], m[ix + 3])
        for i, v in enumerate(snap):
            m[ix + i] = v
        want = update(m, ix, r1, r2)
        for i, v in enumerate(got):
            m[ix + i] = v
        if want is None:
            stats[2] += 1
        elif want == got:
            stats[0] += 1
        else:
            stats[1] += 1
            if len(stats[3]) < 8:
                stats[3].append((p + 1, ix, snap, got, want))

    _drive(key, poke, passes, hook)
    print(f'key {key!r} poke {poke} {passes} passes:  '
          f'matching {stats[0]}  MISMATCHING {stats[1]}  unmodelled {stats[2]}')
    for p, ix, snap, got, want in stats[3]:
        print('   pass %d IX=$%04X pre=%s  Z80=%s  model=%s' % (
            p, ix, ' '.join('%02X' % v for v in snap),
            ' '.join('%02X' % v for v in got),
            ' '.join('%02X' % v for v in want)))
    print('branch census:')
    for k, v in sorted(CENSUS.items(), key=lambda kv: -kv[1]):
        print('   %-46s %d' % (k, v))
    return stats[1]


def coin(key, poke, passes):
    """Does the R coin actually change where an actor ends up?"""
    stats = [0, 0, []]

    def hook(m, pend):
        ix, _r1, _r2, snap, p = pend
        got = (m[ix], m[ix + 1], m[ix + 2], m[ix + 3])
        outs = set()
        for r1 in (0, 1):
            for r2 in (0, 1, 2, 3):
                for i, v in enumerate(snap):
                    m[ix + i] = v
                o = update(m, ix, r1, r2)
                outs.add(o[:2] if o else None)
        for i, v in enumerate(got):
            m[ix + i] = v
        if len(outs) == 1:
            stats[0] += 1
        else:
            stats[1] += 1
            if len(stats[2]) < 12:
                stats[2].append((p + 1, (ix - ACTORS) // 4, snap, sorted(
                    str(o) for o in outs)))

    CENSUS.clear()
    _drive(key, poke, passes, hook)
    print(f'key {key!r} {passes} passes:  actor updates whose (x,y) outcome is '
          f'INDEPENDENT of both coins: {stats[0]};  dependent: {stats[1]}')
    for p, slot, snap, outs in stats[2]:
        print('   pass %d slot %d pre=%s -> %s' % (
            p, slot, ' '.join('%02X' % v for v in snap), outs))


def cull(key, poke, passes):
    """Gate: does actors.onscreen() predict exactly which slots run their
    update hook?  An actor pass matches when every updated slot was predicted
    AND every predicted-but-skipped slot is one of the top (n0-n1) indices
    that a mid-loop swap-remove put out of the DJNZ's reach."""
    import actors as AC
    from harness import PC, T, IFF, IXh, IXl, TAPE_CALL_PC
    from keyprobe import KEYS, keymask
    km = {n: (s, b) for n, s, b in KEYS}
    h = __import__('harness').Harness()
    h.load_state(pickle.load(open(os.path.join(ROOT, 'build',
                                               'state_charsel.pkl'), 'rb')))
    m = h.memobj.m
    if poke:
        m[P1], m[P1 + 1] = poke
    if key and key != '-':
        sel, bit = km[key.upper()]
        h.ports.press(sel, keymask(bit))
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    pred, upd, n0 = None, set(), 0
    ok = bad = 0
    end = regs[T] + passes * 4 * FRAME_T
    while regs[T] < end:
        pc = regs[PC]
        if pc == TAPE_CALL_PC:
            h._tape()
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        if pc == 0xABAD:
            n0, _t, rows = AC.read_table(m)
            cx, cy = m[AC.CAM_X], m[AC.CAM_Y]
            pred = {r['slot'] for r in rows if AC.onscreen(r, cx, cy)}
            upd = set()
        elif pc == 0xAC00 and pred is not None:
            upd.add(((regs[IXl] + 256 * regs[IXh]) - ACTORS) // 4)
        elif pc == 0xABF5 and pred is not None:
            n1 = m[COUNT]
            if upd <= pred and (pred - upd) <= set(range(n1, n0)):
                ok += 1
            else:
                bad += 1
            pred = None
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    print(f'key {key!r} poke {poke}:  actor passes predicted exactly {ok}, '
          f'MISMATCHING {bad}')
    return bad


def main():
    args = sys.argv[1:]
    key, poke, passes, mode = 'Q', None, 200, 'verify'
    while args:
        if args[0] == '--key':
            key = args[1]; del args[:2]
        elif args[0] == '--poke':
            poke = tuple(int(v) for v in args[1].split(',')); del args[:2]
        elif args[0] == '--passes':
            passes = int(args[1]); del args[:2]
        elif args[0] == '--coin':
            mode = 'coin'; passes = 30; del args[:1]
        elif args[0] == '--cull':
            mode = 'cull'; del args[:1]
        else:
            del args[:1]
    if mode == 'cull':
        sys.exit(1 if cull(key, poke, passes) else 0)
    if mode == 'coin':
        coin(key, poke, passes)
    else:
        sys.exit(1 if verify(key, poke, passes) else 0)


if __name__ == '__main__':
    main()
