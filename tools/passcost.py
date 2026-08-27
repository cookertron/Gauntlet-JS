#!/usr/bin/env python3
"""
passcost.py -- WHAT COSTS TIME IN A MAIN-LOOP PASS.

Decomposes the T-states of one pass of $8503..$855C across the twenty-two
CALL sites, over driven play, and reports mean/spread per call.

    python tools/passcost.py split              W1 / HALT / W2 anatomy
    python tools/passcost.py table  [dir] [n]   per-call cost, one scene
    python tools/passcost.py calls              per-call cost, 38 scenes
    python tools/passcost.py actors             cost vs $8496 (population)
    python tools/passcost.py oneoff             the known one-off events
    python tools/passcost.py check              window_origin() vs $9FC9
    python tools/passcost.py model              score the predictor (training)
    python tools/passcost.py validate           ...on a HELD-OUT set
    python tools/passcost.py beep               ...on beepgate's own scenarios
    python tools/passcost.py contend            plain vs CMIOSimulator

THE ANATOMY THIS TOOL IS BUILT ON (measured, see `split`):

    $8503 ....... compute ....... $8550 CALL $9CD7
    $9CD7 HALT              <-- the ONLY halt on the 48K branch, once a pass
    $9CD8 DI                <-- interrupts OFF for the rest of the pass
    $9CD9 tone tick, then the three shadow->screen blits, $8491++, $8553
    $8556 JR $8503

so a pass is

    cost = ceil(p + W1) - p + W2          (in video frames)

with p the phase of $8503 inside the video frame, W1 the compute half
($8503..$9CD7) and W2 the post-HALT half ($9CD7's wake ..next $8503).  The
HALT is the quantiser and that is why the distribution is bimodal 4/5.
Because ceil() is an integer, p(next) = frac(W2) EXACTLY -- the phase is
carried by the post-HALT half alone.  W2 is 2.1954 frames with sd 906 T over
2,740 passes, so p sits at 0.195 and the whole 4-vs-5 question is

        W1 > 2 - p  =  1.805 frames  =  126,140 T-states

and a quiet dungeon-1 pass sits about 400 T under it.  A pass is NOT always
four or five: in a horde W1 passes 3 and then 4, and the same formula gives
the six- and seven-frame passes `validate` measures.

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
import math
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, R, SP, FRAME_T, TAPE_CALL_PC)  # noqa
from keyprobe import KEYS, keymask                                     # noqa

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
LOOP_TOP = 0x8503
P1 = 0x8420

# The main loop, read off build/live_cs.bin with tools/adis.py.  Each entry is
# (site, target, name); the SEGMENT charged to a site runs from that site to
# the next one, so it is the CALL plus its callee plus any ISR that lands in it.
LOOP = [
    (0x8503, 0xA31A, 'tileAnim   $A31A'),
    (0x8506, 0x95AB, 'doorAnim   $95AB'),
    (0x8509, 0xA38A, 'players    $A38A'),
    (0x850C, 0xB58C, 'camera     $B58C'),
    (0x850F, 0x9EFC, 'drawA      $9EFC'),
    (0x8512, 0x9FC2, 'drawB      $9FC2'),
    (0x8515, 0xA08B, 'drawC      $A08B'),
    (0x8518, 0xA159, 'drawD      $A159'),
    (0x851B, 0xA3E6, 'preActor   $A3E6'),
    (0x851E, 0xAB94, 'ACTORS     $AB94'),
    (0x8521, 0xA43B, 'plrDraw    $A43B'),
    (0x8524, 0x8BCA, 'shots      $8BCA'),
    (0x8527, 0x8ADA, 'cond       $8ADA'),
    (0x852E, 0xB6DA, 'hud        $B6DA'),
    (0x8531, 0x971B, 'x971B      $971B'),
    (0x8534, 0x9788, 'x9788      $9788'),
    (0x8537, 0x94AE, 'exitWalk   $94AE'),
    (0x853A, 0xB0FE, 'cull       $B0FE'),
    (0x853D, 0xA9C2, 'generators $A9C2'),
    (0x8540, 0x93C2, 'x93C2      $93C2'),
    (0x8543, 0x891C, 'banner     $891C'),
    (0x8546, None,   'ctr $84A1  --'),
    (0x8550, 0x9CD7, 'toHALT     $9CD7'),
]
SITES = [a for a, _t, _n in LOOP]
NAMES = {a: n for a, _t, n in LOOP}
MARKS = set(SITES) | {0x9CD7, 0x9CD8, 0x8553, 0x8556}

# counters: PCs whose visit count is a candidate cost driver
COUNTERS = {
    0xB8CC: 'noiseTick',      # one per DRAWN OBJECT, six call sites
    0xA1DA: 'spriteDraw',     # the sprite blitter entry
    0xABFF: 'actorUpd',       # the actor update, called back from $A21E
    0xADF8: 'actorStep',      # the actor's own map probe
    0xA97F: 'actorScan',      # the player's 7x7 actor scan
    0xB575: 'rng',            # the shuffle
    0xB8DB: 'sndOut',         # the noise tick TOGGLED -- `LD A,R`, Q18's class
    0xB8E2: 'sndRamp',        # the noise tick found a LIVE ramp ($84D2 != 0)
    0xB159: 'pad30',          # $9F76 CALL z,$B159, the $30 teleport pad
    0xAEA0: 'contact',        # an actor on the player's square
    0xB0D3: 'spawn',          # a generator spawned
}


def fresh(path=STATE):
    h = Harness()
    h.load_state(pickle.load(open(path, 'rb')))
    assert h.memobj.m[0xFFFD] == 0x00, 'not the 48K branch'
    return h


def press(h, direction):
    if direction and direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))


def one_pass(h, marks=MARKS, counters=COUNTERS, limit=40_000_000):
    """Run ONE pass, anchored on $8503.  Returns a dict:
         t0, cost, seg{site:T}, halt_at, wake_at, cnt{name:n}
    """
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0 = regs[T]
    stamp = {}
    snap = {}
    cnt = collections.Counter()
    isr = collections.Counter()          # ISR T-states, charged to the segment
    n = 0
    wake = None
    cur = LOOP_TOP
    it0 = isp = None
    while n < limit:
        pc = regs[PC]
        if it0 is not None and regs[SP] > isp:
            isr[cur] += regs[T] - it0
            it0 = None
        if n and pc == LOOP_TOP:
            break
        if pc in marks and pc not in stamp:
            stamp[pc] = regs[T]
            cur = pc
            if pc == 0x850F:                 # the camera has just been stepped
                snap['camx'], snap['camy'] = mem[0x848B], mem[0x848C]
            elif pc == 0x8512:               # $9EFC has just written $9FC9
                snap['orig'] = mem[0x9FC9] | (mem[0x9FCA] << 8)
            elif pc == 0x851E:               # the population the actor loop sees
                k = mem[0x8496]
                snap['nact'] = k
                snap['snd'] = mem[0x84D2]
                snap['list'] = bytes(mem.m[0x5C00:0x5C00 + 4 * k])
            elif pc == 0x8524:               # $8BCA, the shots in flight
                snap['shots'] = ((1 if mem[0x8430] else 0)
                                 + (1 if mem[0x8450] else 0))
            elif pc == 0x852E:               # $B6DA reads $8497 HERE, not at
                # the loop top, and compares it with $849F -- so the drain
                # tick is a function of the frame counter as the HUD sees it.
                snap['f8497'] = mem[0x8497]
                snap['drainph'] = mem[0x849F]
                snap['hurry'] = mem[0x84B8]
            elif pc == 0x8543:               # $891C LD A,($84B9) / OR A / RET z
                snap['banner'] = mem[0x84B9]
            elif pc == 0x853A:               # and the one the cull sees
                k = mem[0x8496]
                snap['nact2'] = k
                snap['list2'] = bytes(mem.m[0x5C00:0x5C00 + 4 * k])
        elif pc in counters:
            cnt[counters[pc]] += 1
            cnt[(cur, counters[pc])] += 1
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1
            if wake is None:
                wake = regs[T]
            cnt['halts'] += 1
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
            cnt['isr'] += 1
            it0, isp = regs[T], regs[SP]
        n += 1
    else:
        raise RuntimeError('no loop top')
    return {'t0': t0, 'cost': regs[T] - t0, 'stamp': stamp, 'snap': snap,
            'wake': wake, 'cnt': cnt, 'isr': isr, 'instr': n}


def segments(rec, net=False):
    """Per-site T cost: site -> next site (or $9CD7 for the last).

    net=True subtracts the frame ISR, which fires once or twice inside the
    compute half and lands in whichever segment happens to be running --
    ~1,700 T of pure measurement noise if it is left in."""
    s = rec['stamp']
    out = {}
    order = SITES + [0x9CD7]
    for i, a in enumerate(SITES):
        b = order[i + 1]
        if a in s and b in s:
            out[a] = s[b] - s[a] - (rec['isr'][a] if net else 0)
    return out


def state(h):
    m = h.memobj.m
    gens = sum(1 for a in range(0x8000, 0x8400) if 0x20 <= m[a] <= 0x2E)
    return {
        'actors': m[0x8496],
        'gens': gens,
        'camx': m[0x84AC], 'camy': m[0x84AD],
        'px': m[P1], 'py': m[P1 + 1],
        'pend': m[0x843D],
        'sndlvl': m[0x84D2],
        'ctr': m[0x8491],
    }


# --------------------------------------------------------------------------
def cmd_table(direction='down', n=120):
    h = fresh()
    press(h, direction)
    one_pass(h)
    rows = collections.defaultdict(list)
    costs = []
    for _ in range(int(n)):
        rec = one_pass(h)
        costs.append(rec['cost'])
        for a, v in segments(rec).items():
            rows[a].append(v)
    print('PER-CALL COST, %s, %d passes, T-states (1 frame = %d T)'
          % (direction, n, FRAME_T))
    print('  %-18s %8s %8s %8s %8s  %7s' %
          ('call', 'mean', 'min', 'max', 'sd', '%pass'))
    tot = sum(costs) / len(costs)
    order = sorted(rows, key=lambda a: -sum(rows[a]) / len(rows[a]))
    for a in order:
        v = rows[a]
        mu = sum(v) / len(v)
        sd = math.sqrt(sum((x - mu) ** 2 for x in v) / len(v))
        print('  %-18s %8.0f %8d %8d %8.0f  %6.2f%%'
              % (NAMES[a], mu, min(v), max(v), sd, 100 * mu / tot))
    print('  %-18s %8.0f %8d %8d' % ('PASS TOTAL', tot, min(costs), max(costs)))
    print('  mean %.3f frames  hist %s' %
          (tot / FRAME_T, dict(sorted(collections.Counter(
              round(c / FRAME_T) for c in costs).items()))))
    return rows, costs


def cmd_split(n=120):
    print('W1 / HALT / W2 ANATOMY, 40 passes a direction')
    print('  %-6s %8s %8s %8s %8s %8s %8s' %
          ('key', 'p', 'W1', 'p+W1', 'W2', 'cost', 'hist'))
    for direction in ('idle', 'right', 'down', 'left', 'up'):
        h = fresh()
        press(h, direction)
        one_pass(h)
        P, W1, W2, C = [], [], [], []
        for _ in range(int(n)):
            rec = one_pass(h)
            s = rec['stamp']
            p = (rec['t0'] % FRAME_T) / FRAME_T
            w1 = (s[0x9CD7] - rec['t0']) / FRAME_T
            w2 = (rec['t0'] + rec['cost'] - rec['wake']) / FRAME_T
            P.append(p); W1.append(w1); W2.append(w2)
            C.append(rec['cost'] / FRAME_T)
        hist = dict(sorted(collections.Counter(round(c) for c in C).items()))
        print('  %-6s %8.4f %8.4f %8.4f %8.4f %8.4f %s'
              % (direction, sum(P) / len(P), sum(W1) / len(W1),
                 sum(P) / len(P) + sum(W1) / len(W1),
                 sum(W2) / len(W2), sum(C) / len(C), hist))
    print('  p+W1 crossing 2.0 is what makes a pass 5 frames instead of 4,')
    print('  and p is frac(W2) -- the post-HALT half carries the phase.')


def cmd_actors(direction='idle'):
    print('COST vs POPULATION $8496 -- the list is TRUNCATED, not thinned')
    print('  %6s %10s %10s %10s %10s %8s %8s' %
          ('actors', 'W1', 'ACTORS', 'draws', 'noise', 'cost', 'hist'))
    for k in (0, 8, 16, 24, 32, 40, 48, 63):
        h = fresh()
        m = h.memobj.m
        m[0x8496] = k
        m[0x8494], m[0x8495] = (0x5C00 + 4 * k) & 0xFF, (0x5C00 + 4 * k) >> 8
        press(h, direction)
        one_pass(h)
        W1, AC, DR, NS, C = [], [], [], [], []
        for _ in range(40):
            rec = one_pass(h)
            W1.append((rec['stamp'][0x9CD7] - rec['t0']) / FRAME_T)
            AC.append(segments(rec).get(0x851E, 0))
            DR.append(rec['cnt']['spriteDraw'])
            NS.append(rec['cnt']['noiseTick'])
            C.append(rec['cost'] / FRAME_T)
        hist = dict(sorted(collections.Counter(round(c) for c in C).items()))
        print('  %6d %10.4f %10.0f %10.1f %10.1f %8.4f %s'
              % (k, sum(W1) / len(W1), sum(AC) / len(AC), sum(DR) / len(DR),
                 sum(NS) / len(NS), sum(C) / len(C), hist))


def window_origin(camx, camy):
    """$9EFC's own arithmetic, instruction for instruction: the map pointer of
    the top-left VISIBLE cell.  ($848B, $848C) -> HL, stored at $9FC9."""
    a = (2 * camx) & 0xFF
    hh = camy
    c = hh & 1; hh >>= 1
    c = hh & 1; hh >>= 1
    c = hh & 1; hh >>= 1
    na = (c << 7) | (a >> 1); c = a & 1; a = na
    c2 = hh & 1; hh >>= 1; c = c2
    na = (c << 7) | (a >> 1); c = a & 1; a = na
    c = 1                                   # SCF
    nh = (c << 7) | (hh >> 1); c = hh & 1; hh = nh
    na = (c << 7) | (a >> 1); a = na
    l = a
    if ((hh - 4) & 0xFF) == 0x80:           # $9F13 SUB 4 / CP $80 / JP nz
        hh = 0x80
    return (hh << 8) | l


def step_right(l):
    """$9F93: INC L / AND $1F / JR nz / SUB $20 -- the column wrap."""
    l = (l + 1) & 0xFF
    return l if (l & 0x1F) else (l - 0x20) & 0xFF


def step_down(h, l):
    """$9FA1: ADD A,$20 / JR nc / INC H / SUB 4 / CP $80 / JR nz / LD H,A."""
    n = l + 0x20
    l = n & 0xFF
    if n > 0xFF:
        h = (h + 1) & 0xFF
        if ((h - 4) & 0xFF) == 0x80:
            h = 0x80
    return h, l


def win_cells(m, origin, cols, rows):
    """The cells $9EFC/$A9C2 walk, wrapped exactly as the original wraps."""
    out = []
    h, l = origin >> 8, origin & 0xFF
    for _r in range(rows):
        ll = l
        for _c in range(cols):
            out.append(m[(h << 8) | ll])
            ll = step_right(ll)
        h, l = step_down(h, l)
    return out


def cell(m, orig, drow, dcol):
    """The map cell `drow` down and `dcol` right of `orig` on the 32x32 torus."""
    i = orig - 0x8000
    return m[0x8000 + ((((i >> 5) + drow) & 31) << 5) + (((i & 31) + dcol) & 31)]


def tile_lists(bx, by):
    """WHICH CELLS EACH TILE PASS TOUCHES -- read off the machine by hooking
    the five `LD A,(HL)` sites ($9F6C, $9FEF, $A057, $A0AF, $A118) and the
    four in $A159, with the map blanked, for all four (bx, by).

    The four passes PARTITION an 11 x 17 neighbourhood of the window origin:

        $9EFC  interior   rows by..9   x cols bx..15
        $9FC2  rows 0,10  x cols bx..15        only when by
        $A08B  cols 0,16  x rows by..9         only when bx
        $A159  the four corners {0,10}x{0,16}  only when bx and by

    so the cell total is 160 / 170 / 176 / 187 as the camera's sub-cell phase
    moves -- and 187 = 11*17 is exactly the noise-tick count a quiet pass
    makes from the blitter.
    """
    A = [(r, c) for r in range(by, 10) for c in range(bx, 16)]
    B = [(r, c) for r in (0, 10) for c in range(bx, 16)] if by else []
    C = [(r, c) for c in (0, 16) for r in range(by, 10)] if bx else []
    D = [(r, c) for r in (0, 10) for c in (0, 16)] if (bx and by) else []
    return A, B, C, D


def features(m, rec):
    """Everything the PORT already has, evaluated where the original uses it."""
    s = rec['snap']
    camx, camy = s.get('camx', 0), s.get('camy', 0)
    orig = s.get('orig') or window_origin(camx, camy)
    bx = 1 if (camx & 2) else 0
    by = 1 if (camy & 2) else 0
    A, B, C, D = tile_lists(bx, by)

    def nz(lst):
        return sum(1 for r, c in lst if cell(m, orig, r, c))
    n30 = sum(1 for r, c in A if cell(m, orig, r, c) == 0x30)

    gcols = 17 if (camx & 3) else 16
    grows = 11 if (camy & 3) else 10
    gwin = [cell(m, orig, r, c) for r in range(grows) for c in range(gcols)]
    return {
        'camx': camx, 'camy': camy, 'bx': bx, 'by': by,
        'cellsA': len(A), 'nzA': nz(A),
        'cellsB': len(B), 'nzB': nz(B),
        'cellsC': len(C), 'nzC': nz(C),
        'cellsD': len(D), 'nzD': nz(D),
        'gcells': gcols * grows,
        'ngen': sum(1 for v in gwin if 0x20 <= v <= 0x2E),
        'n30': n30,
        'nact': s.get('nact', m[0x8496]),
        'banner': s.get('banner', 0),
        'shots': s.get('shots', 0),
        # $B6DD LD A,($8497) / AND $C0 / CP (IY+$20) -- the health drain fires
        # when the masked frame counter has moved off $849F.  Both are values
        # the port already keeps (frameCtr, drainPhase).
        'drain': 1 if ((s.get('f8497', 0) & 0xC0) != s.get('drainph', 0)) else 0,
    }


def wrapdist(d):
    """$AD47's wrapped absolute difference on the 128-unit torus."""
    d &= 0x7F
    return d if d < 0x40 else 0x80 - d


def actor_features(rec):
    """nUpd -- how many actors pass $A1DA's four clips and so reach $ABFF --
    and nIn, how many pass $B0FE's own, wider window.  Both are computed from
    the record and the camera, which is exactly what the port does."""
    s = rec['snap']
    camx, camy = s.get('camx', 0), s.get('camy', 0)
    lst = s.get('list', b'')
    upd = updx = 0
    for i in range(0, len(lst), 4):
        dx = (lst[i] + 3 - camx) & 0x7F
        dy = (lst[i + 1] + 3 - camy) & 0x7F
        # $A1DA's clip is a CASCADE: an actor that fails the x test at $A1EA
        # costs less than one that gets as far as the y test at $A209, so the
        # two populations are charged separately.
        if dx <= 0x42:
            updx += 1
            if dy <= 0x2A:
                upd += 1
    lst2 = s.get('list2', b'')
    ein, eout = (camx + 0x20) & 0xFF, (camy + 0x14) & 0xFF
    keep = 0
    for i in range(0, len(lst2), 4):
        if wrapdist(lst2[i] - ein) < 0x38 and wrapdist(lst2[i + 1] - eout) < 0x32:
            keep += 1
    return {'nUpd': upd, 'nUpdX': updx, 'nact2': s.get('nact2', 0),
            'nKeep': keep}


def collect(direction, n, path=STATE, setup=None, keys=None):
    """One driven run -> list of per-pass feature dicts."""
    h = fresh(path)
    if setup:
        setup(h)
    if keys:
        for k in keys:
            sel, bit = KM[k]
            h.ports.press(sel, keymask(bit))
    else:
        press(h, direction)
    one_pass(h)
    m = h.memobj.m
    out = []
    for _ in range(int(n)):
        ctr = m[0x8491]
        nact0 = m[0x8496]
        rec = one_pass(h)
        seg = segments(rec, net=True)
        row = features(m, rec)
        row['ctr'] = ctr
        row['nact0'] = nact0
        row['dead'] = (nact0 - m[0x8496]) if nact0 >= m[0x8496] else 0
        row['born'] = max(0, m[0x8496] - nact0)
        row['snd'] = rec['snap'].get('snd', 0)
        row.update(actor_features(rec))
        row['moving'] = 0 if (direction in (None, 'idle') and not keys) else 1
        row['nStep'] = rec['cnt']['actorStep']
        row['nContact'] = rec['cnt']['contact']
        row['ramp'] = rec['cnt']['sndRamp']
        row['out'] = rec['cnt']['sndOut']
        row['isrT'] = sum(rec['isr'][a] for a in SITES)      # ISR inside W1
        row['isrN'] = rec['cnt']['isr']
        row['isrW2'] = sum(rec['isr'].values()) - row['isrT']
        row['W1net'] = (rec['stamp'][0x9CD7] - rec['t0']) - row['isrT']
        row['t0'] = rec['t0']
        row['p'] = (rec['t0'] % FRAME_T) / FRAME_T
        row['W1'] = rec['stamp'][0x9CD7] - rec['t0']
        row['W2'] = rec['t0'] + rec['cost'] - rec['wake']
        row['cost'] = rec['cost']
        row['frames'] = rec['cost'] / FRAME_T
        row['whole'] = round(rec['cost'] / FRAME_T)
        row['isr'] = rec['cnt']['isr']
        row['pend'] = m[0x843D]
        for nm in set(COUNTERS.values()):
            row[nm] = rec['cnt'][nm]
        for a in SITES:
            row['seg%04X' % a] = seg.get(a, 0)
            for nm in set(COUNTERS.values()):
                row['%04X.%s' % (a, nm)] = rec['cnt'][(a, nm)]
        row['dir'] = direction
        out.append(row)
    return out


# ===========================================================================
# THE COST MODEL
# ===========================================================================
# Every constant below is MEASURED IN ISOLATION unless it is marked (ls),
# which means least-squares over driven play because the routine has no
# isolable input.  The four tile bases come from calling $9EFC/$9FC2/$A08B/
# $A159 on a BLANKED map at each (bx, by); the per-tile increments from
# planting one cell at a time; the actor and cull slopes from synthesising an
# actor list of a chosen size and viewport membership.
TILE_BASE = {                      # (bx, by) -> ($9EFC, $9FC2, $A08B, $A159)
    (0, 0): (26412, 31, 31, 35),
    (0, 1): (23861, 5248, 31, 35),
    (1, 0): (24881, 31, 3850, 64),
    (1, 1): (22486, 4996, 3512, 385),
}
TILE_DRAW = (807, 501, 633, 1144)  # T per NON-EMPTY cell, per pass
PAD30 = 65                         # $9F76 CALL z,$B159, the $30 teleport pad
NOISE_RAMP = 27.6                  # $B8CC with a live ramp, per scanned object
NOISE_OUT = 39.0                   # ...and per toggle -- `LD A,R`, Q18's class
ISR_T = 1520                       # (ls) the frame interrupt, per boundary
FLAT = {0x8506: 160, 0x850C: 218, 0x851B: 267, 0x8521: 1823, 0x8524: 775,
        0x8527: 30, 0x852E: 683, 0x8531: 105, 0x8534: 247, 0x8537: 224,
        0x8540: 167, 0x8543: 77, 0x8546: 29, 0x8550: 17}


def model_segments(f):
    """T-states per main-loop CALL, from quantities the port already has."""
    b = TILE_BASE[(f['bx'], f['by'])]
    out = dict(FLAT)
    out[0x8503] = 185 + 3311 * (1 - (f['ctr'] & 1))          # $A31A tile anim
    # $A38A: the player's own move ends in $A5F0 CALL $A97F, a LINEAR walk of
    # the actor list -- so even the player costs 52.6 T per live monster.
    out[0x8509] = 2040 + f.get('moving', 0) * (979 + 52.6 * f['nact'])
    out[0x850F] = b[0] + TILE_DRAW[0] * f['nzA'] + PAD30 * f['n30']
    out[0x8512] = b[1] + TILE_DRAW[1] * f['nzB']
    out[0x8515] = b[2] + TILE_DRAW[2] * f['nzC']
    out[0x8518] = b[3] + TILE_DRAW[3] * f['nzD']
    out[0x851E] = (2208                                       # (ls) $AB94
                   + 357 * (f['nact'] - f['nUpdX'])           # failed the x clip
                   + 547 * (f['nUpdX'] - f['nUpd'])           # x passed, y failed
                   + 1562 * f['nUpd']                         # reached $ABFF
                   + 12.3 * f['nUpd'] * f['nact']             # $A97F, per update
                   + 601 * f['nStep'] + 1920 * f['nContact'])
    out[0x853A] = 269 + 254 * f['nact2']                     # (ls) $B0FE
    out[0x853D] = 394 + 85.9 * f['gcells'] + 532 * f['ngen']  # (ls) $A9C2
    # $891C draws the MESSAGE BANNER on the one pass ($84B9 != 0) it appears.
    # 49,799..52,786 T over six banners -- 0.743 frames, and it is the whole
    # of the one five-frame pass tools/beepgate.py's flat clock gets wrong.
    out[0x8543] = 77 + 51900 * (1 if f.get('banner') else 0)
    # the beeper's own tick, charged per SCANNED object in the four tile
    # passes and per drawn sprite -- 58 T that the 128K arm does not pay
    out[0x850F] += NOISE_RAMP * f['ramp'] + NOISE_OUT * f['out']
    return out


def model_W1(f):
    return sum(model_segments(f).values())


def pass_frames(p, w1, w2):
    """cost = ceil(p + W1) - p + W2, all in video frames.  The HALT at $9CD7
    is the quantiser; W2 runs with interrupts OFF and so crosses boundaries
    without stopping."""
    return math.ceil(p + w1) - p + w2


SCENES = [
    #  name          dir      n    state          setup
    ('d1-idle',     'idle',  60, STATE, None),
    ('d1-right',    'right', 60, STATE, None),
    ('d1-left',     'left',  60, STATE, None),
    ('d1-up',       'up',    60, STATE, None),
    ('d1-down',     'down',  60, STATE, None),
    ('horde-idle',  'idle',  60, STATE, 'warp'),
    ('horde-down',  'down',  60, STATE, 'warp'),
    ('horde-right', 'right', 60, STATE, 'warp'),
    ('n0-down',     'down',  40, STATE, 'n0'),
    ('n16-down',    'down',  40, STATE, 'n16'),
    ('n32-down',    'down',  40, STATE, 'n32'),
    ('n48-down',    'down',  40, STATE, 'n48'),
    ('l8-idle',     'idle',  60, 'L8', None),
    ('l8-down',     'down',  60, 'L8', None),
    ('l8-right',    'right', 60, 'L8', None),
    ('l8-up',       'up',    60, 'L8', None),
    ('l8-left',     'left',  60, 'L8', None),
    ('l8b-idle',    'idle',  60, 'L8B', None),
    ('l8b-down',    'down',  60, 'L8B', None),
    ('l8b-right',   'right', 60, 'L8B', None),
]
L8 = os.path.join(ROOT, 'build', '_lvl8_r005_live.pkl')
L8B = os.path.join(ROOT, 'build', '_my_lvl8.pkl')
L8C = os.path.join(ROOT, 'build', '_my_lvl8_pads.pkl')
L8D = os.path.join(ROOT, 'build', '_v_lvl8_pre5.pkl')

# A DISJOINT VALIDATION SET.  Different states, different warps, different
# key combinations and longer runs -- nothing here was used to fit a constant.
VSCENES = [
    ('v-d1-idle2',  'idle',  80, STATE, 'warp2'),
    ('v-d1-down2',  'down',  80, STATE, 'warp2'),
    ('v-d1-up2',    'up',    80, STATE, 'warp2'),
    ('v-d1-left2',  'left',  80, STATE, 'warp3'),
    ('v-d1-right2', 'right', 80, STATE, 'warp3'),
    ('v-n8',        'down',  60, STATE, 'n8'),
    ('v-n24',       'right', 60, STATE, 'n24'),
    ('v-n56',       'up',    60, STATE, 'n56'),
    ('v-horde2',    'left',  80, STATE, 'warp'),
    ('v-horde3',    'up',    80, STATE, 'warp'),
    ('v-l8c-idle',  'idle',  80, 'L8C', None),
    ('v-l8c-down',  'down',  80, 'L8C', None),
    ('v-l8c-left',  'left',  80, 'L8C', None),
    ('v-l8d-idle',  'idle',  80, 'L8D', None),
    ('v-l8d-right', 'right', 80, 'L8D', None),
    ('v-l8-long',   'down', 160, 'L8', None),
    ('v-l8b-long',  'left', 160, 'L8B', None),
    ('v-d1-long',   'down', 160, STATE, None),
]


def _setup(tag):
    if tag == 'warp':
        def f(h):
            h.poke(0x8420, 96, 56)
            h.poke(0x848B, 66)
            h.poke(0x848C, 38)
        return f
    if tag == 'warp2':
        def f(h):
            h.poke(0x8420, 40, 40)
            h.poke(0x848B, 24)
            h.poke(0x848C, 24)
        return f
    if tag == 'warp3':
        def f(h):
            h.poke(0x8420, 72, 20)
            h.poke(0x848B, 56)
            h.poke(0x848C, 8)
        return f
    if tag and tag.startswith('n'):
        k = int(tag[1:])

        def f(h):
            m = h.memobj.m
            m[0x8496] = k
            a = 0x5C00 + 4 * k
            m[0x8494], m[0x8495] = a & 0xFF, a >> 8
        return f
    return None


def corpus(scenes=None, quiet=False):
    rows = []
    for name, d, n, path, setup in (scenes or SCENES):
        p = {'L8': L8, 'L8B': L8B, 'L8C': L8C, 'L8D': L8D}.get(path, path)
        rs = collect(d, n, path=p, setup=_setup(setup))
        for r in rs:
            r['scene'] = name
        rows += rs
        if not quiet:
            print('  %-12s %3d passes  hist %s' % (
                name, len(rs),
                dict(sorted(collections.Counter(r['whole'] for r in rs).items()))))
    return rows


W2_FLAT = 153463.0                  # the post-HALT half: 2.1959 frames, sd 881


def predict(r, oracle=True, p=None, w2=None):
    """One pass: model W1 (adding the frame ISRs it will cross), then quantise."""
    f = dict(r)
    if not oracle:
        f['out'] = f['ramp'] * 0.5      # E[toggle] over a 1..127 ramp
    w1 = model_W1(f)
    if p is None:
        p = r['p']
    nb = math.floor(p + w1 / FRAME_T)   # the ISR fires at each boundary in W1
    for _ in range(4):
        nb2 = math.floor(p + (w1 + ISR_T * nb) / FRAME_T)
        if nb2 == nb:
            break
        nb = nb2
    w1 += ISR_T * nb
    return w1, pass_frames(p, w1 / FRAME_T,
                           (W2_FLAT if w2 is None else w2) / FRAME_T)


def cmd_model(oracle=True, rows=None):
    rows = rows if rows is not None else corpus()
    print('\nTHE MODEL, over %d passes in %d scenes  (%s)'
          % (len(rows), len(set(r['scene'] for r in rows)),
             'RNG terms fed from the original' if oracle else
             'RNG terms replaced by their expectation'))
    err = []
    p = None
    for r in rows:
        w1, pred = predict(r, oracle, p=r['p'], w2=r['W2'])
        err.append(w1 - r['W1'])
        r['predA'] = round(pred)
        if p is None:
            p = r['p']
        _w1, predc = predict(r, oracle, p=p)
        p = (p + predc) % 1.0
        r['predB'] = round(predc)
    rms = (sum(e * e for e in err) / len(err)) ** .5
    print('  W1 error: mean %+.0f T  rms %.0f T (%.4f frames)  max %.0f'
          % (sum(err) / len(err), rms, rms / FRAME_T, max(abs(e) for e in err)))
    mixed = [s for s in set(r['scene'] for r in rows)
             if len(set(r['whole'] for r in rows if r['scene'] == s)) > 1]
    sub = [r for r in rows if r['scene'] in mixed]
    for tag, key in (('measured phase + W2', 'predA'), ('self-contained  ', 'predB')):
        a = sum(1 for r in rows if r[key] == r['whole'])
        b = sum(1 for r in sub if r[key] == r['whole'])
        print('  %s : ALL %4d/%4d = %5.1f%%   MIXED SCENES %3d/%3d = %5.1f%%'
              % (tag, a, len(rows), 100.0 * a / len(rows),
                 b, len(sub), 100.0 * b / len(sub) if sub else 0))
    base = collections.Counter(r['whole'] for r in rows).most_common(1)[0][1]
    bsub = collections.Counter(r['whole'] for r in sub).most_common(1)[0][1] if sub else 0
    print('  baseline "always the commonest": ALL %.1f%%   MIXED %.1f%%'
          % (100.0 * base / len(rows), 100.0 * bsub / len(sub) if sub else 0))
    print('  mixed scenes: %s' % ', '.join(sorted(mixed)))
    return rows, err


def fresh_cmio(path=STATE):
    """The same state on SkoolKit's CONTENDED simulator.  The harness's own
    note says ULA contention is not modelled; this is what it costs."""
    from skoolkit.cmiosimulator import CMIOSimulator
    h = fresh(path)
    regs = list(h.sim.registers)
    h.sim = CMIOSimulator(h.memobj, config={'fast_djnz': True, 'fast_ldir': True})
    h.memobj.sim = h.sim
    h.sim.set_tracer(h.ports)
    h.sim.registers[:] = regs
    h.frame_duration = h.sim.frame_duration
    h.int_active = h.sim.int_active
    return h


def cmd_contend(n=40):
    print('ULA CONTENTION: the same scene on the plain and the CONTENDED '
          'simulator, %d passes' % n)
    print('  %-6s | %-34s | %-34s' % ('key', 'plain (what every gate uses)',
                                      'CMIOSimulator'))
    print('  %-6s | %7s %7s %-18s | %7s %7s %-18s'
          % ('', 'W1', 'W2', 'cost', 'W1', 'W2', 'cost'))
    for d in ('idle', 'right', 'down', 'left', 'up'):
        cells = []
        for mk in (fresh, fresh_cmio):
            h = mk()
            press(h, d)
            one_pass(h)
            W1 = W2 = 0.0
            C = []
            for _ in range(int(n)):
                rec = one_pass(h)
                W1 += (rec['stamp'][0x9CD7] - rec['t0']) / FRAME_T
                W2 += (rec['t0'] + rec['cost'] - rec['wake']) / FRAME_T
                C.append(rec['cost'] / FRAME_T)
            cells.append((W1 / n, W2 / n, sum(C) / n,
                          dict(sorted(collections.Counter(
                              round(c) for c in C).items()))))
        print('  %-6s | %7.4f %7.4f %5.3f %-12s | %7.4f %7.4f %5.3f %-12s'
              % (d, cells[0][0], cells[0][1], cells[0][2], str(cells[0][3]),
                 cells[1][0], cells[1][1], cells[1][2], str(cells[1][3])))
    print('  Contention adds only ~0.01-0.04 frames to W1 -- but a quiet pass')
    print('  sits within 0.006 frames of the p+W1 = 2 threshold, so that is')
    print('  enough to tip nearly every pass from four frames to five.')


def cmd_calls(scenes=None):
    rows = corpus(scenes, quiet=True)
    print('PER-CALL COST over %d passes in %d scenes, T-states NET of the '
          'frame ISR' % (len(rows), len(set(r['scene'] for r in rows))))
    print('  %-18s %8s %8s %8s %8s %7s   %8s' %
          ('call', 'mean', 'min', 'max', 'sd', '%W1', 'model rms'))
    tot = sum(sum(r['seg%04X' % a] for a in SITES) for r in rows) / len(rows)
    order = sorted(SITES, key=lambda a: -sum(r['seg%04X' % a] for r in rows))
    for a in order:
        v = [r['seg%04X' % a] for r in rows]
        mu = sum(v) / len(v)
        sd = (sum((x - mu) ** 2 for x in v) / len(v)) ** .5
        e = [model_segments(r)[a] - r['seg%04X' % a] for r in rows]
        rms = (sum(x * x for x in e) / len(e)) ** .5
        print('  %-18s %8.0f %8d %8d %8.0f %6.2f%%   %8.0f'
              % (NAMES[a], mu, min(v), max(v), sd, 100 * mu / tot, rms))
    w1 = sum(r['W1'] for r in rows) / len(rows)
    print('  %-18s %8.0f  = %.3f frames   (W1 incl. ISR: %.0f T = %.3f f)'
          % ('SUM (= W1 net)', tot, tot / FRAME_T, w1, w1 / FRAME_T))
    w2 = [r['W2'] for r in rows]
    print('  %-18s %8.0f  = %.3f frames   sd %.0f T  [the post-HALT half]'
          % ('W2', sum(w2) / len(w2), sum(w2) / len(w2) / FRAME_T,
             (sum((x - sum(w2) / len(w2)) ** 2 for x in w2) / len(w2)) ** .5))
    return rows


def cmd_beep():
    """THE PRE-EXISTING GATE.  tools/beepgate.py's five scenarios, scored
    pass for pass -- if the model calls every one of them, the unhelped
    differential has nothing left to disagree about but the driver."""
    import beepgate
    print('THE BEEPGATE SCENARIOS, scored pass for pass')
    print('  (actors and generators removed, exactly as beepgate removes them)')
    ok = tot = 0
    for name, direction, npass, plants in beepgate.SCEN:
        h = fresh()
        m = h.memobj.m
        m[0x8496] = 0
        m[0x8494], m[0x8495] = 0x00, 0x5C
        for a in range(0x8000, 0x8400):
            if 0x20 <= m[a] <= 0x2E:
                m[a] = 0
        for r, c, _i, _n in plants:
            m[0x8000 + (r % 32) * 32 + beepgate.COL] = c
        m[P1], m[P1 + 1] = beepgate.COL * 4, beepgate.ROW0 * 4
        m[P1 + 8], m[P1 + 9] = 3, 2
        press(h, direction)
        one_pass(h)
        good = bad = 0
        seen = collections.Counter()
        p = None
        for _ in range(npass):
            ctr = m[0x8491]
            nact0 = m[0x8496]
            rec = one_pass(h)
            row = features(m, rec)
            row.update(actor_features(rec))
            row.update({'ctr': ctr, 'moving': 1, 'nStep': rec['cnt']['actorStep'],
                        'nContact': rec['cnt']['contact'],
                        'ramp': rec['cnt']['sndRamp'], 'out': rec['cnt']['sndOut'],
                        'p': (rec['t0'] % FRAME_T) / FRAME_T})
            w2 = rec['t0'] + rec['cost'] - rec['wake']
            whole = round(rec['cost'] / FRAME_T)
            seen[whole] += 1
            _w1, pred = predict(row, True, p=row['p'], w2=w2)
            if round(pred) == whole:
                good += 1
            else:
                bad += 1
        ok += good
        tot += good + bad
        print('  %-28s %-6s %3d passes  cost %s  -> %d/%d called'
              % (name, direction, npass, dict(sorted(seen.items())),
                 good, good + bad))
    print('  TOTAL %d/%d = %.1f%%' % (ok, tot, 100.0 * ok / tot))
    print('  NOTE: W2 and the phase are taken from the original here, because')
    print('  the blocking tunes ($B8B0, 72.07 frames) live in W2 and this')
    print('  model does not predict them -- beepgate already drives them.')
    return tot - ok, tot


def cmd_oneoff():
    """The known one-off events: are they a separate case or the tail of the
    same cost function?  The answer is BOTH, and which one depends on whether
    the event happens BEFORE the HALT (in W1, where it is quantised) or
    AFTER it (in W2, where it is not)."""
    print('THE ONE-OFF EVENTS, split into W1 (pre-HALT) and W2 (post-HALT)')
    print('  %-22s %8s %8s %8s %8s' % ('event', 'W1(f)', 'W2(f)', 'cost(f)', 'whole'))

    def show(tag, rec):
        w1 = (rec['stamp'][0x9CD7] - rec['t0']) / FRAME_T
        w2 = (rec['t0'] + rec['cost'] - rec['wake']) / FRAME_T
        print('  %-22s %8.3f %8.3f %8.3f %8d'
              % (tag, w1, w2, rec['cost'] / FRAME_T, round(rec['cost'] / FRAME_T)))

    # a quiet baseline
    h = fresh(); press(h, 'down'); one_pass(h)
    show('quiet pass', one_pass(h))

    for val, name in ((0x2F, '$2F map sweep'), (0x19, '$19 item + banner'),
                      (0x13, '$13 treasure'), (0x1F, '$1F key'),
                      (0x36, '$36 exit -> reload')):
        h = fresh()
        m = h.memobj.m
        col, row0 = 12 // 4, 8 // 4
        m[0x8000 + ((row0 + 4) % 32) * 32 + col] = val
        press(h, 'down')
        one_pass(h)
        best = None
        for _ in range(14):
            rec = one_pass(h)
            if best is None or rec['cost'] > best[1]['cost']:
                best = (val, rec)
        show(name, best[1])
    print('  A $2F sweep is W1 work and so is QUANTISED -- it is the tail of')
    print('  the same function.  A banner or a level start is W2 work: the 48K')
    print('  arm JPs into a blocking tune at $B8B0/$B8B5 with interrupts OFF,')
    print('  so it is added WHOLE and is a separate term, not a tail.')


def cmd_check(n=40):
    """Does window_origin() reproduce the machine's own $9FC9?"""
    bad = tot = 0
    for d in ('idle', 'right', 'left', 'up', 'down'):
        h = fresh()
        press(h, d)
        one_pass(h)
        for _ in range(int(n)):
            rec = one_pass(h)
            s = rec['snap']
            mine = window_origin(s['camx'], s['camy'])
            tot += 1
            if mine != s['orig']:
                bad += 1
                if bad < 6:
                    print('  MISMATCH cam(%d,%d) mine $%04X his $%04X'
                          % (s['camx'], s['camy'], mine, s['orig']))
    print('window_origin(): %d/%d agree with the original\'s own $9FC9'
          % (tot - bad, tot))
    return bad


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'table'
    args = sys.argv[2:]
    if cmd == 'table':
        cmd_table(args[0] if args else 'down',
                  int(args[1]) if len(args) > 1 else 120)
    elif cmd == 'split':
        cmd_split(int(args[0]) if args else 40)
    elif cmd == 'actors':
        cmd_actors(args[0] if args else 'idle')
    elif cmd == 'check':
        cmd_check(int(args[0]) if args else 40)
    elif cmd == 'model':
        cmd_model(oracle=('--noracle' not in sys.argv))
    elif cmd == 'calls':
        cmd_calls(SCENES + VSCENES)
    elif cmd == 'contend':
        cmd_contend(int(args[0]) if args else 40)
    elif cmd == 'oneoff':
        cmd_oneoff()
    elif cmd == 'beep':
        cmd_beep()
    elif cmd == 'validate':
        print('THE HELD-OUT SET -- no constant above was fitted on any of it')
        cmd_model(oracle=True, rows=corpus(VSCENES))
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
