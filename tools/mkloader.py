#!/usr/bin/env python3
"""
mkloader.py -- build a scratch 64K image containing only the BASIC block, laid
out exactly as the ROM loader leaves it, plus the loader stub's own self-relocation
performed by hand, so the stub can be disassembled at its running address.

Phase 2 step 1.  Facts asserted here (each with the evidence):

  - PROG = $5CCB on a stock 48K with the standard 21-byte channel information
    block at CHANS = $5CB6.  The block is LOADed by the ROM at PROG.
  - The BASIC line is:  line 0, length field $FF00 (a deliberately bogus length;
    the program autostarts so LIST is never reached).
  - The line's first statement prints as RANDOMIZE USR 32768, but the 5-byte
    binary form that the interpreter actually uses is 23778 = $5CE2, which is
    the first byte of the REM that follows.  That is the machine-code entry.
  - $5CE2 does: DI / LD HL,$5CF1 / LD DE,$FF00 / LD BC,$0100 / LDIR / JP $FF00
    so 256 bytes from $5CF1 run at $FF00.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'build', 'blocks1', 'Gauntlet_-_Side_1.b02.body.bin')
OUT = os.path.join(ROOT, 'build', 'loader_image.bin')

PROG = 0x5CCB

mem = bytearray(0x10000)
basic = open(SRC, 'rb').read()
mem[PROG:PROG + len(basic)] = basic

# the stub's own LDIR, performed by hand
mem[0xFF00:0xFF00 + 0x100] = mem[0x5CF1:0x5CF1 + 0x100]

open(OUT, 'wb').write(mem)
print(f'BASIC block: {len(basic)} bytes at ${PROG:04X}..${PROG+len(basic)-1:04X}')
print(f'stub relocated: $5CF1+256 -> $FF00')
print(f'wrote {OUT}')
