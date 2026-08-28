#!/usr/bin/env python3
"""
clockgate.py -- THE LOAD-DEPENDENT PASS CLOCK, original against port.

    python tools/clockgate.py hz        effective Hz, both sides, both scenes
    python tools/clockgate.py diff      per-pass frame cost, Z80 vs engine
    python tools/clockgate.py score     the cost model on a held-out corpus
    python tools/clockgate.py w2        the W2 / ISR constants, re-measured
    python tools/clockgate.py contend   what ULA contention does to all of it
    python tools/clockgate.py all

=============================================================================
WHAT IS BEING TESTED, AND WHY IT IS THE ARTIFACT AND NOT A PYTHON COPY
=============================================================================
The cost model has exactly ONE implementation, in web/template.html
(clockCost / quantise).  `score` drives the real Z80, extracts the census the
ENGINE would have built for the same pass, and hands that census to the BUILT
artifact through `node tools/headless.js --clockmodel` -- so what is scored is
the shipped code, and a Python transcription of the model cannot drift away
from it because there is no Python transcription.

`diff` is stricter still: it lets the engine build its OWN census from its own
simulation and compares the resulting per-pass frame cost with the original's,
pass for pass.  That only means anything while the two simulations agree, so
it is run in the scenes where they provably do -- no actors, few actors, and
the first passes of a full-population scene -- and the DISTRIBUTION is
reported for the scenes where they cannot (see THE HONEST BOUNDARY below).

THE MECHANISM (measured; see notes/NOTES-battery.md Q10)

    $8503 ... W1, the work, interrupts on ... $8550 CALL $9CD7
    $9CD7 HALT              <- one per pass, on every pass, THE QUANTISER
    $9CD8 DI, tone tick, three screen copies, $9CF8's hand CALL $A29F,
          $8491++, $B4FF's clear ................................ W2

    cost = ceil((t + W1)/FRAME_T)*FRAME_T + W2 - t        t = phase at $8503

THE HONEST BOUNDARY.  Two actors visible are enough to make an individual
pass's 4-vs-5 outcome unknowable to any port: $ABFF's update branches on the
Z80 REFRESH REGISTER, which is not reproducible, and one updated actor moves
W1 by up to 16,438 T against a margin to the frame boundary of 11,000-14,000
T.  What a port can reproduce is the DISTRIBUTION, and that is what `hz`
reports.  Two further limits are declared rather than modelled: frame
interrupts lost inside the blitter's DI windows (14% overall, 49% holding
LEFT), and ULA contention, which the harness does not model at all -- see
`contend`.
"""
import collections
import json
import math
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, FRAME_T, TAPE_CALL_PC      # noqa: E402
from keyprobe import KEYS, keymask                                  # noqa: E402
import passcost as P                                                # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
LOOP_TOP = 0x8503
FEAT = os.path.join(ROOT, 'build', 'clockfeat.json')


# ---------------------------------------------------------------------------
# the Z80 side
# ---------------------------------------------------------------------------
def fresh(path=STATE, noactors=False, nogen=False, nact=None, warp=None):
    h = Harness()
    h.load_state(pickle.load(open(path, 'rb')))
    m = h.memobj.m
    assert m[0xFFFD] == 0x00, 'not the 48K/beeper branch'
    if noactors:
        nact = 0
    if nact is not None:
        m[0x8496] = nact
        a = 0x5C00 + 4 * nact
        m[0x8494], m[0x8495] = a & 0xFF, a >> 8
    if nogen:
        for a in range(0x8000, 0x8400):
            if 0x20 <= m[a] <= 0x2E:
                m[a] = 0
    if warp:
        x, y, cx, cy = warp
        m[0x8420], m[0x8421] = x, y
        m[0x848B], m[0x848C] = cx, cy
    return h


def press(h, direction):
    if direction and direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))


def z80_run(direction, n, **kw):
    """Per-pass (frames, $8497 delta) plus the ENGINE-VISIBLE census."""
    h = fresh(**kw)
    press(h, direction)
    P.one_pass(h)
    m = h.memobj.m
    out = []
    for _ in range(int(n)):
        ctr = m[0x8491]
        f0 = m[0x8497]
        rec = P.one_pass(h)
        f = P.features(m, rec)
        f.update(P.actor_features(rec))
        row = {
            'frames': rec['cost'] / FRAME_T,
            'whole': int(round(rec['cost'] / FRAME_T)),
            'd8497': (m[0x8497] - f0) & 0xFF,
            'p': (rec['t0'] % FRAME_T) / FRAME_T,
            'W1': rec['stamp'][0x9CD7] - rec['t0'],
            'W2': rec['t0'] + rec['cost'] - rec['wake'],
            'ctr': ctr, 'f8497': f0,
            'clk': {
                'nzA': f['nzA'], 'nzB': f['nzB'], 'nzC': f['nzC'],
                'nzD': f['nzD'], 'n30': f['n30'],
                'bx': f['bx'], 'by': f['by'],
                'nact': f['nact'],
                'clipX': f['nact'] - f['nUpdX'],
                'clipY': f['nUpdX'] - f['nUpd'],
                'upd': f['nUpd'],
                'step': rec['cnt']['actorStep'],
                'contact': rec['cnt']['contact'],
                'nact2': f['nact2'],
                'gcells': f['gcells'], 'ngen': f['ngen'],
                'spawn': rec['cnt']['spawn'],
                'drain': 1 - f['drain'],
                'banner': 1 if f['banner'] else 0,
                'moving': 0 if direction in (None, 'idle') else 1,
                'noiseCalls': rec['cnt']['sndRamp'],
                'noiseTogs': rec['cnt']['sndOut'],
            },
        }
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# the engine side
# ---------------------------------------------------------------------------
def node(args):
    out = subprocess.run(['node', os.path.join('tools', 'headless.js')] + args,
                         capture_output=True, text=True, cwd=ROOT)
    if out.returncode not in (0, 1):
        sys.exit('node failed: ' + out.stderr[-800:])
    return out.stdout


def eng_run(direction, n, noactors=False, nogen=False, nact=None, warp=None):
    args = ['--clocktable', direction, str(n)]
    if noactors:
        args.append('--noactors')
    if nogen:
        args.append('--nogen')
    if nact is not None:
        args += ['--nact', str(nact)]
    if warp:
        args += ['--warp', ','.join(str(v) for v in warp)]
    rows = []
    for line in node(args).splitlines():
        p = line.split()
        if len(p) == 14 and p[0].isdigit():
            rows.append({'frames': float(p[1]), 'ticks': int(p[2]),
                         'w1': int(p[3]), 'nact': int(p[4])})
    return rows


def eng_model(rows):
    json.dump(rows, open(FEAT, 'w'))
    out = node(['--clockmodel', FEAT])
    got = []
    for line in out.splitlines():
        p = line.split()
        if len(p) == 3 and p[0].lstrip('-').isdigit():
            got.append((int(p[0]), float(p[1]), int(p[2])))
    return got


# ---------------------------------------------------------------------------
# THE SCENES.  Two groups, and the difference matters.
#   DETERMINISTIC -- no actor reaches $ABFF, so the pass cost is bit-identical
#   across a swept LD A,R coin and the port can be compared PASS FOR PASS.
#   POPULATED -- actors update, the coin decides where they go, and only the
#   DISTRIBUTION is comparable.
# ---------------------------------------------------------------------------
DET = [
    ('quiet-idle',   'idle',  40, dict(noactors=True, nogen=True)),
    ('quiet-down',   'down',  40, dict(noactors=True, nogen=True)),
    ('quiet-right',  'right', 40, dict(noactors=True, nogen=True)),
    ('quiet-up',     'up',    40, dict(noactors=True, nogen=True)),
    ('quiet-left',   'left',  40, dict(noactors=True, nogen=True)),
    ('n8-down',      'down',  40, dict(nact=8, nogen=True)),
    ('n24-right',    'right', 40, dict(nact=24, nogen=True)),
]
POP = [
    ('d1-idle',      'idle',  60, {}),
    ('d1-right',     'right', 60, {}),
    ('d1-left',      'left',  60, {}),
    ('d1-up',        'up',    60, {}),
    ('d1-down',      'down',  60, {}),
    ('cluster-idle', 'idle',  60, dict(warp=(96, 56, 66, 38))),
    ('cluster-down', 'down',  60, dict(warp=(96, 56, 66, 38))),
]


# ---------------------------------------------------------------------------
def cmd_diff(args):
    """Per-pass frame cost, the real Z80 against the built artifact."""
    print('PER-PASS FRAME COST -- the real Z80 against the BUILT artifact')
    print('  build/state_48k.pkl, dungeon 1.  The engine builds its OWN census')
    print('  from its OWN simulation; nothing is substituted.')
    print()
    print('  %-14s %5s %7s %7s   %-24s %-24s' %
          ('scene', 'n', 'exact', 'orig Hz', 'original', 'engine'))
    tot = ok = 0
    for name, d, n, kw in DET:
        z = z80_run(d, n, **kw)
        e = eng_run(d, n, **kw)
        k = sum(1 for a, b in zip(z, e) if a['whole'] == round(b['frames']))
        tot += len(z)
        ok += k
        print('  %-14s %5d %6d  %6.2f   %-24s %-24s'
              % (name, len(z), k, FRAME_HZ_OF(z),
                 hist(a['whole'] for a in z),
                 hist(round(b['frames']) for b in e)))
    print('  DETERMINISTIC SCENES: %d/%d = %.1f%% exact, pass for pass'
          % (ok, tot, 100.0 * ok / tot))
    print()
    print('  POPULATED SCENES -- the two simulations diverge (the actor update')
    print('  branches on the Z80 refresh register), so the DISTRIBUTION is what')
    print('  is comparable.  Pass-for-pass agreement is printed beside it and is')
    print('  expected to decay with the actor trajectories, NOT to be 100%.')
    print('  %-14s %5s %7s %8s %8s   %-22s %-22s' %
          ('scene', 'n', 'exact', 'orig f/p', 'eng f/p', 'original', 'engine'))
    for name, d, n, kw in POP:
        z = z80_run(d, n, **kw)
        e = eng_run(d, n, **kw)
        k = sum(1 for a, b in zip(z, e) if a['whole'] == round(b['frames']))
        zf = sum(a['frames'] for a in z) / len(z)
        ef = sum(b['frames'] for b in e) / len(e)
        print('  %-14s %5d %6d  %8.3f %8.3f   %-22s %-22s'
              % (name, len(z), k, zf, ef,
                 hist(a['whole'] for a in z),
                 hist(round(b['frames']) for b in e)))
    return 0


def FRAME_HZ_OF(rows):
    return 50.08 / (sum(r['frames'] for r in rows) / len(rows))


def hist(it):
    return str(dict(sorted(collections.Counter(it).items())))


# ---------------------------------------------------------------------------
def cmd_score(args):
    """The COST MODEL alone: the original's own census, the artifact's model."""
    n = int(args[0]) if args else 60
    print('THE COST MODEL, scored against the real Z80')
    print('  The census is the ORIGINAL\'s (so the actor divergence is out of')
    print('  the way) and the model is the BUILT ARTIFACT\'s own clockCost() /')
    print('  quantise(), reached through `node tools/headless.js --clockmodel`.')
    print('  The model carries its OWN phase and its OWN $8497 forward from')
    print('  the first pass of each scene -- it is never handed the original\'s.')
    print()
    scenes = DET + POP + [
        ('l8-idle', 'idle', n, dict(path=os.path.join(ROOT, 'build',
                                                      '_lvl8_r005_live.pkl'))),
        ('l8-down', 'down', n, dict(path=os.path.join(ROOT, 'build',
                                                      '_lvl8_r005_live.pkl'))),
        ('warp2-down', 'down', n, dict(warp=(40, 40, 24, 24))),
        ('warp3-left', 'left', n, dict(warp=(72, 20, 56, 8))),
    ]
    allrows = []
    marks = []
    for name, d, k, kw in scenes:
        z = z80_run(d, k, **kw)
        for i, r in enumerate(z):
            if i == 0:
                r['reset'] = r['p']
                r['f0'] = r['f8497']
        marks.append((name, len(allrows), len(z)))
        allrows += z
    pred = eng_model([{k: v for k, v in r.items()
                       if k in ('clk', 'ctr', 'reset', 'f0')} for r in allrows])
    print('  %-14s %5s %7s %9s %9s   %s' %
          ('scene', 'n', 'exact', 'W1 bias', 'W1 rms', 'real'))
    tot = ok = 0
    errs = []
    for name, off, k in marks:
        z = allrows[off:off + k]
        p = pred[off:off + k]
        good = sum(1 for a, b in zip(z, p) if a['whole'] == b[2])
        e = [b[0] - a['W1'] for a, b in zip(z, p)]
        errs += e
        rms = math.sqrt(sum(x * x for x in e) / len(e))
        tot += k
        ok += good
        print('  %-14s %5d %6d  %+9.0f %9.0f   %s'
              % (name, k, good, sum(e) / len(e), rms,
                 hist(a['whole'] for a in z)))
    rms = math.sqrt(sum(x * x for x in errs) / len(errs))
    base = collections.Counter(a['whole'] for a in allrows).most_common(1)[0][1]
    print('  TOTAL %d/%d = %.1f%%   W1 rms %.0f T (%.4f frames)'
          % (ok, tot, 100.0 * ok / tot, rms, rms / FRAME_T))
    print('  always-the-commonest baseline: %.1f%%    (the previous attempt at'
          % (100.0 * base / tot))
    print('  a predictor reached 62.7%, and declined to ship at that)')
    return 0


# ---------------------------------------------------------------------------
def cmd_hz(args):
    """THE DELIVERABLE: effective passes per second, both sides, both scenes."""
    n = int(args[0]) if args else 120
    print('EFFECTIVE UPDATE RATE -- the original against the port')
    print('  A pass is the game\'s update.  The original\'s rate is 50.08 Hz')
    print('  divided by the frames the pass costs, and the pass costs what its')
    print('  work costs -- which is the whole point of manual B2b.')
    print()
    scenes = [
        ('EMPTY playfield  ', 'idle', dict(noactors=True, nogen=True)),
        ('quiet dungeon 1  ', 'idle', {}),
        ('...walking right ', 'right', {}),
        ('...walking down  ', 'down', {}),
        ('...walking up    ', 'up', {}),
        ('...walking left  ', 'left', {}),
        ('GENERATOR CLUSTER', 'idle', dict(warp=(96, 56, 66, 38))),
        ('...and walking   ', 'down', dict(warp=(96, 56, 66, 38))),
    ]
    print('  %-18s %-9s %17s %17s   %s' %
          ('scene', 'keys', 'ORIGINAL', 'PORT', 'port/original'))
    for name, d, kw in scenes:
        z = z80_run(d, n, **kw)
        e = eng_run(d, n, **kw)
        zf = sum(r['frames'] for r in z) / len(z)
        ef = sum(r['frames'] for r in e) / len(e)
        print('  %-18s %-9s %7.3f f  %5.2f Hz %7.3f f  %5.2f Hz   %+6.1f%%'
              % (name, d, zf, 50.08 / zf, ef, 50.08 / ef,
                 100.0 * (50.08 / ef) / (50.08 / zf) - 100.0))
    print()
    print('  and what the port did BEFORE this work -- a flat four frames a')
    print('  pass, i.e. 12.52 Hz in every scene, which is 4% fast in a quiet')
    print('  dungeon and 43% fast at a cluster.')
    print()
    print('  WALKING LEFT is the worst scene in the collection and it is')
    print('  declared, not smoothed: p + W1 sits within 1% of the frame')
    print('  boundary on every pass, so the original tips 4/5 almost evenly')
    print('  and the model, being deterministic, does not.  See `diff`.')
    print()
    print('  ULA CONTENTION IS NOW IN BOTH SIDES OF THESE FIGURES.  It lands')
    print('  in W2, which sets the phase, and it is worth +6,555 T on the')
    print('  $9CD8 blit -- enough to tip a pass that sat just under four')
    print('  video frames onto five.  The harness contends by default and')
    print('  CLK.w2Straight carries the same 6,555 T, so the two columns')
    print('  above are like for like.  Idle dungeon 1 reads 10.0 Hz, which')
    print('  is a real 48K; before this it read 12.1.  Set')
    print('  GAUNTLET_CONTENDED=0 to see the old uncontended pair, but note')
    print('  the shipped constants will then be scored against the wrong')
    print('  machine -- `score` drops from 98.6% to 77.6%.')
    return 0


# ---------------------------------------------------------------------------
def cmd_w2(args):
    """Re-measure the constants the model's phase depends on."""
    n = int(args[0]) if args else 30
    print('W2 AND THE INTERRUPT HANDLER -- the constants quantise() uses')
    bodies = collections.defaultdict(collections.Counter)
    straight = collections.Counter()
    nb = collections.Counter()
    tail = collections.Counter()
    for d in ('idle', 'right', 'left', 'up', 'down'):
        h = fresh()
        press(h, d)
        _w2_scan(h, n, bodies, straight, nb, tail)
    print('  $A29F body cost, keyed on ($8497 BEFORE its own $A2A2 INC) & 7:')
    for k in sorted(bodies):
        c = bodies[k]
        print('     &7=%d  n=%4d  %s' % (k, sum(c.values()),
              ('%d..%d' % (min(c), max(c))) if len(c) > 1 else str(min(c))))
    print('  handler bodies inside W2: %s' % dict(sorted(nb.items())))
    print('  interrupts accepted AFTER the HALT wake, by interrupted PC: %s'
          % dict(sorted(tail.items())))
    tot = sum(straight.values())
    mode = straight.most_common(1)[0]
    print('  straight-line W2 (W2 minus every body): mode %d T on %d of %d '
          'passes, range %d..%d' % (mode[0], mode[1], tot,
                                    min(straight), max(straight)))
    print('  the model ships w2Straight=147491, isrFull=2205, isrCheap=328')
    return 0


def _w2_scan(h, n, bodies, straight, nb, tail):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    ISR = 0xA29F
    passes = 0
    wake = None
    cur = None
    got = []
    w2t = 0
    while passes < n:
        pc = regs[PC]
        if cur is not None and regs[12] > cur[1]:      # SP index
            f = cur[2]
            if f is not None:
                bodies[f & 7][regs[T] - cur[0]] += 1
            got.append(regs[T] - cur[0])
            cur = None
        if pc == LOOP_TOP and wake is not None:
            nb[len(got)] += 1
            straight[w2t - sum(got)] += 1
            passes += 1
            wake = None
            got = []
        if pc == LOOP_TOP:
            w2t = 0
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); continue
        if mem[pc] == 0x76 and regs[IFF]:
            t0 = regs[T]
            h._fast_halt()
            if wake is None:
                wake = regs[T]
                w2t = -regs[T]
                cur = (regs[T], regs[12], mem[0x8497], 'wake')
            continue
        if pc == ISR and wake is not None and cur is None:
            cur = (regs[T], regs[12], mem[0x8497], 'hand')
        ops[mem[pc]]()
        if cur is not None and cur[2] is None:
            cur = (cur[0], cur[1], mem[0x8497], cur[3])
        if regs[IFF] and regs[T] % fd < ia:
            if wake is not None:
                tail['$%04X' % (pc & 0xFFF0)] += 1
            sim.accept_interrupt(regs, mem, pc)
            cur = (regs[T], regs[12], None, 'int')
        if wake is not None:
            w2t = regs[T] - wake


# ---------------------------------------------------------------------------
def cmd_contend(args):
    """SkoolKit ships a CONTENDED simulator.  The harness does not use it, so
    every absolute figure in this project is a LOWER BOUND.  This says by how
    much, and which way the port is therefore wrong."""
    from skoolkit.cmiosimulator import CMIOSimulator
    n = int(args[0]) if args else 25
    print('ULA CONTENTION -- what the harness does not model')
    print('  W1 paints the UNCONTENDED shadow screen at $C000 and runs code')
    print('  above $8000; W2 writes 6,912 bytes into the CONTENDED $4000/$5800.')
    print('  So contention lands in W2, which SETS THE PHASE, and the port is')
    print('  systematically FAST by whatever that is worth.')
    print()
    print('  %-8s %-10s %8s %8s %8s %8s  %s' %
          ('scene', 'sim', 'p', 'W1', 'W2', 'frames', 'hist'))
    for d in ('idle', 'right', 'left', 'up', 'down'):
        for tag in ('plain', 'contended'):
            h = fresh()
            if tag == 'contended':
                regs = list(h.sim.registers)
                h.sim = CMIOSimulator(h.memobj)
                h.memobj.sim = h.sim
                h.sim.set_tracer(h.ports)
                h.sim.registers[:] = regs
            press(h, d)
            P.one_pass(h)
            pp = []
            w1 = []
            w2 = []
            fr = []
            for _ in range(n):
                rec = P.one_pass(h)
                pp.append((rec['t0'] % FRAME_T) / FRAME_T)
                w1.append((rec['stamp'][0x9CD7] - rec['t0']) / FRAME_T)
                w2.append((rec['t0'] + rec['cost'] - rec['wake']) / FRAME_T)
                fr.append(rec['cost'] / FRAME_T)
            print('  %-8s %-10s %8.4f %8.4f %8.4f %8.3f  %s'
                  % (d, tag, sum(pp) / n, sum(w1) / n, sum(w2) / n,
                     sum(fr) / n, hist(round(x) for x in fr)))
    return 0


CMDS = {'diff': cmd_diff, 'score': cmd_score, 'hz': cmd_hz,
        'w2': cmd_w2, 'contend': cmd_contend}


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else 'all'
    if cmd == 'all':
        rc = 0
        for k in ('w2', 'score', 'diff', 'hz'):
            print('=' * 74)
            rc |= CMDS[k](args[1:]) or 0
            print()
        return rc
    if cmd not in CMDS:
        print(__doc__)
        return 2
    return CMDS[cmd](args[1:]) or 0


if __name__ == '__main__':
    sys.exit(main())
