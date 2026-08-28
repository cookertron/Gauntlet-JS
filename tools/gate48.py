#!/usr/bin/env python3
"""
gate48.py -- does the 48K/BEEPER branch change GAMEPLAY, or only sound and
timing?

This is a Z80-vs-Z80 differential.  It never loads the engine.  Both sides are
the ORIGINAL, booted by tools/boot48.py through the identical key script from
the identical tape bytes, and differing only in whether block A's $7FFD paging
probe ran before block C landed on top of it:

    build/state_48k_ay.pkl    probe skipped -> ($FFFD) = $2A -> $BF21's 128K arm
    build/state_48k.pkl       probe ran     -> ($FFFD) = $00 -> $BF21's  48K arm

Both are anchored at PC=$ABA1 in pass $42 of dungeon 1, which is where
build/state_charsel.pkl was captured, so tools/sim_move.py's opening
step_to_loop_top() lands on the same pass top from all three.

    python tools/gate48.py points     the four direction tables, both branches
    python tools/gate48.py deep       a full per-pass state fingerprint
    python tools/gate48.py p2         the nine two-player scenarios
    python tools/gate48.py clock      frames per pass on both branches
    python tools/gate48.py all        everything

WHAT TO EXPECT, AND WHY IT IS NOT A RULE CHANGE.  $BF21 rewrites ten bytes and
all ten are in the sound region, so no gameplay rule differs.  But the beeper's
noise tick $B8CC is CALLed about 240 times a pass from the blitter, and the
game's random source is `LD A,R` -- the Z80 refresh register, i.e. a count of
M1 cycles ($B575, and $AC25/$AC4C directly).  Extra instructions per pass move
R, so every draw a pass consumes can differ.  The honest way to separate the
two effects is to run each scenario BOTH with the actor list live and with it
emptied, because with `--noactors` a pass consumes no draw at all.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, FRAME_T, TAPE_CALL_PC     # noqa: E402
from keyprobe import KEYS, keymask                                 # noqa: E402
from sim_move import step_to_loop_top, DIRKEY                      # noqa: E402
import p2gate                                                      # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
AY = os.path.join(ROOT, 'build', 'state_48k_ay.pkl')
K48 = os.path.join(ROOT, 'build', 'state_48k.pkl')
REF = os.path.join(ROOT, 'build', 'state_charsel.pkl')

REGIONS = [
    ('map    $8000..$83FF', 0x8000, 0x8400),
    ('players$8420..$845F', 0x8420, 0x8460),
    ('globals$8480..$84D0', 0x8480, 0x84D0),
    ('actors $5C00..$5F00', 0x5C00, 0x5F00),
]


def load(path):
    h = Harness()
    h.load_state(pickle.load(open(path, 'rb')))
    return h


def kill_actors(h):
    m = h.memobj.m
    m[0x8496] = 0
    m[0x8494], m[0x8495] = 0x00, 0x5C


def kill_generators(h):
    """Erase the generator cells, exactly as tools/soundgate.py's strict
    scenarios do: their spawn roll is $AA1D CALL $B575, i.e. LD A,R."""
    m = h.memobj.m
    for a in range(0x8000, 0x8400):
        if 0x20 <= m[a] <= 0x2E:
            m[a] = 0


# --------------------------------------------------------------- points
def table(path, direction, passes, noactors=False):
    """tools/sim_move.py's own table, from an arbitrary saved state."""
    h = load(path)
    if noactors:
        kill_actors(h)
    sel, bit = KM[DIRKEY[direction]]
    h.ports.press(sel, keymask(bit))
    step_to_loop_top(h)
    rows = []
    for i in range(passes):
        dt = step_to_loop_top(h)
        m = h.memobj.m
        rows.append((i + 1, m[0x8420], m[0x8421], m[0x843D], dt))
    return rows


def cmp_tables(a, b, na, nb):
    bad = [(x, y) for x, y in zip(a, b) if x[:4] != y[:4]]
    return bad


def cmd_points(counts=(30, 60, 100, 160)):
    print('POINT DIFFERENTIAL, 48K branch against AY branch (both the real Z80)')
    print('rows are (pass, x, y, pending-interaction); the frame cost is not '
          'compared')
    total = 0
    for noactors in (False, True):
        tag = 'actors OFF' if noactors else 'actors LIVE'
        print(f'\n  -- {tag} --')
        for d in ('right', 'left', 'up', 'down'):
            for n in counts:
                a = table(AY, d, n, noactors)
                b = table(K48, d, n, noactors)
                bad = cmp_tables(a, b, 'ay', '48k')
                total += len(bad)
                extra = ''
                if bad:
                    x, y = bad[0]
                    extra = (f'   first at pass {x[0]}: AY ({x[1]},{x[2]},'
                             f'${x[3]:02X}) vs 48K ({y[1]},{y[2]},${y[3]:02X})')
                fa = sum(r[4] for r in a) / n / FRAME_T
                fb = sum(r[4] for r in b) / n / FRAME_T
                print(f'  {d:>5} {n:>4} passes -> {len(bad):>3} mismatching '
                      f'rows   {fa:.3f} vs {fb:.3f} frames/pass{extra}')
    print(f'\n  TOTAL MISMATCHING ROWS: {total}')
    return total


# ---------------------------------------------------------------- deep
def cmd_deep(direction='down', passes=60):
    """Per-pass byte diff of the four gameplay regions.

    INHERITED differences are separated out.  The two boots already disagree
    at the anchor in 40 bytes of the actor list -- every one of them a byte at
    offset 3 of a 4-byte record, i.e. the `LD A,R` field $9BB7 fills in at
    level build -- so a diff at pass n only means something new if the address
    was not already differing at pass 0.
    """
    print(f'FULL PER-PASS STATE FINGERPRINT, holding {direction.upper()}, '
          f'{passes} passes')
    for noactors in (False, True):
        tag = 'actors OFF' if noactors else 'actors LIVE'
        ha, hb = load(AY), load(K48)
        if noactors:
            kill_actors(ha); kill_actors(hb)
        for h in (ha, hb):
            sel, bit = KM[DIRKEY[direction]]
            h.ports.press(sel, keymask(bit))
            step_to_loop_top(h)
        inherited = {}
        for name, lo, hi in REGIONS:
            inherited[name] = {a for a in range(lo, hi)
                               if ha.memobj.m[a] != hb.memobj.m[a]}
        first, worst, seen = {}, {}, {}
        for i in range(passes):
            step_to_loop_top(ha)
            step_to_loop_top(hb)
            for name, lo, hi in REGIONS:
                d = [a for a in range(lo, hi)
                     if ha.memobj.m[a] != hb.memobj.m[a]]
                new = [a for a in d if a not in inherited[name]]
                seen.setdefault(name, set()).update(new)
                if new:
                    worst[name] = max(worst.get(name, 0), len(new))
                    first.setdefault(name, (i + 1, new[:8]))
        print(f'\n  -- {tag} --')
        for name, lo, hi in REGIONS:
            inh = len(inherited[name])
            if name in first:
                p, addrs = first[name]
                extra = ''
                if name.startswith('actors'):
                    off = {}
                    for a in seen[name]:
                        off[(a - 0x5C00) & 3] = off.get((a - 0x5C00) & 3, 0) + 1
                    extra = ('   by record offset ' +
                             ' '.join(f'+{k}:{v}' for k, v in sorted(off.items())))
                print(f'  {name}: {inh} inherited; NEW from pass {p}, up to '
                      f'{worst[name]} at once, {len(seen[name])} distinct '
                      f'addresses' + extra)
                print('        first: ' + ' '.join(f'${a:04X}' for a in addrs))
            else:
                print(f'  {name}: {inh} inherited; NO NEW difference in '
                      f'{passes} passes')


# -------------------------------------------------------------- strict
def cmd_strict(direction='down', passes=60):
    """The strictest form: no actors and no generators, so the pass consumes
    NO `LD A,R` draw at all.  Anything that still differs here is the branch
    itself, not the entropy."""
    print(f'STRICT DIFFERENTIAL -- no actors, no generators, holding '
          f'{direction.upper()}, {passes} passes')
    ha, hb = load(AY), load(K48)
    for h in (ha, hb):
        kill_actors(h)
        kill_generators(h)
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
        step_to_loop_top(h)
    inherited = {}
    for name, lo, hi in REGIONS:
        inherited[name] = {a for a in range(lo, hi)
                           if ha.memobj.m[a] != hb.memobj.m[a]}
    first, seen = {}, {}
    for i in range(passes):
        step_to_loop_top(ha)
        step_to_loop_top(hb)
        for name, lo, hi in REGIONS:
            new = [a for a in range(lo, hi)
                   if ha.memobj.m[a] != hb.memobj.m[a]
                   and a not in inherited[name]]
            if new:
                seen.setdefault(name, set()).update(new)
                first.setdefault(name, (i + 1, new[:8]))
    for name, lo, hi in REGIONS:
        if name in first:
            p, addrs = first[name]
            print(f'  {name}: {len(inherited[name])} inherited; NEW from pass '
                  f'{p}: ' + ' '.join(f'${a:04X}' for a in sorted(seen[name])))
        else:
            print(f'  {name}: {len(inherited[name])} inherited; '
                  f'BIT-IDENTICAL for {passes} passes')


# --------------------------------------------------------------- drain
def cmd_drain(direction='down', passes=80):
    """Health against the pass counter and against the VIDEO FRAME counter.

    $B6DA drains one BCD point per 64 video frames ($8497, incremented in the
    ISR at $A2A2), not per pass.  If the tick GAP in frames is 64 on both
    branches while the PASS it lands on walks, the drain is the clock and not
    a rule -- and the player on a 48K loses health at the same rate in real
    time and a faster rate per pass.
    """
    for strict in (True, False):
        tag = ('no actors, no generators' if strict
               else 'actors and generators LIVE')
        print(f'\nTHE DRAIN, holding {direction.upper()}, {passes} passes '
              f'({tag})')
        out = {}
        for name, path in (('ay', AY), ('48k', K48)):
            h = load(path)
            if strict:
                kill_actors(h); kill_generators(h)
            sel, bit = KM[DIRKEY[direction]]
            h.ports.press(sel, keymask(bit))
            step_to_loop_top(h)
            rows = []
            for i in range(passes):
                step_to_loop_top(h)
                m = h.memobj.m
                rows.append((i + 1, m[0x8497], (m[0x8422] << 8) | m[0x8423]))
            out[name] = rows
        ticks = {'ay': [], '48k': []}
        for name in ('ay', '48k'):
            rows = out[name]
            for i in range(1, passes):
                if rows[i][2] != rows[i - 1][2]:
                    ticks[name].append((rows[i][0], rows[i][1]))
        for name in ('ay', '48k'):
            fr = [f for _, f in ticks[name]]
            gaps = [(fr[i] - fr[i - 1]) & 0xFF for i in range(1, len(fr))]
            print(f'  {name:>3}: ticks at pass '
                  f'{[p for p, _ in ticks[name]]}, $8497 = {fr}, gaps {gaps}')
        pa = [p for p, _ in ticks['ay']]
        pb = [p for p, _ in ticks['48k']]
        print(f'  pass drift 48K - AY: '
              f'{[b - a for a, b in zip(pa, pb)]}')
        print(f'  final health: AY ${out["ay"][-1][2]:04X} after '
              f'{out["ay"][-1][1]} frames, 48K ${out["48k"][-1][2]:04X} after '
              f'{out["48k"][-1][1]} frames')


# ----------------------------------------------------------------- arm
ARM_BYTES = (0xB8B5, 0xB8CC, 0xBA01, 0xBA2B, 0xBA2C, 0xBA2D, 0xBADB,
             0xBBA7, 0xBBBC, 0xFFFD)


def arm_48k(h):
    """Turn a running AY machine into the 48K one WITHOUT re-booting, by
    transplanting the ten bytes $BF21 owns from the state that a real 48K boot
    produced (build/state_48k.pkl).

    Why not simply CALL $BF21 here.  $BF21 is one-shot boot code and the game
    REUSES $BF00..$BFFF as live data during play -- disassembling the live dump
    at $BF21 gives sprite bitmaps, not the patcher -- so calling it in a
    running machine executes graphics.  (Measured: it "returns" after 11
    instructions having patched nothing.)  The ten values are therefore taken
    from a genuine 48K boot rather than computed here.

    This is the ISOLATION experiment for the branch: every other byte of the
    machine -- including the video-frame counter $8497 -- starts identical, so
    anything that then diverges is those ten bytes and nothing else.
    """
    src = pickle.load(open(K48, 'rb'))[0]
    m = h.memobj.m
    for a in ARM_BYTES:
        m[a] = src[a]


def cmd_arm(direction='down', passes=60):
    print('ISOLATION: ONE machine, the two $BF21 arms, nothing else changed')
    print('  (build/state_48k_ay.pkl, with $BF21\'s ten bytes transplanted')
    print('   from the real 48K boot -- see arm_48k() for why not a CALL)')
    for strict in (True, False):
        ha, hb = load(AY), load(AY)
        arm_48k(hb)
        m = hb.memobj.m
        print('\n  patched bytes now ' +
              ' '.join(f'${a:04X}=${m[a]:02X}' for a in ARM_BYTES))
        d0 = [a for a in range(0x4000, 0x10000)
              if ha.memobj.m[a] != hb.memobj.m[a]]
        print(f'  the two machines now differ in exactly {len(d0)} bytes: ' +
              ' '.join(f'${a:04X}' for a in d0))
        for h in (ha, hb):
            if strict:
                kill_actors(h); kill_generators(h)
            sel, bit = KM[DIRKEY[direction]]
            h.ports.press(sel, keymask(bit))
            step_to_loop_top(h)
        base = set(d0)
        rows_a, rows_b = [], []
        newdiff = {}
        for i in range(passes):
            fa = step_to_loop_top(ha)
            fb = step_to_loop_top(hb)
            rows_a.append((ha.memobj.m[0x8420], ha.memobj.m[0x8421], fa))
            rows_b.append((hb.memobj.m[0x8420], hb.memobj.m[0x8421], fb))
            for name, lo, hi in REGIONS:
                new = [a for a in range(lo, hi)
                       if ha.memobj.m[a] != hb.memobj.m[a] and a not in base]
                if new:
                    newdiff.setdefault(name, (i + 1, set()))[1].update(new)
        tag = 'no actors, no generators' if strict else 'actors LIVE'
        print(f'  -- {tag}, holding {direction.upper()}, {passes} passes --')
        pos = sum(1 for a, b in zip(rows_a, rows_b) if a[:2] != b[:2])
        print(f'     player position rows differing: {pos}/{passes}')
        print(f'     frames/pass  AY {sum(r[2] for r in rows_a)/passes/FRAME_T:.3f}'
              f'   48K {sum(r[2] for r in rows_b)/passes/FRAME_T:.3f}')
        for name, lo, hi in REGIONS:
            if name in newdiff:
                p, s = newdiff[name]
                print(f'     {name}: NEW from pass {p}, {len(s)} addresses: ' +
                      ' '.join(f'${a:04X}' for a in sorted(s)[:16]))
            else:
                print(f'     {name}: BIT-IDENTICAL for {passes} passes')


# ------------------------------------------------------------------ p2
FIELD = (['p1x', 'p1y', 'p1hp', 'p1score', 'p1keys', 'p1potions', 'p1f11',
          'p1+14', 'p1pend', 'p1shot'] +
         ['p2x', 'p2y', 'p2hp', 'p2score', 'p2keys', 'p2potions', 'p2f11',
          'p2+14', 'p2pend', 'p2shot'] +
         ['camx', 'camy', 'tgtx', 'tgty'])


def armed_state():
    """build/state_48k_arm.pkl -- the AY state with $BF21's ten bytes swapped
    for the 48K arm and NOTHING else touched.  Written so that p2gate, which
    boots from a pickle, can run its scenarios on it."""
    h = load(AY)
    arm_48k(h)
    path = os.path.join(ROOT, 'build', 'state_48k_arm.pkl')
    pickle.dump(h.save_state(), open(path, 'wb'))
    return path


def cmd_p2(names=None, armed=False):
    print('TWO-PLAYER SCENARIOS, 48K branch against AY branch'
          + (' (ISOLATED: ten bytes only)' if armed else ''))
    names = names or tuple(p2gate.SCEN)
    other = armed_state() if armed else K48
    total = 0
    for name in names:
        out = {}
        for tag, path in (('ay', AY), ('48k', other)):
            p2gate.STATE = path
            rows, seed = p2gate.run_original(name)
            out[tag] = (rows, seed)
        a, b = out['ay'][0], out['48k'][0]
        bad = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
        total += len(bad)
        note = ''
        if out['ay'][1]['clock'] != out['48k'][1]['clock']:
            note = '   (video-frame clock differs)'
        fields = {}
        for i, x, y in bad:
            for j, (u, v) in enumerate(zip(x, y)):
                if u != v:
                    fields[FIELD[j]] = fields.get(FIELD[j], 0) + 1
        print(f'  {name:>7}: {len(a):>3} rows, {len(bad):>3} differing{note}'
              + ('   fields: ' + ' '.join(f'{k} x{v}'
                                          for k, v in sorted(fields.items()))
                 if fields else ''))
        for i, x, y in bad[:2]:
            print(f'        AY  {p2gate.fmt(i + 1, x)}')
            print(f'        48K {p2gate.fmt(i + 1, y)}')
    p2gate.STATE = REF
    print(f'\n  TOTAL DIFFERING ROWS: {total}')
    return total


# --------------------------------------------------------------- clock
def cmd_clock(passes=16):
    print(f'FRAMES PER PASS, {passes} passes holding DOWN')
    for tag, path in (('AY  ($FFFD)=$2A', AY), ('48K ($FFFD)=$00', K48)):
        for noactors in (False, True):
            rows = table(path, 'down', passes, noactors)
            fr = [r[4] / FRAME_T for r in rows]
            hist = {}
            for f in fr:
                hist[round(f, 2)] = hist.get(round(f, 2), 0) + 1
            print(f'  {tag}  {"actors OFF " if noactors else "actors LIVE"}'
                  f'  mean {sum(fr)/len(fr):.3f} frames/pass '
                  f'= {50.08*len(fr)/sum(fr):.2f} passes/s   '
                  + ' '.join(f'{k}:{v}' for k, v in sorted(hist.items())))


# ---------------------------------------------------------------- port
def cmd_port(counts=(30, 60, 100, 160)):
    """The SHIPPED ENGINE against the 48K branch, tools/pointdiff.py's own
    comparison but with the original driven from build/state_48k.pkl.

    The engine is unchanged and knows nothing about the branch; if it still
    matches row for row, the port's gameplay is as correct on a 48K as it was
    on the 128K it was accidentally measured against.
    """
    import re
    import subprocess
    ROW = re.compile(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*?)\s*$')
    print('THE SHIPPED ENGINE against the ORIGINAL ON THE 48K BRANCH')
    total = 0
    for d in ('right', 'left', 'up', 'down'):
        for n in counts:
            a = table(K48, d, n)
            out = subprocess.run(f'node tools/headless.js --table {d} {n}',
                                 shell=True, capture_output=True, text=True,
                                 cwd=ROOT).stdout
            b = []
            for line in out.splitlines():
                mm = ROW.match(line)
                if mm:
                    b.append((int(mm.group(1)), int(mm.group(2)),
                              int(mm.group(3)), mm.group(4)))
            want = [(r[0], r[1], r[2],
                     '-' if not r[3] else f'interact ${r[3]:02x}') for r in a]
            if len(b) != n:
                print(f'  {d:>5} {n:>4}: ENGINE ROW COUNT {len(b)} != {n}')
                total += n
                continue
            bad = [(x, y) for x, y in zip(want, b) if x != y]
            total += len(bad)
            print(f'  {d:>5} {n:>4} rows -> {len(bad)} mismatching')
            for x, y in bad[:3]:
                print(f'        orig {x}   engine {y}')
    print(f'\n  TOTAL MISMATCHING ROWS: {total}')
    return total


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if cmd == 'port':
        cmd_port()
        return
    if cmd in ('points', 'all'):
        cmd_points((30, 60) if cmd == 'all' else (30, 60, 100, 160))
    if cmd in ('deep', 'all'):
        print()
        cmd_deep()
    if cmd in ('strict', 'all'):
        print()
        cmd_strict()
    if cmd in ('arm', 'all'):
        print()
        cmd_arm()
    if cmd in ('drain', 'all'):
        print()
        cmd_drain()
    if cmd in ('clock', 'all'):
        print()
        cmd_clock()
    if cmd in ('p2', 'all'):
        print()
        cmd_p2()
    if cmd in ('p2arm', 'all'):
        print()
        cmd_p2(armed=True)


if __name__ == '__main__':
    main()
