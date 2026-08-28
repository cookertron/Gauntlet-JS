#!/usr/bin/env python3
"""
beeper48.py -- PHASE 11.1..11.7 for the **48K BEEPER** driver, read in its
PATCHED state and measured, never hand-counted (manual 11.4/E3).

    python tools/beeper48.py arm       show/verify $BF21's 48K arm
    python tools/beeper48.py ids       what each of the 18 ids does on 48K
    python tools/beeper48.py fit       the tone model, swept over 0..255
    python tools/beeper48.py gate      THE GATE: every pair in every stream,
                                       edge COUNT and half-periods asserted
    python tools/beeper48.py noise     the noise predicate, verified in play
    python tools/beeper48.py arch      11.3's discriminator: the per-frame
                                       first->last speaker-write span
    python tools/beeper48.py clock     who calls what, and how often
    python tools/beeper48.py cost      what the driver costs the game clock
    python tools/beeper48.py tunes     the two BLOCKING tunes $B8B0/$B8B5
    python tools/beeper48.py all       everything

=============================================================================
THE STATE THIS MEASURES                                    (manual 6.2/E2)
=============================================================================
build/state_charsel.pkl was captured with RAM ($FFFD) = $2A, i.e. on the
128K/AY arm.  $BF21 does not survive into the dungeon -- by then the front
end's code at $BF21 has been overwritten by graphics -- so the 48K arm cannot
be re-run there.  Instead the arm was READ OUT OF THE REAL MACHINE: boot from
$8400, stop at $BEB9 (the CALL $BF21 itself), snapshot, and run $BF21 to
$BEBC once with ($FFFD)=0 and once with ($FFFD)=1.  ARM_48K below is what the
game's own code wrote.  `python tools/beeper48.py arm` re-derives it from a
live boot and asserts the table, so the table can never drift.

Applying it is the game's own boot-time self-modification, not a measurement
patch: nothing here byte-patches the image to make a breakpoint (Q3/6.6).
=============================================================================
"""
import bisect
import os
import pickle
import statistics
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, R, SP, IFF, A as REG_A,          # noqa
                     TAPE_CALL_PC, FRAME_T, CPU_HZ)
from keyprobe import KEYS, keymask                                     # noqa

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D', 'fire': '0'}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')

LOOP_TOP = 0x8503          # main-loop top, visited exactly once per pass
NOISE = 0xB8CC             # the noise tick, called from six blitter sites
TONE = 0xB8FB              # the tone tick, called once per pass from $9CD9
TRIG = 0xBA2B              # the 23 call sites' entry; the 48K arm makes it
                           # JP $B92B
CNT = 0x84CF               # (IY+$50)  steps left in the tone stream
PTR = 0x84D0               # (IY+$51)  tone stream pointer
LEVEL = 0x84D2             # (IY+$53)  noise level AND its own ramp counter
SHADOW = 0x84CA            # the port-$FE shadow: border(0-2) MIC(3) SPKR(4)
RAMPOP = 0xB8E2            # self-modified: $3C INC A (up) / $3D DEC A (down)
SCRATCH = 0x5B00           # printer buffer: staging for an isolated pair

OUT_SITES = {0xB8DB: 'noise', 0xB91E: 'tone', 0xB4FC: 'border', 0x923C: 'tape'}

# $BF21's own output, read off the machine (see the header).
UNPATCHED = {0xB8B5: 0x21, 0xB8CC: 0xED, 0xBA01: 0x3E, 0xBA2B: 0xC5,
             0xBA2C: 0xD5, 0xBA2D: 0xE5, 0xBADB: 0xD5, 0xBBA7: 0x3E,
             0xBBBC: 0x3E}
ARM_48K = {0xB8B5: 0x21, 0xB8CC: 0xED, 0xBA01: 0xC9, 0xBA2B: 0xC3,
           0xBA2C: 0x2B, 0xBA2D: 0xB9, 0xBADB: 0xC9, 0xBBA7: 0xC9,
           0xBBBC: 0xC9}
ARM_128K = {0xB8B5: 0xC9, 0xB8CC: 0xC9, 0xBA01: 0x3E, 0xBA2B: 0xC5,
            0xBA2C: 0xD5, 0xBA2D: 0xE5, 0xBADB: 0xD5, 0xBBA7: 0x3E,
            0xBBBC: 0x3E}

# the nine tone streams the 48K dispatcher $B92B points at ($B98B stores the
# FIRST byte as the step count and the pointer as byte+1)
STREAMS = {0x02: 0xB995, 0x06: 0xB9E5, 0x07: 0xB9A2, 0x08: 0xB9AF,
           0x0A: 0xB9BA, 0x0B: 0xB9C3, 0x0E: 0xB9DA, 0x0F: 0xB9E5,
           0x11: 0xB9F8}


# ---------------------------------------------------------------- the model
def half_period(p):
    """$B918 LD B,E / NOP / DJNZ, one half cycle.  p == 0 means 256."""
    return 17 * (p or 256) + 31


def burst_cost(c, p):
    """$B8FB, one (count, half-period) pair.  c == 0 means 256."""
    return 152 + (c or 256) * half_period(p)


IDLE_COST = 12811          # $B901 DEC HL from $01EB, measured


# ------------------------------------------------------------------ harness
def fresh48(state=STATE):
    """The live dungeon-1 state carrying $BF21's 48K arm."""
    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    for a, v in ARM_128K.items():
        assert h.peek(a)[0] == v, f'${a:04X} is not the 128K arm'
    for a, v in ARM_48K.items():
        h.poke(a, v)
    h.poke(0xFFFD, 0x00)
    return h


def fresh_ay(state=STATE):
    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    return h


def press(h, name):
    if name:
        sel, bit = KM[DIRKEY[name]]
        h.ports.press(sel, keymask(bit))


def hook(h):
    """Record (T, port, value, PC-of-the-OUT) for every port write."""
    h.ports.writes = []
    h.ports.record_writes = True

    def wp(registers, port, value):
        h.ports.writes.append((registers[T], port, value, registers[PC]))
    h.ports.write_port = wp
    h.sim.set_tracer(h.ports)
    return h


def fe(h, site=None):
    return [(t, v, pc) for t, p, v, pc in h.ports.writes
            if (p & 0xFF) == 0xFE and (site is None or pc == site)]


class Run:
    """A stepping loop with PC hooks.  Breakpoints are PC comparisons."""

    def __init__(self, h):
        self.h = h
        self.noise = []          # (T, A-as-LD-A,R-would-read, level, caller)
        self.tone = []           # (T, count, ptr)
        self.passes = []

    def pass_(self, limit=8_000_000):
        h = self.h
        sim = h.sim
        regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
        fd, ia = h.frame_duration, h.int_active
        t0 = regs[T]
        n = 0
        while n < limit:
            pc = regs[PC]
            if n and pc == LOOP_TOP:
                self.passes.append(regs[T])
                return regs[T] - t0
            if pc == NOISE:
                rv = regs[R]
                sp = regs[SP]
                self.noise.append((regs[T], (rv & 0x80) + ((rv + 2) % 128),
                                   mem[LEVEL], (mem[sp] | mem[sp + 1] << 8) - 3))
            elif pc == TONE:
                self.tone.append((regs[T], mem[CNT],
                                  mem[PTR] | mem[PTR + 1] << 8))
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape()
                n += 1
                continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt()
                n += 1
                continue
            opcodes[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
            n += 1
        raise RuntimeError(f'pass did not end: PC=${regs[PC]:04X}')


def inject(h, sid):
    """Fire an effect the way the game's own call sites do (CALL $BA2B with
    the id in A), then hand the machine back untouched: h.call lands on a
    sentinel and leaves SP low, and the trigger's whole effect is in RAM."""
    saved = list(h.regs)
    rc, dt, _ = h.call(TRIG, regs={'A': sid})
    assert rc == 0, f'trigger exit {rc}'
    t = h.regs[T]
    h.regs[:] = saved
    h.regs[T] = t
    return dt


def drive(key='down', passes=10, trigger=None, at=2):
    h = fresh48()
    press(h, key)
    hook(h)
    r = Run(h)
    r.pass_()
    h.ports.writes = []
    t0 = h.regs[T]
    for i in range(passes):
        if trigger is not None and i == at:
            inject(h, trigger)
        r.pass_()
    return h, r, t0


def play(h, c, p, shadow=0x00):
    """One $B8FB with the pair (c, p) staged in scratch RAM (manual 6.6)."""
    h.poke(SCRATCH, c, p)
    h.poke(CNT, 1)
    h.poke(PTR, SCRATCH & 0xFF, SCRATCH >> 8)
    h.poke(SHADOW, shadow)
    hook(h)
    rc, dt, _ = h.call(TONE)
    assert rc == 0, f'B8FB exit {rc}'
    e = [(t, v, (v >> 4) & 1) for t, v, pc in fe(h) if pc == 0xB91E]
    return e, dt


# ------------------------------------------------------------------ commands
def cmd_arm():
    """Re-derive $BF21's two arms from a live boot and assert the tables."""
    h = Harness()
    reason, n = h.run_until(0xBEB9, limit=2_000_000)
    assert reason == 'target', reason
    print(f'  booted to $BEB9 (CALL $BF21) in {n} instructions; '
          f'RAM ($FFFD) = ${h.peek(0xFFFD)[0]:02X} (the loader stub\'s padding)')
    pre = {a: h.peek(a)[0] for a in ARM_48K}
    assert pre == UNPATCHED, pre
    snap = h.save_state()
    for mode, want, name in ((0x00, ARM_48K, '48K/beeper'),
                             (0x01, ARM_128K, '128K/AY')):
        h.load_state(snap)
        h.poke(0xFFFD, mode)
        h.run_until(0xBEBC, limit=200_000)
        got = {a: h.peek(a)[0] for a in ARM_48K}
        assert got == want, (mode, got)
        print(f'  ($FFFD)=${mode:02X} -> {name:11s} ' +
              ' '.join(f'${a:04X}=${v:02X}' for a, v in sorted(got.items())))
    print('  both arms match the tables in this file.')
    h48, hay = fresh48(), fresh_ay()
    d = [a for a in range(0x4000, 0x10000) if h48.peek(a)[0] != hay.peek(a)[0]]
    print(f'  live 48K state vs live AY state: {len(d)} bytes differ: ' +
          ' '.join(f'${a:04X}' for a in d))


def cmd_ids():
    print('  id  steps   ptr   level  ramp   what')
    for sid in range(0x12):
        h = fresh48()
        h.poke(CNT, 0)
        h.poke(PTR, 0, 0)
        h.poke(LEVEL, 0)
        h.poke(RAMPOP, 0x00)
        rc, dt, _ = h.call(TRIG, regs={'A': sid})
        cnt, ptr, lvl, op = (h.peek(CNT)[0], h.peek16(PTR),
                             h.peek(LEVEL)[0], h.peek(RAMPOP)[0])
        if cnt:
            what = f'TONE   {cnt} steps'
        elif lvl:
            what = 'NOISE  ramp ' + ('UP' if op == 0x3C else 'DOWN')
        else:
            what = 'SILENT'
        print(f'  ${sid:02X}  {cnt:5d}  ${ptr:04X}   ${lvl:02X}    ${op:02X}   '
              f'{what}  ({dt} T)')


def cmd_fit():
    h = fresh48()
    snap = h.save_state()
    print('  p    edges  half-period(T)  model 17p+31  total(T)  model  Hz')
    bad = 0
    for p in list(range(0, 18)) + [0x20, 0x3F, 0x40, 0x7F, 0x80, 0xC0, 0xFE, 0xFF]:
        h.load_state(snap)
        e, dt = play(h, 8, p)
        gaps = set(e[i + 1][0] - e[i][0] for i in range(len(e) - 1))
        want = half_period(p)
        ok = gaps == {want} and len(e) == 8 and dt == burst_cost(8, p)
        bad += not ok
        print(f'  ${p:02X}   {len(e):5d}  {sorted(gaps)!s:14s}  {want:11d}  '
              f'{dt:8d}  {burst_cost(8, p):6d}  {CPU_HZ / (2 * want):8.1f}'
              f'{"" if ok else "   MISMATCH"}')
    print(f'  {bad} mismatching')
    # the wrap, and what the shadow does to the border
    for c in (0, 1, 2, 255):
        h.load_state(snap)
        e, dt = play(h, c, 0x10)
        assert len(e) == (c or 256), (c, len(e))
    print('  edge count == (c or 256) for c in 0,1,2,255: OK')
    h.load_state(snap)
    e, dt = play(h, 4, 8, shadow=0x47)
    print('  $84CA=$47 -> OUTs ' + ' '.join(f'${v:02X}' for _, v, _ in e) +
          f' -> $84CA=${h.peek(SHADOW)[0]:02X}   '
          '(bits 0-2 border and bit 3 MIC are preserved verbatim)')


def cmd_gate():
    """THE GATE (manual 11.5): edge COUNT and half-periods, whole data set."""
    h = fresh48()
    snap = h.save_state()
    bad = pairs = edges = 0
    for sid in sorted(STREAMS):
        a = STREAMS[sid]
        n = h.memobj.m[a]
        row = []
        for i in range(n):
            c, p = h.memobj.m[a + 1 + 2 * i], h.memobj.m[a + 2 + 2 * i]
            h.load_state(snap)
            e, dt = play(h, c, p)
            gaps = set(e[j + 1][0] - e[j][0] for j in range(len(e) - 1))
            ok = (len(e) == (c or 256) and gaps in ({half_period(p)}, set())
                  and dt == burst_cost(c, p))
            bad += not ok
            pairs += 1
            edges += len(e)
            row.append(f'{"" if ok else "BAD "}({c},${p:02X})={len(e)}e@'
                       f'{half_period(p)}T')
        print(f'  id ${sid:02X}: ' + ' '.join(row))
    print(f'\n  {pairs} pairs, {edges} edges, {bad} mismatching')
    print('  extremes and the wrap (not reachable from the data):')
    for c, p in ((1, 1), (1, 0), (0, 1), (0, 0), (255, 255)):
        h.load_state(snap)
        e, dt = play(h, c, p)
        gaps = set(e[i + 1][0] - e[i][0] for i in range(len(e) - 1))
        ok = len(e) == (c or 256) and dt == burst_cost(c, p) and \
            gaps in ({half_period(p)}, set())
        print(f'    c=${c:02X} p=${p:02X}: {len(e):3d} edges (want '
              f'{c or 256}), half-period {sorted(gaps) or "n/a"} (want '
              f'{half_period(p)}), {dt} T (want {burst_cost(c, p)})  '
              f'{"OK" if ok else "MISMATCH"}')
    return bad


def cmd_noise():
    """The predicate, verified against the real OUTs over a whole burst."""
    for sid in (0x00, 0x04):
        h, r, t0 = drive('down', 8, sid, 2)
        outs = sorted(t for t, v, pc in fe(h, 0xB8DB))
        act = [c for c in r.noise if c[2]]
        ok = bad = 0
        for i, (t, a, lvl, _) in enumerate(act):
            nxt = act[i + 1][0] if i + 1 < len(act) else t + 10 ** 9
            hit = bisect.bisect_left(outs, t) < bisect.bisect_left(outs, nxt)
            ok, bad = (ok + 1, bad) if hit == (a < lvl) else (ok, bad + 1)
        span = outs[-1] - outs[0]
        gaps = [b - a for a, b in zip(outs, outs[1:])]
        print(f'  id ${sid:02X}: {len(act)} ticks with level != 0, '
              f'{len(outs)} toggles; predicate  A < ($84D2)  matches {ok}, '
              f'mismatches {bad}')
        print(f'         burst spans {span / FRAME_T:.3f} frames = '
              f'{span / CPU_HZ * 1000:.1f} ms, all inside ONE pass; '
              f'gap min {min(gaps)}T median {sorted(gaps)[len(gaps) // 2]}T '
              f'max {max(gaps)}T')
        d = Counter((b[1] - a[1]) % 128 for a, b in zip(act, act[1:]))
        print(f'         step in the LD A,R value between ticks: '
              f'{dict(sorted(d.items(), key=lambda kv: -kv[1])[:4])}   '
              f'bit 7 always 0: {all(a[1] < 0x80 for a in act)}')
    # the ramps, enumerated under their own guard (manual 11.9)
    for op, name, start in ((0x3C, 'id $00 INC', 0x01), (0x3D, 'id $04 DEC', 0x7F)):
        h = fresh48()
        h.poke(RAMPOP, op)
        h.poke(LEVEL, start)
        seq = [start]
        for _ in range(400):
            h.call(NOISE)
            v = h.peek(LEVEL)[0]
            if v == seq[-1]:
                break
            seq.append(v)
        print(f'  {name}: {len(seq)} levels ${seq[0]:02X}..${seq[-2]:02X} then '
              f'${seq[-1]:02X} = silence; {len(seq) - 1} ticks to silence; '
              f'reachable set = 0..127')


def cmd_arch():
    """11.3: the per-frame first->last speaker-write span."""
    print(f'  span between the FIRST and LAST $FE write in each {FRAME_T}-T '
          'frame:')
    for name, trig in (('silent', None), ('tone id $0F', 0x0F),
                       ('tone id $02', 0x02), ('noise id $00', 0x00),
                       ('noise id $04', 0x04)):
        h, r, t0 = drive('down', 14, trig, 3)
        frames = defaultdict(list)
        for t, v, pc in fe(h):
            frames[t // FRAME_T].append(t)
        spans = Counter(round((v[-1] - v[0]) / FRAME_T, 2) for v in frames.values())
        counts = Counter(len(v) for v in frames.values())
        print(f'   [{name:11s}] writes/frame {dict(sorted(counts.items()))}')
        print(f'                  span/frame  {dict(sorted(spans.items()))}')
    print('  (every frame carries exactly one border write from the ISR;')
    print('   a tone burst is 0.16..0.24 frames and NEVER spans a frame,')
    print('   a noise burst is ~0.46 frames and never leaves its own pass.)')


def cmd_clock():
    h, r, t0 = drive('down', 8, None, 0)
    print(f'  $B8FB: {len(r.tone)} calls over {len(r.passes)} passes '
          f'(one per pass, from $9CD9: HALT / DI / CALL)')
    c = Counter(a for _, _, _, a in r.noise)
    print(f'  $B8CC: {len(r.noise)} calls over 8 passes '
          f'({len(r.noise) / 8:.0f}/pass), from six sites inside the blitter:')
    for a, n in sorted(c.items()):
        print(f'          ${a:04X}  {n:5d}  ({n / 8:.0f}/pass)')
    pb = [t0] + r.passes
    lo, hi = pb[1], pb[2]
    cs = [t for t, _, _, _ in r.noise if lo <= t < hi]
    dec = Counter(int(10 * (t - lo) / (hi - lo)) for t in cs)
    print(f'  the {len(cs)} ticks of one pass, per tenth of the pass: '
          f'{[dec.get(i, 0) for i in range(10)]}')
    print('  -> every tick falls in the first third of the pass (the draw),')
    print('     so a noise effect is over before the pass is.')
    print(f'  the ISR ($DADA -> $A29F) calls $BADB, which the 48K arm has '
          'made a RET: no sound work in the interrupt at all.')


def cmd_cost():
    def run(h, key, n=16, trigger=None, at=4):
        press(h, key)
        r = Run(h)
        r.pass_()
        lens = []
        for i in range(n):
            if trigger is not None and i == at:
                inject(h, trigger)
            lens.append(r.pass_())
        return lens

    def show(label, lens):
        fr = [round(x / FRAME_T, 2) for x in lens]
        print(f'  {label:32s} {statistics.mean(fr):6.3f} frames/pass  '
              f'{CPU_HZ / statistics.mean(lens):5.2f} passes/s  '
              f'{dict(sorted(Counter(fr).items()))}')

    for key in ('down', None):
        print(f'  key held: {key}')
        show('AY (128K arm)', run(fresh_ay(), key))
        show('48K beeper', run(fresh48(), key))
        h = fresh48()
        h.poke(0xB8CC, 0xC9)
        show('48K, ablate $B8CC := C9', run(h, key))
        h = fresh48()
        h.poke(0xB8FB, 0xC9)
        show('48K, ablate $B8FB := C9', run(h, key))
        show('48K, noise id $00 playing', run(fresh48(), key, trigger=0x00))
        show('48K, tone id $0F playing', run(fresh48(), key, trigger=0x0F))
        print()
    print(f'  $B8FB idle path ($B901, DEC HL from $01EB): {IDLE_COST} T = '
          f'{IDLE_COST / FRAME_T:.3f} frames')
    print(f'  a real burst: {burst_cost(10, 0x3F)}..{burst_cost(200, 3)} T = '
          f'{burst_cost(10, 0x3F) / FRAME_T:.3f}..'
          f'{burst_cost(200, 3) / FRAME_T:.3f} frames')
    print('  -> the idle delay is the same size as a burst ON PURPOSE: '
          '(30,$17) costs 12812 T against the idle 12811.')


def cmd_tunes():
    for entry, src, what in ((0xB8B0, 0x6740, 'banner  (bit 5 clear)'),
                             (0xB8B5, 0x685D, 'level intro (bit 5 set)')):
        h = fresh48()
        hook(h)
        rc, dt, steps = h.call(entry, limit=60_000_000)
        w = fe(h)
        pcs = Counter(pc for _, _, pc in w)
        gaps = Counter(b[0] - a[0] for a, b in zip(w, w[1:]))
        chg, prev = [], None
        for t, v, pc in w:
            b = (v >> 4) & 1
            if prev is None or b != prev:
                chg.append(t)
            prev = b
        cg = Counter(b - a for a, b in zip(chg, chg[1:]))
        notes = [(g, n) for g, n in cg.most_common(20) if g > 1000]
        print(f'  ${entry:04X} {what}: {dt} T = {dt / FRAME_T:.2f} frames = '
              f'{dt / CPU_HZ:.2f} s, {len(w)} speaker writes, '
              f'{len(chg)} LEVEL CHANGES')
        print(f'        blob: $13E bytes LDIRed from ${src:04X} to $C000, '
              f'$C010..$C013 filled with $97 (SUB A) so the play loop cannot '
              f'exit early')
        print(f'        OUT sites: ' +
              ' '.join(f'${a:04X}:{n}' for a, n in sorted(pcs.items())) +
              f'; write-to-write {gaps.most_common(1)[0][0]}T, i.e. two OUTs '
              f'per 96-T loop = 36.46 kHz per channel slot')
        # 11.3(c): the notes CANNOT be read off the combined edge train --
        # channel 1's edges swamp it.  Sample the two counter reloads at
        # $C081 (LD IXH,D) instead, which is where both are live.
        h2 = fresh48()
        h2.regs[PC] = entry
        h2.run_until(0xC000, limit=100_000, interrupts=False)
        sim = h2.sim
        regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
        seen = []
        n = 0
        while n < 3_000_000 and regs[PC] != 0xC017:
            if regs[PC] == 0xC081:
                k = (mem[0xC018], mem[0xC019], regs[4], regs[6])   # D, H
                if not seen or seen[-1] != k:
                    seen.append(k)
            opcodes[mem[regs[PC]]]()
            n += 1
        print(f'        {len(seen)} note pairs; (note1,note2)->(reload1,'
              'reload2)->Hz:')
        for n1, n2, d, hh in seen[:6]:
            print(f'          (${n1:02X},${n2:02X}) -> ({d:3d},{hh:3d}) -> '
                  f'{CPU_HZ / 96 / (2 * d):8.1f} / {CPU_HZ / 96 / (2 * hh):7.1f} Hz'
                  + ('   [reload 1 = the REST marker, and it is NOT muted: '
                     'it plays an 18.2 kHz square]' if 1 in (d, hh) else ''))
    print('  two down-counters (E reloaded from IXH, L from H) and two speaker')
    print('  states (A and A\'), interleaved in one 96-T loop with an OUT for')
    print('  each: manual 11.3 case (c), a two-voice engine.  A channel\'s')
    print('  pitch is 36458.33 / (2 * reload) Hz; the reloads come from a')
    print('  53-entry chromatic table at $C0D4 (fitted step 100.4 cents,')
    print('  ~71..1519 Hz) indexed by (note + 12).')
    print('  DI at $C00C, EI at $C016 -- the whole tune runs with interrupts')
    print('  OFF, so the game\'s own frame counter does not advance.')
    for name, mk in (('AY (128K arm)', fresh_ay), ('48K (beeper arm)', fresh48)):
        for bit5 in (0, 1):
            h = mk()
            v = h.peek(0x847D)[0] | 0x04
            v = (v | 0x20) if bit5 else (v & ~0x20)
            h.poke(0x847D, v)
            f0 = h.peek(0x8497)[0]
            rc, dt, _ = h.call(0x9D01, limit=60_000_000, interrupts=True)
            f1 = h.peek(0x8497)[0]
            print(f'  $9D01 {name:18s} bit5={bit5}: {dt / FRAME_T:7.2f} wall '
                  f'frames, ISR frame counter $8497 +{(f1 - f0) % 256}')


def cmd_all():
    for name, fn in (('ARM', cmd_arm), ('IDS', cmd_ids), ('FIT', cmd_fit),
                     ('GATE', cmd_gate), ('NOISE', cmd_noise),
                     ('ARCH', cmd_arch), ('CLOCK', cmd_clock),
                     ('COST', cmd_cost), ('TUNES', cmd_tunes)):
        print(f'\n=== {name} ' + '=' * (68 - len(name)))
        fn()


if __name__ == '__main__':
    c = sys.argv[1] if len(sys.argv) > 1 else 'all'
    {'arm': cmd_arm, 'ids': cmd_ids, 'fit': cmd_fit, 'gate': cmd_gate,
     'noise': cmd_noise, 'arch': cmd_arch, 'clock': cmd_clock,
     'cost': cmd_cost, 'tunes': cmd_tunes, 'all': cmd_all}.get(
        c, lambda: print(__doc__))()
