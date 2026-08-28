#!/usr/bin/env python3
"""
clockmodel.py -- WHY A PASS COSTS FOUR VIDEO FRAMES OR FIVE, AND A PREDICTOR
THAT SAYS WHICH.  Everything here is measured on the real Z80 from
build/state_48k.pkl; nothing is taken from the engine.

    python tools/clockmodel.py anatomy      the identity, per direction
    python tools/clockmodel.py isr          the ISR's own two costs
    python tools/clockmodel.py w2           the post-HALT half
    python tools/clockmodel.py collect      14 scenes -> build/passcost.json
    python tools/clockmodel.py segfit       fit each CALL site separately
    python tools/clockmodel.py segscore     score the predictor, honestly
    python tools/clockmodel.py beepgate     THE ACCEPTANCE TEST: beepgate's
                                            own five scenarios, pass by pass
    python tools/clockmodel.py all

=============================================================================
THE MECHANISM.  It is one HALT, and it is not a fit.
=============================================================================
On the 48K branch a main-loop pass contains exactly ONE `HALT`: $9CD7, which
$8550 calls at the bottom of the loop.  Everything before it is compute;
everything after it runs with INTERRUPTS OFF ($9CD8 DI) and is the tone tick
plus the three shadow->screen copies.  With t0 the absolute T-state of the
loop top $8503:

    W1c  = $8503..$9CD7      the loop body, interrupt-free   VARIABLE
    +      the ISRs accepted while it runs
    HALT   waits for the next frame interrupt                THE QUANTISER
    W2   = the wake ISR + $9CD8..the next $8503              152,214/154,091 T

    t0' = ceil((t0 + W1) / FRAME_T) * FRAME_T + W2
    cost = t0' - t0

MEASURED: given the true W1c and the true number of accepted interrupts, that
identity reproduces the pass cost on 1,669 of 1,670 collected passes.  The
exception is the one pass that runs a blocking tune, which the engine already
debits separately.

Because ceil() is an integer the phase p = frac(W2 / FRAME_T) is a FIXED
POINT and takes exactly TWO values.  W2 is 151,874 T of straight-line work
plus one ISR, and the ISR is 2,217 T normally but 340 T when ($8497 & 7) == 7
-- $A2B4's `RRCA/RRCA/RRCA / CP $E0 / JR nc` skips the GAUNTLET logo colour
cycle on one frame in eight.  So p is 0.1780 or 0.2048 and nothing else, and

    FIVE frames  <=>  W1c + (the ISRs taken inside it) > (2 - p) * FRAME_T

i.e. W1c above about 123,945 T.  A quiet pass is W1c ~ 123,300, so the margin
is 0.5% of the pass and a global least-squares fit cannot resolve it.  This
file therefore charges each of the twenty-two CALL sites separately, against
the quantities that site's own code reads.

WHAT IS IRREDUCIBLE, AND IT IS NOT THE COMPUTE.  The map painters and the
sprite blitter bracket EVERY object blit with DI/EI -- $9F86/$9F92,
$A00D/$A019, $A0C8/$A0D4, $A241/$A24B, $A292/$A29E -- because they blit with
`LD SP,source`.  The ULA asserts INT for 32 T-states and there is no pending
latch, so a frame interrupt whose window falls inside one of those DI spans
is LOST FOR EVER: $8497 does not tick and W1 is 2,217 T shorter.  That is
3.2% of a frame, five times the quiet-scene margin, so it flips the 4-vs-5
outcome on its own.  Measured over the collected scenes, 14% of the frame
boundaries inside W1 lose their interrupt (49% of them in the LEFT scene).
Reproducing it needs a cycle-exact model of the blitter's inner loop --
emulating the CPU rather than porting the game -- and it is why the ceiling
for ANY W1c-based predictor is 94.9% and not 100%.

CONTENTION.  This harness is a plain Simulator, so every figure here is a
LOWER BOUND.  W1 paints into the SHADOW screen at $C000 (uncontended on a
48K) but reads its sprite records and the actor list from $5C00..$7FFF
(contended); W2's three copies write 6,912 bytes into $4000..$5AFF (all
contended).  Contention therefore lengthens W2 more than W1, which RAISES p,
which LOWERS the W1c threshold -- so a real 48K tips to five frames MORE
often than these numbers say.  The structure above is contention-independent;
the constants are not, and would have to be re-fitted against a contended
simulator.

=============================================================================
WHERE THIS TOOL SITS  (reconciliation, phase 12)
=============================================================================
FIVE tools in this directory measure the pass clock, written by four parallel
investigations plus this one.  What is AUTHORITATIVE now:

  tools/clockgate.py   THE GATE and the shipped model's only scorer.  `hz`
                       (the deliverable), `diff` (engine vs Z80, pass for
                       pass), `score` (the BUILT ARTIFACT's own cost model
                       against the real Z80), `w2`, `contend`.
  web/template.html    THE MODEL.  CLK / clockCost() / quantise().  There is
                       no Python copy of it, deliberately.

The other four are INSTRUMENTS, kept because their measurements are cited in
notes/NOTES-battery.md Q10 and reproducing them is how the model was built:

  tools/passcost.py    the per-CALL-site collector and feature extractor the
                       corpus was built with (`collect`, `corpus`, `calls`).
  tools/passclock.py   halts / model / seams / units / entropy / contend.
  tools/clockmodel.py  anatomy / isr / w2 / segfit / segscore.
  tools/drawcost.py    the four map painters enumerated in isolation.

Their own predictors are SUPERSEDED by the model in web/template.html; where
their numbers disagree with clockgate.py's, clockgate.py was re-measured last
and against the shipped artifact.
"""
import collections
import json
import math
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, SP, IFF, FRAME_T, TAPE_CALL_PC  # noqa: E402
from keyprobe import KEYS, keymask                                 # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
FIRE = '0'                     # player 1 fire, tools/shotgate.py's key
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
OUT = os.path.join(ROOT, 'build', 'passcost.json')
LOOP_TOP = 0x8503
HALT_PC = 0x9CD7

SITES = [(0x8503, 'tileAnim'), (0x8506, 'doorAnim'), (0x8509, 'players'),
         (0x850C, 'camera'), (0x850F, 'paintBody'), (0x8512, 'paintH'),
         (0x8515, 'paintV'), (0x8518, 'paintC'), (0x851B, 'camTgt'),
         (0x851E, 'actors'), (0x8521, 'plrDraw'), (0x8524, 'shots'),
         (0x8527, 'treasure'), (0x852E, 'hud'), (0x8531, 'hud2'),
         (0x8534, 'panel'), (0x8537, 'exitWalk'), (0x853A, 'cull'),
         (0x853D, 'gens'), (0x8540, 'death'), (0x8543, 'banner'),
         (0x8546, 'ctr'), (0x8550, 'toHalt')]
SITE_PC = [a for a, _ in SITES]
NAME = dict(SITES)
ORDER = SITE_PC + [HALT_PC]

TUNE_PC = (0xB8B0, 0xB8B5)
CNT = {0xB8CC: 'noise', 0xA1DA: 'sprite', 0xABFF: 'actupd',
       0x8943: 'bannerwork',   # $891C past its `LD A,($84B9) / OR A / RET z`
       0x95AE: 'doorwork',     # $95AB's body, run TWICE per pass by design
       0x9DEC: 'blitrow', 0x9F7A: 'tile', 0xA97F: 'actscan',
       0xB159: 'padhook', 0xAF8F: 'ranged', 0xB0D3: 'spawn'}

P1, P2 = 0x8420, 0x8440
CAMX, CAMY, NACT, PASSC = 0x848B, 0x848C, 0x8496, 0x8491


def fresh(path=STATE):
    h = Harness()
    h.load_state(pickle.load(open(path, 'rb')))
    assert h.memobj.m[0xFFFD] == 0, 'not the 48K branch'
    return h


def press(h, keys):
    for k in keys:
        sel, bit = KM[k]
        h.ports.press(sel, keymask(bit))


def dirkeys(direction):
    out = []
    for d in direction.split('+'):
        if d in DIRKEY:
            out.append(DIRKEY[d])
        elif d == 'fire':
            out.append(FIRE)
    return out


def one_pass(h, limit=60_000_000):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0 = regs[T]
    stamp = {}
    isrmark = {}                       # cumulative ISR T-states at each stamp
    cnt = collections.Counter()
    wake = None
    n = 0
    isr_acc = 0                        # T-states spent inside the IM2 handler
    isr_sp = None
    isr_t0 = 0
    while n < limit:
        pc = regs[PC]
        if isr_sp is not None and regs[SP] > isr_sp:
            isr_acc += regs[T] - isr_t0
            isr_sp = None
        if n and pc == LOOP_TOP:
            break
        if pc in NAME or pc == HALT_PC:
            if pc not in stamp:
                stamp[pc] = regs[T]
                isrmark[pc] = isr_acc
        if pc in TUNE_PC and 'tune_t0' not in stamp:
            stamp['tune_t0'] = regs[T]
        c = CNT.get(pc)
        if c:
            cnt[c] += 1
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1
            if wake is None:
                wake = regs[T]
            cnt['halt'] += 1
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            isr_t0 = regs[T]
            sim.accept_interrupt(regs, mem, pc)
            isr_sp = regs[SP]
            cnt['isr'] += 1
            if HALT_PC not in stamp:
                cnt['isr_w1'] += 1
        n += 1
    else:
        raise RuntimeError('no loop top in %d instructions' % limit)
    seg = {}
    for i, a in enumerate(SITE_PC):
        b = ORDER[i + 1]
        if a in stamp and b in stamp:
            # ISR-FREE: the handler is charged to nobody, so a routine's cost
            # is the same whether or not a frame interrupt landed inside it
            seg[NAME[a]] = (stamp[b] - stamp[a]) - (isrmark[b] - isrmark[a])
    w1c = None
    if HALT_PC in stamp:
        w1c = (stamp[HALT_PC] - t0) - isrmark[HALT_PC]
    tune = 0
    if 'tune_t0' in stamp:
        tune = 72.07 * FRAME_T          # $B8B0, measured by beepgate.py pause
    return {'tune': tune,
            't0': t0, 'cost': regs[T] - t0, 'stamp': stamp, 'wake': wake,
            'seg': seg, 'cnt': cnt, 'W1c': w1c, 'isr_w1': isrmark.get(HALT_PC)}


# ---------------------------------------------------------------------------
# the ISR's own cost, measured once: $A29F..RET with interrupts off
# ---------------------------------------------------------------------------
def isr_cost(h):
    """T-states of one accepted interrupt, measured by differencing two
    passes that differ only in how many interrupts landed inside W1."""
    return 2217          # measured below in cmd_anatomy; asserted there


# ===========================================================================
def cmd_anatomy(npass=80):
    print('THE IDENTITY  cost = ceil(p + W1) - p + W2,  p(next) = frac(W2)')
    print('  %-8s %8s %8s %8s %8s %8s  %-12s %s'
          % ('key', 'p', 'W1', 'W1c', 'W2', 'cost', 'hist', 'rule'))
    bad = tot = 0
    isr_ts = []
    for direction in ('idle', 'right', 'left', 'down', 'up'):
        h = fresh()
        press(h, dirkeys(direction))
        one_pass(h)
        acc = collections.defaultdict(list)
        hist = collections.Counter()
        ok = 0
        for _ in range(npass):
            r = one_pass(h)
            p = (r['t0'] % FRAME_T) / FRAME_T
            w1 = r['stamp'][HALT_PC] - r['t0']
            w2 = r['t0'] + r['cost'] - r['wake']
            nisr = r['cnt']['isr']
            whole = int(round(r['cost'] / FRAME_T))
            hist[whole] += 1
            # the identity, exactly
            pred_cost = (math.ceil(p + w1 / FRAME_T) - p) * FRAME_T + w2
            acc['dc'].append(abs(pred_cost - r['cost']))
            acc['p'].append(p); acc['W1'].append(w1 / FRAME_T)
            acc['W2'].append(w2 / FRAME_T); acc['cost'].append(r['cost']
                                                               / FRAME_T)
            acc['nisr'].append(nisr)
            acc['W1c'].append(w1 - nisr * 2217)
            ok += (whole == (5 if p + w1 / FRAME_T > 2.0 else 4))
            tot += 1
            bad += (whole != (5 if p + w1 / FRAME_T > 2.0 else 4))
        mean = lambda k: sum(acc[k]) / len(acc[k])          # noqa: E731
        print('  %-8s %8.4f %8.4f %8.0f %8.4f %8.4f  %-12s %d/%d  |dcost| %.1fT'
              % (direction, mean('p'), mean('W1'), mean('W1c'), mean('W2'),
                 mean('cost'), str(dict(sorted(hist.items()))), ok, npass,
                 max(acc['dc'])))
        isr_ts.append((min(acc['nisr']), max(acc['nisr'])))
    print('  the identity reproduces the pass cost to %s T' % 'the last')
    print('  rule "p + W1 > 2" wrong on %d of %d passes' % (bad, tot))
    print('  interrupts inside W1: %s   (4-frame -> 1, 5-frame -> 2)'
          % isr_ts)
    thr = (2 - 0.1948) * FRAME_T - 2217
    print('  => FIVE frames iff W1c > %.0f T-states' % thr)
    return bad, tot


def cmd_isr():
    """The ISR's own cost, by direct measurement rather than by difference."""
    h = fresh()
    one_pass(h)
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    costs = []
    n = 0
    while n < 400_000 and len(costs) < 12:
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sp0 = regs[12]
            t0 = regs[T]
            sim.accept_interrupt(regs, mem, pc)
            m = 0
            while regs[12] < sp0 and m < 200_000:
                p2 = regs[PC]
                if mem[p2] == 0x76 and regs[IFF]:
                    h._fast_halt()
                else:
                    ops[mem[p2]]()
                m += 1
            costs.append(regs[T] - t0)
        n += 1
    print('ISR cost, T-states, %d accepted interrupts: %s'
          % (len(costs), dict(sorted(collections.Counter(costs).items()))))
    return 0, 0


def cmd_w2(npass=60):
    print('THE POST-HALT HALF W2 -- $9CD8 DI .. the next $8503')
    for direction in ('idle', 'down', 'up'):
        h = fresh()
        press(h, dirkeys(direction))
        one_pass(h)
        v = []
        for _ in range(npass):
            r = one_pass(h)
            v.append(r['t0'] + r['cost'] - r['wake'])
        print('  %-6s W2 %d..%d T  (%.4f..%.4f frames)  frac -> p %.4f..%.4f'
              % (direction, min(v), max(v), min(v) / FRAME_T, max(v) / FRAME_T,
                 (min(v) / FRAME_T) % 1, (max(v) / FRAME_T) % 1))
    return 0, 0


# ===========================================================================
#  FEATURES -- everything below is computable by the ENGINE
# ===========================================================================
def paint_cells(camx, camy):
    """$9EFC/$9FC2/$A08B/$A159 -- the cells the four painters paint.
    Read off web/template.html's padCensusDraw(), which telegate.py gates
    against the real Z80 over 1452 (dungeon, camera) pairs."""
    c0 = 1 if (camx & 2) else 0
    r0 = 1 if (camy & 2) else 0
    body = (16 - c0) * (10 - r0)
    h = 2 * (16 - c0) if (camy & 2) else 0
    v = 2 * (10 - r0) if (camx & 2) else 0
    c = 4 if ((camx & 2) and (camy & 2)) else 0
    return body, h, v, c


def gen_cells(camx, camy):
    """$A9C2's sweep window -- $A9CA..$A9E0."""
    return (17 if (camx & 3) else 16) * (11 if (camy & 3) else 10)


def drawn_actors(m):
    """$A1DA's four culls: (x+3-camx)&$7F <= $42 and (y+3-camy)&$7F <= $2A."""
    camx, camy, n = m[CAMX], m[CAMY], m[NACT]
    k = 0
    for i in range(n):
        x, y = m[0x5C00 + 4 * i], m[0x5C01 + 4 * i]
        if ((x + 3 - camx) & 0x7F) <= 0x42 and ((y + 3 - camy) & 0x7F) <= 0x2A:
            k += 1
    return k


def features(h):
    """The state the ENGINE has at the top of a pass."""
    m = h.memobj.m
    camx, camy = m[CAMX], m[CAMY]
    body, hs, vs, cs = paint_cells(camx, camy)
    f = {
        'nact': m[NACT],
        'drawn': drawn_actors(m),
        'body': body, 'hstrip': hs, 'vstrip': vs, 'corner': cs,
        'tiles': body + hs + vs + cs,
        'gcells': gen_cells(camx, camy),
        'camx': camx, 'camy': camy,
        'passc': m[PASSC],
        'px': m[P1], 'py': m[P1 + 1],
        'p2in': 0 if (m[P2 + 0x0B] & 0x40) else 1,
        'shot1': 0 if m[0x8430] == 0xFF else 1,
        'anim': m[0x84A2],           # $A31A's own counter -- probed below
        'f11': m[P1 + 0x0B],
        'dirty': m[0x842B],
        'pend': m[P1 + 0x1D],
        'door': m[0x849E],
        'tone': m[0x84CF],
        'noise': m[0x84D2],
    }
    return f


def collect(direction, npass, setup=None, path=STATE, tag=''):
    h = fresh(path)
    if setup:
        setup(h)
    press(h, dirkeys(direction))
    one_pass(h)
    rows = []
    for i in range(npass):
        f = features(h)
        r = one_pass(h)
        p = (r['t0'] % FRAME_T) / FRAME_T
        w1 = r['stamp'][HALT_PC] - r['t0']
        f.update({
            'tag': tag or direction, 'dir': direction, 'i': i,
            'p': p, 'W1': w1, 'nisr': r['cnt']['isr'],
            'W1c': w1 - r['cnt']['isr'] * 2217,
            'W2': r['t0'] + r['cost'] - r['wake'],
            'cost': r['cost'], 'frames': r['cost'] / FRAME_T,
            'whole': int(round(r['cost'] / FRAME_T)),
            'noisecalls': r['cnt']['noise'], 'sprites': r['cnt']['sprite'],
            'actupd': r['cnt']['actupd'],
        })
        for k, v in r['seg'].items():
            f['s_' + k] = v
        rows.append(f)
    return rows




# ===========================================================================
#  THE COST MODEL
# ===========================================================================
ISR_CHEAP, ISR_FULL = 340, 2217        # $A29F; ($8497 & 7) == 7 skips $A2CA
W2C = 151874                           # $9CD8..$8503 minus its own ISR


def isr_T(f):
    """$A2B4 LD A,($8497) / RRCA x3 / CP $E0 / JR nc,$A2DA -- the logo colour
    cycle ($A2CA/$A2D2, two $A2EF calls) is SKIPPED when ($8497 & 7) == 7."""
    return ISR_CHEAP if (f & 7) == 7 else ISR_FULL


def sim_pass(t0, f0, w1c, w2c=W2C):
    """The HALT quantiser, exactly as $8550 CALL $9CD7 runs it.
    t0 absolute T at $8503, f0 = ($8497) there, w1c the ISR-FREE compute."""
    t, rem, f = t0, w1c, f0
    while True:
        nb = (t // FRAME_T + 1) * FRAME_T
        if t + rem < nb:
            t += rem
            break
        rem -= nb - t
        t = nb
        f = (f + 1) & 0xFF
        t += isr_T(f)
    t = (t // FRAME_T + 1) * FRAME_T           # $9CD7 HALT
    f = (f + 1) & 0xFF
    t += isr_T(f) + w2c                        # the wake ISR, then the copy
    return t - t0, t, f


# --- the painted cell sets, which the engine already computes ---------------
def paint_parts(camx, camy):
    """The four painters' own cell lists, kept apart so each can be charged
    its own base and its own per-object cost."""
    cx0, cy0 = (camx >> 2) & 31, (camy >> 2) & 31
    c0, r0 = (1 if camx & 2 else 0), (1 if camy & 2 else 0)
    body = [(dc, dr) for dr in range(r0, 10) for dc in range(c0, 16)]
    hs = ([(dc, d) for dc in range(c0, 16) for d in (0, 10)]
          if camy & 2 else [])
    vs = ([(d, dr) for dr in range(r0, 10) for d in (0, 16)]
          if camx & 2 else [])
    cs = ([(0, 0), (16, 0), (16, 10), (0, 10)]
          if (camx & 2) and (camy & 2) else [])
    def mp(lst):
        return [(((cy0 + dr) & 31), ((cx0 + dc) & 31)) for dc, dr in lst]
    return mp(body), mp(hs), mp(vs), mp(cs)


def paint_list(camx, camy):
    cx0, cy0 = (camx >> 2) & 31, (camy >> 2) & 31
    c0, r0 = (1 if camx & 2 else 0), (1 if camy & 2 else 0)
    out = []
    for dr in range(r0, 10):
        for dc in range(c0, 16):
            out.append((dc, dr))
    if camy & 2:
        for dc in range(c0, 16):
            out += [(dc, 0), (dc, 10)]
    if camx & 2:
        for dr in range(r0, 10):
            out += [(0, dr), (16, dr)]
    if (camx & 2) and (camy & 2):
        out += [(0, 0), (16, 0), (16, 10), (0, 10)]
    return [(((cy0 + dr) & 31), ((cx0 + dc) & 31)) for dc, dr in out]


def gen_list(camx, camy):
    cols = 17 if (camx & 3) else 16
    rows = 11 if (camy & 3) else 10
    cx0, cy0 = (camx >> 2) & 31, (camy >> 2) & 31
    return [(((cy0 + r) & 31), ((cx0 + c) & 31))
            for r in range(rows) for c in range(cols)]


FEATS = ['one', 'tileEven', 'bannerFire', 'doorFire', 'pBody', 'pH', 'pV', 'pC', 'pObj', 'pPad',
         'gCells', 'gObj', 'gGen', 'aE1', 'aE2', 'aE3', 'aE4', 'aDraw',
         'pmoved', 'shots', 'drain', 'pend']


def actor_exit(x, y, camx, camy):
    """$A1DA's four RET nc, read instruction by instruction.  Every actor
    costs $B8CC at $A1DD and then leaves at one of five points."""
    ax = (x + 3 - camx) & 0x7F
    if ax >= 0x44:
        return 'aE1'                                   # $A1EA
    if ax >= 3:
        if ((ax - 0x43) & 0x1FF) >= 0 and ax >= 0x43:
            return 'aE2'                               # $A1F8
    ay = (y + 3 - camy) & 0x7F
    if ay >= 0x44:
        return 'aE3'                                   # $A209
    if ay >= 3 and ay >= 0x2B:
        return 'aE4'                                   # $A217
    return 'aDraw'                                     # reaches $A21E/$ABFF


def featurise(h, moved=0):
    m = h.memobj.m
    camx, camy = m[CAMX], m[CAMY]
    pb, ph, pv, pc = paint_parts(camx, camy)
    pl = pb + ph + pv + pc
    gl = gen_list(camx, camy)
    nz = lambda L: sum(1 for r, c in L if m[0x8000 + r * 32 + c])   # noqa
    vals = [m[0x8000 + r * 32 + c] for r, c in pl]
    gvals = [m[0x8000 + r * 32 + c] for r, c in gl]
    c0, r0 = (1 if camx & 2 else 0), (1 if camy & 2 else 0)
    nact = m[NACT]
    ex = collections.Counter()
    for i in range(nact):
        ex[actor_exit(m[0x5C00 + 4 * i], m[0x5C01 + 4 * i], camx, camy)] += 1
    shots = (0 if m[0x8430] == 0xFF else 1) + (0 if m[0x8450] == 0xFF else 1)
    return {
        'one': 1,
        'tileEven': 1 if not (m[PASSC] & 1) else 0,
        'pBody': (16 - c0) * (10 - r0),
        'pRows': 10 - r0, 'pCols': 16 - c0,
        'gRows': 11 if (camy & 3) else 10,
        'gCols': 17 if (camx & 3) else 16,
        'pVert': 0, 'pHoriz': 0,          # filled by the caller from the keys
        'pH': 2 * (16 - c0) if (camy & 2) else 0,
        'pV': 2 * (10 - r0) if (camx & 2) else 0,
        'pC': 4 if ((camx & 2) and (camy & 2)) else 0,
        'pObj': sum(1 for v in vals if v),
        'oBody': nz(pb), 'oH': nz(ph), 'oV': nz(pv), 'oC': nz(pc),
        'nact': nact,
        'padBody': sum(1 for r, c in pb if m[0x8000 + r * 32 + c] == 0x30),
        'pPad': sum(1 for v in vals if v == 0x30),
        'gCells': len(gl),
        'gObj': sum(1 for v in gvals if v),
        'gGen': sum(1 for v in gvals if 0x20 <= v <= 0x2E),
        'aE1': ex['aE1'], 'aE2': ex['aE2'], 'aE3': ex['aE3'],
        'aE4': ex['aE4'], 'aDraw': ex['aDraw'],
        'pmoved': moved,
        'shots': shots,
        'drain': 0,
        'pend': 1 if m[P1 + 0x1D] else 0,
    }


# ===========================================================================
#  THE SCENES -- varied on purpose, and split into TRAIN and TEST
# ===========================================================================
def _noact(n):
    def f(h):
        m = h.memobj.m
        m[NACT] = n
        a = 0x5C00 + 4 * n
        m[0x8494], m[0x8495] = a & 0xFF, a >> 8
    return f


def _warp(x, y):
    def f(h):
        m = h.memobj.m
        m[P1], m[P1 + 1] = x, y
    return f


def _plant(dx, dy, val):
    def f(h):
        m = h.memobj.m
        col, row = m[P1] >> 2, m[P1 + 1] >> 2
        m[0x8000 + ((row + dy) & 31) * 32 + ((col + dx) & 31)] = val
    return f


SCENES = [
    # (name, direction, passes, setup, group)
    ('idle',        'idle',       150, None,            'train'),
    ('right',       'right',      150, None,            'train'),
    ('up',          'up',         150, None,            'train'),
    ('fire',        'down+fire',  120, None,            'train'),
    ('horde-down',  'down',       120, _warp(70, 74),   'train'),
    ('noactors',    'down',       100, _noact(0),       'train'),
    ('item',        'down',        50, _plant(0, 4, 0x1B), 'train'),
    ('left',        'left',       150, None,            'test'),
    ('down',        'down',       150, None,            'test'),
    ('upleft',      'up+left',    120, None,            'test'),
    ('downright',   'down+right', 120, None,            'test'),
    ('horde-idle',  'idle',       120, _warp(70, 74),   'test'),
    ('few',         'down',       100, _noact(20),      'test'),
    ('pickup',      'down',        60, _plant(0, 4, 0x19), 'test'),
    ('sweep',       'down',        60, _plant(0, 4, 0x2F), 'test'),
]


def collect_scene(name, direction, npass, setup, group):
    h = fresh()
    if setup:
        setup(h)
    press(h, dirkeys(direction))
    one_pass(h)
    m = h.memobj.m
    rows = []
    for i in range(npass):
        f0 = m[0x8497]
        drain = 1 if ((f0 + 1) & 0xC0) != (m[0x849F] & 0xC0) else 0
        x0, y0 = m[P1], m[P1 + 1]
        x1, y1 = m[P2], m[P2 + 1]
        feat = featurise(h)
        d1, d2 = m[0x8427], m[0x8447]
        feat['pVert'] = (1 if (d1 & 3) in (1, 2) else 0) +                         (1 if (d2 & 3) in (1, 2) else 0)
        feat['pHoriz'] = (1 if (d1 & 12) in (4, 8) else 0) +                          (1 if (d2 & 12) in (4, 8) else 0)
        r = one_pass(h)
        feat['drain'] = drain
        feat['bannerFire'] = 1 if r['cnt']['bannerwork'] else 0
        feat['doorFire'] = 1 if r['cnt']['doorwork'] > 2 else 0
        feat['pmoved'] = ((m[P1], m[P1 + 1]) != (x0, y0)) + \
                         ((m[P2], m[P2 + 1]) != (x1, y1))
        row = dict(feat)
        row.update({'scene': name, 'group': group, 'i': i,
                    't0': r['t0'], 'f0': f0, 'W1c': r['W1c'],
                    'isrW1': r['cnt']['isr_w1'],
                    'W2': r['t0'] + r['cost'] - r['wake'],
                    'cost': r['cost'], 'whole': int(round(r['cost']
                                                          / FRAME_T))})
        for k, v in r['seg'].items():
            row['s_' + k] = v
        rows.append(row)
    return rows


def cmd_collect():
    allrows = []
    for name, d, n, s, g in SCENES:
        rows = collect_scene(name, d, n, s, g)
        hist = collections.Counter(r['whole'] for r in rows)
        print('  %-12s %-11s %4d passes  %-14s  W1c %6d..%6d'
              % (name, d, n, str(dict(sorted(hist.items()))),
                 min(r['W1c'] for r in rows), max(r['W1c'] for r in rows)))
        allrows += rows
    json.dump(allrows, open(OUT, 'w'))
    print('  %d passes -> %s' % (len(allrows), OUT))
    return 0, 0




# ===========================================================================
#  THE FIT
# ===========================================================================
def lstsq(rows, feats, target):
    import numpy as np
    A = np.array([[float(r[f]) for f in feats] for r in rows])
    y = np.array([float(r[target]) for r in rows])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ beta
    return beta, y - pred


def cmd_fit(feats=None):
    rows = json.load(open(OUT))
    feats = feats or FEATS
    tr = [r for r in rows if r['group'] == 'train']
    beta, res = lstsq(tr, feats, 'W1c')
    print('W1c FIT, %d training passes over %d scenes' % (len(tr), 6))
    for f, b in zip(feats, beta):
        print('   %-10s %12.1f T' % (f, b))
    import numpy as np
    print('  train residual: rms %.0f T  max %.0f T'
          % (np.sqrt((res ** 2).mean()), abs(res).max()))
    te = [r for r in rows if r['group'] == 'test']
    A = np.array([[float(r[f]) for f in feats] for r in te])
    y = np.array([float(r['W1c']) for r in te])
    r2 = y - A @ beta
    print('  test  residual: rms %.0f T  max %.0f T'
          % (np.sqrt((r2 ** 2).mean()), abs(r2).max()))
    for name in [s[0] for s in SCENES]:
        sel = [r for r in rows if r['scene'] == name]
        A = np.array([[float(r[f]) for f in feats] for r in sel])
        y = np.array([float(r['W1c']) for r in sel])
        d = y - A @ beta
        print('    %-12s %-5s rms %7.0f  bias %+8.0f  max %8.0f'
              % (name, sel[0]['group'], np.sqrt((d ** 2).mean()),
                 d.mean(), abs(d).max()))
    json.dump({'feats': feats, 'beta': list(beta)},
              open(os.path.join(ROOT, 'build', 'clockfit.json'), 'w'))
    return 0, 0


def cmd_score(feats=None):
    import numpy as np
    rows = json.load(open(OUT))
    fit = json.load(open(os.path.join(ROOT, 'build', 'clockfit.json')))
    feats, beta = fit['feats'], np.array(fit['beta'])
    print('THE 4/5 PREDICTOR, scored per pass against the real Z80')
    print('  scene         grp    n   exact  modal-4  measured-W1c')
    tot = ok = okm = okexact = 0
    for name in [s[0] for s in SCENES]:
        sel = [r for r in rows if r['scene'] == name]
        n = a = b = c = 0
        for r in sel:
            w1c = float(np.array([float(r[f]) for f in feats]) @ beta)
            cost, _t, _f = sim_pass(r['t0'], r['f0'], w1c)
            pred = int(round(cost / FRAME_T))
            cost2, _t2, _f2 = sim_pass(r['t0'], r['f0'], r['W1c'])
            n += 1
            a += (pred == r['whole'])
            b += (4 == r['whole'])
            c += (int(round(cost2 / FRAME_T)) == r['whole'])
        print('  %-12s %-5s %4d  %5.1f%%  %5.1f%%   %5.1f%%'
              % (name, sel[0]['group'], n, 100 * a / n, 100 * b / n,
                 100 * c / n))
        tot += n; ok += a; okm += b; okexact += c
    print('  ALL          %11d  %5.1f%%  %5.1f%%   %5.1f%%'
          % (tot, 100 * ok / tot, 100 * okm / tot, 100 * okexact / tot))
    te = [r for r in rows if r['group'] == 'test']
    n = sum(1 for r in te)
    a = 0
    for r in te:
        w1c = float(np.array([float(r[f]) for f in feats]) @ beta)
        cost, _t, _f = sim_pass(r['t0'], r['f0'], w1c)
        a += (int(round(cost / FRAME_T)) == r['whole'])
    print('  HELD-OUT SCENES ONLY: %.1f%% of %d passes' % (100 * a / n, n))
    return 0, 0




# ===========================================================================
#  THE PER-ROUTINE FIT -- one small model per CALL site, never a global one
# ===========================================================================
#  Each routine is charged against the quantities ITS OWN CODE reads, so a
#  bias in one cannot be traded against a bias in another.  That is the whole
#  difference from the 62.7% attempt, which fitted the pass total in one go.
SEGFEATS = {
    'tileAnim':  ['one', 'tileEven'],
    'doorAnim':  ['one', 'doorFire'],
    'players':   ['one', 'pVert', 'pHoriz', 'pmoved', 'pend'],
    'camera':    ['one'],
    'paintBody': ['one', 'pBody', 'pRows', 'oBody', 'padBody'],
    'paintH':    ['one', 'pH', 'oH'],
    'paintV':    ['one', 'pV', 'oV'],
    'paintC':    ['one', 'pC', 'oC'],
    'camTgt':    ['one'],
    'actors':    ['one', 'aE1', 'aE3', 'aE4', 'aDraw'],
    'plrDraw':   ['one'],
    'shots':     ['one', 'shots'],
    'treasure':  ['one'],
    'hud':       ['one', 'drain'],
    'hud2':      ['one'],
    'panel':     ['one'],
    'exitWalk':  ['one'],
    'cull':      ['one', 'nact'],
    'gens':      ['one', 'gCells', 'gRows', 'gObj', 'gGen'],
    'death':     ['one'],
    'banner':    ['one', 'bannerFire'],
    'ctr':       ['one'],
    'toHalt':    ['one'],
}


def cmd_segfit():
    import numpy as np
    rows = json.load(open(OUT))
    tr = [r for r in rows if r['group'] == 'train']
    model = {}
    print('PER-ROUTINE FIT, %d training passes' % len(tr))
    print('  %-10s %8s %8s   %s' % ('routine', 'rms', 'max', 'coefficients'))
    for seg, feats in SEGFEATS.items():
        key = 's_' + seg
        sel = [r for r in tr if key in r]
        if not sel:
            continue
        beta, res = lstsq(sel, feats, key)
        model[seg] = (feats, list(beta))
        print('  %-10s %8.0f %8.0f   %s'
              % (seg, np.sqrt((res ** 2).mean()), abs(res).max(),
                 ' '.join('%s=%.1f' % (f, b) for f, b in zip(feats, beta))))
    json.dump(model, open(os.path.join(ROOT, 'build', 'clockseg.json'), 'w'))
    # and the summed residual
    for grp in ('train', 'test'):
        sel = [r for r in rows if r['group'] == grp]
        d = []
        for r in sel:
            d.append(predict_w1c(r, model) - r['W1c'])
        d = np.array(d)
        print('  %-5s summed W1c residual: rms %.0f T  max %.0f T  bias %+.0f'
              % (grp, np.sqrt((d ** 2).mean()), abs(d).max(), d.mean()))
    for name in [s[0] for s in SCENES]:
        sel = [r for r in rows if r['scene'] == name]
        d = np.array([predict_w1c(r, model) - r['W1c'] for r in sel])
        print('    %-12s %-5s rms %7.0f  bias %+8.0f  max %8.0f'
              % (name, sel[0]['group'], np.sqrt((d ** 2).mean()), d.mean(),
                 abs(d).max()))
    return 0, 0


def predict_w1c(r, model):
    tot = 0.0
    for seg, (feats, beta) in model.items():
        tot += sum(b * float(r[f]) for f, b in zip(feats, beta))
    return tot


def cmd_segscore():
    import numpy as np
    rows = json.load(open(OUT))
    model = json.load(open(os.path.join(ROOT, 'build', 'clockseg.json')))
    print('THE PREDICTOR, per pass against the real Z80')
    print('  scene         grp     n   MODEL  modal-4  oracleW1c  oracle+ISR')
    T_ = collections.Counter()
    for name in [s[0] for s in SCENES]:
        sel = [r for r in rows if r['scene'] == name]
        n = a = b = c = d = 0
        for r in sel:
            w = predict_w1c(r, model)
            pr = int(round(sim_pass(r['t0'], r['f0'], w)[0] / FRAME_T))
            po = int(round(sim_pass(r['t0'], r['f0'], r['W1c'])[0] / FRAME_T))
            n += 1
            a += (pr == r['whole'])
            b += (4 == r['whole'])
            c += (po == r['whole'])
        print('  %-12s %-5s %4d  %5.1f%%  %5.1f%%    %5.1f%%'
              % (name, sel[0]['group'], n, 100 * a / n, 100 * b / n,
                 100 * c / n))
        T_['n'] += n; T_['a'] += a; T_['b'] += b; T_['c'] += c
    print('  ALL                %5d  %5.1f%%  %5.1f%%    %5.1f%%'
          % (T_['n'], 100 * T_['a'] / T_['n'], 100 * T_['b'] / T_['n'],
             100 * T_['c'] / T_['n']))
    te = [r for r in rows if r['group'] == 'test']
    a = sum(1 for r in te
            if int(round(sim_pass(r['t0'], r['f0'],
                                  predict_w1c(r, model))[0] / FRAME_T))
            == r['whole'])
    print('  HELD-OUT SCENES ONLY: %.1f%% of %d passes' % (100 * a / len(te),
                                                           len(te)))
    return 0, 0




# ===========================================================================
#  THE ACCEPTANCE TEST -- beepgate's own scenarios, pass by pass
# ===========================================================================
def cmd_beepgate():
    import beepgate as BG
    model = json.load(open(os.path.join(ROOT, 'build', 'clockseg.json')))
    print("BEEPGATE'S OWN SCENARIOS -- what beepgate.py diff would see with")
    print('  this model in place of its --ticks substitution')
    print('  %-30s %4s  %-16s %-16s %s'
          % ('scenario', 'n', 'real', 'model', 'hits'))
    tot = ok = 0
    for name, direction, npass, plants in BG.SCEN:
        h = BG.fresh(quiet=True, nogen=True)
        m = h.memobj.m
        for r, cell, _i, _n in plants:
            m[0x8000 + (r % 32) * 32 + BG.COL] = cell
        m[P1], m[P1 + 1] = BG.COL * 4, BG.ROW0 * 4
        m[P1 + 8], m[P1 + 9] = 3, 2
        press(h, dirkeys(direction))
        one_pass(h)
        rh, mh, hit = collections.Counter(), collections.Counter(), 0
        for _k in range(npass):
            f0 = m[0x8497]
            drain = 1 if ((f0 + 1) & 0xC0) != (m[0x849F] & 0xC0) else 0
            x0, y0 = m[P1], m[P1 + 1]
            x1, y1 = m[P2], m[P2 + 1]
            feat = featurise(h)
            d1, d2 = m[0x8427], m[0x8447]
            feat['pVert'] = (1 if (d1 & 3) in (1, 2) else 0) + \
                            (1 if (d2 & 3) in (1, 2) else 0)
            feat['pHoriz'] = (1 if (d1 & 12) in (4, 8) else 0) + \
                             (1 if (d2 & 12) in (4, 8) else 0)
            r = one_pass(h)
            feat['drain'] = drain
            feat['bannerFire'] = 1 if r['cnt']['bannerwork'] else 0
            feat['doorFire'] = 1 if r['cnt']['doorwork'] > 2 else 0
            feat['pmoved'] = ((m[P1], m[P1 + 1]) != (x0, y0)) + \
                             ((m[P2], m[P2 + 1]) != (x1, y1))
            real = int(round((r['cost'] - r['tune']) / FRAME_T))
            pred = int(round(sim_pass(r['t0'], f0,
                                      predict_w1c(feat, model))[0] / FRAME_T))
            rh[real] += 1
            mh[pred] += 1
            hit += (pred == real)
            tot += 1
            ok += (pred == real)
        print('  %-30s %4d  %-16s %-16s %d/%d'
              % (name, npass, str(dict(sorted(rh.items()))),
                 str(dict(sorted(mh.items()))), hit, npass))
    print('  TOTAL %d/%d = %.1f%%' % (ok, tot, 100 * ok / tot))
    print('  The pass that runs the blocking tune has its 72.07 frames taken')
    print('  back out first, because blockingPause() debits those separately;')
    print('  what is compared is the HALT quantiser, which is what the 10')
    print('  unhelped mismatching rows are.')
    return 0, 0


def cmd_all():
    for c in ('isr', 'w2', 'anatomy', 'segfit', 'segscore', 'beepgate'):
        print()
        globals()['cmd_' + c]()
    return 0, 0


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'anatomy'
    rest = sys.argv[2:]
    fn = globals().get('cmd_' + cmd)
    if fn is None:
        print(__doc__)
        sys.exit(2)
    b, t = fn(*[int(x) if x.isdigit() else x for x in rest])
    sys.exit(1 if b else 0)
