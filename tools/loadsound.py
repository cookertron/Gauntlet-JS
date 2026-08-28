#!/usr/bin/env python3
"""
loadsound.py -- IS WHAT YOU HEAR UNDER LOAD THE GAME, OR THE PORT?

    python tools/loadsound.py tone    the ORIGINAL's chirp train, quiet vs at
                                      a generator cluster: pitch and cadence
    python tools/loadsound.py rasp    what the ORIGINAL puts on the speaker
                                      when left to play itself, by scene
    python tools/loadsound.py ticks   the blitter's own $B8CC issue times --
                                      the window a noise burst lives in
    python tools/loadsound.py noise   that window against the PORT's curve,
                                      and against the pre-slowdown flat clock
    python tools/loadsound.py all

Written for a PLAY REPORT ("the sound now has slight noise to it").  It
answers the prior question the report cannot: what does the REAL MACHINE do
to its own beeper when the screen gets busy?  Manual phase 16 -- a play
report is a bug report, never a specification, and the instrument gets
pointed at the ORIGINAL first.  The port's half is `node tools/loadsound.js`.

=============================================================================
THE ANSWER, IN ONE BLOCK
=============================================================================
THE TONE CANNOT CHANGE PITCH UNDER LOAD, AND DOES NOT.  $B8FB's half period
is 17*(E or 256)+31 T-states of DEC HL, run with interrupts disabled under
$9CD8's DI, so it is immune to everything else in the pass.  What load moves
is the CADENCE: one chirp is one MAIN-LOOP PASS, so a pass that costs 6.875
video frames instead of 4.125 puts the next chirp 75% later.  Measured, id 7
(a key, a six-row warble) in two scenes:

    quiet dungeon 1, idle    pass 4.125 f   chirps 79.8 ms apart, span 402 ms
    generator cluster, down  pass 6.875 f   chirps 139.8 ms apart, span 702 ms
    PITCH 2873.6 / 1588.0 Hz in BOTH -- worst change over six chirps 0.000%

so a busy screen makes an effect SLOWER AND LONGER and leaves its pitch
alone.  Anything the port does to the pitch is therefore its own.

THE NOISE IS THE PART THAT CAN CHANGE TIMBRE, and on the original it changes
by getting SPARSER, not denser.  $B8CC is called once per DRAWN OBJECT from
six sites inside the blitter, so under load the window a 127-call ramp lives
in WIDENS: 1.20 video frames quiet, 3.40 at a generator cluster, 10.58 with
190 planted actors, i.e. mean spacing 338 -> 1,014 -> 1,988 T.  The port's
tick curve is indexed by the fraction j/(n-1) of the pass's own call count
and so keeps a FIXED window -- see `noise` for what that costs and where.

AND THE ORIGINAL ITSELF RASPS AT A CLUSTER.  Left to play itself for 40
passes it triggers id 0 -- $AEFC, a ghost touching the player, which IS the
noise ramp -- 28 times at a cluster against once in a quiet dungeon, and puts
253 noise edges a second on the speaker against 19.  That rasp is the game.
"""
import collections
import os
import pickle
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, A as REG_A, TAPE_CALL_PC,    # noqa
                     FRAME_T, CPU_HZ)
from keyprobe import KEYS, keymask                                     # noqa
import passclock as PCK                                                # noqa

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
LOOP_TOP = 0x8503
B8CC = 0xB8CC

# the instructions in the whole image that write bit 4 of port $FE, and what
# each one is -- tools/beepgate.py's OUT_SITE, confirmed at instruction
# boundaries by disassembling build/state_48k.pkl's own memory
OUT_SITE = {0xB8DB: 'noise', 0xB91E: 'tone', 0xB4FC: 'border',
            0xC089: 'tune', 0xC095: 'tune', 0xC0AB: 'tune'}
BA2B = 0xBA2B                # patched to JP $B92B on the 48K branch


# ==========================================================================
# 1.  THE TONE -- pitch and cadence, quiet vs at a generator cluster
# ==========================================================================
# the two scenes clockgate.py names, and their measured rates
tone_SCENES = [
    ('quiet dungeon 1, idle', dict(direction='idle')),
    ('GENERATOR CLUSTER, walking down',
     dict(direction='down', warp=(96, 56, 66, 38))),
]


def tone_fresh(direction=None, warp=None, nogen=False):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    assert m[0xFFFD] == 0x00, 'not the 48K/beeper branch'
    if nogen:
        for a in range(0x8000, 0x8400):
            if 0x20 <= m[a] <= 0x2E:
                m[a] = 0
    if warp:
        x, y, cx, cy = warp
        m[0x8420], m[0x8421] = x, y
        m[0x848B], m[0x848C] = cx, cy
    if direction and direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    return h


def tone_run_pass(h, record=False):
    """One main-loop pass.  Returns (T cost, [(T, level, src)]) -- the SPEAKER
    edges only, attributed by the PC of the instruction that wrote them."""
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0 = regs[T]
    srcs = []
    if record:
        h.ports.writes = []
        h.ports.record_writes = True
    n = 0
    first = True
    while n < 60_000_000:
        pc = regs[PC]
        if pc == LOOP_TOP and not first:
            break
        first = False
        if record and pc in OUT_SITE:
            srcs.append(OUT_SITE[pc])
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    dt = regs[T] - t0
    out = []
    if record:
        h.ports.record_writes = False
        w = [(t, v) for t, p, v in h.ports.writes if (p & 0xFF) == 0xFE]
        assert len(w) == len(srcs), 'writer attribution lost a write'
        out = [(t, (v >> 4) & 1, s) for (t, v), s in zip(w, srcs)]
    return dt, out


def tone_drive(h, npass, arm=None, arm_at=4):
    """Run npass passes; call arm(h) at the TOP of pass arm_at.  Returns
    (per-pass T costs, [(absolute T, level, src)] speaker EDGES)."""
    costs, edges = [], []
    lvl = 0
    for i in range(npass):
        if arm is not None and i == arm_at:
            arm(h)
        dt, w = tone_run_pass(h, record=True)
        for t, b, s in w:
            if b == lvl:
                continue                 # only CHANGES are edges
            lvl = b
            edges.append((t, b, s))
        costs.append(dt)
    return costs, edges


# ---------------------------------------------------------------------------
def tone_arm_tone(idv):
    """$B98B LD A,(HL) / LD (IY+$50),A / INC HL / LD ($84D0),HL -- exactly what
    the dispatcher writes for a tone id."""
    import json
    D = json.load(open(os.path.join(ROOT, 'build', 'beeper_data.json')))
    addr = D['dispatch'][str(idv)]

    def go(h):
        m = h.memobj.m
        m[0x84CF] = m[addr]
        m[0x84D0], m[0x84D1] = (addr + 1) & 0xFF, (addr + 1) >> 8
    return go


def tone_arm_noise_down(h):
    """$B979 CP 4 / LD A,$7F / JP $B8F2 -- (IY+$53)=$7F and $B8E2 := DEC A."""
    m = h.memobj.m
    m[0x84D2] = 0x7F
    m[0xB8E2] = 0x3D


def tone_chirps(edges):
    """Group TONE edges into CHIRPS: a new chirp starts wherever the gap to
    the previous tone edge is more than 4x the local half period."""
    te = [(t, b) for t, b, s in edges if s == 'tone']
    out, cur = [], []
    for i, (t, b) in enumerate(te):
        if cur and t - cur[-1] > 8000:      # >0.11 frames: a new pass
            out.append(cur); cur = []
        cur.append(t)
    if cur:
        out.append(cur)
    return out


def tone_report_tone(idv=7, npass=16):
    print('=' * 74)
    print('THE ORIGINAL, id %d (a KEY) -- a %d-row TONE effect, one row a PASS'
          % (idv, 6))
    print('=' * 74)
    rows = []
    for tag, kw in tone_SCENES:
        h = tone_fresh(**kw)
        tone_run_pass(h)                              # align
        costs, edges = tone_drive(h, npass, arm=tone_arm_tone(idv), arm_at=3)
        ch = tone_chirps(edges)
        # each chirp: start T, half period (median dT), edge count
        info = []
        for c in ch:
            d = [c[i + 1] - c[i] for i in range(len(c) - 1)]
            hp = statistics.median(d) if d else 0
            info.append((c[0], hp, len(c), c[-1] - c[0]))
        gaps = [info[i + 1][0] - info[i][0] for i in range(len(info) - 1)]
        mean_pass = statistics.mean(costs) / FRAME_T
        print()
        print('  %s' % tag)
        print('    pass cost  %.3f video frames = %.2f Hz  %s'
              % (mean_pass, 50.08 / mean_pass,
                 dict(collections.Counter(round(c / FRAME_T) for c in costs))))
        print('    %-6s %-12s %-9s %-7s %-10s %s'
              % ('chirp', 'start (ms)', 'half T', 'edges', 'pitch Hz',
                 'gap to next (ms)'))
        t00 = info[0][0] if info else 0
        for i, (t, hp, n, span) in enumerate(info):
            g = ('%.1f' % (gaps[i] * 1000.0 / CPU_HZ)) if i < len(gaps) else '-'
            print('    %-6d %-12.2f %-9d %-7d %-10.1f %s'
                  % (i + 1, (t - t00) * 1000.0 / CPU_HZ, hp, n,
                     CPU_HZ / (2.0 * hp) if hp else 0, g))
        span = (info[-1][0] + info[-1][3] - info[0][0]) if info else 0
        rows.append(dict(tag=tag, frames=mean_pass, info=info,
                         gaps=gaps, span=span * 1000.0 / CPU_HZ,
                         pitches=[CPU_HZ / (2.0 * hp) for _, hp, _, _ in info
                                  if hp]))
        print('    EFFECT SPAN (first edge to last) = %.1f ms'
              % (span * 1000.0 / CPU_HZ))
    a, b = rows
    print()
    print('  ---- WHAT LOAD DOES TO THE ORIGINAL -------------------------')
    print('    pass cost      %.3f -> %.3f frames   %+.1f%%'
          % (a['frames'], b['frames'],
             100.0 * (b['frames'] / a['frames'] - 1)))
    pa = [p for p in a['pitches']]
    pb = [p for p in b['pitches']]
    k = min(len(pa), len(pb))
    worst = max(abs(pb[i] / pa[i] - 1) for i in range(k)) * 100
    print('    CHIRP PITCH    %s' % ' '.join('%.0f' % p for p in pa[:6]))
    print('                   %s' % ' '.join('%.0f' % p for p in pb[:6]))
    print('                   worst pitch change over %d chirps: %.3f%%'
          % (k, worst))
    print('    CHIRP CADENCE  %.2f -> %.2f ms between chirps   %+.1f%%'
          % (statistics.mean(a['gaps']) * 1000.0 / CPU_HZ,
             statistics.mean(b['gaps']) * 1000.0 / CPU_HZ,
             100.0 * (statistics.mean(b['gaps']) /
                      statistics.mean(a['gaps']) - 1)))
    print('    EFFECT SPAN    %.1f -> %.1f ms                   %+.1f%%'
          % (a['span'], b['span'], 100.0 * (b['span'] / a['span'] - 1)))
    return rows


def tone_report_noise(npass=10):
    print()
    print('=' * 74)
    print('THE ORIGINAL, id 4 (FIRE) -- the 127-call NOISE ramp')
    print('=' * 74)
    rows = []
    for tag, kw in tone_SCENES:
        h = tone_fresh(**kw)
        tone_run_pass(h)
        costs, edges = tone_drive(h, npass, arm=tone_arm_noise_down, arm_at=3)
        ne = [(t, b) for t, b, s in edges if s == 'noise']
        # split into BURSTS: the game re-arms id 0 on its own at a cluster
        # (a ghost touching the player), so only the FIRST burst is the one
        # this tool armed.  30,000 T = 0.43 video frames, far above the
        # 1,903 T worst in-burst gap measured in the quiet scene.
        bursts, cur = [], []
        for t, b in ne:
            if cur and t - cur[-1] > 30_000:
                bursts.append(cur); cur = []
            cur.append(t)
        if cur:
            bursts.append(cur)
        first = bursts[0] if bursts else []
        mean_pass = statistics.mean(costs) / FRAME_T
        span = (first[-1] - first[0]) if len(first) > 1 else 0
        print()
        print('  %s' % tag)
        print('    pass cost  %.3f video frames = %.2f Hz'
              % (mean_pass, 50.08 / mean_pass))
        print('    bursts seen %d (the rest are the GAME\'s own id 0 triggers)'
              % len(bursts))
        print('    ARMED burst: %d edges   span %.1f ms (%.3f video frames)'
              % (len(first), span * 1000.0 / CPU_HZ, span / FRAME_T))
        if len(first) > 2:
            d = [first[i + 1] - first[i] for i in range(len(first) - 1)]
            print('    toggle gaps: min %d  median %d  max %d T'
                  % (min(d), int(statistics.median(d)), max(d)))
        rows.append(dict(tag=tag, n=len(first), span=span * 1000.0 / CPU_HZ,
                         frames=mean_pass))
    a, b = rows
    print()
    print('  ---- WHAT LOAD DOES TO THE ORIGINAL -------------------------')
    print('    noise burst         %.1f -> %.1f ms   %+.1f%%   (edges %d -> %d)'
          % (a['span'], b['span'], 100.0 * (b['span'] / a['span'] - 1),
             a['n'], b['n']))
    return rows

# ==========================================================================
# 2.  THE RASP -- what the original puts on the speaker unaided
# ==========================================================================
def rasp_fresh(direction=None, warp=None):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    assert m[0xFFFD] == 0x00
    if warp:
        x, y, cx, cy = warp
        m[0x8420], m[0x8421] = x, y
        m[0x848B], m[0x848C] = cx, cy
    if direction and direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    return h


def rasp_run(h, npass):
    from harness import A as REG_A
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    h.ports.record_writes = True
    edges = collections.Counter()
    trig = collections.Counter()
    t_start = regs[T]
    lvl = 0
    for _ in range(npass):
        h.ports.writes = []
        srcs = []
        n = 0
        first = True
        while n < 60_000_000:
            pc = regs[PC]
            if pc == LOOP_TOP and not first:
                break
            first = False
            if pc in OUT_SITE:
                srcs.append(OUT_SITE[pc])
            if pc == BA2B:
                trig[regs[REG_A]] += 1
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape(); n += 1; continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt(); n += 1; continue
            ops[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
            n += 1
        w = [(t, v) for t, p, v in h.ports.writes if (p & 0xFF) == 0xFE]
        assert len(w) == len(srcs)
        for (t, v), s in zip(w, srcs):
            b = (v >> 4) & 1
            if b == lvl:
                continue
            lvl = b
            edges[s] += 1
    h.ports.record_writes = False
    return edges, trig, (regs[T] - t_start) / CPU_HZ


def rasp_main(npass=40):
    rasp_SCENES = [
        ('quiet dungeon 1, idle', dict()),
        ('quiet dungeon 1, walking down', dict(direction='down')),
        ('GENERATOR CLUSTER, idle', dict(warp=(96, 56, 66, 38))),
        ('GENERATOR CLUSTER, walking down',
         dict(direction='down', warp=(96, 56, 66, 38))),
    ]
    print('=' * 78)
    print('THE ORIGINAL LEFT TO PLAY ITSELF -- WHAT REACHES THE SPEAKER')
    print('(%d passes a scene, nothing armed by hand, every edge attributed'
          ' to its WRITER)' % npass)
    print('=' * 78)
    print()
    print('  %-33s %8s %8s %8s %9s' %
          ('scene', 'seconds', 'noise', 'tone', 'noise/s'))
    rows = []
    for tag, kw in rasp_SCENES:
        h = rasp_fresh(**kw)
        rasp_run(h, 3)
        e, t, secs = rasp_run(h, npass)
        print('  %-33s %8.2f %8d %8d %9.0f' %
              (tag, secs, e['noise'], e['tone'], e['noise'] / secs))
        rows.append((tag, secs, e, t))
    print()
    print('  WHAT THE GAME TRIGGERED ITSELF ($BA2B, i.e. $B92B, with A =):')
    for tag, secs, e, t in rows:
        print('    %-33s %s' % (tag, dict(sorted(t.items())) or '{}'))
    print()
    # baseline is quiet-WALKING, not quiet-idle: idle fires nothing at all
    # and a ratio against zero is not a number
    a, b = rows[1], rows[3]
    print('  ---- QUIET -> CLUSTER, ON THE ORIGINAL -----------------------')
    print('    NOISE edges per second   %.0f -> %.0f   x%.1f'
          % (a[2]['noise'] / a[1], b[2]['noise'] / b[1],
             (b[2]['noise'] / b[1]) / max(1e-9, a[2]['noise'] / a[1])))
    print('    id-0 triggers in %d passes  %d -> %d'
          % (40, a[3].get(0, 0), b[3].get(0, 0)))
    print('    (id 0 is $AEFC, a ghost touching the player, and on the 48K')
    print('     branch it IS the noise ramp -- $B982 OR A / LD A,$01 /')
    print('     JP $B8E9.  At a cluster the original fires it on most')
    print('     passes and the speaker rasps continuously.  THAT RASP IS')
    print('     THE GAME, and it is the loudest thing a busy screen adds.)')

# ==========================================================================
# 3.  THE BLITTER'S $B8CC ISSUE TIMES -- a noise burst's own window
# ==========================================================================
tick_SCENES = [
    ('quiet dungeon 1, idle', dict()),
    ('quiet dungeon 1, walking down', dict(direction='down')),
    ('GENERATOR CLUSTER, idle', dict(warp=(96, 56, 66, 38))),
    ('GENERATOR CLUSTER, walking down',
     dict(direction='down', warp=(96, 56, 66, 38))),
]


def tick_fresh(direction=None, warp=None):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    assert m[0xFFFD] == 0x00
    if warp:
        x, y, cx, cy = warp
        m[0x8420], m[0x8421] = x, y
        m[0x848B], m[0x848C] = cx, cy
    if direction and direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    return h


def tick_one_pass(h):
    """Return (T cost, [T of every $B8CC call, relative to the loop top])."""
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0 = regs[T]
    calls = []
    n = 0
    first = True
    while n < 60_000_000:
        pc = regs[PC]
        if pc == LOOP_TOP and not first:
            break
        first = False
        if pc == B8CC:
            calls.append(regs[T] - t0)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return regs[T] - t0, calls


def tick_main(npass=25):
    print('=' * 78)
    print('THE ORIGINAL BLITTER\'S $B8CC ISSUE TIMES -- one call per DRAWN')
    print('OBJECT, and the whole of a noise tick_burst\'s duration')
    print('=' * 78)
    print()
    print('  %-32s %6s %7s %7s %7s %8s' %
          ('scene', 'calls', 'first f', 'last f', 'span f', 'gap T'))
    out = []
    for tag, kw in tick_SCENES:
        h = tick_fresh(**kw)
        tick_one_pass(h)
        tick_one_pass(h)
        ncalls, firsts, lasts, spans, gaps, costs = [], [], [], [], [], []
        q50 = []
        for _ in range(npass):
            dt, c = tick_one_pass(h)
            if len(c) < 10:
                continue
            costs.append(dt / FRAME_T)
            ncalls.append(len(c))
            firsts.append(c[0] / FRAME_T)
            lasts.append(c[-1] / FRAME_T)
            spans.append((c[-1] - c[0]) / FRAME_T)
            d = [c[i + 1] - c[i] for i in range(len(c) - 1)]
            gaps.append(statistics.median(d))
            # where the 127th call lands -- exactly the ramp's own length
            q50.append((c[min(126, len(c) - 1)] - c[0]) / FRAME_T)
        m = statistics.mean
        print('  %-32s %6.0f %7.3f %7.3f %7.3f %8.0f' %
              (tag, m(ncalls), m(firsts), m(lasts), m(spans), m(gaps)))
        out.append(dict(tag=tag, calls=m(ncalls), span=m(spans),
                        gap=m(gaps), burst127=m(q50), cost=m(costs)))
    print()
    print('  A 127-CALL tick_RAMP (the whole of a noise tick_burst) armed at the top of')
    print('  the pass, measured as the offset of the 127th call from the first:')
    print()
    print('  %-32s %8s %10s %10s %10s' %
          ('scene', 'objects', 'tick_burst f', 'tick_burst ms', 'pass f'))
    for r in out:
        print('  %-32s %8.0f %10.3f %10.1f %10.3f' %
              (r['tag'], r['calls'], r['burst127'],
               r['burst127'] * FRAME_T * 1000.0 / CPU_HZ, r['cost']))
    a, b = out[0], out[3]
    print()
    print('  ---- QUIET -> CLUSTER, ON THE ORIGINAL -----------------------')
    print('    drawn objects a pass   %.0f -> %.0f   %+.1f%%'
          % (a['calls'], b['calls'], 100 * (b['calls'] / a['calls'] - 1)))
    print('    the blit WINDOW        %.3f -> %.3f frames   %+.1f%%'
          % (a['span'], b['span'], 100 * (b['span'] / a['span'] - 1)))
    print('    median call spacing    %.0f -> %.0f T   %+.1f%%'
          % (a['gap'], b['gap'], 100 * (b['gap'] / a['gap'] - 1)))
    print('    a 127-call noise tick_burst %.1f -> %.1f ms   %+.1f%%'
          % (a['burst127'] * FRAME_T * 1000.0 / CPU_HZ,
             b['burst127'] * FRAME_T * 1000.0 / CPU_HZ,
             100 * (b['burst127'] / a['burst127'] - 1)))
    print()
    print('  THE PORT places tick j of n at beepTickAt(j/(n-1)) -- a curve')
    print('  that runs 0.128..1.325 frames WHATEVER n is.  So its 127-call')
    print('  tick_burst is  curve(126/(n-1)) - curve(0)  and it SHRINKS as n')
    print('  grows, where the original\'s spacing is set by the blitter.')
    return out

# ==========================================================================
# 4.  THE NOISE BURST against the PORT's curve and the flat clock
# ==========================================================================
# where the two arming classes fire, in VIDEO FRAMES from the loop top
# ($8509 the player's own move, $851E the actor update -- beepgate.py `where`)
nz_ARM_EARLY, nz_ARM_LATE = 0.03, 0.86
nz_RAMP = 127


def nz_one_pass(h):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0 = regs[T]
    calls = []
    n = 0
    first = True
    while n < 60_000_000:
        pc = regs[PC]
        if pc == LOOP_TOP and not first:
            break
        first = False
        if pc == B8CC:
            calls.append((regs[T] - t0) / FRAME_T)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return (regs[T] - t0) / FRAME_T, calls


nz_CURVE = None


def nz_port_tick(u):
    """beepTickAt() out of the SHIPPED build/beeper_data.json, walking curve."""
    global nz_CURVE
    if nz_CURVE is None:
        import json
        nz_CURVE = json.load(open(os.path.join(ROOT, 'build',
                                            'beeper_data.json'))
                          )['noise']['tick_curve']['frames']
    c = nz_CURVE
    n = len(c) - 1
    x = min(n, max(0.0, u * n))
    i = min(n - 1, int(x))
    return c[i] + (c[i + 1] - c[i]) * (x - i)


def nz_burst(calls_by_pass, arm):
    """127 ramp calls from `arm` frames into the first pass, spilling into the
    passes after it.  Returns (span in frames, passes used)."""
    got, span, base = 0, 0.0, 0.0
    used = 0
    start = None
    for k, (cost, calls) in enumerate(calls_by_pass):
        ph = arm if k == 0 else 0.0
        for f in calls:
            if f < ph:
                continue
            if start is None:
                start = base + f
            got += 1
            span = base + f - start
            if got >= nz_RAMP:
                return span, used + 1
        base += cost
        used += 1
        if used > 6:
            break
    return span, used


def nz_main(npass=8):
    nz_SCENES = [('quiet dungeon, idle', dict(), None),
              ('quiet dungeon, down', dict(direction='down'), None),
              ('cluster idle', dict(warp=(88, 108)), None),
              ('cluster down', dict(warp=(88, 108), direction='down'), None),
              ('plant 60, down', dict(direction='down'), 60),
              ('plant 100, down', dict(direction='down'), 100),
              ('plant 150, down', dict(direction='down'), 150),
              ('plant 190, down', dict(direction='down'), 190)]
    print('=' * 78)
    print('THE BLITTER\'S $B8CC RATE UNDER DRAW LOAD -- ORIGINAL vs PORT MODEL')
    print('=' * 78)
    print()
    print('  %-22s %6s %7s %8s   %-17s %-26s' %
          ('scene', 'objs', 'pass f', 'window f',
           'nz_burst EARLY (ms)', 'nz_burst LATE (ms)'))
    print('  %-22s %6s %7s %8s   %8s %8s %8s %8s %8s' %
          ('', '', '', '', 'orig', 'port', 'orig', 'port', 'flat4'))
    rows = []
    for tag, kw, nplant in nz_SCENES:
        h = PCK.nz_fresh(**kw)
        if nplant:
            PCK.plant(h, nplant)
        nz_one_pass(h); nz_one_pass(h)
        seq = []
        for _ in range(npass):
            seq.append(nz_one_pass(h))
        seq = [s for s in seq if len(s[1]) > 50]
        objs = statistics.mean(len(c) for _, c in seq)
        cost = statistics.mean(t for t, _ in seq)
        win = statistics.mean(c[-1] - c[0] for _, c in seq)
        oe = statistics.mean(nz_burst(seq[i:], nz_ARM_EARLY)[0]
                             for i in range(len(seq) - 4))
        ol = statistics.mean(nz_burst(seq[i:], nz_ARM_LATE)[0]
                             for i in range(len(seq) - 4))
        # the PORT: the same arm, on its curve, with its own object census.
        # TWO pass clocks -- the SHIPPED variable one (the original's own
        # measured cost) and the FLAT FOUR the engine charged before the
        # slowdown -- because the spill into the next pass is where the
        # clock reaches the noise nz_burst.
        n = int(round(objs))
        curve = [nz_port_tick(j / (n - 1)) for j in range(n)]
        pseq = [(cost, curve) for _ in range(8)]
        fseq = [(4.0, curve) for _ in range(8)]
        pe = nz_burst(pseq, nz_ARM_EARLY)[0]
        pl = nz_burst(pseq, nz_ARM_LATE)[0]
        fe = nz_burst(fseq, nz_ARM_EARLY)[0]
        fl = nz_burst(fseq, nz_ARM_LATE)[0]
        ms = FRAME_T * 1000.0 / CPU_HZ
        print('  %-22s %6.0f %7.3f %8.3f   %8.1f %8.1f %8.1f %8.1f %8.1f' %
              (tag, objs, cost, win, oe * ms, pe * ms, ol * ms, pl * ms,
               fl * ms))
        rows.append((tag, objs, cost, win, oe, pe, ol, pl, fe, fl))
    print()
    print('  A NOISE BURST IS 127 OF THOSE CALLS.  "EARLY" is $8CAD FIRE and')
    print('  $A783, armed 0.03 frames into the pass; "LATE" is $AEFC, a ghost')
    print('  touching the player, armed at 0.86 -- the two classes the')
    print('  catalogue renders separately.')
    print()
    ms = FRAME_T * 1000.0 / CPU_HZ
    print('  %-22s %10s %10s %9s' %
          ('scene', 'orig T/call', 'port T/call', 'port/orig'))
    for tag, objs, cost, win, oe, pe, ol, pl, fe, fl in rows:
        ot = win * FRAME_T / max(1, objs - 1)
        pt = (nz_port_tick(1.0) - nz_port_tick(0.0)) * FRAME_T / max(1, objs - 1)
        print('  %-22s %10.0f %10.0f %9.2f' % (tag, ot, pt, pt / ot))
    print()
    print('  T-states between consecutive blitter calls is what sets the')
    print('  NOISE TOGGLE RATE, and the toggle rate is the timbre.')
    print('  port/orig > 1 means the port\'s noise is SPARSER (duller) than')
    print('  the original\'s; < 1 means DENSER (hissier).')


CMDS = {'tone': lambda: (tone_report_tone(), tone_report_noise()),
        'rasp': rasp_main,
        'ticks': tick_main,
        'noise': nz_main}
ORDER = ['tone', 'rasp', 'ticks', 'noise']

if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if what != 'all' and what not in CMDS:
        sys.exit('usage: loadsound.py [%s|all]' % '|'.join(ORDER))
    for k in (ORDER if what == 'all' else [what]):
        CMDS[k]()
        print()
