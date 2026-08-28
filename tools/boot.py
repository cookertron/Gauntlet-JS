#!/usr/bin/env python3
"""
boot.py -- boot the original in the harness and dump the LIVE memory image.

The game relocates itself extensively at start-up ($BDED onwards: five LDIR /
LDDR blocks), so the static block-copy image is NOT the runtime layout and
disassembling it gives plausible nonsense in the moved regions.  Everything
downstream disassembles against build/live.bin, produced here.

Usage:
    python tools/boot.py [--steps N] [--out build/live.bin] [--ints]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, CPU_HZ, top, I, IM, IFF, SP, PC, T   # noqa: E402


def main():
    args = sys.argv[1:]
    steps = 400_000
    out = os.path.join(ROOT, 'build', 'live.bin')
    ints = False
    while args:
        if args[0] == '--steps':
            steps = int(args[1], 0); del args[:2]
        elif args[0] == '--out':
            out = args[1]; del args[:2]
        elif args[0] == '--ints':
            ints = True; del args[:1]
        else:
            del args[:1]

    h = Harness()
    hist = h.profile(steps, interrupts=ints)
    open(out, 'wb').write(bytes(h.memobj.m))
    print(f'ran {steps} instructions (interrupts {"ON" if ints else "OFF"})')
    print(f'PC=${h.pc():04X}  SP=${h.regs[SP]:04X}  I=${h.regs[I]:02X}  '
          f'IM={h.regs[IM]}  IFF={h.regs[IFF]}  T={h.regs[T]} ({h.regs[T]/CPU_HZ:.3f}s)')
    print(f'ports read: {sorted("$%04X" % p for p in h.ports.reads)}')
    print(f'writes into ROM (ignored): {h.memobj.rom_writes}')
    print('hot addresses:')
    for a, c in top(hist, 25):
        print(f'  ${a:04X}  {c}')
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
