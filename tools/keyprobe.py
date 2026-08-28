#!/usr/bin/env python3
"""
keyprobe.py -- find which key does something, by pressing each of the 40 in
turn and watching for an escape from a wait loop or a change in memory.

The usual trick (manual 8.1 -- single-step to the first port read with low byte
$FE and read the bit tested) does NOT discriminate in this game: $B4E8 scans
ALL EIGHT half-rows unconditionally into $847F..$8486 every pass, so every
half-row is read on every frame.  The consumers test bits of that table
instead.  So we probe behaviourally, from ONE boot, restoring state per key.

Usage:
    python tools/keyprobe.py [--warm N] [--hold N] [--vars LO HI]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC          # noqa: E402

HALFROWS = [
    (0xFE, ['CAPS', 'Z', 'X', 'C', 'V']),
    (0xFD, ['A', 'S', 'D', 'F', 'G']),
    (0xFB, ['Q', 'W', 'E', 'R', 'T']),
    (0xF7, ['1', '2', '3', '4', '5']),
    (0xEF, ['0', '9', '8', '7', '6']),
    (0xDF, ['P', 'O', 'I', 'U', 'Y']),
    (0xBF, ['ENTER', 'L', 'K', 'J', 'H']),
    (0x7F, ['SPACE', 'SYM', 'M', 'N', 'B']),
]

KEYS = [(name, sel, bit) for sel, names in HALFROWS
        for bit, name in enumerate(names)]


def keymask(bit):
    return 0xFF & ~(1 << bit)


def run(h, n, interrupts=True):
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    seen = set()
    for _ in range(n):
        pc = regs[PC]
        seen.add(pc)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        ops[mem[pc]]()
        if interrupts and regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    return seen


def main():
    args = sys.argv[1:]
    warm = 400_000
    hold = 150_000
    lo, hi = 0x8400, 0x8600
    while args:
        if args[0] == '--warm':
            warm = int(args[1], 0); del args[:2]
        elif args[0] == '--hold':
            hold = int(args[1], 0); del args[:2]
        elif args[0] == '--vars':
            lo = int(args[1], 0); hi = int(args[2], 0); del args[:3]
        else:
            del args[:1]

    h = Harness()
    h.profile(warm)
    state = h.save_state()
    print(f'booted {warm} instructions; PC=${h.pc():04X}')

    snap0 = h.snapshot(lo, hi)
    baseline = run(h, hold)
    snapb = h.snapshot(lo, hi)
    idle = {i + lo for i in range(hi - lo) if snapb[i] != snap0[i]}
    print(f'baseline: PC=${h.pc():04X}, {len(baseline)} distinct PCs, '
          f'{len(idle)} vars change while idle')

    for name, sel, bit in KEYS:
        h.load_state(state)
        h.ports.press(sel, keymask(bit))
        seen = run(h, hold)
        new = sorted(seen - baseline)
        snap = h.snapshot(lo, hi)
        changed = [i + lo for i in range(hi - lo)
                   if snap[i] != snap0[i] and (i + lo) not in idle]
        note = ''
        if new:
            note += f'  NEW PCs {len(new)}: ' + ' '.join(f'${a:04X}' for a in new[:5])
        if changed:
            note += '  vars: ' + ' '.join(f'${a:04X}' for a in changed[:8])
        print(f'{name:>6} (${sel:02X}.{bit}): PC=${h.pc():04X}{note}')


if __name__ == '__main__':
    main()
