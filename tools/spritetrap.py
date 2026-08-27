#!/usr/bin/env python3
"""
spritetrap.py -- trap the 16-wide blitter at $9DEC, record the LIVE source
bytes it POPs and the block it writes, and compare the two.

The whole point: the blit is OPAQUE (LD (HL),E), 16 POPs = 32 bytes, so the
bytes at SP MUST equal the 16x16 block written. If they don't, either the
source is not where we think, or the bank is not static.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, SP, H, L, TAPE_CALL_PC  # noqa: E402
from keyprobe import KEYS, keymask                               # noqa: E402

KEYMAP = {name: (sel, bit) for name, sel, bit in KEYS}
FRAME_T = 69888
BLIT16_START = 0x9DEC          # first POP DE of the 16-wide 16-row blitter
BLIT16_END = 0x9E49            # JP (IX)


def collect(h, frames, want_dest=None):
    """Run `frames` video frames, capturing every pass through $9DEC..$9E49."""
    target = h.regs[T] + frames * FRAME_T
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    hits = []
    while regs[T] < target:
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        if pc == BLIT16_START:
            src = regs[SP]
            dst = (regs[H] << 8) | regs[L]
            srcbytes = bytes(mem[src:src + 32])
            hits.append({'src': src, 'dst': dst, 'srcbytes': srcbytes,
                         't': regs[T]})
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    return hits


def read_block(mem, dst):
    """Read the 16x16 pixel block the blitter wrote, following its own address
    arithmetic exactly: 8 rows by INC H, then H-=7 / L+=$20 (carry -> H+=8)."""
    out = bytearray()
    hi, lo = dst >> 8, dst & 0xFF
    for half in range(2):
        for r in range(8):
            a = ((hi + r) << 8) | lo
            out.append(mem[a])
            out.append(mem[(a & 0xFF00) | ((lo + 1) & 0xFF)])
        hi = (hi + 7) - 7
        hi = (dst >> 8) + 7 if half == 0 else hi
        # replicate: after 8 INC H, H = base+8; SUB 7 -> base+1; L += 0x20
        hi = ((dst >> 8) + 8 - 7) & 0xFF
        nl = lo + 0x20
        if nl > 0xFF:
            hi = (hi + 8) & 0xFF
        lo = nl & 0xFF
        dst = (hi << 8) | lo
    return bytes(out)


def bits(b):
    return format(b, '08b').replace('0', '.').replace('1', '#')


def show(data, label):
    print(label)
    for r in range(len(data) // 2):
        print('   ' + bits(data[r * 2]) + bits(data[r * 2 + 1]))


def main():
    state = os.path.join(ROOT, 'build', 'state_charsel.pkl')
    keys = []
    frames = 8
    args = sys.argv[1:]
    while args:
        if args[0] == '--keys':
            keys = [k for k in args[1].split(',') if k]; del args[:2]
        elif args[0] == '--frames':
            frames = int(args[1]); del args[:2]
        elif args[0] == '--state':
            state = args[1]; del args[:2]
        else:
            del args[:1]

    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    for k in keys:
        sel, bit = KEYMAP[k.upper()]
        h.ports.press(sel, keymask(bit))

    mem = h.memobj.m
    hits = collect(h, frames)
    print(f'{len(hits)} blit16 calls in {frames} frames')
    seen = {}
    for hh in hits:
        seen.setdefault(hh['src'], []).append(hh['dst'])
    for src in sorted(seen):
        print(f'  src ${src:04X}  n={len(seen[src]):3d}  dests={sorted(set(seen[src]))[:4]}')

    # static dump for comparison
    static = open(os.path.join(ROOT, 'build', 'live_cs.bin'), 'rb').read()
    for src in sorted(seen)[:6]:
        live = bytes(mem[src:src + 32])
        st = static[src:src + 32]
        print(f'\n=== ${src:04X}  live==static? {live == st}')
        show(live, f'  LIVE bytes at ${src:04X} as 2x16 row-major:')


if __name__ == '__main__':
    main()
