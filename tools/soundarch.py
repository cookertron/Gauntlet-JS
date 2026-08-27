#!/usr/bin/env python3
"""
soundarch.py -- PHASE 11.3: decide the SOUND ARCHITECTURE by measurement.

Drives the real Z80 from build/state_charsel.pkl and measures, per main-loop
pass ($8503, the one-per-pass sampler):

  * every OUT (T-state, port, value), split into AY ($FFFD/$BFFD) and
    ULA ($xxFE, speaker bit 4 of the value);
  * entry/exit T-state cost of every sound entry point, with its caller;
  * which entries run inside the 50 Hz ISR ($DADA -> $A29F) and which run
    synchronously from the main loop;
  * the AY channel/tune state block $BDB1..$BDEC as it changes.

Sub-commands:
    trace  <dir> <passes>      port + cost trace over driven play
    costs  <dir> <passes>      T-state cost histogram per sound entry
    regs   <dir> <passes>      per-50Hz-frame AY register dump
    beeper <dir> <passes>      same, with the 48K beeper build selected
    fanfare                    isolate the $9D0A blocking block
    sfx    <id>                call $BA2B / $B92B in isolation with one id
"""
import os
import pickle
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, SP, TAPE_CALL_PC, FRAME_T, CPU_HZ  # noqa
from keyprobe import KEYS, keymask                                          # noqa

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D',
          'fire': '0', 'none': None}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LOOP_TOP = 0x8503
ISR = 0xDADA

# ---------------------------------------------------------------- the region
ENTRIES = {
    0xB8CC: 'beepNoiseTick',    # LD A,R vs level -> maybe toggle bit 4
    0xB8E9: 'beepNoiseUp',
    0xB8F2: 'beepNoiseDn',
    0xB8FB: 'beepToneTick',     # one (count,period) pair per call
    0xB92B: 'beepTrigger',      # 48K sfx entry (patched over $BA2B)
    0xB8B0: 'beepTune1',
    0xB8B5: 'beepTune2',
    0xBA01: 'ayInit',
    0xBA2B: 'sfxEntry',         # 128K sfx entry, 23 call sites
    0xBADB: 'ayTick',           # per-ISR
    0xBB35: 'aySfxStep',
    0xBB96: 'ayWrite',
    0xBBA7: 'ayTuneA',
    0xBBBC: 'ayTuneB',
    0xBC0C: 'ayTunePlayer',
    0x9CD7: 'frameFlip',        # main-loop tail: HALT, $B8FB, screen, fanfare
    0x9D0A: 'fanfareSelect',
}

# the 48K patch $BF21 applies when ($FFFD) == 0.  Read out of the STATIC
# image at $BF21..$BF47 (see report); applied here byte for byte.
PATCH_48K = {0xBADB: [0xC9], 0xBA01: [0xC9], 0xBBA7: [0xC9], 0xBBBC: [0xC9],
             0xBA2B: [0xC3, 0x2B, 0xB9]}
# and what $BF21 does when ($FFFD) != 0, which is what the live dump already has
PATCH_128K = {0xB8B5: [0xC9], 0xB8CC: [0xC9]}
UNPATCH_128K = {0xB8B5: [0x21, 0x5D, 0x68], 0xB8CC: [0xED, 0x5F]}


def load(direction='down', beeper=False):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if beeper:
        # reproduce $BF21's 48K arm exactly
        h.poke(0xFFFD, 0)
        for a, vs in UNPATCH_128K.items():
            h.poke(a, *vs)
        for a, vs in PATCH_48K.items():
            h.poke(a, *vs)
    if direction and DIRKEY.get(direction):
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    return h


class Tracer:
    """Custom stepper with entry/exit accounting for the sound routines."""

    def __init__(self, h):
        self.h = h
        self.open = []          # [(addr, t0, sp0, ret, in_isr)]
        self.calls = []         # completed: (name, t0, cost, ret, in_isr)
        self.isr_depth = 0
        self.isr_sp = None

    def run_to_loop_top(self, limit=40_000_000):
        h = self.h
        sim = h.sim
        regs, opcodes, mem = sim.registers, sim.memory and sim.memory, sim.memory
        opcodes = sim.opcodes
        fd, ia = h.frame_duration, h.int_active
        t0 = regs[T]
        n = 0
        started = False
        while n < limit:
            pc = regs[PC]
            if started and pc == LOOP_TOP:
                return regs[T] - t0
            started = True
            # --- entry hooks ---------------------------------------------
            if pc in ENTRIES:
                self.open.append((pc, regs[T], regs[SP], self.isr_depth))
            if pc == ISR:
                self.isr_depth += 1
                self.isr_sp = regs[SP]
            # --- step -----------------------------------------------------
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
            # --- exit hooks ------------------------------------------------
            while self.open and regs[SP] > self.open[-1][2]:
                a, ts, sp, isrd = self.open.pop()
                self.calls.append((ENTRIES[a], ts, regs[T] - ts, isrd))
            if self.isr_depth and self.isr_sp is not None and regs[SP] > self.isr_sp:
                self.isr_depth = 0
                self.isr_sp = None
        raise RuntimeError('no loop top')


def fmt_frames(t):
    return t / FRAME_T


# ------------------------------------------------------------------ commands
def cmd_trace(direction, passes, beeper=False):
    h = load(direction, beeper)
    h.ports.record_writes = True
    tr = Tracer(h)
    tr.run_to_loop_top()
    h.ports.writes.clear()
    tr.calls.clear()
    print(f'{"pass":>4} {"frames":>7}  {"AY":>4} {"FEwr":>5} {"spk":>4}   events')
    for i in range(passes):
        w0 = len(h.ports.writes)
        c0 = len(tr.calls)
        t0 = h.regs[T]
        dt = tr.run_to_loop_top()
        w = h.ports.writes[w0:]
        ay = [x for x in w if (x[1] & 0xFF) == 0xFD]
        fe = [x for x in w if (x[1] & 0xFF) == 0xFE]
        spk = 0
        last = None
        for _, _, v in fe:
            b = v & 0x10
            if last is not None and b != last:
                spk += 1
            last = b
        ev = Counter(n for n, _, _, _ in tr.calls[c0:])
        big = [f'{n}x{c}' for n, c in sorted(ev.items()) if n not in
               ('ayWrite', 'beepNoiseTick', 'frameFlip', 'ayTick',
                'beepToneTick', 'aySfxStep', 'ayTunePlayer')]
        print(f'{i+1:>4} {fmt_frames(dt):>7.2f}  {len(ay):>4} {len(fe):>5} '
              f'{spk:>4}   {" ".join(big)}')
    return h, tr


def cmd_costs(direction, passes, beeper=False):
    h = load(direction, beeper)
    tr = Tracer(h)
    tr.run_to_loop_top()
    tr.calls.clear()
    for _ in range(passes):
        tr.run_to_loop_top()
    agg = defaultdict(list)
    isr = defaultdict(int)
    for n, ts, cost, isrd in tr.calls:
        agg[n].append(cost)
        if isrd:
            isr[n] += 1
    print(f'{"routine":<16} {"calls":>6} {"inISR":>6} {"min T":>8} {"med T":>8} '
          f'{"max T":>9} {"max f":>7} {"total f":>8}')
    for n in sorted(agg, key=lambda k: -sum(agg[k])):
        v = sorted(agg[n])
        print(f'{n:<16} {len(v):>6} {isr[n]:>6} {v[0]:>8} {v[len(v)//2]:>8} '
              f'{v[-1]:>9} {fmt_frames(v[-1]):>7.2f} {fmt_frames(sum(v)):>8.2f}')
    return h, tr


def cmd_regs(direction, passes, beeper=False):
    h = load(direction, beeper)
    h.ports.record_writes = True
    tr = Tracer(h)
    tr.run_to_loop_top()
    h.ports.writes.clear()
    for _ in range(passes):
        tr.run_to_loop_top()
    # pair up (select, data)
    frames = defaultdict(list)
    sel = None
    for t, p, v in h.ports.writes:
        if (p & 0xFFFF) == 0xFFFD:
            sel = v
        elif (p & 0xFFFF) == 0xBFFD and sel is not None:
            frames[t // FRAME_T].append((sel, v))
    ks = sorted(frames)
    print(f'{len(h.ports.writes)} port writes, {len(ks)} frames with AY writes')
    for k in ks:
        print(f'  frame {k:>7}: ' +
              ' '.join(f'R{r}={v:02X}' for r, v in frames[k]))
    tot = Counter(r for k in ks for r, _ in frames[k])
    print('register histogram:', dict(sorted(tot.items())))
    return h, tr


def cmd_plant(value, passes=8, beeper=False, actors=True):
    """Plant one interaction cell in the player's path and measure the cost of
    the pass that consumes it, attributing the T-states to sound or not."""
    h = load('down', beeper)
    m = h.memobj.m
    if not actors:
        m[0x8496] = 0
    m[0x8428] = 3                       # keys, so a door can open
    row, col = 10, 3
    m[0x8000 + row * 32 + col] = value
    m[0x8420], m[0x8421] = col * 4, (row - 3) * 4
    tr = Tracer(h)
    tr.run_to_loop_top()
    tr.calls.clear()
    out = []
    for i in range(passes):
        c0 = len(tr.calls)
        dt = tr.run_to_loop_top()
        ev = tr.calls[c0:]
        snd = sum(c for n, _, c, _ in ev if n not in ('frameFlip',))
        flip = sum(c for n, _, c, _ in ev if n == 'frameFlip')
        names = Counter(n for n, _, _, _ in ev)
        out.append((i + 1, dt, snd, flip, names, m[0x843D]))
    print(f'value ${value:02X}')
    for i, dt, snd, flip, names, slot in out:
        tag = ' '.join(f'{n}x{c}' for n, c in sorted(names.items())
                       if n in ('sfxEntry', 'beepTrigger', 'fanfareSelect',
                                'ayTuneA', 'ayTuneB', 'ayInit', 'beepTune1',
                                'beepTune2'))
        print(f'  pass {i:>2}  {fmt_frames(dt):>7.2f}f  slot=${slot:02X}  '
              f'sound={fmt_frames(snd):.2f}f flip={fmt_frames(flip):.2f}f  {tag}')
    return out


def cmd_scan(passes=10, beeper=False):
    vals = list(range(0x11, 0x39))
    print(f'{"cell":>5} {"worst pass (frames)":>20} {"where":>28}')
    for v in vals:
        h = load('down', beeper)
        m = h.memobj.m
        m[0x8496] = 0
        m[0x8428] = 3
        row, col = 10, 3
        m[0x8000 + row * 32 + col] = v
        m[0x8420], m[0x8421] = col * 4, (row - 3) * 4
        tr = Tracer(h)
        try:
            tr.run_to_loop_top()
            tr.calls.clear()
            worst = 0
            worstev = ''
            for i in range(passes):
                c0 = len(tr.calls)
                dt = tr.run_to_loop_top()
                if dt > worst:
                    worst = dt
                    ev = Counter(n for n, _, _, _ in tr.calls[c0:])
                    worstev = ' '.join(f'{n}x{c}' for n, c in sorted(ev.items())
                                       if n in ('sfxEntry', 'fanfareSelect',
                                                'beepTrigger', 'ayInit'))
        except Exception as e:                                # noqa
            worst, worstev = -1, f'({e})'
        print(f'  ${v:02X} {fmt_frames(worst):>20.2f} {worstev:>28}')


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else 'trace'
    beeper = '--beeper' in a
    a = [x for x in a if not x.startswith('--')]
    d = a[1] if len(a) > 1 else 'down'
    n = int(a[2]) if len(a) > 2 else 30
    if cmd == 'trace':
        cmd_trace(d, n, beeper)
    elif cmd == 'costs':
        cmd_costs(d, n, beeper)
    elif cmd == 'regs':
        cmd_regs(d, n, beeper)
    elif cmd == 'plant':
        cmd_plant(int(a[1], 0), int(a[2]) if len(a) > 2 else 8, beeper)
    elif cmd == 'scan':
        cmd_scan(int(a[1]) if len(a) > 1 else 10, beeper)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
