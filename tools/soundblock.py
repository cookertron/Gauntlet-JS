#!/usr/bin/env python3
"""
soundblock.py -- how long does each sound path BLOCK, and who drives it?

    python tools/soundblock.py tune       $B8B0 / $B8B5, the 48K blocking tunes
    python tools/soundblock.py isr        is $BADB driven from $DADA?
    python tools/soundblock.py dump       per-50Hz-frame AY register dump over
                                          a driven pickup (the .psg-shaped data)
"""
import os
import pickle
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, SP, TAPE_CALL_PC, FRAME_T   # noqa
from keyprobe import KEYS, keymask                                   # noqa
from sounddrv import fresh                                           # noqa

KM = {n: (s, b) for n, s, b in KEYS}
LOOP_TOP = 0x8503
ISR = 0xDADA


def cmd_tune():
    for keyheld in (False, True):
        for entry in (0xB8B0, 0xB8B5):
            h = fresh(beeper=True)
            if keyheld:
                sel, bit = KM['Q']
                h.ports.press(sel, keymask(bit))
            h.ports.record_writes = True
            t0 = h.regs[T]
            try:
                h.call(entry, limit=40_000_000)
                ok = 'RET'
            except Exception as e:                                    # noqa
                ok = f'({e})'
            dt = h.regs[T] - t0
            fe = [w for w in h.ports.writes if (w[1] & 0xFF) == 0xFE]
            edges = 0
            last = None
            for _, _, v in fe:
                b = v & 0x10
                if last is not None and b != last:
                    edges += 1
                last = b
            print(f'  ${entry:04X}  key={"Q" if keyheld else "-"}  '
                  f'{dt:>10} T = {dt / FRAME_T:>7.2f} frames  {ok}  '
                  f'{len(fe)} $FE writes, {edges} speaker edges')


def cmd_isr():
    """Count $BADB entries reached through the ISR vector versus the main
    loop's own hand call at $9CF8."""
    h = fresh()
    sel, bit = KM['Q']
    h.ports.press(sel, keymask(bit))
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    src = Counter()
    passes = 0
    n = 0
    isr_sp = None
    in_isr = False
    while passes < 30 and n < 40_000_000:
        pc = regs[PC]
        if pc == LOOP_TOP and n:
            passes += 1
        if pc == ISR:
            in_isr = True
            isr_sp = regs[SP]
        if pc == 0xBADB:
            ret = mem[regs[SP]] | (mem[regs[SP] + 1] << 8)
            src[('ISR' if in_isr else 'main', ret)] += 1
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
        if in_isr and regs[SP] > isr_sp:
            in_isr = False
    print(f'$BADB entries over {passes} passes, by (context, return address):')
    for (ctx, ret), c in sorted(src.items()):
        print(f'   {ctx:<5} ret=${ret:04X}  {c}')
    print('  ($A2A8 is inside $A29F, the body the $DADA vector jumps to and')
    print('   which $9CF8 also calls by hand once per pass with interrupts off)')


def cmd_dump():
    h = fresh()
    m = h.memobj.m
    m[0x8496] = 0
    m[0x8428] = 3
    row, col = 10, 3
    m[0x8000 + row * 32 + col] = 0x19
    m[0x8420], m[0x8421] = col * 4, (row - 3) * 4
    sel, bit = KM['Q']
    h.ports.press(sel, keymask(bit))
    h.ports.record_writes = True
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    passes = 0
    while passes < 7 and n < 40_000_000:
        pc = regs[PC]
        if pc == LOOP_TOP and n:
            passes += 1
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    frames = defaultdict(list)
    sel_v = None
    t0 = h.ports.writes[0][0] if h.ports.writes else 0
    for t, p, v in h.ports.writes:
        if (p & 0xFFFF) == 0xFFFD:
            sel_v = v
        elif (p & 0xFFFF) == 0xBFFD and sel_v is not None:
            frames[(t - t0) // FRAME_T].append((sel_v, v))
    ks = sorted(frames)
    print(f'{len(ks)} 50Hz frames with AY writes over 7 passes '
          f'(the pickup is pass 5)')
    changed = 0
    prev = {}
    for k in ks:
        row = frames[k]
        interesting = [(r, v) for r, v in row if prev.get(r) != v]
        for r, v in row:
            prev[r] = v
        if interesting:
            changed += 1
        if 0 < len(interesting):
            print(f'  f{k:>4}: ' + ' '.join(f'R{r}={v:02X}' for r, v in row))
    print(f'{changed} frames carried a CHANGED register value')
    hist = Counter(len(frames[k]) for k in ks)
    print('writes per frame histogram:', dict(sorted(hist.items())))


if __name__ == '__main__':
    c = sys.argv[1] if len(sys.argv) > 1 else 'isr'
    {'tune': cmd_tune, 'isr': cmd_isr, 'dump': cmd_dump}.get(
        c, lambda: print(__doc__))()
