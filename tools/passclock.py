#!/usr/bin/env python3
"""
passclock.py -- THE MECHANISM OF THE 4-OR-5 FRAME PASS.

    python tools/passclock.py halts     every HALT a pass can execute, and
                                        what the two pause paths do
    python tools/passclock.py model     the quantiser identity, per pass, and
                                        the THRESHOLD in T-states
    python tools/passclock.py seams     WHAT W1 IS: the tile census as an exact
                                        function of two bits of the camera
    python tools/passclock.py units     the unit cost of one map cell and of
                                        one actor, by cull stage
    python tools/passclock.py entropy   how much of the pass cost NO PORT CAN
                                        KNOW  (the LD A,R sweep)
    python tools/passclock.py contend   the same scenes with ULA MEMORY
                                        CONTENTION modelled (CMIOSimulator)
    python tools/passclock.py all

=============================================================================
THE ANSWER, IN ONE BLOCK
=============================================================================
A pass executes EXACTLY ONE HALT, at $9CD7, and it does so on every pass
without exception -- measured over every scene below including both blocking
pauses.  The loop is therefore a QUANTISER, not a budget:

    $8503 .. $854D  the work, interrupts ON        W1
    $8550 CALL $9CD7 -> HALT                       wait for the next frame
    $9CD8 DI                                       ... and then, interrupts OFF,
    $9CD9..$9CFE    $B8FB, three shadow->screen copies, $A29F by hand,
                    the pass counter, $B4FF's shadow clear
    $8553 CALL $A36F, $8556 loop back to $8503     W2

    frames(pass) = ceil((p + W1)/F) + 2 ,   F = 69888 T,  p = t0 mod F

760/760 passes over 19 scenes.  W2 does not wait for anything (it runs with
interrupts disabled), so it does not quantise -- it only sets the phase of the
NEXT pass:  p = (ISR + W2) mod F, and that is why p is nearly constant.

The "+2" is  round(W2 - p) = round(2.1675 - 0.194) = 2.  So the game gives
itself a compute budget of TWO VIDEO FRAMES MINUS THE PHASE and the whole
4-vs-5 question is whether W1 fits in it.

    THE THRESHOLD:  W1 > 2F - p.  p is 10,818..14,336 T over 760 passes, so
    the 4->5 threshold is W1 = 125,440..128,958 T (mean 126,216 T = 1.806
    frames).  The 5->6 threshold is one frame higher, and SIX-FRAME PASSES
    ARE REAL: they are the norm at a generator cluster.  "Bimodal 4 or 5" is
    what a nearly-two-frame W1 looks like, not a law.

W1 in ordinary play is 1.78..1.89 frames against a 1.806-frame threshold, so
THE GAME RUNS PERMANENTLY WITHIN 1% OF THE FRAME BOUNDARY.  That is why this
is hard, and it is not an accident: it is what a 1985 developer tuning to
"just fits" produces.

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
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, R, TAPE_CALL_PC,            # noqa
                     FRAME_T, CPU_HZ)
from keyprobe import KEYS, keymask                                    # noqa

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
AYSTATE = os.path.join(ROOT, 'build', 'state_48k_ay.pkl')
LIVE = os.path.join(ROOT, 'build', 'live_cs.bin')

LOOP_TOP, SYNC, POST = 0x8503, 0x9CD7, 0x9CD8
CAMX, CAMY, COUNT, CTR, P1 = 0x848B, 0x848C, 0x8496, 0x8491, 0x8420

# the top-level steps of the main loop, in order, with what each one is
STEPS = [
    (0x8503, '$A31A logo/anim'), (0x8506, '$95AB door anim'),
    (0x8509, '$A38A PLAYER 1'), (0x850C, '$B58C CAMERA'),
    (0x850F, '$9EFC MAIN MAP'), (0x8512, '$9FC2 ROW SEAM'),
    (0x8515, '$A08B COL SEAM'), (0x8518, '$A159 CORNER'),
    (0x851B, '$A3E6'), (0x851E, '$AB94 ACTORS'), (0x8521, '$A43B player draw'),
    (0x8524, '$8BCA'), (0x8527, 'BIT6 -> $8ADA'), (0x852E, '$B6DA'),
    (0x8531, '$971B'), (0x8534, '$9788'), (0x8537, '$94AE exit walk'),
    (0x853A, '$B0FE cull'), (0x853D, '$A9C2 GENERATORS'), (0x8540, '$93C2'),
    (0x8543, '$891C'), (0x8546, 'tail $84A1'), (0x8550, '(the HALT)'),
]
SPCS = {a for a, _ in STEPS}

# the five tile loops: (the CALL $B8CC that heads the iteration, the
# fall-through taken when the cell is NON-ZERO and therefore drawn)
LOOPS = [(0x9F69, 0x9F70, '$9EFC main   B x C'),
         (0x9FEC, 0x9FF3, '$9FC2 row A  B x 1'),
         (0xA054, 0xA05B, '$9FC2 row B  B x 1'),
         (0xA0AC, 0xA0B3, '$A08B col A  1 x C'),
         (0xA115, 0xA11C, '$A08B col B  1 x C')]
VIS = [a for a, _, _ in LOOPS]
DRW = [b for _, b, _ in LOOPS]
CORNER_RUN = 0xA167                 # $A159 past BOTH its RET z
CORNER_DRAW = [0xA177, 0xA198, 0xA1B7, 0xA1D8]
SPRITE, ACTUPD = 0xA1DA, 0xABFF
CULL = [0xA1EA, 0xA1F8, 0xA209, 0xA217]      # $A1DA's four RET nc
GENCELL, GENSPAWN = 0xA9ED, 0xAA26
PSCAN, COMMIT = 0xA97F, 0xA620
AB94, A43B = 0x851E, 0x8521


def fresh(direction=None, warp=None, quiet=False, path=STATE, contended=False):
    h = Harness()
    if contended:
        from skoolkit.cmiosimulator import CMIOSimulator
        h.sim = CMIOSimulator(h.memobj)
        h.memobj.sim = h.sim
        h.sim.set_tracer(h.ports)
        h.frame_duration = h.sim.frame_duration
        h.int_active = h.sim.int_active
    h.load_state(pickle.load(open(path, 'rb')))
    m = h.memobj.m
    if warp:
        m[P1], m[P1 + 1] = warp
    if quiet:
        m[COUNT] = 0
        m[0x8494], m[0x8495] = 0x00, 0x5C
    if direction:
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    return h


def plant(h, n):
    """DECLARED SYNTHETIC: dungeon 1 ships 63 actors and a short run only ever
    loses some, so a controlled load curve has to be planted.  The record
    format is the game's own and the real Z80 runs the real update on them."""
    m = h.memobj.m
    camx, camy = m[CAMX], m[CAMY]
    px, py = m[P1], m[P1 + 1]
    k, a = 0, 0x5C00
    for dy in range(0, 0x2B, 2):
        for dx in range(0, 0x43, 2):
            if k >= n:
                break
            x, y = (camx + dx - 3) & 0x7F, (camy + dy - 3) & 0x7F
            if abs(((x - px + 64) & 0x7F) - 64) < 10 and \
               abs(((y - py + 64) & 0x7F) - 64) < 10:
                continue
            m[a], m[a + 1], m[a + 2], m[a + 3] = x, y, 0x00, 0x00
            a += 4
            k += 1
        if k >= n:
            break
    m[COUNT] = k
    m[0x8494], m[0x8495] = a & 0xFF, a >> 8
    return k


SCENES = [
    ('idle', {}, None), ('right', dict(direction='right'), None),
    ('left', dict(direction='left'), None),
    ('down', dict(direction='down'), None), ('up', dict(direction='up'), None),
    ('warp(60,60) idle', dict(warp=(60, 60)), None),
    ('warp(60,60) right', dict(warp=(60, 60), direction='right'), None),
    ('warp(60,60) down', dict(warp=(60, 60), direction='down'), None),
    ('warp(30,90) left', dict(warp=(30, 90), direction='left'), None),
    ('warp(30,90) up', dict(warp=(30, 90), direction='up'), None),
    ('cluster idle', dict(warp=(88, 108)), None),
    ('cluster down', dict(warp=(88, 108), direction='down'), None),
    ('plant 20', dict(direction='down'), 20),
    ('plant 60', dict(direction='down'), 60),
    ('plant 100', dict(direction='down'), 100),
    ('plant 150', dict(direction='down'), 150),
    ('plant 190', dict(direction='down'), 190),
    ('quiet', dict(quiet=True), None),
    ('quiet right', dict(quiet=True, direction='right'), None),
]


# --------------------------------------------------------------------------
# the sampler.  $8503 is visited exactly once per pass; a four-frame window is
# NOT a sampler and manufactured a phantom differential the last time one was
# used (NOTES-engine.md, "the broken ruler").
# --------------------------------------------------------------------------
def passes(h, npass, hooks=(), steps=False, timeline=False, skip=2):
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    hooks = set(hooks)
    rows, hit, cur, tl, st = [], collections.Counter(), {'halts': []}, [], {}
    t_prev = t_sync = t_post = None
    nhalt = done = n = 0
    while n < 900_000_000:
        pc = regs[PC]
        if pc in hooks:
            hit[pc] += 1
            if timeline:
                tl.append((pc, regs[T]))
        if pc == SYNC:
            t_sync = regs[T]
        elif pc == POST:
            t_post = regs[T]
        if pc == LOOP_TOP:
            t = regs[T]
            if t_prev is not None:
                r = dict(t0=t_prev, dt=t - t_prev, w1=t_sync - t_prev,
                         w2=t - t_post, nhalt=nhalt, halts=cur.get('halts', []))
                r.update({f'h{a:04X}': hit[a] for a in hooks})
                r.update(st)
                if steps:
                    r['steps'] = {a: cur.get(a) for a, _ in STEPS}
                if timeline:
                    r['tl'] = tl
                rows.append(r)
                done += 1
                if done >= npass + skip:
                    return rows[skip:]
            t_prev, t_sync, t_post, nhalt = t, None, None, 0
            hit.clear()
            cur = {LOOP_TOP: t, 'halts': []}
            tl = []
            st = dict(count=mem[COUNT], ctr=mem[CTR], camx=mem[CAMX],
                      camy=mem[CAMY])
        elif steps and pc in SPCS:
            cur[pc] = regs[T]
            if pc == 0x850F:            # AFTER $B58C: the camera the DRAW sees
                st['camx'], st['camy'] = mem[CAMX], mem[CAMY]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            cur['halts'].append(pc)
            nhalt += 1
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('runaway')


# --------------------------------------------------------------------------
def cmd_halts(args=()):
    n = int(args[0]) if args else 40
    print('=== EVERY HALT A PASS CAN EXECUTE, ON THE SHIPPED 48K BRANCH')
    print(r"""
  $9CD7 is the frame sync and it is UNCONDITIONAL -- $8550 CALL $9CD7 sits
  outside every branch in the loop:

      9CD7  76        HALT          <- the one and only wait in a pass
      9CD8  F3        DI            <- and the rest of the pass runs with
      9CD9  CD FB B8  CALL $B8FB       interrupts OFF, so nothing else can
      ...                              quantise

  The OTHER pair of HALTs in the image is $9D31/$9D32, inside $9D2D's
  fifty PUSH BC / CALL $9432 / HALT / HALT / POP BC / DJNZ.  On a 48K
  $9D0A LD A,($FFFD) reads 0 and the JR nz is NOT taken, so control goes to
  $9D14/$9D17 -- the two blocking tunes, which run with interrupts off and
  contain no HALT at all.  $9D2D IS UNREACHABLE ON THIS BRANCH.
""")
    live = open(LIVE, 'rb').read()
    raw = [i for i in range(0x8400, 0x10000) if live[i] == 0x76]
    print(f'  raw $76 bytes in the live image $8400..$FFFF: {len(raw)} '
          f'(mostly data -- the dynamic census below is the real answer)')
    print()
    CASES = [
        ('ordinary play, idle', dict(), None, n),
        ('ordinary play, holding down', dict(direction='down'), None, n),
        ('generator cluster', dict(warp=(88, 108), direction='down'), None, n),
        ('MESSAGE BANNER armed ($847D bit 2, bit 5 clear)', dict(),
         lambda v: (v & ~0x20 & 0xFF) | 0x04, 6),
        ('LEVEL-START tune armed ($847D bits 2+5)', dict(),
         lambda v: v | 0x24, 6),
    ]
    for tag, kw, arm, np_ in CASES:
        h = fresh(**kw)
        if arm is not None:
            passes(h, 1, skip=0)
            h.memobj.m[0x847D] = arm(h.memobj.m[0x847D])
        rows = passes(h, np_, skip=0)
        c = collections.Counter(tuple(sorted(collections.Counter(r['halts'])
                                             .items())) for r in rows)
        print(f'  {tag}')
        for k, v in c.items():
            s = ' '.join(f'${a:04X} x{x}' for a, x in k) or '(none)'
            print(f'      {v:3d} passes: {s}')
        print('      pass costs (frames): ' +
              ' '.join(f'{r["dt"]/FRAME_T:.1f}' for r in rows[:8]))
    print()
    print('  THE CONTROL -- the same two pauses on the 128K/AY branch, where')
    print('  $9D0A\'s JR nz IS taken and $9D2D does run:')
    for tag, arm in [('MESSAGE BANNER', lambda v: (v & ~0x20 & 0xFF) | 0x04),
                     ('LEVEL-START', lambda v: v | 0x24)]:
        h = fresh(path=AYSTATE)
        passes(h, 1, skip=0)
        h.memobj.m[0x847D] = arm(h.memobj.m[0x847D])
        rows = passes(h, 4, skip=0)
        c = collections.Counter(tuple(sorted(collections.Counter(r['halts'])
                                             .items())) for r in rows)
        print(f'    {tag} (AY): ' + '; '.join(
            f'{v} passes ' + (' '.join(f'${a:04X} x{x}' for a, x in k) or 'none')
            for k, v in c.items()))
        print('      pass costs (frames): ' +
              ' '.join(f'{r["dt"]/FRAME_T:.1f}' for r in rows))


# --------------------------------------------------------------------------
def cmd_model(args=()):
    n = int(args[0]) if args else 40
    print('=== THE QUANTISER IDENTITY AND THE THRESHOLD')
    print(f'    {n} passes a scene, anchored on $8503, one frame = {FRAME_T} T')
    print()
    print(f'  {"scene":18s} {"n":>4} {"phase p":>14} {"W1 (frames)":>24} '
          f'{"W2":>14}  frames')
    allrows = []
    for tag, kw, pl in SCENES:
        h = fresh(**kw)
        if pl:
            plant(h, pl)
        rows = passes(h, n)
        for r in rows:
            r['scene'] = tag
        allrows += rows
        p = [(r['t0'] % FRAME_T) / FRAME_T for r in rows]
        w1 = [r['w1'] / FRAME_T for r in rows]
        w2 = [r['w2'] / FRAME_T for r in rows]
        fr = collections.Counter(round(r['dt'] / FRAME_T) for r in rows)
        print(f'  {tag:18s} {len(rows):4d} {min(p):.3f}-{max(p):.3f} '
              f'{min(w1):8.4f}-{max(w1):7.4f} mu {statistics.mean(w1):7.4f} '
              f'{min(w2):.3f}-{max(w2):.3f}  {dict(sorted(fr.items()))}')

    ok = sum(1 for r in allrows
             if math.ceil((r['t0'] % FRAME_T + r['w1']) / FRAME_T) + 2
             == round(r['dt'] / FRAME_T))
    print()
    print(f'  frames == ceil((p + W1)/F) + 2   on {ok}/{len(allrows)} passes')
    ps = [r['t0'] % FRAME_T for r in allrows]
    w2s = [r['w2'] for r in allrows]
    print(f'  p  {min(ps)}..{max(ps)} T ({min(ps)/FRAME_T:.4f}..'
          f'{max(ps)/FRAME_T:.4f} f)   W2 {min(w2s)}..{max(w2s)} T '
          f'({min(w2s)/FRAME_T:.4f}..{max(w2s)/FRAME_T:.4f} f)')
    print(f'  p = (ISR + W2) mod F -- it is NOT free, it is last pass\'s tail.')
    print()
    print(f'  THE 4->5 THRESHOLD  W1 = 2F - p = {2*FRAME_T-max(ps)}..'
          f'{2*FRAME_T-min(ps)} T, mean {2*FRAME_T-statistics.mean(ps):.0f} T '
          f'= {(2*FRAME_T-statistics.mean(ps))/FRAME_T:.4f} frames')
    print(f'  THE 5->6 THRESHOLD  W1 = 3F - p = {3*FRAME_T-max(ps)}..'
          f'{3*FRAME_T-min(ps)} T')
    print()
    print('  HOW CLOSE EACH SCENE SITS TO ITS OWN BOUNDARY:')
    print(f'  {"scene":18s} {"W1 mean":>9} {"margin":>9} {"as % of W1":>11}'
          f'   frames')
    for tag, _, _ in SCENES:
        rs = [r for r in allrows if r['scene'] == tag]
        mg = [math.ceil((r['t0'] % FRAME_T + r['w1']) / FRAME_T) * FRAME_T
              - (r['t0'] % FRAME_T + r['w1']) for r in rs]
        w1m = statistics.mean(r['w1'] for r in rs)
        fr = collections.Counter(round(r['dt'] / FRAME_T) for r in rs)
        print(f'  {tag:18s} {w1m:9.0f} {statistics.mean(mg):9.0f} '
              f'{100.0*statistics.mean(mg)/w1m:10.2f}%   {dict(sorted(fr.items()))}')
    return allrows


# --------------------------------------------------------------------------
def cmd_seams(args=()):
    n = int(args[0]) if args else 40
    print('=== WHAT W1 IS: THE TILE CENSUS IS TWO BITS OF THE CAMERA')
    print(r"""
  One map cell is 4 coordinate units = 16 screen pixels, and the camera moves
  in 2-unit (8-pixel) steps -- HALF A TILE.  So the playfield is drawn by four
  routines and three of them exist only to fill in the half-tile seam:

    $850F $9EFC  the main grid.  $9F41/$9F51 self-modify the width to
                 B = 16 - (camx&2 ? 1 : 0);  $9F1E/$9F2B set C = 10 - (camy&2)
    $8512 $9FC2  9FC5 AND 2 / RET z on camy&2   -> TWO ROWS of B cells
    $8515 $A08B  A08E AND 2 / RET z on camx&2   -> TWO COLUMNS of C cells
    $8518 $A159  RETs unless BOTH bits are set  -> the FOUR corner cells

  so the number of map cells the pass walks is exactly

      cells(camx, camy) = B*C + 2B*[camy&2] + 2C*[camx&2] + 4*[both]
                        = 160, 170, 176 or 187
""")
    rows = []
    for tag, kw, pl in SCENES:
        h = fresh(**kw)
        if pl:
            plant(h, pl)
        rs = passes(h, n, hooks=VIS + DRW + CORNER_DRAW + [CORNER_RUN],
                    steps=True)
        for r in rs:
            r['scene'] = tag
        rows += rs
    print(f'  {"camx&2":>7} {"camy&2":>7} {"n":>5} {"main":>8} {"rows":>8} '
          f'{"cols":>8} {"corner":>7} {"TOTAL observed":>22}   predicted')
    ok = 0
    for sx in (0, 2):
        for sy in (0, 2):
            rs = [r for r in rows if (r['camx'] & 2) == sx
                  and (r['camy'] & 2) == sy]
            if not rs:
                continue
            B, C = (15 if sx else 16), (9 if sy else 10)
            pred = B * C + (2 * B if sy else 0) + (2 * C if sx else 0) + \
                (4 if (sx and sy) else 0)
            got = collections.Counter(
                sum(r[f'h{a:04X}'] for a in VIS) + 4 * r[f'h{CORNER_RUN:04X}']
                for r in rs)
            ok += sum(v for k, v in got.items() if k == pred)
            print(f'  {int(bool(sx)):7d} {int(bool(sy)):7d} {len(rs):5d} '
                  f'{statistics.mean(r[f"h{VIS[0]:04X}"] for r in rs):8.2f} '
                  f'{statistics.mean(r[f"h{VIS[1]:04X}"]+r[f"h{VIS[2]:04X}"] for r in rs):8.2f} '
                  f'{statistics.mean(r[f"h{VIS[3]:04X}"]+r[f"h{VIS[4]:04X}"] for r in rs):8.2f} '
                  f'{statistics.mean(4*r[f"h{CORNER_RUN:04X}"] for r in rs):7.2f} '
                  f'{str(dict(sorted(got.items()))):>22}   {B}x{C}+... = {pred}')
    print(f'  -> EXACT on {ok}/{len(rows)} passes')

    print()
    print('=== AND THAT IS "W1": WHY HOLDING UP COSTS 0.108 FRAMES MORE THAN')
    print('=== HOLDING RIGHT WITH AN IDENTICAL ACTOR CENSUS')
    print()
    print(f'  {"scene":8s} {"camx&2":>18s} {"camy&2":>18s} {"cells":>18s} '
          f'{"W1":>9s}  frames')
    for tag in ('right', 'up', 'left', 'down', 'idle'):
        rs = [r for r in rows if r['scene'] == tag]
        cx = collections.Counter(r['camx'] & 2 for r in rs)
        cy = collections.Counter(r['camy'] & 2 for r in rs)
        cl = collections.Counter(sum(r[f'h{a:04X}'] for a in VIS)
                                 + 4 * r[f'h{CORNER_RUN:04X}'] for r in rs)
        fr = collections.Counter(round(r['dt'] / FRAME_T) for r in rs)
        print(f'  {tag:8s} {str(dict(sorted(cx.items()))):>18s} '
              f'{str(dict(sorted(cy.items()))):>18s} '
              f'{str(dict(sorted(cl.items()))):>18s} '
              f'{statistics.mean(r["w1"] for r in rs)/FRAME_T:9.4f}f '
              f'{dict(sorted(fr.items()))}')
    print(r"""
  Holding RIGHT walks the camera, so camx&2 alternates and $A08B takes its
  RET z on half the passes.  Holding UP at the top of dungeon 1 does not move
  the camera at all, so camx stays at 2 and the column seam is redrawn EVERY
  pass.  That is the whole of the differential.
""")
    print('  THE SAME THING AS A PER-STEP TABLE (mean T-states a pass):')
    print(f'  {"step":22s}' + ''.join(f'{t:>10s}' for t in
                                      ('idle', 'right', 'left', 'down', 'up'))
          + f'{"up-right":>10s}')
    for i, (a, label) in enumerate(STEPS[:-1]):
        b = STEPS[i + 1][0]
        cells = []
        for tag in ('idle', 'right', 'left', 'down', 'up'):
            rs = [r for r in rows if r['scene'] == tag]
            cells.append(statistics.mean(r['steps'][b] - r['steps'][a]
                                         for r in rs))
        print(f'  {label:22s}' + ''.join(f'{c:10.0f}' for c in cells) +
              f'{cells[4]-cells[1]:+10.0f}')
    for tag in ('right', 'up'):
        rs = [r for r in rows if r['scene'] == tag]
        print(f'  W1 {tag}: {statistics.mean(r["w1"] for r in rs)/FRAME_T:.4f} f')
    return rows


# --------------------------------------------------------------------------
def cmd_units(args=()):
    n = int(args[0]) if args else 12
    print('=== THE UNIT COST OF ONE MAP CELL (visit -> next visit, so the')
    print('=== figure includes CALL $B8CC, the cell test and the blit)')
    hooks = VIS + DRW + [SPRITE, ACTUPD, CORNER_RUN, AB94, A43B] + CULL
    empty = collections.defaultdict(list)
    drawn = collections.defaultdict(list)
    for tag, kw in (('idle', {}), ('down', dict(direction='down')),
                    ('right', dict(direction='right'))):
        for r in passes(fresh(**kw), n, hooks=hooks, timeline=True):
            tl = r['tl']
            for i, (pc, t) in enumerate(tl):
                if pc not in VIS:
                    continue
                nxt, isd = None, False
                for pc2, t2 in tl[i + 1:]:
                    if pc2 in DRW:
                        isd = True
                        continue
                    if pc2 in VIS or pc2 == CORNER_RUN or pc2 == AB94:
                        nxt = t2
                        break
                if nxt is not None:
                    (drawn if isd else empty)[pc].append(nxt - t)

    def st(xs):
        return (f'n={len(xs):5d} mean {statistics.mean(xs):7.1f} T median '
                f'{statistics.median(xs):6.0f}') if xs else f'n={len(xs):5d}'
    for v, d, name in LOOPS:
        print(f'  {name:22s} EMPTY {st(empty[v]):46s} DRAWN {st(drawn[v])}')
    ae = [x for v in VIS for x in empty[v]]
    ad = [x for v in VIS for x in drawn[v]]
    print(f'  {"ALL FIVE":22s} EMPTY {st(ae):46s} DRAWN {st(ad)}')
    print(f'  -> a DRAWN cell costs {statistics.mean(ad)-statistics.mean(ae):.0f} '
          f'T more than an empty one.  BOTH are known to the port: the cell '
          f'value is the map.')

    print()
    print('=== THE UNIT COST OF ONE ACTOR, BY HOW FAR $A1DA\'s CULL LET IT GET')
    print('    $A1EA RET nc   (x+3-camx)&$7F >= $44                  stage 1')
    print('    $A1F8 RET nc   ...-$43 carry clear                    stage 2')
    print('    $A209 RET nc   (y+3-camy)&$7F >= $44                  stage 3')
    print('    $A217 RET nc   ...-$2B carry clear                    stage 4')
    print('    $ABFF reached  -> DRAWN **AND** UPDATED               stage 5')
    print('    Stages 1-4 are pure arithmetic on (x,y,camx,camy): the port has')
    print('    every input.  Stage 5 is the branchy update whose two decisive')
    print('    branches are LD A,R.')
    print()
    stage = {a: i + 1 for i, a in enumerate(CULL)}
    cost = collections.defaultdict(list)
    for tag, kw, pl in (('cluster idle', dict(warp=(88, 108)), None),
                        ('plant 100', dict(direction='down'), 100),
                        ('plant 190', dict(direction='down'), 190),
                        ('down', dict(direction='down'), None)):
        h = fresh(**kw)
        if pl:
            plant(h, pl)
        for r in passes(h, n, hooks=hooks, timeline=True):
            tl = r['tl']
            lo = [i for i, (p, _) in enumerate(tl) if p == AB94]
            hi = [i for i, (p, _) in enumerate(tl) if p == A43B]
            if not lo or not hi:
                continue
            seg = tl[lo[0]:hi[0]]
            idx = [i for i, (p, _) in enumerate(seg) if p == SPRITE]
            for j, i in enumerate(idx[:-1]):
                body = seg[i:idx[j + 1]]
                s = 0
                for p, _ in body:
                    if p in stage:
                        s = max(s, stage[p])
                    elif p == ACTUPD:
                        s = 5
                cost[s].append(seg[idx[j + 1]][1] - seg[i][1])
    for s in sorted(cost):
        xs = cost[s]
        print(f'    stage {s}  n={len(xs):6d}  mean {statistics.mean(xs):8.1f} T'
              f'  median {statistics.median(xs):7.0f}  min {min(xs):6d}  '
              f'max {max(xs):7d}  sd {statistics.pstdev(xs):7.0f}')
    if 5 in cost:
        print(f'\n  -> stages 1-4 are flat to within {max(statistics.pstdev(cost[s]) for s in cost if s < 5):.0f} T.')
        print(f'     STAGE 5 HAS AN sd OF {statistics.pstdev(cost[5]):.0f} T ON A MEAN OF '
              f'{statistics.mean(cost[5]):.0f} T, range {min(cost[5])}..{max(cost[5])}.')
        print(f'     With v visible actors the irreducible uncertainty in W1 is')
        print(f'     about sqrt(v) * {statistics.pstdev(cost[5]):.0f} T; the margin to the frame')
        print('     boundary in ordinary play is 11,000-13,000 T.  So four')
        print('     visible actors are already enough to make the 4-vs-5')
        print('     outcome of an individual pass unknowable.')


# --------------------------------------------------------------------------
def cmd_entropy(args=()):
    n = int(args[0]) if args else 40
    print('=== HOW MUCH OF THE PASS COST CAN NO PORT KNOW?')
    print(r"""
  The only entropy in this game is the Z80 REFRESH REGISTER (battery Q18).
  Patch every LD A,R the game's LOGIC reads -- $AC25, $AC4C and $B586, all
  `ED 5F`, to `3E nn` = LD A,imm, THE SAME TWO BYTES so nothing moves -- and
  the machine becomes deterministic.  Sweep the immediate: whatever moves is
  what no port can reproduce.  ($B8CC is left alone: with $84D2 = 0 its cost
  is 68 T for every value of R, enumerated in tools/clock48.py.)
""")
    PATCH = [0xAC25, 0xAC4C, 0xB586]
    COINS = [0x00, 0x2A, 0x55, 0x7F, 0xAA, 0xD5, 0xFF]
    for tag, kw in (('idle (no visible actors)', {}),
                    ('down', dict(direction='down')),
                    ('generator cluster, idle', dict(warp=(88, 108))),
                    ('generator cluster, down',
                     dict(warp=(88, 108), direction='down'))):
        print(f'  --- {tag}')
        means = []
        for c in COINS:
            h = fresh(**kw)
            for a in PATCH:
                h.memobj.m[a], h.memobj.m[a + 1] = 0x3E, c
            rows = passes(h, n)
            w1 = statistics.mean(r['w1'] for r in rows)
            means.append(w1)
            fr = collections.Counter(round(r['dt'] / FRAME_T) for r in rows)
            print(f'      coin ${c:02X}   W1 {w1/FRAME_T:.4f}f ({w1:7.0f} T)   '
                  f'frames {dict(sorted(fr.items()))}')
        rows = passes(fresh(**kw), n)
        w1 = statistics.mean(r['w1'] for r in rows)
        fr = collections.Counter(round(r['dt'] / FRAME_T) for r in rows)
        print(f'      REAL R    W1 {w1/FRAME_T:.4f}f ({w1:7.0f} T)   '
              f'frames {dict(sorted(fr.items()))}')
        print(f'      -> the coin moves the MEAN W1 by {max(means)-min(means):.0f} T '
              f'= {(max(means)-min(means))/FRAME_T:.4f} frames '
              f'({100.0*(max(means)-min(means))/statistics.mean(means):.1f}%)')
        a = [r['w1'] for r in passes(_coin(fresh(**kw), PATCH, 0x00), n)]
        b = [r['w1'] for r in passes(_coin(fresh(**kw), PATCH, 0xFF), n)]
        d = [abs(x - y) for x, y in zip(a, b)]
        print(f'      -> PER PASS, coin $00 vs $FF: |dW1| mean '
              f'{statistics.mean(d):.0f} T, max {max(d):.0f} T '
              f'= {max(d)/FRAME_T:.3f} frames')


def _coin(h, sites, v):
    for a in sites:
        h.memobj.m[a], h.memobj.m[a + 1] = 0x3E, v
    return h


# --------------------------------------------------------------------------
def cmd_contend(args=()):
    n = int(args[0]) if args else 30
    print('=== ULA MEMORY CONTENTION, MEASURED (SkoolKit CMIOSimulator)')
    print(r"""
  Every timing figure this project has ever quoted comes from the PLAIN
  Simulator, which does not model contention -- so they are lower bounds.
  CMIOSimulator does model it (48K: $4000..$7FFF delayed by DELAYS_48K).
  CONTROL: CMIOSimulator forces fast_djnz/fast_ldir OFF; running the plain
  simulator with those flags off gives BYTE-IDENTICAL W1 and W2, so the
  comparison below isolates contention and nothing else.

  Where it lands matters.  The game's code ($8400+), its map ($8000..$83FF)
  and its shadow screen ($C000..$DAFF) are all UNCONTENDED.  What is
  contended is the sprite pointer table at $7B00, the player's sprite bank at
  $5F00 -- and, above all, THE REAL SCREEN $4000..$5AFF, which W2 writes 6,912
  bytes to every pass.  So contention lands almost entirely in W2, and W2 sets
  the phase.  It does not slow the work down; IT EATS THE BUDGET.
""")
    print(f'  {"scene":14s} |{"W1":>8}{"W2":>8}{"p":>7}{"f/pass":>7}  frames'
          f'{"":16s}|{"W1":>8}{"W2":>8}{"p":>7}{"f/pass":>7}  frames')
    print(f'  {"":14s} |{"------ PLAIN (no contention) ------":^46s}'
          f'|{"----- CONTENDED (real 48K) -----":^46s}')
    for tag, kw in (('idle', {}), ('right', dict(direction='right')),
                    ('left', dict(direction='left')),
                    ('down', dict(direction='down')),
                    ('up', dict(direction='up')),
                    ('quiet', dict(quiet=True)),
                    ('cluster idle', dict(warp=(88, 108))),
                    ('cluster down', dict(warp=(88, 108), direction='down'))):
        cells = []
        for cont in (False, True):
            rows = passes(fresh(contended=cont, **kw), n)
            cells.append((
                statistics.mean(r['w1'] for r in rows) / FRAME_T,
                statistics.mean(r['w2'] for r in rows) / FRAME_T,
                statistics.mean(r['t0'] % FRAME_T for r in rows) / FRAME_T,
                statistics.mean(r['dt'] for r in rows) / FRAME_T,
                dict(sorted(collections.Counter(
                    round(r['dt'] / FRAME_T) for r in rows).items()))))
        a, b = cells
        print(f'  {tag:14s} |{a[0]:8.4f}{a[1]:8.4f}{a[2]:7.4f}{a[3]:7.3f}  '
              f'{str(a[4]):22s}|{b[0]:8.4f}{b[1]:8.4f}{b[2]:7.4f}{b[3]:7.3f}  '
              f'{b[4]}')
    print(r"""
  READ THE p COLUMN.  Contention adds ~0.093 frames to W2 and therefore the
  SAME ~0.093 to the phase, which comes straight off the compute budget:
  2 - p falls from 1.806 to 1.712 frames.  W1 barely moves (+0.01).  So a
  scene that fitted with 0.008 frames to spare no longer fits, and ORDINARY
  PLAY IN DUNGEON 1 IS A FIVE-FRAME PASS ON REAL HARDWARE -- 10.02 Hz, not
  12.52.  Only a playfield with no actors at all (W1 = 1.22 f) still fits.
""")


CMDS = {'halts': cmd_halts, 'model': cmd_model, 'seams': cmd_seams,
        'units': cmd_units, 'entropy': cmd_entropy, 'contend': cmd_contend}


def main():
    args = sys.argv[1:]
    if not args or args[0] == 'all':
        for k in ('halts', 'model', 'seams', 'units', 'entropy', 'contend'):
            CMDS[k](args[1:])
            print()
        return
    CMDS[args[0]](args[1:])


if __name__ == '__main__':
    main()
