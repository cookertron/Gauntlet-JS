#!/usr/bin/env python3
"""
beepgate.py -- THE PHASE-11 GATE FOR THE SHIPPED BRANCH: an EDGE-LEVEL
BEEPER DIFFERENTIAL, plus the measurements the engine's constants come from.

    python tools/beepgate.py fit       the tone model, fitted to recorded edges
    python tools/beepgate.py ids       $B92B's own id map, parsed from the bytes
    python tools/beepgate.py noise     the noise predicate, ENUMERATED over R
    python tools/beepgate.py where     where in a pass the two ticks run
    python tools/beepgate.py burst     how long a noise burst lasts, and why
    python tools/beepgate.py clock     frames per pass, both branches
    python tools/beepgate.py pause     the blocking pause, both branches
    python tools/beepgate.py table <dir> <n>    the ORIGINAL's edge table
    python tools/beepgate.py diff      THE DIFFERENTIAL
    python tools/beepgate.py all       everything, with a total

=============================================================================
WHAT IS COMPARED, AND WHY IT IS NOT THE SAME TEST THE AY GATE RUNS
=============================================================================
The AY driver is a register dump, so tools/soundgate.py asks the manual's own
question -- "the same registers with the same values on the same frames?".
THE BEEPER HAS NO REGISTERS.  Its entire output is ONE BIT of port $FE, so
the equivalent question is

    "does your player put the same edges on the speaker, at the same times?"

and that is what this file asks.  Both sides print ONE ROW PER SPEAKER EDGE:

    pass   source   level   offset-into-the-pass (video frames)   dT

`source` is which of the three mechanisms wrote it, taken on the Z80 side
from the WRITER'S PC and on the engine side from the driver's own tag:

    tone    $B91E   one (toggles, half-period) step per MAIN-LOOP PASS
    noise   $B8DB   one sample per DRAWN OBJECT, six sites in the blitter
    border  $B4FC   the ISR's once-a-frame write, which clears bit 4
    tune    $C089 / $C095 / $C0AB   the two blocking two-voice tunes

`dT` is the T-states since the previous edge OF THE SAME SOURCE in the same
pass.  For a tone chirp that is the half-period, i.e. THE PITCH, and it is
the quantity the model 17*E + 31 predicts -- so the differential asserts the
EDGE COUNT and the PERIODS, which an edge count alone would not catch and a
period alone would not either.

THREE COMPARISON RULES, and they are not the same for the three sources:

  * TONE   compared EXACTLY: the count per pass, the level of every edge,
           every dT, and the offset of the first edge to within 0.06 of a
           video frame (the measured phase jitter is 0.178..0.205).
  * NOISE  compared by COUNT PER BURST and by the burst's frame span, never
           edge for edge.  $B8CC is `LD A,R / CP (IY+$53)` -- the Z80
           REFRESH REGISTER -- so which samples toggle is Q18's class and no
           port can reproduce it.  The engine uses a DECLARED SUBSTITUTE
           stream, exactly as the AY driver's id-0 coin did, and what is
           gated is the RULE: 127 ramp calls, density level/128, the ramp
           rising (id 0) or falling (id 4).
  * TUNE   compared as a BLOCK: which tune, when it started, how long it
           blocked and how many edges it made.  The edge train itself is an
           18 kHz interleave carrier and no useful row-for-row comparison
           exists (manual 11.3(c)); the two OUT counts, 79,872 and 292,864,
           ARE compared and they are exact.

THE ONE SUBSTITUTION, DECLARED.  The engine charges a flat four video frames
a pass; on this branch the original's cost FOUR OR FIVE (measured: idle
4.145 mean {4:171, 5:29}, holding UP 5.000 {5:200} over 200 passes).  Left
alone the two pass grids walk apart and every chirp after the first
five-frame pass lands a whole frame out.  `diff` therefore hands the engine
the ORIGINAL'S OWN per-pass frame cost through --ticks, exactly as
tools/soundgate.py hands it the original's $BADB tick count and
tools/p2gate.py the original's $8497.  What is under test is the DRIVER.
The unhelped number is printed too, as "flat clock", so the size of the
approximation is visible rather than hidden.

=============================================================================
THE BRANCH
=============================================================================
Everything here runs from build/state_48k.pkl, in which block A's own $7FFD
paging probe ran, wrote 0 to RAM $FFFD and $BEB9 CALL $BF21 applied the ten
bytes of the 48K arm.  No branch byte is poked.  The control, where one is
needed, is build/state_48k_ay.pkl: the identical boot script with the probe
SKIPPED, so $FFFD keeps the loader stub's $2A and $BF21 takes its 128K arm.
"""
import collections
import json
import os
import pickle
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, R, D, H, A as rA,        # noqa
                     FRAME_T, CPU_HZ, TAPE_CALL_PC)
from keyprobe import KEYS, keymask                                     # noqa

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
AYSTATE = os.path.join(ROOT, 'build', 'state_48k_ay.pkl')
LOOP_TOP = 0x8503
P1 = 0x8420

# the FOUR instructions in the whole image that write bit 4 of port $FE, and
# what each one is.  Confirmed at instruction boundaries by disassembling
# build/state_48k.pkl's own memory, not by a byte scan.
OUT_SITE = {0x8DB + 0xB000: 'noise',      # $B8DB  the noise toggle
            0x91E + 0xB000: 'tone',       # $B91E  the chirp
            0x4FC + 0xB000: 'border',     # $B4FC  the ISR's border write
            0xC089: 'tune', 0xC095: 'tune', 0xC0AB: 'tune'}

STAGE = 0x5B00          # two scratch bytes for the isolated tone rig
ROW = re.compile(r'^\s*(\d+)\s+(\w+)\s+(\d+)\s+(-?[\d.]+)\s+(-?\d+)\s*$')


def fresh(path=STATE, quiet=False, nogen=False):
    h = Harness()
    h.load_state(pickle.load(open(path, 'rb')))
    m = h.memobj.m
    assert m[0xFFFD] == (0x00 if path == STATE else 0x2A)
    if quiet:
        m[0x8496] = 0
        m[0x8494], m[0x8495] = 0x00, 0x5C
    if nogen:
        for a in range(0x8000, 0x8400):
            if 0x20 <= m[a] <= 0x2E:
                m[a] = 0
    return h


def step_pass(h, limit=40_000_000, hooks=None):
    """Run ONE main-loop pass and return (T cost, [hook events]).

    Anchored on $8503, which is visited exactly once per pass -- the sampler
    lesson tools/sim_move.py's docstring records at length.
    """
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0 = regs[T]
    ev = []
    n = 0
    while n < limit:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return regs[T] - t0, ev
        if hooks is not None and pc in hooks:
            ev.append((regs[T], pc))
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('no main-loop top in %d instructions' % limit)


def align(h, direction):
    if direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    step_pass(h)


# ===========================================================================
# 1.  THE TONE MODEL, fitted to RECORDED EDGES (manual E3)
# ===========================================================================
def tone_call(h, c, e):
    """Stage one (count, delay) pair, call $B8FB, return the recorded edges."""
    h.poke(STAGE, c, e)
    h.poke(0x84CF, 1)                       # (IY+$50) one step remaining
    h.poke(0x84D0, STAGE & 0xFF, STAGE >> 8)
    h.poke(0x84CA, 0x00)                    # the shadow: border 0, speaker LOW
    h.poke(0x84D2, 0)                       # noise off
    h.ports.writes = []
    h.ports.record_writes = True
    _idx, dt, _st = h.call(0xB8FB)
    h.ports.record_writes = False
    w = [(t, v) for t, p, v in h.ports.writes if (p & 0xFF) == 0xFE]
    edges, lvl = [], 0
    for t, v in w:
        b = (v >> 4) & 1
        if b != lvl:
            edges.append((t, b))
            lvl = b
    return dt, w, edges


def half(e):
    return 17 * (e or 256) + 31


def cmd_fit():
    """THE MODEL, and it is fitted to the recorded edge times, never counted
    by hand.  Sweeps both fields over their whole 0..255 range and then
    checks every one of the 50 records the game ships."""
    h = fresh()
    data = json.load(open(os.path.join(ROOT, 'build', 'beeper_data.json')))
    print('THE TONE MODEL, $B8FB in isolation, measured at the port boundary')
    bad = 0
    n = 0
    for e in range(256):
        dt, w, edges = tone_call(h, 4, e)
        gaps = sorted({edges[i + 1][0] - edges[i][0]
                       for i in range(len(edges) - 1)})
        n += 1
        if not (len(edges) == 4 and gaps == [half(e)]
                and dt == 152 + 4 * half(e)):
            bad += 1
            print('  E=%d edges=%d gaps=%s dt=%d' % (e, len(edges), gaps, dt))
    print('  E sweep 0..255 at C=4     : %d of %d mismatching' % (bad, n))
    b2 = 0
    for c in range(256):
        dt, w, edges = tone_call(h, c, 0x17)
        nn = c or 256
        if not (len(edges) == nn and dt == 152 + nn * half(0x17)):
            b2 += 1
    print('  C sweep 0..255 at E=$17   : %d of 256 mismatching' % b2)
    print('  half-period H(E) = 17*(E or 256) + 31 T   cost = 152 + N*H')
    rows = [tuple(r) for st in data['streams'].values() for r in st]
    b3 = 0
    for c, e in rows:
        dt, w, edges = tone_call(h, c, e)
        gaps = sorted({edges[i + 1][0] - edges[i][0]
                       for i in range(len(edges) - 1)})
        alt = all(edges[i][1] == (i % 2 == 0) for i in range(len(edges)))
        border = all((v & 0x0F) == 0 for _t, v in w)
        if not (len(edges) == c and gaps == [half(e)]
                and dt == 152 + c * half(e) and alt and border):
            b3 += 1
    print('  the 59 shipped steps      : %d of %d mismatching '
          '(edge count, half-period SET, total cost, first edge a RISE, '
          'border bits preserved)' % (b3, len(rows)))
    print('  the two 256-wraps and the extremes:')
    for c, e in [(1, 1), (1, 0), (0, 1), (0, 0), (255, 255)]:
        dt, w, edges = tone_call(h, c, e)
        print('    (C=%3d,E=%3d)  %4d edges  %8d T   model %d / %d'
              % (c, e, len(edges), dt, c or 256, 152 + (c or 256) * half(e)))
    h.poke(0x84CF, 0)
    h.ports.writes = []
    _i, idle, _s = h.call(0xB8FB)
    print('  the IDLE path ($B901, DEC HL from $01EB): %d T = %.4f frames, '
          'charged EVERY pass on BOTH branches' % (idle, idle / FRAME_T))
    assert idle == 12811
    return bad + b2 + b3, n + 256 + len(rows)


# ===========================================================================
# 2.  THE DISPATCHER
# ===========================================================================
def cmd_ids():
    data = json.load(open(os.path.join(ROOT, 'build', 'beeper_data.json')))
    print('$B92B, the dispatcher $BA2B becomes -- parsed from the CP chain by '
          'tools/beepdata.py')
    for k, v in sorted(data['dispatch'].items(), key=lambda kv: int(kv[0])):
        st = data['streams'][k]
        hz = ' '.join('%.0f' % (CPU_HZ / (2 * half(e))) for _c, e in st)
        print('  id %2d  $%04X  %2d steps  %s Hz' % (int(k), v, len(st), hz))
    print('  id  0  arms the NOISE UP from %d, id 4 DOWN from %d, 127 calls'
          % (data['noise']['up_level'], data['noise']['down_level']))
    print('  SILENT (a bare RET at $B98A): %s'
          % ' '.join('$%02X' % i for i in data['silent']))
    bad = 0
    if sorted(data['silent']) != [1, 3, 5, 9, 12, 13, 16]:
        bad += 1
    if len(data['streams']) != 9:
        bad += 1
    if sum(len(r) for r in data['streams'].values()) != 59:
        bad += 1
    print('  9 ids reach a stream, 8 distinct streams tiling $B995..$BA00, '
          '50 records, 59 played steps -> %d assertion(s) failed' % bad)
    return bad, 3


# ===========================================================================
# 3.  THE NOISE, ENUMERATED
# ===========================================================================
def cmd_noise():
    print('$B8CC, the noise sample.  `LD A,R / CP (IY+$53)`, and (IY+$53) IS')
    print('$84D2 -- the threshold and the ramp counter are ONE BYTE.')
    h = fresh()
    bad = 0
    n = 0
    print('  level   OUTs/256   model L/256   costs (T)')
    for lvl in (0, 1, 32, 64, 96, 127):
        outs = 0
        costs = collections.Counter()
        for r in range(256):
            h.poke(0x84D2, lvl)
            h.poke(0x84CA, 0x00)
            h.sim.registers[R] = r
            h.poke(0xB8E2, 0x3C)
            h.ports.writes = []
            h.ports.record_writes = True
            _i, dt, _s = h.call(0xB8CC)
            h.ports.record_writes = False
            outs += len([1 for _t, p, _v in h.ports.writes
                         if (p & 0xFF) == 0xFE])
            costs[dt] += 1
        n += 1
        if outs != lvl:
            bad += 1
        print('   %4d    %4d       %4d          %s'
              % (lvl, outs, lvl, dict(sorted(costs.items()))))
    print('  So the duty is EXACTLY level/256 over all 256 values of R, i.e.')
    print('  level/128 over the 128 the game can reach: the image contains no')
    print('  `LD R,A` anywhere, so bit 7 of R is 0 for the whole run and the')
    print('  128 values with it set never toggle.')
    print('  128K arm: $B8CC is patched to C9, a 10 T RET.  The 48K pays 58 T')
    print('  MORE PER DRAWN OBJECT in total silence -- that is the whole of')
    print('  the branch\'s slowdown, and it is a RENDERING cost, not audio.')
    for arm, start, name in ((0xB8E9, 0x01, 'id $00, ramp UP  '),
                             (0xB8F2, 0x7F, 'id $04, ramp DOWN')):
        hh = fresh()
        hh.call(arm, {'A': start})
        calls = 0
        seq = []
        while calls < 400:
            hh.poke(0x84CA, 0x00)
            hh.call(0xB8CC)
            calls += 1
            seq.append(hh.memobj.m[0x84D2])
            if hh.memobj.m[0x84D2] == 0:
                break
        n += 1
        if calls != 127:
            bad += 1
        print('  %s: %d calls to silence, levels %d..%d, $B8E2 = $%02X'
              % (name, calls, seq[0], seq[-2], hh.memobj.m[0xB8E2]))
    return bad, n


# ===========================================================================
# 4.  WHERE IN A PASS THE TWO TICKS RUN
# ===========================================================================
MARKS = {0x8503: '$8503 loop top', 0x8506: '$8506 doorPass',
         0x8509: '$8509 the moves $A38A', 0x850C: '$850C camera $B58C',
         0x851E: '$851E actors $AB94', 0x8521: '$8521 draw $A43B',
         0x852E: '$852E hud $B6DA', 0x853D: '$853D generators',
         0x8543: '$8543 banner $891C', 0x8550: '$8550 $9CD7'}


def cmd_where():
    print('WHERE INSIDE A PASS, in VIDEO FRAMES from the loop top $8503')
    for direction in ('idle', 'down'):
        h = fresh()
        align(h, direction)
        acc = collections.defaultdict(list)
        blit = []
        tone = []
        for _ in range(20):
            hooks = set(MARKS) | {0xB8CC, 0xB8FB}
            dt, ev = step_pass(h, hooks=hooks)
            if not ev:
                continue
            t0 = ev[0][0] if ev[0][1] == 0x8503 else None
            base = t0
            first = last = None
            for t, pc in ev:
                if base is None:
                    base = t
                if pc in MARKS:
                    acc[pc].append((t - base) / FRAME_T)
                elif pc == 0xB8CC:
                    if first is None:
                        first = t - base
                    last = t - base
                elif pc == 0xB8FB:
                    tone.append((t - base) / FRAME_T)
            if first is not None:
                blit.append((first / FRAME_T, last / FRAME_T))
        print('  %s:' % direction)
        for pc in sorted(MARKS):
            v = acc.get(pc)
            if v:
                print('    %-24s %.3f..%.3f' % (MARKS[pc], min(v), max(v)))
        print('    $B8CC first/last tick    %.3f..%.3f / %.3f..%.3f'
              % (min(b[0] for b in blit), max(b[0] for b in blit),
                 min(b[1] for b in blit), max(b[1] for b in blit)))
        print('    $B8FB the chirp          %.3f..%.3f'
              % (min(tone), max(tone)))
    print('  THE BLIT IS THE FIRST THIRD OF THE PASS and the chirp is right')
    print('  after the HALT.  That is why an effect armed by the player\'s own')
    print('  move (0.005..0.052) finishes its noise inside one pass and one')
    print('  armed by the actor update (0.723..0.990) does not -- see `burst`.')
    return 0, 0


# ===========================================================================
# 5.  THE NOISE BURST'S DURATION -- a live game value
# ===========================================================================
def cmd_burst(npass=200):
    print('THE NOISE BURST, in DRIVEN PLAY (build/state_48k.pkl, DOWN, %d '
          'passes)' % npass)
    h = fresh()
    align(h, 'down')
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    passes = steps = 0
    t_top = None
    dt_last = 1
    armed = None
    ticks = 0
    out = []
    while passes < npass and steps < 200_000_000:
        pc = regs[PC]
        if pc == LOOP_TOP:
            if t_top is not None:
                dt_last = regs[T] - t_top
            t_top = regs[T]
            passes += 1
        if pc in (0xB8E9, 0xB8F2):
            armed = (regs[T], pc, passes,
                     (regs[T] - t_top) / max(dt_last, 1))
            ticks = 0
        if pc == 0xB8CC and armed is not None:
            ticks += 1
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); steps += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); steps += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        steps += 1
        if armed is not None and ticks > 0 and mem[0x84D2] == 0:
            out.append((armed[2], armed[1], armed[3], ticks,
                        regs[T] - armed[0]))
            armed = None
    print('  pass  arm     offset-into-pass  ticks   span T   frames     ms')
    for p, site, off, nn, dt in out:
        print('  %4d  $%04X       %5.2f        %4d  %7d   %6.3f  %6.1f'
              % (p, site, off, nn, dt, dt / FRAME_T, dt / 3500.0))
    bad = sum(1 for _p, _s, _o, nn, _d in out if nn != 127)
    print('  %d bursts, %d with a tick count other than 127.' % (len(out), bad))
    print('  THE DURATION IS A LIVE GAME VALUE.  The 127 ramp calls come from')
    print('  the BLITTER, so a burst armed at the top of the pass is over in')
    print('  0.49 of a frame (9.8 ms) and one armed inside the actor update')
    print('  waits for the next pass\'s blit and takes ~4.3 frames (85 ms).')
    print('  Both figures are real; the two earlier write-ups each reported')
    print('  one of them as "the" duration.')
    return bad, len(out)


# ===========================================================================
# 5b.  THE DRAWN-OBJECT TICK CURVE, and the burst-span differential
# ===========================================================================
def cmd_ticks(npass=140):
    """Two things, both about the same quantity.

    FIRST, an INDEPENDENT RE-MEASUREMENT of the tick curve that
    build/beeper_data.json ships, in a scene that file does not use, checked
    against it point for point.  The curve is the offset (video frames from
    $8503) of the blitter's $B8CC call at fraction q of the pass's own call
    count; the engine places its noise ticks on it.

    SECOND, THE BURST-SPAN DIFFERENTIAL.  For every noise burst the original
    fires in driven play, the engine's own BeeperDriver is run at the SAME
    arm phase with the SAME per-pass object census and the SAME per-pass
    video-frame cost -- so this engine's flat four-frame clock, a declared
    gap, is substituted out and what is left under test is the tick model.
    The tolerance is the measured scene-to-scene spread of the curve itself
    (0.126 video frames at its worst point), not a number chosen to pass.
    """
    import json as _json
    print('THE DRAWN-OBJECT TICK CURVE and the burst-span differential')
    shipped = _json.load(open(os.path.join(ROOT, 'build',
                                           'beeper_data.json')))
    curve = shipped['noise']['tick_curve']
    steps = curve['q_steps']
    bad = total = 0

    # ---- 1. re-measure, in a scene the shipped curve did not pool over ----
    per, counts = [], []
    h = fresh()
    align(h, 'left')
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    passes = nsteps = 0
    t_top, cur = None, []
    while passes < npass and nsteps < 200_000_000:
        pc = regs[PC]
        if pc == LOOP_TOP:
            if t_top is not None and cur:
                per.append(cur)
            cur = []
            t_top = regs[T]
            passes += 1
        if pc == 0xB8CC and t_top is not None:
            cur.append((regs[T] - t_top) / FRAME_T)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); nsteps += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); nsteps += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        nsteps += 1
    counts = [len(p) for p in per]
    qs = [i / steps for i in range(steps + 1)]
    mine = [sum(p[min(len(p) - 1, int(round(q * (len(p) - 1))))]
                for p in per) / len(per) for q in qs]
    tol = curve['spread_frames']
    print('  a FOURTH scene (holding LEFT, %d passes, %d..%d calls a pass)'
          % (len(per), min(counts), max(counts)))
    print('     q     shipped   re-measured   diff   (tolerance %.3f f)' % tol)
    for i, q in enumerate(qs):
        d = abs(mine[i] - curve['frames'][i])
        flag = '' if d <= tol else '   <-- OUTSIDE'
        if d > tol:
            bad += 1
        if i % 4 == 0 or d > tol:
            print('   %.2f    %.4f      %.4f     %+.4f%s'
                  % (q, curve['frames'][i], mine[i],
                     mine[i] - curve['frames'][i], flag))
        total += 1
    # the two universal properties, which no fit can fake
    assert all(curve['frames'][i] <= curve['frames'][i + 1]
               for i in range(steps)), 'the shipped curve is not monotonic'
    print('  the shipped curve is MONOTONIC (it is a list of times in issue')
    print('  order) and lies inside the measured blit window %.3f..%.3f.'
          % (curve['frames'][0], curve['frames'][-1]))

    # ---- 2. the burst-span differential ----------------------------------
    spans = _burst_spans('down', 220)
    if not spans:
        print('  no bursts fired -- nothing to compare')
        return bad, total
    spec = ';'.join('%.5f:%s' % (s['phase'], ','.join(str(o)
                                                     for o in s['objects']))
                    for s in spans)
    frames = ';'.join(','.join('%.4f' % c for c in s['costs']) for s in spans)
    walking = ';'.join(','.join(str(w) for w in s['walks']) for s in spans)
    out = subprocess.run(['node', os.path.join('tools', 'headless.js'),
                          '--beepburst', '--spec', spec, '--frames', frames,
                          '--walking', walking],
                         capture_output=True, text=True, cwd=ROOT)
    if out.returncode not in (0, 1):
        sys.exit('node failed: ' + out.stderr[-800:])
    eng = []
    for line in out.stdout.splitlines():
        f = line.split()
        if len(f) >= 6 and f[0][0].isdigit():
            eng.append({'phase': float(f[0]), 'edges': int(f[1]),
                        'passes': int(f[2]), 'span': float(f[3])})
    print('\n  THE BURST SPAN, original against the engine, arm phase and')
    print('  per-pass frame cost SUBSTITUTED so only the tick model is under')
    print('  test.  A span is arm -> the ramp call that reaches level 0.')
    print('   pass   phase(f)   orig span    engine span    diff      ms')
    nb = 0
    for s, e in zip(spans, eng):
        d = e['span'] - s['span']
        if abs(d) > tol:
            nb += 1
        print('   %4d    %6.3f     %7.3f       %7.3f     %+6.3f  %+6.1f%s'
              % (s['pass'], s['phase'], s['span'], e['span'], d,
                 d * 1000 / 50.08, '' if abs(d) <= tol else '   <-- OUTSIDE'))
        total += 1
    bad += nb
    print('  %d of %d bursts outside the curve\'s own scene spread (%.3f f).'
          % (nb, len(spans), tol))
    print('  Before the measured curve replaced a UNIFORM spread over the')
    print('  blit window the engine gave 0.610 f for the 0.098-frame arm')
    print('  against the original\'s 0.493 -- 24%% long on id 4, which is')
    print('  FIRE and the effect the player hears most.')
    return bad, total


def _burst_spans(direction='down', npass=220):
    """Every noise burst the original fires, with the arm phase in VIDEO
    FRAMES, the span to level 0, and the per-pass object census and frame
    cost the engine needs to be driven with the same inputs."""
    h = fresh()
    align(h, direction)
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    passes = nsteps = 0
    t_top = None
    armed = None
    ticks = 0
    objs = []                       # $B8CC calls in each pass, in order
    costs = []
    walks = []                      # did player 1 MOVE in that pass?
    cur = 0
    out = []
    xy0 = None
    while passes < npass and nsteps < 200_000_000:
        pc = regs[PC]
        if pc == LOOP_TOP:
            xy = (mem[0x8420], mem[0x8421])
            if t_top is not None:
                objs.append(cur)
                costs.append((regs[T] - t_top) / FRAME_T)
                walks.append(1 if xy != xy0 else 0)
            xy0 = xy
            cur = 0
            t_top = regs[T]
            passes += 1
        if pc in (0xB8E9, 0xB8F2) and t_top is not None:
            armed = {'t': regs[T], 'pass': passes,
                     'phase': (regs[T] - t_top) / FRAME_T,
                     'idx': len(objs)}
            ticks = 0
        if pc == 0xB8CC:
            cur += 1
            if armed is not None:
                ticks += 1
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); nsteps += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); nsteps += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        nsteps += 1
        if armed is not None and ticks > 0 and mem[0x84D2] == 0:
            armed['span'] = (regs[T] - armed['t']) / FRAME_T
            armed['ticks'] = ticks
            out.append(armed)
            armed = None
    # attach each burst's own pass census/costs (the arming pass and 3 after)
    keep = []
    for b in out:
        i = b['idx']
        if i + 4 > len(objs):
            continue
        b['objects'] = objs[i:i + 4]
        b['costs'] = costs[i:i + 4]
        b['walks'] = walks[i:i + 4]
        keep.append(b)
    return keep


# ===========================================================================
# 6.  THE CLOCK
# ===========================================================================
def cmd_clock(npass=200):
    print('FRAMES PER PASS, anchored on $8503, %d passes a cell' % npass)
    print('  key      48K/beeper                              128K/AY')
    bad = 0
    for direction in ('idle', 'right', 'down', 'left', 'up'):
        line = []
        for path in (STATE, AYSTATE):
            h = fresh(path)
            align(h, direction)
            fr = [step_pass(h)[0] / FRAME_T for _ in range(npass)]
            whole = collections.Counter(round(f) for f in fr)
            assert set(whole) <= {4, 5}, 'a pass that is not 4 or 5 frames'
            line.append((sum(fr) / len(fr), dict(sorted(whole.items()))))
        print('  %-7s %6.3f  %-28s  %6.3f  %s'
              % (direction, line[0][0], str(line[0][1]),
                 line[1][0], str(line[1][1])))
    print('  A PASS IS FOUR OR FIVE WHOLE VIDEO FRAMES, never three and never')
    print('  six: 4.375 was a duty cycle, not a period, and the 16-pass')
    print('  figures in the notes were a window.  This engine still charges a')
    print('  flat four -- see the clock note at the top of web/template.html.')
    return bad, 0


def cmd_pause():
    print('THE BLOCKING PAUSE, both branches (`$9D01` with $847D bit 2 armed)')
    bad = 0
    n = 0
    for path, tag in ((STATE, '48K/beeper'), (AYSTATE, 'AY/128K   ')):
        for b5, form in ((0, 'short'), (1, 'long ')):
            h = fresh(path, quiet=True)
            m = h.memobj.m
            m[0x847D] |= 0x04
            m[0x847D] = (m[0x847D] & ~0x20) | (0x20 if b5 else 0)
            f0 = m[0x8497]
            h.ports.writes = []
            h.ports.record_writes = True
            _i, dt, _s = h.call(0x9D01, interrupts=True, limit=60_000_000)
            h.ports.record_writes = False
            w = len([1 for _t, p, _v in h.ports.writes if (p & 0xFF) == 0xFE])
            n += 1
            print('  %s %s isolated: %9d T = %7.2f frames   $8497 +%3d   '
                  '$FE writes %6d'
                  % (tag, form, dt, dt / FRAME_T, (m[0x8497] - f0) & 0xFF, w))
    for path, tag in ((STATE, '48K/beeper'), (AYSTATE, 'AY/128K   ')):
        h = fresh(path, quiet=True)
        m = h.memobj.m
        col, row0 = 8, 10
        m[0x8000 + ((row0 + 4) % 32) * 32 + col] = 0x19
        m[P1], m[P1 + 1] = col * 4, row0 * 4
        m[P1 + 8], m[P1 + 9] = 3, 2
        align(h, 'down')
        prev = m[0x8497]
        costs = []
        for _ in range(12):
            dt, _e = step_pass(h)
            now = m[0x8497]
            costs.append((dt / FRAME_T, (now - prev) & 0xFF))
            prev = now
        big = [(i + 1, c, d) for i, (c, d) in enumerate(costs) if c > 10]
        n += 1
        for i, c, d in big:
            print('  %s in situ  : the banner PASS costs %6.2f frames and '
                  'advances $8497 by %d' % (tag, c, d))
    print('  So the port must debit 72 wall frames on this branch, not 100,')
    print('  and must NOT advance $8497 through them: the tune holds the')
    print('  interrupt off for all of it.  On the AY branch both are 100.')
    return bad, n


# ===========================================================================
# 7.  THE DIFFERENTIAL
# ===========================================================================
COL, ROW0 = 3, 2                 # the player's own cell in build/state_48k.pkl
# cells planted in his path, and the id each one fires.  Every one of these
# is a rule this project already gates elsewhere ($A79E, $A6F2, $A783, $A7C5).
PLANTS = [(6, 0x1F, 7, 'a KEY        -> id 7,  a 6-step warble'),
          (10, 0x11, 14, 'a DOOR       -> id $0E, a 5-step fall'),
          (14, 0x18, 4, 'a POWER-UP   -> id 4,  the NOISE ramp DOWN'),
          (18, 0x1B, 17, 'an ITEM      -> id $11 AND the blocking $B8B0 TUNE')]


def orig_edges(direction, npass, plants=PLANTS, quiet=True, nogen=True):
    """Drive the real Z80 and return one row per SPEAKER EDGE, plus the
    per-pass frame cost and the tune blocks."""
    h = fresh(quiet=quiet, nogen=nogen)
    m = h.memobj.m
    for r, cell, _i, _n in plants:
        m[0x8000 + (r % 32) * 32 + COL] = cell
    m[P1], m[P1 + 1] = COL * 4, ROW0 * 4
    m[P1 + 8], m[P1 + 9] = 3, 2
    align(h, direction)
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    rows, costs, blocks, fired = [], [], [], []
    h.ports.record_writes = True
    lvl = 0
    for i in range(npass):
        t0 = regs[T]
        h.ports.writes = []
        srcs = []
        n = 0
        first = True
        tune_edges = 0
        tune_t = None
        while n < 60_000_000:
            pc = regs[PC]
            if pc == LOOP_TOP and not first:
                break
            first = False
            if pc in OUT_SITE:
                srcs.append(OUT_SITE[pc])
            if pc == 0xBA2B:
                fired.append((i + 1, regs[rA]))
            if pc == 0xB8B0 or pc == 0xB8B5:
                tune_t = regs[T]
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape(); n += 1; continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt(); n += 1; continue
            ops[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
            n += 1
        dt = regs[T] - t0
        # THE PASS'S ORDINARY COST, i.e. WITHOUT the blocking tune.  The tune
        # is reached from $9D01 at the very bottom of $9CD7's tail, AFTER the
        # HALT and after the chirp, so it does not move the chirp -- the
        # engine models it separately (blockingPause) and beepgate asserts
        # its length, so feeding it through --ticks would count it twice.
        tune_cost = 0 if tune_t is None else regs[T] - tune_t
        costs.append((dt - tune_cost) / FRAME_T)
        w = [(t, v) for t, p, v in h.ports.writes if (p & 0xFF) == 0xFE]
        assert len(w) == len(srcs), 'writer attribution lost a write'
        prev = {}
        for (t, v), src in zip(w, srcs):
            b = (v >> 4) & 1
            if b == lvl:
                continue
            lvl = b
            if src == 'tune':
                tune_edges += 1
                continue
            dT = -1 if src not in prev else t - prev[src]
            prev[src] = t
            rows.append((i + 1, src, b, (t - t0) / FRAME_T, dT))
        if tune_edges:
            blocks.append((i + 1, tune_edges, (regs[T] - tune_t) / FRAME_T))
    h.ports.record_writes = False
    return rows, costs, blocks, fired


def eng_edges(direction, npass, plants=PLANTS, ticks=None, quiet=True,
              nogen=True):
    args = ['node', os.path.join('tools', 'headless.js'), '--beeptable',
            direction, str(npass)]
    if quiet:
        args.append('--noactors')
    if nogen:
        args.append('--nogen')
    for r, cell, _i, _n in plants:
        args += ['--plant', '%d,%d,%d' % (COL, r, cell)]
    args += ['--keys', '3', '--potions', '2']
    if ticks:
        args += ['--ticks', ','.join(str(t) for t in ticks)]
    out = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    if out.returncode not in (0, 1):
        sys.exit('node failed: ' + out.stderr[-800:])
    rows, blocks, fired = [], [], []
    for line in out.stdout.splitlines():
        mm = ROW.match(line)
        if mm:
            rows.append((int(mm.group(1)), mm.group(2), int(mm.group(3)),
                         float(mm.group(4)), int(mm.group(5))))
        elif line.startswith('# engine blocks:'):
            blocks = line.split(':', 1)[1].strip()
        elif line.startswith('# engine triggers:'):
            fired = line.split(':', 1)[1].strip()
    return rows, blocks, fired


def compare(orig, eng, tag, tol=0.06):
    """TONE exactly; NOISE by count; BORDER by count."""
    def by(rows, src):
        d = collections.defaultdict(list)
        for p, s, lv, off, dT in rows:
            if s == src:
                d[p].append((lv, off, dT))
        return d
    bad = 0
    total = 0
    ot, et = by(orig, 'tone'), by(eng, 'tone')
    for p in sorted(set(ot) | set(et)):
        a, b = ot.get(p, []), et.get(p, [])
        total += max(len(a), len(b))
        if len(a) != len(b):
            bad += max(len(a), len(b))
            print('    pass %d: %d tone edges on the Z80, %d in the engine'
                  % (p, len(a), len(b)))
            continue
        for i, (x, y) in enumerate(zip(a, b)):
            if x[0] != y[0] or x[2] != y[2] or abs(x[1] - y[1]) > tol:
                bad += 1
                if bad < 8:
                    print('    pass %d edge %d: orig lvl=%d off=%.4f dT=%d  '
                          'engine lvl=%d off=%.4f dT=%d'
                          % (p, i, x[0], x[1], x[2], y[0], y[1], y[2]))
    onz, enz = by(orig, 'noise'), by(eng, 'noise')
    nb = 0
    for p in sorted(set(onz) | set(enz)):
        a, b = len(onz.get(p, [])), len(enz.get(p, []))
        # THE DRAW IS `LD A,R`.  What is compared is the RULE: a burst
        # occupies the same PASSES, and its toggle count sits in the same
        # class.  Over a 127-call ramp with density level/128 the expected
        # toggle count is sum(L/128) for L = 1..127 = 63.5 and the standard
        # deviation is sqrt(sum p(1-p)) = 5.1, so anything inside 63.5 +- 20
        # is the same coin; anything outside is a different rule.
        if (a > 0) != (b > 0):
            nb += 1
            print('    pass %d: %d noise edges on the Z80, %d in the engine'
                  % (p, a, b))
    na = sum(len(v) for v in onz.values())
    ne = sum(len(v) for v in enz.values())
    nburst = max(len(onz), len(enz))
    if nburst and (abs(na - 63.5 * nburst) > 20 * nburst
                   or abs(ne - 63.5 * nburst) > 20 * nburst):
        nb += 1
        print('    the noise toggle count is out of class: Z80 %d, engine %d, '
              'expected %.1f +- 20 per burst' % (na, ne, 63.5 * nburst))
    obd, ebd = by(orig, 'border'), by(eng, 'border')
    # THE BORDER EDGES ARE REPORTED, NOT GATED, and only where a noise burst
    # ran: the ISR's write ($A2B1 CALL $B4F9) only makes an EDGE when the
    # speaker was left HIGH, and a noise burst's toggle count is odd or even
    # according to the substituted draw.  Tone steps all carry EVEN counts,
    # so their border edges DO agree and those are the ones worth reading.
    print('  %-22s tone %4d edges, %d mismatching | noise %d/%d edges over '
          '%d/%d passes (COUNT ONLY -- the draw is LD A,R; per burst %s / %s),'
          ' %d disagreeing | border %d/%d (parity-driven, reported not gated)'
          % (tag, total, bad, na, ne, len(onz), len(enz),
             [len(v) for _p, v in sorted(onz.items())],
             [len(v) for _p, v in sorted(enz.items())], nb,
             sum(len(v) for v in obd.values()),
             sum(len(v) for v in ebd.values())))
    return bad + nb, total


# the scenarios.  Each is (name, direction, passes, planted cells) and each
# reaches a DIFFERENT set of the eleven ids the beeper handles.  The rows are
# the game's own map values, gated elsewhere in this project ($A79E the key,
# $A6F2 the door, $A783 the $18 power-up, $A7C5 every other pickup, $A7FE the
# $2F sweep, $A6AC the exit).
SCEN = [
    ('key+door+noise+banner', 'down', 40, PLANTS),
    ('the same id retriggered', 'down', 40,
     [(6, 0x1F, 7, 'key -> id 7'), (14, 0x1F, 7, 'key -> id 7 again, which'),
      (16, 0x1F, 7, 'key -> id 7 two passes later: a RESTART mid-stream'),
      (24, 0x2F, 9, 'the $2F sweep -> id 9, one of the seven SILENT ids')]),
    ('the exit and a door', 'down', 32,
     [(6, 0x11, 14, 'door -> id $0E, and it spends a key'),
      (12, 0x36, 6, 'the exit -> id 6, the 9-step rising sweep'),
      (20, 0x11, 14, 'door -> id $0E')]),
    ('noise twice, both ramps', 'down', 36,
     [(6, 0x18, 4, 'power-up -> id 4, ramp DOWN'),
      (14, 0x18, 4, 'power-up -> id 4, ramp DOWN'),
      (22, 0x1F, 7, 'key')]),
    ('nothing planted (silence)', 'right', 24, []),
]


def cmd_diff():
    print('THE EDGE-LEVEL DIFFERENTIAL -- the real Z80 against the built '
          'artifact')
    print('  build/state_48k.pkl, dungeon 1, ACTORS AND GENERATORS REMOVED')
    print('  (they are the per-pass LD A,R consumers), the player put at cell')
    print('  (%d,%d) and walked into planted cells.' % (COL, ROW0))
    bad = total = 0
    flat = 0
    for name, direction, npass, plants in SCEN:
        print('  --- %s (%s, %d passes) ---' % (name, direction, npass))
        for r, cell, _idv, note in plants:
            print('        row %2d  cell $%02X  %s' % (r, cell, note))
        orig, costs, blocks, fired = orig_edges(direction, npass, plants)
        whole = [int(round(c)) for c in costs]
        print('    Z80 fired %s;  per-pass frame cost %s'
              % (' '.join('p%d:$%02X' % (p, i) for p, i in fired) or 'nothing',
                 dict(sorted(collections.Counter(whole).items()))))
        eng, eblocks, efired = eng_edges(direction, npass, plants, ticks=whole)
        print('    engine fired %s' % (efired or 'nothing'))
        # THE GATE IS THE UNHELPED RUN.  The engine models the per-pass cost
        # itself now (web/template.html clockCost/quantise), so the primary
        # comparison hands it NOTHING; the substituted run is kept beside it
        # only to show that the two agree, i.e. that the model and the
        # original's own measured cost put every edge in the same place.
        eng2, _b2, _f2 = eng_edges(direction, npass, plants)
        b, t = compare(orig, eng2, 'MODELLED clock (no help)')
        bad += b
        total += t
        b2, _t2 = compare(orig, eng, 'the original\'s own cost')
        flat += b2
        if blocks or eblocks != 'none':
            print('    blocking tunes: Z80 %s | engine %s'
                  % ('  '.join('pass %d, %d edges, %.2f frames' % x
                               for x in blocks) or 'none', eblocks))
    print('  TOTAL MISMATCHING BEEPER EDGES: %d   (%d compared)  <- UNHELPED'
          % (bad, total))
    print('  the same runs with the ORIGINAL\'S OWN per-pass cost substituted')
    print('  in: %d mismatching.  THE TWO AGREE, so what is left in the gate' % flat)
    print('  is the driver, and the clock is no longer being lent to it.')
    print('  HISTORY: before the pass cost model this gate scored 0 with the')
    print('  substitution and 10 without it, and all ten were tone edges of ONE')
    print('  pass -- scenario 1 pass 34, the MESSAGE BANNER, where $891C\'s text')
    print('  render costs 52,727 T against 77 T and takes the pass from four')
    print('  video frames to five.  The engine now charges that itself.')
    return bad, total


def cmd_table(args):
    direction = args[0] if args else 'down'
    npass = int(args[1]) if len(args) > 1 else 40
    rows, costs, blocks, fired = orig_edges(direction, npass)
    print('pass  source  lvl   offset      dT')
    for p, s, lv, off, dT in rows:
        print('%4d  %-7s%2d  %9.5f  %7d' % (p, s, lv, off, dT))
    print('# orig triggers: %s'
          % ' '.join('p%d:$%02X' % (p, i) for p, i in fired))
    print('# orig blocks: %s' % ('  '.join('pass %d,%d edges,%.2ff' % x
                                           for x in blocks) or 'none'))
    return 0, 0


def main():
    cmds = sys.argv[1:] or ['all']
    if cmds[0] == 'table':
        cmd_table(cmds[1:])
        return
    which = cmds[0]
    order = ['ids', 'fit', 'noise', 'where', 'burst', 'ticks', 'clock',
             'pause', 'diff']
    run = order if which == 'all' else [which]
    bad = total = 0
    for c in run:
        print()
        fn = globals()['cmd_' + c]
        b, t = fn()
        bad += b
        total += t
    print()
    print('TOTAL FAILING ASSERTIONS/EDGES: %d   (%d compared)' % (bad, total))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
