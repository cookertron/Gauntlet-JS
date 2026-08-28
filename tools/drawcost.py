#!/usr/bin/env python3
"""
drawcost.py -- what the DRAW phase of one main-loop pass costs, and why.

    python tools/drawcost.py split   [dir] [n]   per-call-site T per pass
    python tools/drawcost.py phase   [dir] [n]   per-pass table + camera phase
    python tools/drawcost.py cells               cell sweep vs camera parity
    python tools/drawcost.py scroll              H scroll vs V scroll, isolated
    python tools/drawcost.py model   [n]         fit + predict the 4/5 frame
    python tools/drawcost.py w1                  the RIGHT-vs-UP W1 gap

Everything is anchored on $8503 (the main-loop top, visited exactly once per
pass -- see tools/sim_move.py).  Nothing here advances the machine by a fixed
number of video frames.

THE STRUCTURE OF A PASS, measured not assumed:

    $8503 .. $8550   twenty-two CALLs -- the SIMULATION and the DRAW INTO THE
                     SHADOW SCREEN.  This is W1.
    $8550 CALL $9CD7
       $9CD7 HALT    <-- the only frame sync in an ordinary pass
       $9CD8 DI
       $9CD9 $B8FB   beeper tone tick
       $9CE1 $9D3B   shadow bitmap  $C000 -> $4000, 64 pixel lines
       $9CE9 $9DA3   shadow attrs   $D800 -> $5800, 40 groups
       $9CF1 $9D3B   shadow bitmap  $C800 -> $4800, 96 pixel lines
       $9CF8 $A29F   the HUD / frame counter
       $9CFE $B4FF   clear the shadow playfield
    $8553 $A36F, $8556 loop test                 -- this tail is W2.

so one pass = W1 (variable) + the HALT wait + W2 (near-constant), and the
whole 4-vs-5-frame question is whether the phase p plus W1 crosses 2.0.

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
import os
import pickle
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, SP, IFF, TAPE_CALL_PC,     # noqa: E402
                     FRAME_T)
from keyprobe import KEYS, keymask                               # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
LOOP_TOP = 0x8503

# ---------------------------------------------------------------- the probes
# T-stamps: the first time each PC is reached inside a pass.
STAMP = [
    0x8503,                                             # top
    0x8506, 0x8509, 0x850C, 0x850F, 0x8512, 0x8515,
    0x8518, 0x851B, 0x851E, 0x8521, 0x8524, 0x8527,
    0x852E, 0x8531, 0x8534, 0x8537, 0x853A, 0x853D,
    0x8540, 0x8543, 0x8546, 0x8550,                     # -> $9CD7
    0x9CD7, 0x9CD8, 0x9CDC, 0x9CE4, 0x9CEC, 0x9CF4,
    0x9CF8, 0x9CFE, 0x9D01, 0x8553, 0x8556,
]
STAMP_NAME = {
    0x8503: 'a31a', 0x8506: '95ab', 0x8509: 'a38a', 0x850C: 'b58c cam',
    0x850F: '9efc MAP', 0x8512: '9fc2 ROW', 0x8515: 'a08b COL',
    0x8518: 'a159 CNR', 0x851B: 'a3e6', 0x851E: 'ab94 ACTORS',
    0x8521: 'a43b', 0x8524: '8bca', 0x8527: '8ada?', 0x852E: 'b6da',
    0x8531: '971b', 0x8534: '9788', 0x8537: '94ae', 0x853A: 'b0fe cull',
    0x853D: 'a9c2 gen', 0x8540: '93c2', 0x8543: '891c banner',
    0x8546: '(tail)', 0x8550: '->9cd7',
    0x9CD7: 'HALT', 0x9CD8: 'tone', 0x9CDC: 'bmp1', 0x9CE4: 'attr',
    0x9CEC: 'bmp2', 0x9CF4: 'a29f hud', 0x9CF8: '(iy12)', 0x9CFE: 'b4ff clr',
    0x9D01: 'tailtest', 0x8553: 'a36f', 0x8556: 'looptest',
}
# counters
COUNT = {
    0x9F6C: 'map_cell',      # $9EFC main sweep: a cell inspected
    0x9F70: 'map_draw',      # ...and it was non-zero
    0x9FEF: 'row_cell',      # $9FC2 extra ROW (camy bit1): inspected
    0x9FF3: 'row_draw',
    0xA0AF: 'col_cell',      # $A08B extra COL (camx bit1): inspected
    0xA0B3: 'col_draw',
    0xA057: 'row2_cell',     # ...the SECOND sweep of each: the extra row and
    0xA05B: 'row2_draw',     # the extra column are each drawn TWICE, once at
    0xA118: 'col2_cell',     # each edge of the viewport, which is why the
    0xA11C: 'col2_draw',     # cell counts are 160/170/176/187 and not 160
    0xA172: 'cnr_cell',      # $A159 corner: its four cell tests...
    0xA193: 'cnr_c1', 0xA1B2: 'cnr_c2', 0xA1D3: 'cnr_c3',
    0xA177: 'cnr_d0', 0xA198: 'cnr_d1',   # ...and the non-zero ones
    0xA1B7: 'cnr_d2', 0xA1D7: 'cnr_d3',
    0xA1DA: 'actor_try',     # actor draw entry (per live actor considered)
    0xA225: 'actor_full',    # ...drew a whole 16x16
    0xA24C: 'actor_clip',    # ...drew a clipped one
    0x9DD2: 'blit16',        # the full 16x16 tile blitter
    0x9E4B: 'blit_col',      # partial entry used by the extra COLUMN
    0x9EA0: 'blit_row',      # partial entry used by the extra ROW
    0xB8CC: 'noise',         # beeper noise sample: one per drawn object
    0xB8D3: 'noise_tog',     # ...and it toggled the speaker (ramp armed)
    0xB8E2: 'noise_ramp',    # ...the ramp counter is live
    0xB156: 'b156',          # the $30 teleporter hook
    0x9D3B: 'bmpblit',
    0x9DA3: 'attrblit',
    0xA2F8: 'attrfill',
}
NS = len(STAMP)
PROBE = {}
for i, pc in enumerate(STAMP):
    PROBE[pc] = i
CNAMES = []
for pc, nm in COUNT.items():
    PROBE[pc] = NS + len(CNAMES)
    CNAMES.append(nm)
NC = len(CNAMES)


def run_pass(h):
    """Step to the next $8503, collecting stamps, counts and ISR time.

    Returns (total_T, stamps, counts, isr).  stamps[i] is the T-state at which
    STAMP[i] was first reached this pass, or None.  isr[i] is the T-state cost
    of every frame interrupt that landed inside segment i -- WITHOUT it every
    per-segment figure is contaminated by ~2,200 T of ISR wherever the
    interrupt happened to fall, which is exactly the size of the spurious
    variation this tool first reported.
    """
    sim = h.sim
    regs = sim.registers
    opcodes = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    probe = PROBE
    stamps = [None] * NS
    counts = [0] * NC
    isr = [0] * NS
    snap = []
    t0 = regs[T]
    stamps[0] = t0
    cur = 0
    # The ISR is `$DADA JP $A29F` -- the same routine $9CF8 CALLs by hand --
    # and it exits on a plain RET, not RETI, so it is bracketed by the STACK:
    # the interrupt pushes the return address, and the handler is done when SP
    # has climbed back above it.  (Safe here because every LD SP,source
    # blitter in this game runs behind DI.)
    inisr = False
    isr_sp = 0
    tin = 0
    n = 0
    while n < 8_000_000:
        pc = regs[PC]
        if inisr and regs[SP] > isr_sp:
            inisr = False
            isr[cur] += regs[T] - tin
        if n and pc == LOOP_TOP and not inisr:
            return (regs[T] - t0, stamps, counts, isr, snap)
        hit = probe.get(pc)
        if hit is not None:
            if hit < NS:
                if stamps[hit] is None:
                    stamps[hit] = regs[T]
                    cur = hit
                    if pc == 0x850F:      # the instant the draw phase starts
                        snap.append((bytes(mem[0x8000:0x8400]),
                                     mem[0x848B], mem[0x848C]))
            else:
                counts[hit - NS] += 1
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            sp0 = regs[SP]
            h._fast_halt()          # jumps the clock AND takes the interrupt
            if not inisr and regs[SP] != sp0:
                inisr, isr_sp, tin = True, regs[SP], regs[T]
            n += 1
            continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sp0, tacc = regs[SP], regs[T]   # tacc: BEFORE the 19 T IM2 ack
            sim.accept_interrupt(regs, mem, pc)
            # accept_interrupt DECLINES right after an EI (the Z80's one
            # instruction of grace).  Only a real acceptance pushes a return
            # address, so the stack, not the call, says whether it happened --
            # bracketing on the call alone charged a whole 9,784 T of ordinary
            # blitting to the ISR and made $A08B look scene-dependent.
            if not inisr and regs[SP] != sp0:
                inisr, isr_sp, tin = True, regs[SP], tacc
        n += 1
    raise RuntimeError('no main-loop top in 8M instructions')


# ------------------------------------------------------- THE DRAW COST MODEL
# Enumerated, not fitted: every constant below came from calling the routine
# in isolation with exactly one map cell non-zero, over all 1,024 cells, at
# each of the four camera parities.  See `python tools/drawcost.py probe`.
#
#   camx/camy are in QUARTER-CELL units, so bit 1 is a HALF-TILE offset.
#   origin cell = (camy >> 2, camx >> 2); the viewport is 16 x 10 tiles of
#   16x16 pixels; a half-tile offset on an axis splits one row/column of the
#   viewport into two partial ones, drawn by a separate routine.
MAP_BASE = {(0, 0): 26412, (2, 0): 24881, (0, 2): 23861, (2, 2): 22486}
ROW_BASE = {0: 5248, 2: 4996}        # keyed on camx & 2
COL_BASE = {0: 3850, 2: 3512}        # keyed on camy & 2
CNR_BASE = 385
THIRD = (3, 7)      # sweep rows whose 16x16 straddles a screen-third boundary


def predict_draw(m, camx, camy):
    """(map bytes at $8000, camx, camy) -> the T-states $850F..$851B will
    cost.  Returns (map, row, col, cnr)."""
    cx2, cy2 = camx & 2, camy & 2
    r0, c0 = camy >> 2, camx >> 2
    cols = [(c0 + 1 + i) & 31 for i in range(15)] if cx2 else            [(c0 + i) & 31 for i in range(16)]
    rows = [(r0 + 1 + i) & 31 for i in range(9)] if cy2 else            [(r0 + i) & 31 for i in range(10)]
    cell = lambda r, c: m[((r & 31) << 5) | (c & 31)]

    # The sweep walks the map with 8-bit L arithmetic, so the loop's own cost
    # moves with the ORIGIN as well as the parity: stepping to the next map
    # row is `ADD A,$20 / JR nc`, 29 T dearer when L carries (one map row in
    # eight), and 1 T cheaper on the row that wraps $83xx -> $80xx; stepping
    # along a row is `INC L / AND $1F / JR nz`, 10 T dearer on the column that
    # wraps.  Both are the original's own address arithmetic, and a port that
    # walks the same map indices can count them without knowing any of this.
    cfirst = cols[0]
    carries = sum(1 for r in rows
                  if ((((r & 31) << 5) | cfirst) & 0xFF) + 0x20 > 0xFF)
    hwraps = sum(1 for i in range(1, len(rows)) if rows[i] < rows[i - 1])
    colwrap = 1 if any(((c + 1) & 31) == 0 for c in cols[:-1]) or         ((cols[-1] + 1) & 31) == 0 else 0
    t_map = MAP_BASE[(cx2, cy2)] + 29 * (carries - 1) - hwraps         + 10 * len(rows) * colwrap
    for i, r in enumerate(rows):
        extra = 19 if (cy2 and i in THIRD) else 0
        for c in cols:
            if cell(r, c):
                t_map += 807 + extra

    t_row = 31
    if cy2:
        t_row = ROW_BASE[cx2]
        for c in cols:
            if cell(r0, c):
                t_row += 501                      # the top partial row
            if cell(r0 + 10, c):
                t_row += 486                      # the bottom partial row
    t_col = 31
    if cx2:
        t_col = COL_BASE[cy2] + 2 * 29 * (carries - 1) - 2 * hwraps
        for i, r in enumerate(rows):
            extra = 19 if (cy2 and i in THIRD) else 0
            if cell(r, c0):
                t_col += 633 + extra              # the left partial column
            if cell(r, c0 + 16):
                t_col += 627 + extra              # the right partial column
    t_cnr = 35 if not cx2 else (64 if not cy2 else CNR_BASE)
    if cx2 and cy2:
        for dr, dc, cost in ((0, 0, 1144), (0, 16, 1143),
                             (10, 0, 1062), (10, 16, 1121)):
            if cell(r0 + dr, c0 + dc) & 0x7F:     # the corner tests AND $7F
                t_cnr += cost
    # +17 for the CALL at $850F/$8515/$8518 and +21 at $8512 (the segment a
    # pass-level model has to charge is the call site, not the routine body).
    return t_map + 17, t_row + (21 if cy2 else 17), t_col + 17, t_cnr + 17


def cmd_verify(npass=60):
    """Predict the four draw segments from the map and the camera, and
    compare with what the real Z80 spent, pass by pass."""
    bad = tot = 0
    worst = 0
    print('-- draw-cost model vs the real Z80, per pass, per routine')
    for d in (None, 'right', 'left', 'up', 'down'):
        for act in (True, False):
            h = fresh(d, actors=act)
            rows = record(h, int(npass))
            errs = []
            loud = 0
            for r in rows:
                m, camx, camy = r['snap']
                pred = predict_draw(m, camx, camy)
                meas = (seg(r, 0x850F, 0x8512), seg(r, 0x8512, 0x8515),
                        seg(r, 0x8515, 0x8518), seg(r, 0x8518, 0x851B))
                if r['noise_ramp']:
                    # the beeper's noise ramp charges every DRAWN OBJECT an
                    # extra 28 T (no toggle) or 67 T (toggle) -- a sound term,
                    # measured in NOTES-battery Q17, not a draw term
                    loud += 1
                    continue
                for p_, q_ in zip(pred, meas):
                    tot += 1
                    if p_ != q_:
                        bad += 1
                        errs.append(p_ - q_)
                        worst = max(worst, abs(p_ - q_))
            tag = f'{d or "idle"}{"" if act else "/noact"}'
            print(f'   {tag:<14} {len(rows) - loud:>4} silent passes '
                  f'(+{loud} with the noise ramp armed)  '
                  f'{"all exact" if not errs else str(len(errs)) + " off by " + str(sorted(set(errs)))}')
    print(f'   TOTAL {tot - bad}/{tot} exact, worst error {worst} T '
          f'({worst/FRAME_T:.5f} frames)')


def fresh(direction=None, actors=True):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if direction:
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    if not actors:
        h.poke(0x8496, 0)
    run_pass(h)
    return h


def record(h, n):
    """n passes -> list of dicts."""
    out = []
    for i in range(n):
        m = h.memobj.m
        camx0, camy0 = m[0x848B], m[0x848C]
        dt, st, ct, isr, snap = run_pass(h)
        m = h.memobj.m
        r = {'i': i, 'dt': dt, 'f': dt / FRAME_T, 'isr': sum(isr),
             'snap': snap[0] if snap else None,
             'camx0': camx0, 'camy0': camy0,
             'camx': m[0x848B], 'camy': m[0x848C],
             'px': m[0x8420], 'py': m[0x8421],
             'nact': m[0x8496]}
        for k, name in enumerate(CNAMES):
            r[name] = ct[k]
        for j, pc in enumerate(STAMP):
            r['t_%04x' % pc] = st[j]
            r['isr_%04x' % pc] = isr[j]
        out.append(r)
    return out


def seg(r, a, b, net=True):
    """T-states between two stamps, or None.

    net=True subtracts the frame interrupts that landed inside the span, so
    the figure is the game's own work.  Without it every segment carries a
    random 2,198 T depending on where the ISR fell.
    """
    x, y = r['t_%04x' % a], r['t_%04x' % b]
    if x is None or y is None:
        return None
    d = y - x
    if net:
        i0, i1 = STAMP.index(a), STAMP.index(b)
        for j in range(i0, i1):
            d -= r['isr_%04x' % STAMP[j]]
    return d


def hist(vals, digits=2):
    c = Counter(round(v, digits) for v in vals)
    return ' '.join(f'{k}:{c[k]}' for k in sorted(c))


# ------------------------------------------------------------------- split
def cmd_split(direction='right', n=40):
    h = fresh(direction if direction != 'idle' else None)
    rows = record(h, int(n))
    print(f'-- per-call-site cost, {direction}, {len(rows)} passes '
          f'(T-states; {FRAME_T} T = 1 frame)')
    print(f'{"segment":<16}{"mean":>9}{"min":>9}{"max":>9}{"mean f":>9}')
    order = STAMP
    tot = 0
    for a, b in zip(order, order[1:]):
        vals = [seg(r, a, b) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        if mean < 1.0:
            continue
        tot += mean
        print(f'{STAMP_NAME[a]:<16}{mean:>9.0f}{min(vals):>9}{max(vals):>9}'
              f'{mean/FRAME_T:>9.3f}')
    fr = [r['f'] for r in rows]
    print(f'{"TOTAL":<16}{sum(r["dt"] for r in rows)/len(rows):>9.0f}'
          f'{"":>18}{sum(fr)/len(fr):>9.3f}')
    w1 = [seg(r, 0x8503, 0x9CD7) / FRAME_T for r in rows]
    w2 = [(r['dt'] - (r['t_9cd8'] - r['t_8503'])) / FRAME_T for r in rows]
    print(f'  W1 (top->HALT)  mean {sum(w1)/len(w1):.3f} f  '
          f'[{min(w1):.3f}..{max(w1):.3f}]')
    print(f'  W2 (DI->top)    mean {sum(w2)/len(w2):.3f} f  '
          f'[{min(w2):.3f}..{max(w2):.3f}]')
    print('  frames: ' + hist(fr))


# ------------------------------------------------------------------- phase
def cmd_phase(direction='up', n=30):
    h = fresh(direction if direction != 'idle' else None)
    rows = record(h, int(n))
    print(f'-- {direction}: per pass.  cam parity = (camx&2, camy&2) BEFORE '
          f'the pass drew')
    print(' i  camx camy par  cells drawn  actors blits  W1     draw   f')
    for r in rows:
        cells = r['map_cell'] + r['row_cell'] + r['col_cell'] + r['cnr_cell']
        drawn = r['map_draw'] + r['row_draw'] + r['col_draw']
        w1 = seg(r, 0x8503, 0x9CD7) / FRAME_T
        draw = seg(r, 0x850F, 0x851B) / FRAME_T
        par = f"{1 if r['camx'] & 2 else 0}{1 if r['camy'] & 2 else 0}"
        print(f"{r['i']:>2} {r['camx']:>5}{r['camy']:>5}  {par}  "
              f"{cells:>6}{drawn:>6} {r['actor_try']:>6}{r['blit16']:>6}  "
              f"{w1:>6.3f} {draw:>6.3f} {r['f']:>6.2f}")


# ------------------------------------------------------------------- cells
def cmd_cells(n=60):
    """Cells swept and cost, bucketed by camera parity, over all directions."""
    print('-- cell sweep and DRAW cost by camera parity (camx&2, camy&2)')
    print('   the draw phase is $850F..$851B = $9EFC + $9FC2 + $A08B + $A159')
    agg = defaultdict(list)
    for d in ('right', 'left', 'up', 'down'):
        h = fresh(d)
        for r in record(h, int(n)):
            par = (1 if r['camx'] & 2 else 0, 1 if r['camy'] & 2 else 0)
            agg[par].append(r)
    print(f'{"par":<6}{"n":>4}{"map":>6}{"row":>5}{"col":>5}{"cnr":>5}'
          f'{"cells":>7}{"drawn":>7}{"blit16":>8}{"draw T":>9}{"draw f":>8}')
    for par in sorted(agg):
        rs = agg[par]
        def mn(k):
            return sum(r[k] for r in rs) / len(rs)
        cells = mn('map_cell') + mn('row_cell') + mn('col_cell') + mn('cnr_cell')
        drawn = mn('map_draw') + mn('row_draw') + mn('col_draw')
        dt = sum(seg(r, 0x850F, 0x851B) for r in rs) / len(rs)
        print(f'{str(par):<6}{len(rs):>4}{mn("map_cell"):>6.1f}'
              f'{mn("row_cell"):>5.1f}{mn("col_cell"):>5.1f}'
              f'{mn("cnr_cell"):>5.1f}{cells:>7.1f}{drawn:>7.1f}'
              f'{mn("blit16"):>8.1f}{dt:>9.0f}{dt/FRAME_T:>8.3f}')


# ------------------------------------------------------------------ scroll
def cmd_scroll():
    """HORIZONTAL versus VERTICAL scroll, from the enumerated constants.

    The camera steps 2 units a pass and a map cell is 4 units, so a move on
    an axis toggles that axis's HALF-TILE bit every pass.  Crossing it is the
    only thing a "scroll" costs, because the game does not scroll the display
    file at all: it recomposites the shadow screen from scratch every pass
    and blits the whole of it with a fixed-cost unrolled copy.
    """
    def total(cx2, cy2, d):
        """draw cost, T, at density d (fraction of viewport cells solid)."""
        rows = 9 if cy2 else 10
        cols = 15 if cx2 else 16
        t = MAP_BASE[(cx2, cy2)] + d * (rows * cols * 807 + (2 * cols * 19 if cy2 else 0))
        t += (ROW_BASE[cx2] + d * cols * (501 + 486)) if cy2 else 31
        t += (COL_BASE[cy2] + d * rows * (633 + 627 + (2 * 19 * 2 / rows if cy2 else 0)))             if cx2 else 31
        t += (CNR_BASE + d * (1144 + 1143 + 1062 + 1121)) if (cx2 and cy2) else              (64 if cx2 else 35)
        return t
    print('-- what a half-tile camera offset costs, by axis and by how solid')
    print('   the view is (T-states; 69,888 T = one video frame)')
    print(f'{"solid":>7}{"flat (0,0)":>12}{"+H (2,0)":>12}{"+V (0,2)":>12}'
          f'{"+both":>12}   {"dH":>8}{"dV":>8}{"dH f":>8}{"dV f":>8}')
    for d in (0.0, 0.1, 0.2, 0.2136, 0.3, 0.5, 1.0):
        a = total(0, 0, d); hx = total(2, 0, d); v = total(0, 2, d)
        b = total(2, 2, d)
        print(f'{d*100:>6.0f}%{a:>12.0f}{hx:>12.0f}{v:>12.0f}{b:>12.0f}   '
              f'{hx-a:>8.0f}{v-a:>8.0f}{(hx-a)/FRAME_T:>8.4f}{(v-a)/FRAME_T:>8.4f}')
    print('   (0.2136 = 40 of 187 cells: dungeon 1 start room density)')
    print()
    print('   The two axes cross over at 33.5% solid: below it a VERTICAL')
    print('   half-tile offset is dearer, above it a HORIZONTAL one is, and')
    print('   the gap never exceeds 0.014 frames = 0.7% of a pass.')


# ------------------------------------------------------------------ budget
def cmd_budget(npass=60):
    """The whole pass, in order, with the DRAW broken out -- and how much of
    each part MOVES, which is what a cost model has to chase."""
    rows = []
    for d in (None, 'right', 'left', 'up', 'down'):
        h = fresh(d)
        rows += record(h, int(npass))
    print(f'-- one pass, {len(rows)} passes over five scenes '
          f'(T-states net of the frame interrupt; 69,888 T = 1 frame)')
    print(f'{"part":<18}{"mean":>8}{"min":>8}{"max":>8}{"spread":>8}'
          f'{"mean f":>9}{"share":>7}')
    order = STAMP
    tot = sum(r['dt'] for r in rows) / len(rows)
    for a, b in zip(order, order[1:]):
        vals = [seg(r, a, b) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        if mean < 100:
            continue
        print(f'{STAMP_NAME[a]:<18}{mean:>8.0f}{min(vals):>8}{max(vals):>8}'
              f'{max(vals)-min(vals):>8}{mean/FRAME_T:>9.3f}{100*mean/tot:>6.1f}%')
    w1 = [seg(r, 0x8503, 0x9CD7, net=False) / FRAME_T for r in rows]
    dr = [seg(r, 0x850F, 0x851B) / FRAME_T for r in rows]
    print(f'   W1 (top -> HALT, the whole 4-vs-5 question)  '
          f'{sum(w1)/len(w1):.3f} f  [{min(w1):.3f}..{max(w1):.3f}]')
    print(f'   of which the DRAW $850F..$851B                '
          f'{sum(dr)/len(dr):.3f} f  [{min(dr):.3f}..{max(dr):.3f}]  '
          f'= {100*sum(dr)/sum(w1):.0f}% of W1')
    ok = sum(1 for r in rows if round(r['f']) ==
             4 + ((r['t_8503'] % FRAME_T) / FRAME_T
                  + seg(r, 0x8503, 0x9CD7, net=False) / FRAME_T > 2.0))
    print(f'   frames = 4 + (p + W1 > 2):  {ok}/{len(rows)} passes')
    print('   frames: ' + hist([r['f'] for r in rows]))


def main():
    args = [a for a in sys.argv[1:]]
    cmd = args[0] if args else 'split'
    rest = args[1:]
    fn = globals().get('cmd_' + cmd)
    if fn is None:
        print(__doc__)
        return
    fn(*rest)


if __name__ == '__main__':
    main()
