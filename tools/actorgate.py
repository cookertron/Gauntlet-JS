#!/usr/bin/env python3
"""
actorgate.py -- THE EVIDENCE BEHIND THE ACTOR PORT.

Every expected value in tools/headless.js's actor block, and every number in
the "Actors" section of notes/NOTES-engine.md, is printed by one of the
subcommands below.  Each one drives the REAL Z80 through tools/harness.py from
build/state_charsel.pkl; nothing here reads the JS engine.

    python tools/actorgate.py gt      [dir] [n]   per-pass reference table
    python tools/actorgate.py update  [dir] [n]   the $ABFF..$AD1B differential
    python tools/actorgate.py cull    [dir] [n]   the $A1DA update-window gate
    python tools/actorgate.py coins   [dir] [n]   the two LD A,R censuses
    python tools/actorgate.py sens    [dir] [n]   is the table coin-sensitive?
    python tools/actorgate.py walls               $A8E7 vs $ADF8, in isolation
    python tools/actorgate.py draws   [dir] [ctr] the original's actor blits
    python tools/actorgate.py all                 everything, briefly

Options:  --warp X,Y   poke the player there first (drives the horde)
          --class $NN  repaint every live actor's class bits first

SAMPLING.  `gt` and `draws` hook a PC, not a frame.  run_frames(4) -- which
tools/sim_move.py uses for the player table -- advances a fixed T-state window
that is NOT the game's pass, so it drifts: by pass 18 of the down run it lands
inside the actor loop and reports records with y == 0, which is $AC73's
self-exclusion caught mid-update.  For the ACTOR list the sample point has to
be $ABF5 (the end of the loop), which is exactly where the port's onePass()
ends.

THE MAIN LOOP ORDER, measured here and not what the CALL list suggests:
    $8503 TOP -> player move + $A5F0 scan -> $850C camera -> ...
              -> $851E actor loop -> $8550/$9CFB pass counter ++
The player's move is reached from $8509 CALL $A38A -> $A3AF CALL $A4DD; the
stack at $A5F0 reads [$A3B2, $850C].  $8521 CALL $A43B is the player's
frame/draw bookkeeping, NOT his move.
"""
import os
import pickle
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC          # noqa: E402
from harness import A as rA, B as rB, C as rC, E as rE          # noqa: E402
from harness import F as rF, H as rH, L as rL, IXh, IXl         # noqa: E402
from keyprobe import KEYS, keymask                              # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
FRAME_T = 69888

ACT, COUNT, TAIL = 0x5C00, 0x8496, 0x8494
CAMX, CAMY, PASSC, F847E = 0x848B, 0x848C, 0x8491, 0x847E
P1 = 0x8420


def load(direction=None, warp=None, cls=None):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    if warp:
        m[P1], m[P1 + 1] = warp
    if cls is not None:
        for k in range(m[COUNT]):
            m[ACT + 4 * k + 2] = (m[ACT + 4 * k + 2] & 0x1F) | cls
    if direction:
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    return h


def run_frames_hooked(h, n, hooks):
    """Advance n video frames, calling hooks[pc](h) BEFORE that instruction."""
    target = h.regs[T] + n * FRAME_T
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    while regs[T] < target:
        pc = regs[PC]
        fn = hooks.get(pc)
        if fn is not None:
            fn(h)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)


# ===================== THE MODEL, transcribed =====================
DIRMASK = [0x01, 0x09, 0x08, 0x0A, 0x02, 0x06, 0x04, 0x05]   # $8418..$841F
DEFLECT = [0x06, 0x02, 0x00, 0x04, 0x02, 0x06, 0x04, 0x00, 0x06]  # $8460..$8468


def wrap7(a):
    """$AD37: fold a 7-bit difference into a signed byte."""
    a &= 0xFF
    if a < 0x80:
        return a if a < 0x40 else (a ^ 0x80)
    return a if a >= 0xC0 else (a ^ 0x80)


def dist_dir(ax, ay, px, py):
    """$AD1C + $AD9B: L=dx, H=dy (signed bytes), returns (manhattan, slot)."""
    L = wrap7(ax - px)
    H = wrap7(ay - py)
    d = L if L < 0x80 else (-L) & 0xFF
    d2 = H if H < 0x80 else (-H) & 0xFF
    dist = (d + d2) & 0xFF
    # $AD9B  SUB A / CP H  (A=0)
    if H != 0:
        if L != 0:
            # $ADB4 JP p,$ADBF -- the sign of (0-L).  L in $81..$FF -> S clear
            # -> $ADBF D=3/1;  L in $01..$80 -> S set -> $ADB7 D=5/7.
            if L & 0x80:
                slot = 3 if (H & 0x80) else 1
            else:
                slot = 5 if (H & 0x80) else 7
        else:
            slot = 0 if not (H & 0x80) else 4
    else:
        slot = 2 if (L & 0x80) else 6
    return dist, slot


def probe(x, y, mask, mapv):
    """$ADF8: step 2 units per set mask bit, then test up to four map cells.
    Returns (nx, ny, blocked)."""
    nx, ny = x, y
    if mask & 1:
        ny = (ny - 2) & 0x7F
    if mask & 2:
        ny = (ny + 2) & 0x7F
    if mask & 4:
        nx = (nx - 2) & 0x7F
    if mask & 8:
        nx = (nx + 2) & 0x7F
    row = (ny >> 2) & 31
    col = (nx >> 2) & 31
    if mapv[row][col]:                                   # $AE2D
        return nx, ny, True
    if nx & 3:                                           # $AE30
        if mapv[row][(col + 1) & 31]:
            return nx, ny, True
    if ny & 3:                                           # $AE43
        r2 = (row + 1) & 31
        c2 = (col + 1) & 31 if (nx & 3) else col
        if mapv[r2][c2]:
            return nx, ny, True
        if nx & 3:                                       # $AE5A the fourth cell
            if mapv[r2][col]:
                return nx, ny, True
    return nx, ny, False


def scan(acts, cx, cy, skip=None):
    """$A97F: 7x7 box, x wraps mod 128, y does NOT.  Returns the slot found."""
    bx = (cx - 3) & 0x7F
    by = (cy - 3) & 0x7F
    for i, a in enumerate(acts):
        ay = 0 if i == skip else a[1]
        if ((a[0] - bx) & 0x78) != 0:                    # $A99D
            continue
        if ((a[0] - bx) & 0x7F) == 7:                    # $A9B1
            continue
        d = (ay - by) & 0xFF                             # $A9B8 plain SUB
        if d >= 0x80 or d >= 7:                          # $A9BA / $A9BE
            continue
        return i
    return None


def cull(x, y, camx, camy):
    """$A1DA's four RET nc: the actor is neither drawn NOR updated outside."""
    if ((x + 3 - camx) & 0x7F) > 0x42:
        return False
    if ((y + 3 - camy) & 0x7F) > 0x2A:
        return False
    return True


def update(acts, slot, pass_ctr, players, mapv, coin25, coin4c, tick2):
    """$ABFF..$AD1B for ONE actor.  Mutates acts[slot] in place and returns a
    tag: 'moved' | 'blocked' | 'nomove' | 'contact' | 'kill' (deleted itself)."""
    x, y, state, flags = acts[slot]
    E = state
    facing = state & 7                                     # A'
    D = None

    # $AC0E  round robin
    if not ((slot ^ pass_ctr) & 1):
        if E < 0xA0 or not (pass_ctr & 2):                 # $AC18/$AC1E
            return _tail(acts, slot, E, flags, tick2, blocked_now=None)

    if coin25 & 1:                                         # $AC25 keep facing
        pass
    else:
        dist, D = dist_dir(x, y, players[0][0], players[0][1])  # $AD51 -> $AD77
        if (E & 0xE0) == 0x60 and dist < 12:               # $AC2D class 3 flees
            D ^= 4
        E = (E & 0xF8)
        facing = D                                          # $AC44 EX AF,AF'
        if flags & 0x20:                                    # $AC46 blocked
            if coin4c & 3:                                  # $AC4C
                D = DEFLECT[D + (1 if (flags & 0x10) else 0)]
        E |= D                                              # $AC60

    nx, ny, blk = probe(x, y, DIRMASK[E & 7], mapv)         # $AC63
    if not blk:
        hit = _player_overlap(nx, ny, players)              # $AC68 $AEA0
        if hit is not None:
            flags &= ~0x08                                  # $AEEF RES 3
            if (E & 0xE0) == 0:                             # $AEF8 class 0
                acts[slot] = [x, y, state, flags]
                return 'kill'                               # damages, deletes
            flags &= ~0x20                                  # $AF06 RES 5
            if (E & 0xE0) != 0xA0 and (flags & 3) == 0:
                flags |= 0x02                               # $AF15 SET 1
            acts[slot] = [x, y, state, flags]
            return _tail(acts, slot, E, flags, tick2, blocked_now=None)
        if scan(acts, nx, ny, skip=slot) is None:           # $AC77
            x, y = nx, ny                                   # $AC7C commit
            flags &= ~0x20                                  # $AC82 RES 5
            flags = _blink(E, flags)                        # $ACA8
            acts[slot] = [x, y, state, flags]
            return _tail(acts, slot, E, flags, tick2, blocked_now=False)
    # $AC8D  blocked
    if flags & 0x20:
        flags ^= 0x10                                       # $AC9A
    flags |= 0x20                                           # $AC9C
    E = (E & 0xF8) | facing                                 # $ACA1..$ACA7
    flags = _blink(E, flags)                                # $ACA8
    acts[slot] = [x, y, state, flags]
    return _tail(acts, slot, E, flags, tick2, blocked_now=True)


def _blink(E, flags):
    """$ACA8..$ACBF: class 4 ($80) only -- toggle the INVISIBLE bit when the
    phase counter is at $C0, and force visible while blocked."""
    if (E & 0xE0) != 0x80:
        return flags
    if flags >= 0xC0:
        flags ^= 0x08
    if flags & 0x20:
        flags &= 0xF7
    return flags


def _player_overlap(cx, cy, players):
    """$AEA0, player 1 arm only (player 2 is not active in this state)."""
    px, py = players[0]
    if ((px + 3 - cx) & 0x7F) < 7 and ((py + 3 - cy) & 0x7F) < 7:
        return 0
    return None


def _tail(acts, slot, E, flags, tick2, blocked_now):
    """$ACC2..$ACF2: the shoot call, the animation tick, the write-back."""
    x, y, state, _ = acts[slot]
    if tick2:                                               # $ACCE BIT 2
        cls = E & 0xE0
        go = cls == 0 or cls == 0x60 or not (flags & 0x20)  # $ACD5..$ACE2
        if go:
            d = (flags + 0x40) & 0xFF                       # $ACE4
            if d & 3:
                d = (d - 1) & 0xFF
            flags = d
    acts[slot] = [x, y, E, flags]                           # $ACF2
    if blocked_now is None:
        return 'nomove'
    return 'blocked' if blocked_now else 'moved'


def sprite_id(state, flags):
    """$ACF5..$AD13"""
    ph = 0 if not (flags & 0x40) else (2 if (flags & 0x80) else 1)
    return 0x40 + 24 * (state >> 5) + (state & 7) + 8 * ph


# ===================== THE SUBCOMMANDS =====================

def cmd_gt(direction='down', passes=30, warp=None, cls=None):
    """The per-pass reference table, sampled at $ABF5 -- the END of the actor
    loop, which is exactly where the port's onePass() finishes.  Row n is the
    state after n port passes and carries pass counter (seed + n)."""
    h = load(direction, warp, cls)
    m = h.memobj.m
    rows = {}

    def at(hh):
        n = m[COUNT]
        rows[m[PASSC]] = dict(
            x=m[P1], y=m[P1 + 1], camx=m[CAMX], camy=m[CAMY], count=n,
            hp=(m[0x8422] << 8) | m[0x8423],
            acts=[list(m[ACT + 4 * k:ACT + 4 * k + 4]) for k in range(n)])

    base = m[PASSC]
    for _ in range(passes + 2):
        run_frames_hooked(h, 4, {0xABF5: at})
    print(f'seed: pass counter {base}')
    print('pass ctr  player      cam      n   hp    slot0                 slot1')
    for n in range(1, passes + 1):
        g = rows.get((base + n) & 0xFF)
        if not g:
            continue
        s1 = str(g['acts'][1]) if len(g['acts']) > 1 else ''
        print(f'{n:>4}{(base+n)&0xFF:>4}  ({g["x"]:>3},{g["y"]:>3})  '
              f'({g["camx"]:>2},{g["camy"]:>2})  {g["count"]:>3} {g["hp"]:04X}  '
              f'{str(g["acts"][0]):<21} {s1}')
    return rows


def cmd_update(direction='down', passes=30, warp=None, cls=None):
    """Differential-test update() against the real Z80, one actor update at a
    time.  Both R coins are FED FROM THE TRACE, so this tests every rule except
    the entropy source itself."""
    h = load(direction, warp, cls)
    m = h.memobj.m
    st, stats, bad = {}, Counter(), []
    mapv = [[0] * 32 for _ in range(32)]

    def at_abff(hh):
        ix = (hh.regs[IXh] << 8) | hh.regs[IXl]
        n = m[COUNT]
        st.clear()
        st.update(slot=(ix - ACT) // 4, n=n, pass_=m[PASSC],
                  tick2=(m[F847E] >> 2) & 1, magic=(m[F847E] >> 3) & 1,
                  players=[(m[P1], m[P1 + 1])], c25=None, c4c=None,
                  acts=[list(m[ACT + 4 * k:ACT + 4 * k + 4]) for k in range(n)])

    def at_ac27(hh):
        st['c25'] = hh.regs[rA]          # just after $AC25 LD A,R

    def at_ac4e(hh):
        st['c4c'] = hh.regs[rA]          # just after $AC4C LD A,R

    def at_end(hh):
        if not st or st.get('done'):
            return
        st['done'] = True
        if st['magic']:
            stats['magic-skip'] += 1
            return
        acts = [list(a) for a in st['acts']]
        slot = st['slot']
        tag = update(acts, slot, st['pass_'], st['players'], mapv,
                     1 if st['c25'] is None else st['c25'],
                     0 if st['c4c'] is None else st['c4c'], st['tick2'])
        stats[tag] += 1
        if tag == 'kill':
            ok = m[COUNT] == st['n'] - 1
        else:
            ok = list(m[ACT + 4 * slot:ACT + 4 * slot + 4]) == acts[slot]
        if ok:
            stats['ok'] += 1
        else:
            stats['MISMATCH'] += 1
            if len(bad) < 20:
                bad.append((st['pass_'], slot, st['acts'][slot], acts[slot],
                            list(m[ACT + 4 * slot:ACT + 4 * slot + 4]), tag))

    hooks = {0xABFF: at_abff, 0xAC27: at_ac27, 0xAC4E: at_ac4e,
             0xAD13: at_end, 0xAF02: at_end, 0xAF6F: at_end}
    for _ in range(passes):
        for r in range(32):
            for c in range(32):
                mapv[r][c] = m[0x8000 + 32 * r + c]
        run_frames_hooked(h, 4, hooks)
    for b in bad:
        print('  MISMATCH pass', b)
    print(f'update differential: {stats["ok"]} matching, '
          f'{stats["MISMATCH"]} mismatching   census {dict(stats)}')
    return stats


def cmd_cull(direction='down', passes=30, warp=None, cls=None):
    """Gate the $A1DA window: for every visit of the actor loop, predict from
    the record and the camera whether $ABFF is reached.  Matched PAIRWISE per
    visit -- a swap-remove makes the loop revisit the same IX with new
    contents, so matching by slot index invents mismatches."""
    h = load(direction, warp, cls)
    m = h.memobj.m
    cur, stats, bad = {}, Counter(), []

    def settle():
        if not cur:
            return
        want = cull(cur['x'], cur['y'], *cur['cam'])
        stats['ok' if want == cur['hit'] else 'MISMATCH'] += 1
        stats['visible' if cur['hit'] else 'culled'] += 1
        if want != cur['hit'] and len(bad) < 10:
            bad.append((cur['slot'], cur['x'], cur['y'], cur['cam'], want))
        cur.clear()

    def at_abd2(hh):
        settle()
        ix = (hh.regs[IXh] << 8) | hh.regs[IXl]
        cur.update(slot=(ix - ACT) // 4, x=m[ix], y=m[ix + 1],
                   cam=(m[CAMX], m[CAMY]), hit=False)

    def at_abff(hh):
        cur['hit'] = True

    def at_abf5(hh):
        settle()

    for _ in range(passes):
        run_frames_hooked(h, 4, {0xABD2: at_abd2, 0xABFF: at_abff,
                                 0xABF5: at_abf5})
    for b in bad:
        print('  MISMATCH', b)
    print(f'cull gate: {stats["ok"]} matching, {stats["MISMATCH"]} mismatching '
          f'({stats["visible"]} visible, {stats["culled"]} culled)')
    return stats


def cmd_coins(direction='right', passes=40, warp=(70, 74), cls=None):
    """Census the two LD A,R coins and the M1 distance between them."""
    h = load(direction, warp, cls)
    c25, c4c, delta, par = Counter(), Counter(), Counter(), Counter()
    cur = {}

    def at_ac27(hh):
        a = hh.regs[rA]
        c25[a & 1] += 1
        slot = (((hh.regs[IXh] << 8) | hh.regs[IXl]) - ACT) // 4
        par[(slot & 1, a & 1)] += 1
        cur['r'] = a

    def at_ac4e(hh):
        a = hh.regs[rA]
        c4c[a & 3] += 1
        if 'r' in cur:
            delta[(a - cur.pop('r')) & 0x7F] += 1

    for _ in range(passes):
        run_frames_hooked(h, 4, {0xAC27: at_ac27, 0xAC4E: at_ac4e})
    n25, n4c = sum(c25.values()), sum(c4c.values())
    print(f'$AC25  n={n25}  bit0 ones {c25[1]} ({100.0*c25[1]/max(n25,1):.1f}%)'
          f'   by (slot parity, bit0): {dict(sorted(par.items()))}')
    print(f'$AC4C  n={n4c}  A&3 {dict(sorted(c4c.items()))}  '
          f'-> deflection SKIPPED {c4c[0]}/{n4c} = '
          f'{100.0*c4c[0]/max(n4c,1):.2f}%  (the AND 3 mask suggests 25%)')
    print(f'       R($AC4C)-R($AC25) mod 128: {dict(sorted(delta.items()))} '
          f'-- odd gaps force bit 0 high, which is why 0 is rare')
    return c25, c4c, delta


def cmd_sens(direction='down', passes=30, warp=None, cls=None):
    """Is the player's table sensitive to the coins?  Patch BOTH LD A,R sites
    to LD A,imm (ED 5F -> 3E nn, the same length) and run every policy."""
    def run(v25, v4c):
        h = load(direction, warp, cls)
        m = h.memobj.m
        if v25 is not None:
            m[0xAC25], m[0xAC26] = 0x3E, v25
        if v4c is not None:
            m[0xAC4C], m[0xAC4D] = 0x3E, v4c
        rows = []
        for _ in range(passes):
            run_frames_hooked(h, 4, {})
            rows.append((m[P1], m[P1 + 1], m[COUNT]))
        return rows

    base = run(None, None)
    print('natural R, last rows:', ' '.join(f'{x},{y}' for x, y, n in base[-4:]),
          '| final count', base[-1][2])
    same = True
    for v25 in (0x00, 0x01):
        for v4c in (0x00, 0x03):
            r = run(v25, v4c)
            eq = [p[:2] for p in r] == [p[:2] for p in base]
            same &= eq
            print(f'  $AC25={v25:02X} $AC4C={v4c:02X}: player table '
                  f'{"IDENTICAL" if eq else "DIFFERS"}  final count {r[-1][2]}')
    print('=> coin-INDEPENDENT' if same else '=> COIN-SENSITIVE')
    return same


def cmd_walls():
    """ISOLATION: $A8E7 (the player) vs $ADF8 (an actor) on one planted cell."""
    h = load()
    m = h.memobj.m
    m[0x8428] = 9                        # keys, so a door is affordable
    cell = 0x8000 + 32 * 10 + 10
    assert m[cell] == 0, 'pick an empty cell'
    print(' val  player($A8E7)  actor($ADF8)')
    for v in [0x00, 0x01, 0x05, 0x10, 0x11, 0x12, 0x13, 0x15, 0x1F, 0x20,
              0x22, 0x30, 0x33, 0x36, 0x38, 0x40, 0x7F]:
        m[cell] = v
        h.call(0xA8E7, {'HL': cell, 'IX': P1})
        p = bool(h.regs[rF] & 1)                       # carry set = blocked
        h.call(0xADF8, {'E': 0x00, 'BC': (42 << 8) | 42, 'IX': ACT})
        a = not bool(h.regs[rF] & 0x40)                # NZ = blocked
        m[cell] = 0
        print(f'  ${v:02X}  {"BLOCK" if p else "pass":>8}     '
              f'{"BLOCK" if a else "pass":>6}'
              + ('   <-- DIFFERENT' if p != a else ''))


def cmd_draws(direction='down', want_ctr=89, warp=None, cls=None):
    """The ORIGINAL's actor blits for ONE actor loop, named by pass counter:
    screen destination, the id it handed the blitter at $AD13, and the record.
    The destination is the PRE-update position; the id is POST-update."""
    h = load(direction, warp, cls)
    m = h.memobj.m
    st, draws = {'in': False, 'done': False}, []

    def unaddr(a):
        a -= 0xC000
        return (a & 31) * 8, ((a >> 8) & 7) | ((a >> 2) & 0x38) | ((a >> 5) & 0xC0)

    def at_abad(hh):
        st['in'] = (m[PASSC] == want_ctr)
        if st['in']:
            st['snap'] = (m[P1], m[P1 + 1], m[CAMX], m[CAMY], m[COUNT])

    def at_abf5(hh):
        if st['in']:
            st['done'] = True
        st['in'] = False

    def at_ad13(hh):
        ix = (hh.regs[IXh] << 8) | hh.regs[IXl]
        st['id'], st['rec'] = hh.regs[rA], list(m[ix:ix + 4])

    def at_9dd2(hh):
        if st['in']:
            draws.append((unaddr((hh.regs[rH] << 8) | hh.regs[rL]),
                          st.get('id'), st.get('rec')))

    hooks = {0xABAD: at_abad, 0xABF5: at_abf5, 0xAD13: at_ad13, 0x9DD2: at_9dd2}
    for _ in range(150):
        if st['done']:
            break
        run_frames_hooked(h, 4, hooks)
    s = st.get('snap')
    print(f'actor loop at pass counter {want_ctr}: player({s[0]},{s[1]}) '
          f'cam({s[2]},{s[3]}) count={s[4]}')
    for (x, y), sid, rec in draws:
        print(f'   blit ({x},{y})  id {sid}  record {rec}')
    return draws


def main():
    args = sys.argv[1:]
    warp = cls = None
    if '--warp' in args:
        i = args.index('--warp')
        warp = [int(v) for v in args[i + 1].split(',')]
        del args[i:i + 2]
    if '--class' in args:
        i = args.index('--class')
        cls = int(args[i + 1].replace('$', '0x'), 0)
        del args[i:i + 2]
    cmd = args[0] if args else 'all'
    d = args[1] if len(args) > 1 else 'down'
    n = int(args[2]) if len(args) > 2 else (89 if cmd == 'draws' else 30)
    if cmd == 'gt':
        cmd_gt(d, n, warp, cls)
    elif cmd == 'update':
        cmd_update(d, n, warp, cls)
    elif cmd == 'cull':
        cmd_cull(d, n, warp, cls)
    elif cmd == 'coins':
        cmd_coins(d, n, warp or (70, 74), cls)
    elif cmd == 'sens':
        cmd_sens(d, n, warp, cls)
    elif cmd == 'walls':
        cmd_walls()
    elif cmd == 'draws':
        cmd_draws(d, n, warp, cls)
    elif cmd == 'all':
        print('== update differential, down ==')
        cmd_update('down', 30)
        print('== update differential, in the horde ==')
        cmd_update('right', 40, (70, 74))
        print('== cull gate ==')
        cmd_cull('down', 30)
        cmd_cull('right', 40, (70, 74))
        print('== coins ==')
        cmd_coins()
        print('== coin sensitivity of the down table ==')
        cmd_sens('down', 30)
        print('== wall rules ==')
        cmd_walls()
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
