#!/usr/bin/env python3
"""
clock.py -- Q10/Q11: the clock model, by histogram.

Q10: histogram the T-states between successive visits to the top of the main
loop.  DO NOT take the mean -- the shape of the distribution is the answer:

    tight cluster at exactly one frame period (69888)  -> HALT-synced
    cluster at 1x with a tail at 2x, 3x                -> frame-synced WITH
                                                          DROPPED FRAMES
    one frame period plus a variable remainder         -> polls FRAMES/ISR flag
    broad distribution in the hundreds/low thousands   -> free-running
    no meaningful loop top at all                      -> ISR-DRIVEN

Usage:
    python tools/clock.py --pc 0x8503 [--visits 400] [--warm 400000]
"""
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, CPU_HZ, PC, T, IFF, TAPE_CALL_PC     # noqa: E402

FRAME_T = 69888


def intervals(h, pc_target, visits, limit=40_000_000, interrupts=True):
    sim = h.sim
    regs = sim.registers
    opcodes = sim.opcodes
    mem = sim.memory
    deck = h.deck
    fd, ia = h.frame_duration, h.int_active
    out = []
    last = None
    n = 0
    while len(out) < visits and n < limit:
        pc = regs[PC]
        if pc == pc_target:
            t = regs[T]
            if last is not None:
                out.append(t - last)
            last = t
        if deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        opcodes[mem[pc]]()
        if interrupts and regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return out, n


def report(name, xs):
    if not xs:
        print(f'{name}: NO VISITS')
        return
    c = Counter(xs)
    xs_sorted = sorted(xs)
    print(f'\n{name}: {len(xs)} intervals')
    print(f'  min {min(xs)}  max {max(xs)}  mean {sum(xs)/len(xs):.1f}  '
          f'median {xs_sorted[len(xs)//2]}')
    print(f'  in frame periods: min {min(xs)/FRAME_T:.3f}  max {max(xs)/FRAME_T:.3f}  '
          f'mean {sum(xs)/len(xs)/FRAME_T:.3f}')
    print('  most common:')
    for v, k in c.most_common(10):
        print(f'    {v:>8}  x{k:<5}  = {v/FRAME_T:.3f} frames')
    buckets = Counter(round(x / FRAME_T, 1) for x in xs)
    print('  frame-period buckets:')
    for k in sorted(buckets):
        print(f'    {k:5.1f}x  {"#" * min(60, buckets[k])} {buckets[k]}')


def main():
    args = sys.argv[1:]
    pcs = []
    visits = 300
    warm = 400_000
    while args:
        if args[0] == '--pc':
            pcs.append(int(args[1], 0)); del args[:2]
        elif args[0] == '--visits':
            visits = int(args[1], 0); del args[:2]
        elif args[0] == '--warm':
            warm = int(args[1], 0); del args[:2]
        else:
            del args[:1]
    if not pcs:
        pcs = [0x8503]

    h = Harness()
    h.profile(warm)
    print(f'warmed {warm} instructions; PC=${h.pc():04X}')
    for p in pcs:
        xs, n = intervals(h, p, visits)
        report(f'PC ${p:04X} (interrupts ON)', xs)
        print(f'  ({n} instructions to collect)')


if __name__ == '__main__':
    main()
