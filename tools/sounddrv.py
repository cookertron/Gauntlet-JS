#!/usr/bin/env python3
"""
sounddrv.py -- isolate the two sound drivers and measure them (manual 6.6/11.4).

    python tools/sounddrv.py beeper        every id through $B92B + $B8FB
    python tools/sounddrv.py ay            every id through $BA2B + $BADB
    python tools/sounddrv.py table         the $73DA sfx pointer table
    python tools/sounddrv.py idle          the cost of an idle $B8FB / $BADB
"""
import os
import pickle
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, SP, A as REG_A, FRAME_T, CPU_HZ   # noqa

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
UNPATCH_128K = {0xB8B5: [0x21, 0x5D, 0x68], 0xB8CC: [0xED, 0x5F]}
PATCH_48K = {0xBADB: [0xC9], 0xBA01: [0xC9], 0xBBA7: [0xC9], 0xBBBC: [0xC9],
             0xBA2B: [0xC3, 0x2B, 0xB9]}


def fresh(beeper=False):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if beeper:
        h.poke(0xFFFD, 0)
        for a, v in UNPATCH_128K.items():
            h.poke(a, *v)
        for a, v in PATCH_48K.items():
            h.poke(a, *v)
    return h


def cmd_beeper():
    print('BEEPER: $B92B triggers, $B8FB plays ONE step per call.')
    print('(T-state costs at 3.5MHz; one video frame = %d T)' % FRAME_T)
    for sid in range(0, 0x12):
        h = fresh(beeper=True)
        h.poke(0x84CF, 0)                     # (IY+$50) idle
        h.poke(0x84D2, 0)                     # noise level off
        h.call(0xB92B, regs={'A': sid})
        n = h.peek(0x84CF)[0]
        ptr = h.peek16(0x84D0)
        lvl = h.peek(0x84D2)[0]
        ramp = h.peek(0xB8E2)[0]
        if not n and not lvl:
            print(f'  id ${sid:02X}  -- SILENT (no arm)')
            continue
        if lvl:
            print(f'  id ${sid:02X}  NOISE  level=${lvl:02X} ramp='
                  f'{"INC" if ramp == 0x3C else "DEC"} '
                  f'(density {lvl}/128 per drawn object)')
            continue
        # tone: play out all n steps, recording FE edges
        h.ports.record_writes = True
        steps = []
        for _ in range(n + 1):
            t0 = h.regs[T]
            w0 = len(h.ports.writes)
            h.call(0xB8FB)
            cost = h.regs[T] - t0
            w = [x for x in h.ports.writes[w0:] if (x[1] & 0xFF) == 0xFE]
            edges = len(w)
            per = 0
            if edges > 2:
                per = (w[-1][0] - w[0][0]) / (edges - 1)
            steps.append((cost, edges, per))
        s = ' '.join(f'{e}@{per:.0f}T' if e else 'idle' for _, e, per in steps)
        costs = ' '.join(str(c) for c, _, _ in steps)
        print(f'  id ${sid:02X}  TONE   {n} steps, ptr=${ptr:04X}')
        print(f'          edges/half-period: {s}')
        print(f'          T per call:        {costs}')


def cmd_ay():
    print('AY: $BA2B allocates a channel, $BADB steps every channel per ISR.')
    for sid in range(0, 0x12):
        h = fresh()
        # silence anything already running
        for a in range(0xBDB1, 0xBDC2):
            h.poke(a, 0)
        h.poke(0xBDE9, 0)
        h.call(0xBA2B, regs={'A': sid})
        mask = h.peek(0xBDC0)[0]
        ch = [h.peek16(0xBDB3 + 4 * i) for i in range(3)]
        ids = [h.peek(0xBDB6 + 4 * i)[0] for i in range(3)]
        h.ports.record_writes = True
        frames = []
        for f in range(200):
            w0 = len(h.ports.writes)
            t0 = h.regs[T]
            h.call(0xBADB)
            cost = h.regs[T] - t0
            w = h.ports.writes[w0:]
            regs = []
            sel = None
            for _, p, v in w:
                if (p & 0xFFFF) == 0xFFFD:
                    sel = v
                elif (p & 0xFFFF) == 0xBFFD:
                    regs.append((sel, v))
            frames.append((cost, regs))
            if h.peek(0xBDC0)[0] & 7 == 0:
                break
        nf = len(frames)
        body = []
        for cost, regs in frames[:8]:
            body.append('{' + ' '.join(f'R{r}={v:02X}' for r, v in regs) + '}')
        print(f'  id ${sid:02X}  mask=${mask:02X} ptr=${ch[0]:04X} ids={ids} '
              f'-> {nf} frames ({nf / 50:.2f}s), max T={max(c for c, _ in frames)}')
        print(f'         first frames: {" ".join(body)}')


def cmd_table():
    h = fresh()
    m = h.memobj.m
    base = 0x73DA
    print(f'sfx pointer table at ${base:04X} (16-bit RELATIVE, +${base:04X}):')
    ends = []
    for sid in range(0, 0x18):
        off = m[base + 2 * sid] | (m[base + 2 * sid + 1] << 8)
        p = (base + off) & 0xFFFF
        ends.append((sid, off, p))
    for sid, off, p in ends:
        # walk the stream: 2 bytes per frame, 0 terminates
        q = p
        n = 0
        while n < 400 and m[q] != 0:
            q += 2
            n += 1
        print(f'  id ${sid:02X}  off=${off:04X}  data=${p:04X}  '
              f'{n} frames ({n / 50:.2f}s)  len={q - p + 1}')


def cmd_idle():
    h = fresh(beeper=True)
    t0 = h.regs[T]
    h.call(0xB8FB)
    print(f'$B8FB idle: {h.regs[T] - t0} T = {(h.regs[T] - t0) / FRAME_T:.3f} frames')
    h2 = fresh()
    t0 = h2.regs[T]
    h2.call(0xB8FB)
    print(f'$B8FB idle (128K build, still called): {h2.regs[T] - t0} T')
    t0 = h2.regs[T]
    h2.call(0xBADB)
    print(f'$BADB quiet: {h2.regs[T] - t0} T')
    t0 = h2.regs[T]
    h2.call(0xB8CC)
    print(f'$B8CC (128K, patched to RET): {h2.regs[T] - t0} T')
    t0 = h.regs[T]
    h.call(0xB8CC)
    print(f'$B8CC (48K, level 0): {h.regs[T] - t0} T')
    h.poke(0x84D2, 0x40)
    costs = Counter()
    h.ports.record_writes = True
    tog = 0
    for _ in range(400):
        t0 = h.regs[T]
        w0 = len(h.ports.writes)
        h.call(0xB8CC)
        costs[h.regs[T] - t0] += 1
        tog += len(h.ports.writes[w0:])
        h.poke(0x84D2, 0x40)          # hold the level
    print(f'$B8CC (48K, level $40): costs {dict(costs)}, '
          f'{tog}/400 toggles ({tog / 4:.0f}%)')


if __name__ == '__main__':
    c = sys.argv[1] if len(sys.argv) > 1 else 'beeper'
    {'beeper': cmd_beeper, 'ay': cmd_ay, 'table': cmd_table,
     'idle': cmd_idle}.get(c, lambda: print(__doc__))()
