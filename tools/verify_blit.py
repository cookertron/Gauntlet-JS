#!/usr/bin/env python3
"""Independent blitter instrumentation.  Written from scratch.

Logs every write into the shadow screen during one main-loop pass, groups the
log into DRAWS by the blitter's own entry write, inverts the display-file
address, and reports geometry.  No reuse of the colleague's blitwatch.py.
"""
import os, sys, pickle
sys.path.insert(0, r'E:\Software\Gauntlet\tools')
SCRATCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRATCH)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC, SP, H, L, B, C, IXh, IXl
from keyprobe import KEYS, keymask
KEYMAP = {n: (s, b) for n, s, b in KEYS}
FRAME_T = 69888

# --- my own bank repair, from the tape, independent of fixchar.py ----------
from harness import tape_blocks, SIDE1
def master_table():
    by = {}
    for f, p in tape_blocks(SIDE1):
        by.setdefault(f, []).append(p)
    return by[0x82][0][0x3C00:0x3C00 + 0x1080]

def repair(mem, p1=1, p2=0):
    mt = master_table()
    mem[0x5F00:0x5F00 + 0x420] = mt[p1*0x420:(p1+1)*0x420]
    mem[0x5F00+0x420:0x5F00+0x840] = mt[p2*0x420:(p2+1)*0x420]
    mem[0xFFFF], mem[0xFFFE] = p1, p2

# --- display-file address inversion ---------------------------------------
def unscramble(addr, base=0xC000):
    """Spectrum bitmap address -> (x_byte, y_pixelrow) within the screen."""
    a = addr - base
    third = (a >> 11) & 3
    line  = (a >> 8) & 7        # pixel row within char row
    row   = (a >> 5) & 7        # char row within third
    col   = a & 31
    y = third*64 + row*8 + line
    return col, y

def scramble(col, y, base=0xC000):
    third, r = divmod(y, 64)
    row, line = divmod(r, 8)
    return base + (third << 11) + (line << 8) + (row << 5) + col

def step_pass(h, frames=4):
    target = h.regs[T] + frames*FRAME_T
    sim = h.sim; regs = sim.registers; ops = sim.opcodes; mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    while regs[T] < target:
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)

# --- draw grouping ---------------------------------------------------------
ATTR_ENTRY = {0x9DDD, 0x9E56}          # first attribute write of each variant
BITMAP_PCS_16 = set(range(0x9DEC, 0x9E4B))
def group(log):
    """Split the write log into draws.  A draw begins at an attribute-entry
    write and runs until the next one."""
    draws = []
    cur = None
    for pc, addr, val in log:
        if pc in ATTR_ENTRY:
            if cur: draws.append(cur)
            cur = {'entry': pc, 'attr': [], 'bmp': [], 'other': []}
        if cur is None:
            continue
        if 0xD800 <= addr:
            cur['attr'].append((pc, addr, val))
        elif 0x9DEC <= pc <= 0x9E80:
            cur['bmp'].append((pc, addr, val))
        else:
            cur['other'].append((pc, addr, val))
    if cur: draws.append(cur)
    return draws
