#!/usr/bin/env python3
"""
clock48.py -- THE 48K CLOCK.  Phase 11 / battery Q10, re-measured on the branch
the owner's machine actually runs.

    python tools/clock48.py sites     what $B8CC is, where it is called from,
                                      and what one call costs, enumerated
    python tools/clock48.py hist      frames-per-pass HISTOGRAM, both branches,
                                      several hundred passes, anchored on $8503
    python tools/clock48.py model     WHY it costs 0.375: the HALT quantiser
    python tools/clock48.py load      frames-per-pass against LIVE ACTOR COUNT,
                                      both branches (manual B2b)
    python tools/clock48.py natural   the same curve without planting anything
    python tools/clock48.py all

=============================================================================
WHY THIS EXISTS
=============================================================================
$BF21 reads RAM $FFFD (0 = 48K, 1 = 128K, written by block A's $7FFD paging
probe at $CE50) and patches ten bytes.  The harness never runs block A, so
$FFFD holds the BASIC stub's padding $2A -- non-zero -- and every timing
measurement this project made before now was taken on the 128K/AY branch by
accident.  The owner's machine is a 48K.  On it $B8B5 and $B8CC are NOT
patched to RET, and $B8CC -- the beeper's noise tick -- is called from six
sites inside the DRAWING code, once per drawn object.

THE SAMPLER IS $8503, THE MAIN-LOOP TOP.  It is visited exactly once per pass
(NOTES-engine.md, "the broken ruler"), so stepping to it is a one-per-pass
sampler whatever a pass costs in frames.  A four-video-frame window is NOT a
sampler and manufactured a two-row phantom the last time it was used.

THE PASS IS A HALT QUANTISER, WHICH IS WHY THE ANSWER IS NOT A MEAN.

    $8503 .. $854D   the work            W1  (interrupts on; the ISR is in it)
    $8550 CALL $9CD7 -> $9CD7 HALT       wait for the next frame interrupt
    $9CD8 DI / $9CD9 CALL $B8FB          the beeper step + its dead delay
    $9CDC..$9CF3                         three shadow-screen -> screen copies
    $9CF8 CALL $A29F                     the ISR body, paid back by hand
    $9CFB INC (IY+$12)                   the pass counter $8491
    ... RET to $8553, and $8503 again     W2

one pass = ceil((phase + W1)/F)*F - phase + W2, and the phase is stable
because W2 is.  A pass length is therefore QUANTISED TO WHOLE VIDEO FRAMES and
its mean is a DUTY CYCLE BETWEEN TWO QUANTA, not a period.  4.375 is not
"a pass costs 4.375 frames"; it is "5 passes in 8 cost five frames".
"""
import math
import os
import pickle
import statistics
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, R, TAPE_CALL_PC,          # noqa: E402
                     FRAME_T, CPU_HZ)
from keyprobe import KEYS, keymask                                  # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
STATIC = os.path.join(ROOT, 'build', 'image.bin')
LIVE = os.path.join(ROOT, 'build', 'live_cs.bin')

FRAME_HZ = CPU_HZ / FRAME_T                 # 50.08
LOOP_TOP = 0x8503
SYNC = 0x9CD7                      # the one HALT of an ordinary pass
POST = 0x9CD8                      # the instruction the interrupt returns to
NOISE = 0xB8CC                     # the beeper noise tick
AYTICK = 0xBADB                    # the AY 50 Hz service, from the ISR
SPRITE = 0xA1DA                    # the sprite blitter
ACTUPD = 0xABFF                    # the actor update, called from inside it
COUNT = 0x8496                     # live actor count
CTR = 0x8491                       # the pass counter
P1 = 0x8420
CAM = (0x848B, 0x848C)             # the camera's own coordinate
LEVEL = 0x84D2                     # the beeper noise level == (IY+$53)

# $B8CC's six call sites, from a static scan of build/live_cs.bin for CD CC B8
SITES = [
    (0x9F69, 'the map-tile blitter ($9F31)'),
    (0x9FEC, 'the strip blitter $9FC2, arm 1'),
    (0xA054, 'the strip blitter $9FC2, arm 2'),
    (0xA0AC, 'the strip blitter $A08B, arm 1'),
    (0xA115, 'the strip blitter $A159, arm 1'),
    (0xA1DD, 'the SPRITE blitter $A1DA, once per drawn OBJECT'),
]

UNPATCH_128K = {0xB8B5: [0x21, 0x5D, 0x68], 0xB8CC: [0xED, 0x5F]}
DELTA_T = 58                       # 48K $B8CC silent path (68 T) minus RET (10)


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def fresh(direction=None, warp=None):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    if warp:
        m[P1], m[P1 + 1] = warp
    if direction:
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    return h


# The TEN bytes $BF21 writes, measured by booting the real game from $8400
# with ($FFFD) forced to $00 and to $01 and diffing all of $4000..$FFFF.
# Poked directly rather than by re-running $BF21, because $BEDF has already
# copied $1CE bytes of GRAPHICS over $BE32..$BFFF by the time the live state
# was saved -- restoring $BF21's own code there would destroy sprite data the
# blitter then reads, and it does (the game wedges inside the draw).
BEEPER_PATCH = {0xB8B5: 0x21, 0xB8CC: 0xED, 0xBADB: 0xC9, 0xBA01: 0xC9,
                0xBBA7: 0xC9, 0xBBBC: 0xC9, 0xBA2B: 0xC3, 0xBA2C: 0x2B,
                0xBA2D: 0xB9}
SOUNDSEL = 0xFFFD


def to_beeper(h):
    """The state a 48K boot leaves the machine in: $BF21's own 48K arm,
    applied byte for byte, plus the mode flag $9D0A reads at runtime."""
    for a, v in BEEPER_PATCH.items():
        h.poke(a, v)
    h.poke(SOUNDSEL, 0)
    # cross-check against the tape bytes: the two restored opcodes must be
    # what build/image.bin holds, i.e. tape material and not an invention
    img = open(STATIC, 'rb').read()
    assert h.memobj.m[0xB8B5] == img[0xB8B5] and h.memobj.m[0xB8CC] == img[0xB8CC]
    return h


def branch(name, **kw):
    h = fresh(**kw)
    if name == '48k':
        to_beeper(h)
    elif name == '48k-noB8CC':
        to_beeper(h)
        h.memobj.m[0xB8CC] = 0xC9
    elif name != 'ay':
        raise SystemExit('unknown branch ' + name)
    return h


HOOKS = (NOISE, SPRITE, ACTUPD, SYNC, POST, AYTICK)


def passes(h, npass, skip=1):
    """Step npass main-loop passes.  One row per pass:
        dt   $8503 -> $8503, T-states          (the pass's WALL CLOCK)
        w1   $8503 -> the HALT at $9CD7        (the pass's WORK)
        ctr  $8491     count  $8496 (live actors, read at the top)
        vis  how many actors reached $ABFF     (drawn AND updated)
        spr  $A1DA entries       noise  $B8CC entries      ay  $BADB entries
    $B8CC is CALLED on both branches -- on the 128K it is one RET -- so `noise`
    is a branch-independent census of drawn objects.
    """
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    hooks = set(HOOKS)
    rows = []
    hit = Counter()
    t_prev = t_sync = t_post = None
    ay1 = 0
    n = done = 0
    while n < 900_000_000:
        pc = regs[PC]
        if pc in hooks:
            hit[pc] += 1
            if pc == SYNC:
                t_sync = regs[T]
                ay1 = hit[AYTICK]
            elif pc == POST:
                t_post = regs[T]
        if pc == LOOP_TOP:
            t = regs[T]
            if t_prev is not None:
                rows.append(dict(t0=t_prev, dt=t - t_prev,
                                 w1=(t_sync - t_prev) if t_sync else 0,
                                 w2=(t - t_post) if t_post else 0,
                                 ay1=ay1, ctr=mem[CTR], count=mem[COUNT],
                                 vis=hit[ACTUPD], spr=hit[SPRITE],
                                 noise=hit[NOISE], ay=hit[AYTICK]))
                done += 1
                if done >= npass + skip:
                    return rows[skip:]
            t_prev, t_sync, t_post, ay1 = t, None, None, 0
            hit.clear()
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('runaway')


def fhist(rows, key='dt'):
    return Counter(round(r[key] / FRAME_T, 2) for r in rows)


def show_hist(tag, rows, key='dt'):
    xs = [r[key] / FRAME_T for r in rows]
    c = fhist(rows, key)
    mean = sum(xs) / len(xs)
    print(f'  {tag}: {len(xs)} passes, mean {mean:.4f} frames/pass '
          f'= {FRAME_HZ / mean:.2f} passes/s')
    wide = max(c.values())
    for k in sorted(c):
        bar = '#' * max(1, int(56 * c[k] / wide))
        print(f'    {k:6.2f}f {bar} {c[k]}')
    q = Counter(int(round(x)) for x in xs)
    print('    whole frames: ' + '   '.join(
        f'{k}f x{v} ({100.0*v/len(xs):.1f}%)' for k, v in sorted(q.items())))
    return mean


# --------------------------------------------------------------------------
# sites
# --------------------------------------------------------------------------
def cmd_sites(_=()):
    print('=== $B8CC -- THE BEEPER NOISE TICK, and the six sites that call it')
    img = open(STATIC, 'rb').read()
    live = open(LIVE, 'rb').read()
    print(f'  tape bytes at $B8CC: {img[0xB8CC:0xB8CE].hex()}  (ED 5F = LD A,R)')
    print(f'  live dump  at $B8CC: {live[0xB8CC:0xB8CE].hex()}  '
          f'(C9 = RET, the 128K arm $BF21 wrote)')
    print(r"""
  the 48K body, from build/image.bin.  IY = $847F in the main loop, so
  (IY+$53) IS $84D2 -- THE THRESHOLD AND THE RAMP LEVEL ARE THE SAME BYTE:

    B8CC  ED 5F        LD A,R          9 T   the refresh register
    B8CE  FD BE 53     CP (IY+$53)    19 T   = CP ($84D2), the noise LEVEL
    B8D1  30 0A        JR nc,$B8DD    12/7   toggle only when R < level
    B8D3  3A CA 84     LD A,($84CA)   13 T   the shadow of the last $FE write
    B8D6  EE 10        XOR $10         7 T   BIT 4 IS THE SPEAKER
    B8D8  32 CA 84     LD ($84CA),A   13 T
    B8DB  D3 FE        OUT ($FE),A    11 T   <- the entire 48K noise output
    B8DD  3A D2 84     LD A,($84D2)   13 T   the ramp level again
    B8E0  B7           OR A            4 T
    B8E1  C8           RET z          11/5   <- THE SILENT EXIT, 68 T
    B8E2  3C           INC A           4 T   self-modified: $B8E9 -> INC A,
    B8E3  E6 7F        AND $7F         7 T   $B8F2 -> DEC A (ramp up / down)
    B8E5  32 D2 84     LD ($84D2),A   13 T
    B8E8  C9           RET            10 T

  On the 128K the whole thing is one RET, 10 T.  So the 48K pays 58 T MORE
  PER DRAWN OBJECT even in total silence, because the "is anything playing?"
  test is inside the per-object routine instead of around it.  THAT is the
  9%, and it is a RENDERING cost, not an audio one: it is charged whether or
  not a single speaker edge is produced.
""")
    for a, what in SITES:
        print(f'    ${a:04X}  CALL $B8CC   {what}')

    print('\n  ENUMERATED, real Z80, $B8CC entered directly (so the figures')
    print('  are the body + its RET; the CALL is the same 17 T either way).')
    print('  R swept over all 128 values it can hold:')
    h = to_beeper(fresh())
    st = h.save_state()
    for lvl in (0, 1, 0x20, 0x40, 0x60, 0x7F):
        costs = Counter()
        tog = 0
        for r in range(128):
            h.load_state(st)
            h.memobj.m[LEVEL] = lvl
            h.ports.record_writes = True
            h.ports.writes = []
            h.sim.registers[R] = r
            _, dt, _ = h.call(NOISE)
            tog += sum(1 for _, p, _ in h.ports.writes if (p & 0xFF) == 0xFE)
            h.ports.record_writes = False
            costs[dt] += 1
        print(f'    $84D2 = {lvl:3d}   T per call {dict(sorted(costs.items()))}'
              f'   speaker OUTs {tog:3d}/128  ({100.0*tog/128:.0f}% density)')
    h2 = fresh()
    _, dt, _ = h2.call(NOISE)
    print(f'    128K, $B8CC = RET      T per call {{{dt}: 128}}'
          f'   speaker OUTs   0/128')
    print(f'\n  -> the 48K silent path is {68 - dt} T dearer than the 128K RET,')
    print(f'     a playing-but-not-toggling one {96 - dt} T dearer, and a')
    print(f'     toggling one {135 - dt} T dearer.  The SILENT figure is the')
    print('     one that matters: nothing is playing on all but a few passes.')

    print('\n  THE OTHER SIX PATCHED BYTES cost the 48K NOTHING per pass except')
    print('  $BADB, the AY 50 Hz service, which the 48K turns into a RET:')
    hay = fresh()
    _, dt_ay, _ = hay.call(AYTICK)
    h48 = to_beeper(fresh())
    _, dt_48, _ = h48.call(AYTICK)
    print(f'    $BADB quiet: AY {dt_ay} T, 48K {dt_48} T  '
          f'-> the 48K SAVES {dt_ay - dt_48} T per ISR, ~4 ISRs a pass '
          f'= {4*(dt_ay-dt_48)} T')
    print('    $B8FB (the beeper step, $9CD9) is NOT patched by $BF21 and runs')
    hb = fresh()
    _, dt_b, _ = hb.call(0xB8FB)
    print(f'    on both branches: {dt_b} T = {dt_b/FRAME_T:.3f} frames a pass '
          f'of dead DEC HL delay, identical either side.')


# --------------------------------------------------------------------------
# hist
# --------------------------------------------------------------------------
SCENES = [('down', dict(direction='down')),
          ('idle', dict()),
          ('right', dict(direction='right'))]


def cmd_hist(args=()):
    n = int(args[0]) if args else 400
    print('=== FRAMES PER PASS, ANCHORED ON $8503')
    print(f'    {n} passes per cell; 50.08 Hz; one frame = {FRAME_T} T')
    summary = {}
    for scene, kw in SCENES:
        print(f'\n--- {scene} ---')
        for br in ('ay', '48k', '48k-noB8CC'):
            rows = passes(branch(br, **kw), n)
            summary[(scene, br)] = show_hist(f'{br:11s}', rows)
    print('\n=== SUMMARY: mean frames per pass / passes per second')
    print(f'  {"scene":6s} {"AY (128K)":>21s} {"BEEPER (48K)":>21s} '
          f'{"48K, $B8CC:=C9":>21s}   48K/AY')
    for scene, _ in SCENES:
        cells, mus = [], []
        for br in ('ay', '48k', '48k-noB8CC'):
            mu = summary[(scene, br)]
            mus.append(mu)
            cells.append(f'{mu:.4f}f {FRAME_HZ/mu:6.2f}/s')
        print(f'  {scene:6s} ' + ' '.join(f'{c:>21s}' for c in cells) +
              f'   {mus[1]/mus[0]:.3f}')
    return summary


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
def cmd_model(args=()):
    n = int(args[0]) if args else 300
    print('=== WHY THE 48K COSTS 0.375 FRAMES A PASS: IT DOES NOT.  IT COSTS')
    print('=== 0.19, AND THE HALT AT $9CD7 ROUNDS THAT UP TO A WHOLE FRAME ON')
    print('=== THE PASSES THAT WERE ALREADY WITHIN 0.19 OF A BOUNDARY.')
    print(r"""
  MEASURED, by stamping T at $8503, at the HALT $9CD7 and at $9CD8 (the
  instruction the interrupt returns to), and reading the identity off the
  numbers rather than deriving it:

      p   = ($8503's T) mod 69888          the pass's phase on the frame grid
      W1  = $8503 -> $9CD7                 EVERYTHING THE PASS COMPUTES
      W2  = $9CD8 -> the next $8503        the three shadow-screen copies,
                                           $B8FB, $A29F by hand, $B4FF
      one pass = ceil(p + W1) - p + W2     frames

  W2 is 2.15..2.19 frames and is NOT quantised -- it runs with interrupts
  disabled ($9CD8 DI) and nothing waits for anything.  p is therefore pinned
  at W2 mod 1 = 0.18/0.21 and only W1 is on the clock.  So the game gives
  itself a COMPUTE BUDGET OF EXACTLY TWO VIDEO FRAMES minus the phase, and
  the whole question is whether W1 fits in it:

      p + W1 <= 2   ->  a 4-frame pass          p + W1 > 2  ->  a 5-frame pass

  There is exactly ONE HALT executed per pass and it is $9CD7 (measured by
  logging every HALT the machine retires over eight passes: {$9CD7: 8}).
""")
    hay = fresh()
    _, ay_tick, _ = hay.call(AYTICK)
    print(f'  $BADB costs {ay_tick} T on the AY and 10 T (a RET) on the 48K,')
    print(f'  so each ISR inside W1 gives the 48K {ay_tick - 10} T back.')
    for scene, kw in (('down', dict(direction='down')), ('idle', dict())):
        print(f'\n--- {scene} ---')
        base = None
        for br in ('ay', '48k'):
            rows = passes(branch(br, **kw), n)
            if br == 'ay':
                base = rows
            w1 = [r['w1'] / FRAME_T for r in rows]
            ph = [(r['t0'] % FRAME_T) / FRAME_T for r in rows]
            dt = [r['dt'] / FRAME_T for r in rows]
            nb = [r['noise'] for r in rows]
            five = sum(1 for d in dt if d > 4.5)
            print(f'  {br:5s} phase {min(ph):.3f}..{max(ph):.3f}   '
                  f'W1 mean {statistics.mean(w1):.4f}f  min {min(w1):.4f}  '
                  f'max {max(w1):.4f}  sd {statistics.pstdev(w1):.4f}')
            print(f'        p+W1 mean {statistics.mean(p + w for p, w in zip(ph, w1)):.4f}'
                  f'   over 2.0 on {sum(1 for p, w in zip(ph, w1) if p + w > 2):3d}'
                  f'/{n} passes;  5-frame passes {five}/{len(dt)} '
                  f'= {100.0*five/len(dt):.1f}%')
            print(f'        $B8CC calls/pass {statistics.mean(nb):7.1f} '
                  f'(min {min(nb)} max {max(nb)}) = '
                  f'{statistics.mean(nb)*DELTA_T/FRAME_T:.4f} frames at '
                  f'{DELTA_T} T a call')
            b = Counter(round(x, 1) for x in w1)
            print('        W1 (0.1f bins): ' +
                  ' '.join(f'{k}:{v}' for k, v in sorted(b.items())))
        # THE GATE: does "p + W1 > 2" call the 5-frame pass, PER PASS?
        print('  THE RULE AS A PER-PASS GATE ("p+W1 > 2" <=> a 5-frame pass):')
        for br in ('ay', '48k'):
            rows = passes(branch(br, **kw), n)
            ok = sum(1 for r in rows
                     if ((r['t0'] % FRAME_T + r['w1']) > 2 * FRAME_T)
                     == (r['dt'] > 4.5 * FRAME_T))
            print(f'        {br:5s} {ok}/{len(rows)} passes called correctly')
        # and the cross-branch prediction, with its residual stated
        b48 = passes(branch('48k', **kw), n)
        d_meas = (statistics.mean(r['w1'] for r in b48)
                  - statistics.mean(r['w1'] for r in base))
        d_pred = (statistics.mean(r['noise'] for r in base) * DELTA_T
                  - statistics.mean(r['ay1'] for r in base) * (ay_tick - 10))
        print('  THE PER-PASS EXTRA WORK, predicted against measured:')
        print(f'        predicted  {DELTA_T} T x {statistics.mean(r["noise"] for r in base):.1f}'
              f' calls - {ay_tick-10} T x {statistics.mean(r["ay1"] for r in base):.2f}'
              f' ISRs = {d_pred:8.0f} T = {d_pred/FRAME_T:.4f} frames')
        print(f'        measured   W1(48K) - W1(AY)             '
              f'      = {d_meas:8.0f} T = {d_meas/FRAME_T:.4f} frames')
        print(f'        residual                                '
              f'      = {d_meas-d_pred:8.0f} T = '
              f'{100*(d_meas-d_pred)/d_pred:+.1f}%  (the phase moves too:')
        print('        the 48K\'s W2 is one $BADB cheaper, which shifts p)')
        f = 100.0 / n
        base5 = sum(1 for r in base if r['dt'] > 4.5 * FRAME_T)
        got = sum(1 for r in b48 if r['dt'] > 4.5 * FRAME_T)
        print(f'  5-FRAME PASSES: AY {base5:3d}/{n} = {base5*f:5.1f}%   '
              f'48K {got:3d}/{n} = {got*f:5.1f}%')


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------
def plant(h, n):
    """Fill the actor list with n class-0 records spread over the viewport.

    DECLARED SYNTHETIC.  Dungeon 1 ships 63 actors and a short run only ever
    loses some, so a controlled load curve has to be planted.  The record
    format is the game's own, the real Z80 runs the real update and the real
    blitter on them, and every row below is binned by the count the pass
    ACTUALLY ran with, not by the planted one.
    """
    m = h.memobj.m
    camx, camy = m[CAM[0]], m[CAM[1]]
    px, py = m[P1], m[P1 + 1]
    k, a = 0, 0x5C00
    # the $A1DA window: (x+3-camx)&$7F <= $42 across, <= $2A down
    for dy in range(0, 0x2B, 2):
        for dx in range(0, 0x43, 2):
            if k >= n:
                break
            x = (camx + dx - 3) & 0x7F
            y = (camy + dy - 3) & 0x7F
            if abs(((x - px + 64) & 0x7F) - 64) < 10 and \
               abs(((y - py + 64) & 0x7F) - 64) < 10:
                continue                      # keep them off the player
            m[a], m[a + 1], m[a + 2], m[a + 3] = x, y, 0x00, 0x00
            a += 4
            k += 1
        if k >= n:
            break
    m[COUNT] = k
    m[0x8494], m[0x8495] = a & 0xFF, a >> 8
    return k


LOADS = (0, 10, 20, 30, 40, 50, 63, 80, 100, 120, 150, 190)


def cmd_load(args=()):
    npass = int(args[0]) if args else 20
    print('=== FRAMES PER PASS AGAINST LIVE ACTOR COUNT -- BOTH BRANCHES')
    print('    (manual B2b: a port running a rigid timestep is uniformly')
    print('     EASIER than the original exactly where the game should bite)')
    print()
    print(f'  {npass} passes at each planted population, holding DOWN.')
    print('  "count" and "vis" are the MEASURED per-pass means: vis is how')
    print('  many actors reached $ABFF, i.e. were inside the viewport and so')
    print('  were both drawn and updated.  "B8CC" is drawn objects per pass.')
    print()
    hdr = (f'  {"plant":>5} {"count":>6} {"vis":>5} {"spr":>5} {"B8CC":>6}  '
           f'{"AY f/pass":>9} {"AY Hz":>6}   {"48K f/pass":>10} {"48K Hz":>6} '
           f'{"48K/AY":>7}')
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    allrows = {'ay': [], '48k': []}
    for nplant in LOADS:
        cells = {}
        for br in ('ay', '48k'):
            h = branch(br, direction='down')
            k = plant(h, nplant)
            rows = passes(h, npass)
            cells[br] = rows
            allrows[br].extend(rows)
        a, b = cells['ay'], cells['48k']
        fa = statistics.mean(r['dt'] for r in a) / FRAME_T
        fb = statistics.mean(r['dt'] for r in b) / FRAME_T
        print(f'  {k:5d} {statistics.mean(r["count"] for r in a):6.1f} '
              f'{statistics.mean(r["vis"] for r in a):5.1f} '
              f'{statistics.mean(r["spr"] for r in a):5.1f} '
              f'{statistics.mean(r["noise"] for r in a):6.0f}  '
              f'{fa:9.3f} {FRAME_HZ/fa:6.2f}   {fb:10.3f} {FRAME_HZ/fb:6.2f} '
              f'{fb/fa:7.3f}')

    print('\n  THE SAME ROWS RE-BINNED BY THE PASS\'S OWN VISIBLE-ACTOR COUNT')
    print(f'  {"vis":>7} {"n":>5} {"AY f/pass":>10} {"AY Hz":>7}   '
          f'{"48K f/pass":>10} {"48K Hz":>7}  {"48K/AY":>7}')
    bins = [(0, 0), (1, 10), (11, 20), (21, 30), (31, 40), (41, 50),
            (51, 60), (61, 75), (76, 95), (96, 120), (121, 999)]
    for lo, hi in bins:
        a = [r['dt'] / FRAME_T for r in allrows['ay'] if lo <= r['vis'] <= hi]
        b = [r['dt'] / FRAME_T for r in allrows['48k'] if lo <= r['vis'] <= hi]
        if not a or not b:
            continue
        fa, fb = statistics.mean(a), statistics.mean(b)
        tag = f'{lo}' if lo == hi else f'{lo}-{hi}'
        print(f'  {tag:>7} {len(a):5d} {fa:10.3f} {FRAME_HZ/fa:7.2f}   '
              f'{fb:10.3f} {FRAME_HZ/fb:7.2f}  {fb/fa:7.3f}')

    print('\n  the whole-frame histograms at the extremes:')
    for nplant in (0, 100, 190):
        for br in ('ay', '48k'):
            h = branch(br, direction='down')
            k = plant(h, nplant)
            rows = passes(h, npass)
            c = fhist(rows)
            print(f'    {k:3d} planted  {br:4s}  ' +
                  '  '.join(f'{kk:.2f}f x{v}' for kk, v in sorted(c.items())))


def cmd_natural(args=()):
    """The unplanted curve: the game's own actors and its own generators."""
    npass = int(args[0]) if args else 200
    print('=== UNPLANTED: dungeon 1, its own 63 actors and 12 generators')
    for tag, kw in (('start, DOWN', dict(direction='down')),
                    ('start, idle', dict()),
                    ('warped to the generator cluster (88,108), idle',
                     dict(warp=(88, 108))),
                    ('warped to the generator cluster (88,108), DOWN',
                     dict(warp=(88, 108), direction='down'))):
        print(f'\n--- {tag}, {npass} passes ---')
        for br in ('ay', '48k'):
            rows = passes(branch(br, **kw), npass)
            by = defaultdict(list)
            for r in rows:
                by[r['vis'] // 5 * 5].append(r['dt'] / FRAME_T)
            mu = statistics.mean(r['dt'] for r in rows) / FRAME_T
            print(f'  {br:4s} count {rows[0]["count"]}->{rows[-1]["count"]} '
                  f'(max {max(r["count"] for r in rows)})  '
                  f'mean {mu:.4f}f = {FRAME_HZ/mu:.2f}/s')
            print('       by visible actors: ' + '  '.join(
                f'{k}+:{statistics.mean(v):.2f}f(n={len(v)})'
                for k, v in sorted(by.items())))


# --------------------------------------------------------------------------
# fit: the cost model an engine could actually charge
# --------------------------------------------------------------------------
def solve(A, y):
    """Least squares by the normal equations, k x k Gauss-Jordan.  No numpy."""
    k = len(A[0])
    M = [[sum(r[i] * r[j] for r in A) for j in range(k)] +
         [sum(r[i] * v for r, v in zip(A, y))] for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        d = M[c][c]
        M[c] = [v / d for v in M[c]]
        for r in range(k):
            if r != c and M[r][c]:
                f = M[r][c]
                M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [M[i][k] for i in range(k)]


def collect(br, npass=20):
    rows = []
    for nplant in LOADS:
        h = branch(br, direction='down')
        plant(h, nplant)
        rows.extend(passes(h, npass))
    for kw in (dict(direction='down'), dict(),
               dict(warp=(88, 108)), dict(warp=(88, 108), direction='down')):
        rows.extend(passes(branch(br, **kw), 60))
    return [r for r in rows if r['w1'] and r['w2']]


def cmd_fit(args=()):
    npass = int(args[0]) if args else 20
    print('=== THE COST MODEL AN ENGINE CAN CHARGE, FITTED AND THEN GATED')
    print()
    print('  W1 (T-states of compute in one pass) is regressed on two')
    print('  censuses the port already has at the same point in the pass:')
    print('    objects = how many records reach the sprite blitter $A1DA')
    print('              plus the map tiles the strip blitters redraw, i.e.')
    print('              exactly the $B8CC call count -- and MEASURED below,')
    print('              it is 180 + (live actor count), with no slack;')
    print('    vis     = how many actors reached $ABFF, i.e. survived the')
    print('              viewport cull and were therefore UPDATED as well.')
    print()
    MODELS = [
        ('count', lambda r: [1.0, r['count']]),
        ('count + vis', lambda r: [1.0, r['count'], r['vis']]),
        ('count + vis + vis*count',
         lambda r: [1.0, r['count'], r['vis'], r['vis'] * r['count']]),
    ]
    coeffs = {}
    for br in ('ay', '48k'):
        rows = collect(br, npass)
        y = [float(r['w1']) for r in rows]
        ybar = statistics.mean(y)
        w2 = [r['w2'] for r in rows]
        print(f'  --- {br} ({len(rows)} passes over 12 planted loads and 4 '
              f'unplanted scenes) ---')
        d = Counter(r['noise'] - r['count'] for r in rows)
        print('    $B8CC calls MINUS live actor count, per pass: ' +
              ' '.join(f'{k}:{v}' for k, v in sorted(d.items())[:8]) +
              (' ...' if len(d) > 8 else ''))
        d2 = Counter(r['spr'] - r['count'] for r in rows)
        print('    $A1DA entries MINUS live actor count:          ' +
              ' '.join(f'{k}:{v}' for k, v in sorted(d2.items())[:8]) +
              (' ...' if len(d2) > 8 else ''))
        for name, feat in MODELS:
            A = [feat(r) for r in rows]
            k = solve(A, y)
            res = [yy - sum(a * b for a, b in zip(feat(r), k))
                   for yy, r in zip(y, rows)]
            r2 = 1 - sum(e * e for e in res) / sum((v - ybar) ** 2 for v in y)
            ok = Counter()
            for r in rows:
                w1 = sum(a * b for a, b in zip(feat(r), k))
                t0 = r['t0']
                pred = (math.ceil((t0 + w1) / FRAME_T) * FRAME_T - t0
                        + statistics.mean(w2))
                ok[round(pred / FRAME_T) - round(r['dt'] / FRAME_T)] += 1
            print(f'    W1 ~ {name:24s} R2 {r2:.4f}  '
                  f'residual sd {statistics.pstdev(res)/FRAME_T:.4f}f  '
                  f'quantiser gate {100.0*ok[0]/len(rows):5.1f}% exact '
                  f'{dict(sorted(ok.items()))}')
            print('        ' + '  '.join(f'{v:+.1f}T' for v in k))
            coeffs[(br, name)] = k
        print(f'    W2 = {statistics.mean(w2):.0f} T '
              f'= {statistics.mean(w2)/FRAME_T:.4f} frames  '
              f'(sd {statistics.pstdev(w2):.0f} T, min {min(w2)} max {max(w2)})')
    print('\n  THE 48K SURCHARGE, read straight off the two fits (the `count`')
    print(f'  coefficient must rise by {DELTA_T} T, one $B8CC prologue per')
    print('  drawn object, and the intercept by 180 * that):')
    for name, _ in MODELS:
        ka, kb = coeffs[('ay', name)], coeffs[('48k', name)]
        print(f'    {name:24s} ' +
              '  '.join(f'{b-a:+8.1f}T' for a, b in zip(ka, kb)))


# --------------------------------------------------------------------------
# consts: every measured constant in the engine that is keyed to this clock
# --------------------------------------------------------------------------
VIDCTR = 0x8497                    # the ISR's VIDEO FRAME counter
DRAINP = 0x849F                    # the drain's phase latch
HURRY = 0x84B8                     # the hurry-up counter, +1 per 64 frames
ANIMF = 0x847E                     # bit 2 = advance the actor walk phase


def cmd_consts(args=()):
    n = int(args[0]) if args else 160
    print('=== EVERY CONSTANT KEYED TO THIS CLOCK, MEASURED ON BOTH BRANCHES')
    print()
    print(f'  1. THE VIDEO-FRAME COUNTER $8497 PER PASS ({n} passes each).')
    print('     This is the number the engine calls FRAMES_PER_PASS and the')
    print('     one the drain, the hurry-up timer, the logo cycle and the')
    print('     low-health flash are all divided out of.')
    for scene, kw in (('idle', dict()), ('down', dict(direction='down')),
                      ('generator cluster', dict(warp=(88, 108)))):
        for br in ('ay', '48k'):
            h = branch(br, **kw)
            deltas, drains, hurries, anims = [], 0, 0, 0
            prev = None
            ph = h.memobj.m[DRAINP]
            hy = h.memobj.m[HURRY]
            for _ in range(n):
                rows = passes(h, 1, skip=0)
                m = h.memobj.m
                v = m[VIDCTR]
                if prev is not None:
                    deltas.append((v - prev) & 0xFF)
                prev = v
                if m[DRAINP] != ph:
                    drains += 1
                    ph = m[DRAINP]
                if m[HURRY] != hy:
                    hurries += 1
                    hy = m[HURRY]
                anims += bool(m[ANIMF] & 0x04)
            c = Counter(deltas)
            print(f'     {scene:18s} {br:4s} $8497 +/pass '
                  f'{dict(sorted(c.items()))}  mean '
                  f'{statistics.mean(deltas):.3f}')
            tf = sum(deltas)
            print(f'     {"":18s}      {tf} video frames in {n} passes; drain '
                  f'ticks {drains} = 1 per {tf/max(drains,1):.1f} FRAMES '
                  f'= 1 per {n/max(drains,1):.1f} PASSES; hurry-up ticks '
                  f'{hurries}; walk-phase gate set on {anims}/{n}')

    print('\n  2. THE BLOCKING PAUSE.  $9D0A reads ($FFFD) AGAIN at runtime and')
    print('     takes a DIFFERENT pause on each branch:')
    print('       AY   $9D22 LD A,$10 / CALL $BA2B, B=$32 -> 50 x HALT/HALT')
    print('       48K  $9D17 JP $B8B0 (message) or $9D14 JP $B8B5 (level start)')
    print('            -- each LDIRs 318 bytes to $C000 AND EXECUTES THEM')
    for br in ('ay', '48k'):
        for bit5, tag in ((0, 'message banner'), (0x20, 'level start')):
            h = branch(br, direction='down')
            passes(h, 1)
            m = h.memobj.m
            m[0x847D] = (m[0x847D] & ~0x20 & 0xFF) | 0x04 | bit5
            rows = passes(h, 3, skip=0)
            costs = ' '.join(f'{r["dt"]/FRAME_T:.1f}' for r in rows)
            print(f'     {br:4s} {tag:14s} pass costs {costs} video frames')

    print('\n  3. WHAT A PASS COSTS IN REAL TIME, and therefore every rate the')
    print('     engine expresses per second:')
    for scene, kw in (('idle', dict()), ('down', dict(direction='down')),
                      ('generator cluster', dict(warp=(88, 108)))):
        line = []
        for br in ('ay', '48k'):
            rows = passes(branch(br, **kw), n)
            mu = statistics.mean(r['dt'] for r in rows) / FRAME_T
            line.append(f'{br}: {mu:.3f}f = {mu/FRAME_HZ*1000:.1f} ms '
                        f'= {FRAME_HZ/mu:.2f} Hz')
        print(f'     {scene:18s} ' + '   '.join(line))


CMDS = {'sites': cmd_sites, 'hist': cmd_hist, 'model': cmd_model,
        'load': cmd_load, 'natural': cmd_natural, 'fit': cmd_fit,
        'consts': cmd_consts}


def main():
    args = sys.argv[1:]
    if not args or args[0] == 'all':
        for name in ('sites', 'hist', 'model', 'load', 'natural', 'fit',
                     'consts'):
            CMDS[name](())
            print()
        return
    CMDS[args[0]](args[1:])


if __name__ == '__main__':
    main()
