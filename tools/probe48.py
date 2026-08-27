#!/usr/bin/env python3
"""
probe48.py -- run the game's OWN 128K paging probe in the harness and see what
it decides, WITHOUT forcing ($FFFD) by hand.

Why this exists.  Everything this project measured about sound before now was
taken with RAM $FFFD holding the loader stub's padding byte $2A, because the
harness loads block A and then overwrites it with block C and never calls
$C1F2.  $2A is non-zero, so $BF21 took the 128K/AY arm.  The owner's machine is
a 48K.  The honest way onto the 48K branch is not to poke $FFFD: it is to let
the probe run.

    $C22D  LD HL,$CE50 / LD DE,$61A8 / LD BC,$0064 / LDIR     100 bytes
    $C238  CALL $61A8
    $C23B  CP $D1 / LD A,1 / JR nz,$C242 / SUB A
    $C242  LD ($FFFD),A

and the relocated probe itself:

    $CE50  DI / EX AF,AF' / LD A,($C000) / EX AF,AF'      save ($C000) in A'
    $CE56  SUB A / LD ($C000),A                            write 0 there
    $CE5A  LD BC,$7FFD / LD A,$D1 / OUT (C),A              page BANK 1 in
    $CE61  LD ($C000),A                                    write $D1 there
    $CE64  LD A,$D0 / OUT (C),A                            page BANK 0 back
    $CE68  LD A,($C000)                                    READ BACK
    $CE6B  EX AF,AF' / LD ($C000),A / EX AF,AF' / RET      restore, A = read

On a 128K the read-back is the 0 written into bank 0; on a 48K nothing paged,
so the read-back is the $D1 that was written through the same window.  The
harness is a flat 64K with no paging and its OUT is a no-op recorder, which is
precisely a 48K -- so the probe should return $D1 and store 0 of its own
accord.  That is what this tool checks.

Usage:
    python tools/probe48.py            all three checks
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, A, PC, SP, T, rom48                 # noqa: E402

IMAGE_A = os.path.join(ROOT, 'build', 'image_a.bin')

PROBE_SRC = 0xCE50
PROBE_DST = 0x61A8
PROBE_LEN = 0x64
COPY_PC = 0xC22D
STORE_PC = 0xC242
AFTER_PC = 0xC245
FFFD = 0xFFFD


def stage_a():
    """A harness whose 64K is the STAGE-A image: block A at $8600 and the
    loader stub at $FF00, with the real ROM below $4000 -- i.e. the machine at
    the moment the loader executes CALL $C1F2."""
    h = Harness(deck=False)
    img = bytearray(open(IMAGE_A, 'rb').read())
    img[0:0x4000] = rom48()
    h.memobj.m[:] = img
    r = h.regs
    r[PC] = 0xC1F2
    r[SP] = 0x5C00                    # $FF00 LD SP,$5C00
    return h


def show(h, label):
    m = h.memobj.m
    print(f'  {label}: ($FFFD)=${m[FFFD]:02X}  ($FFFE)=${m[0xFFFE]:02X}  '
          f'($FFFF)=${m[0xFFFF]:02X}')


def check_isolated():
    """The probe alone, called at $61A8 exactly as $C238 calls it."""
    print('1. THE PROBE IN ISOLATION')
    h = stage_a()
    m = h.memobj.m
    m[PROBE_DST:PROBE_DST + PROBE_LEN] = m[PROBE_SRC:PROBE_SRC + PROBE_LEN]
    before = m[0xC000]
    h.ports.record_writes = True
    idx, t, steps = h.call(PROBE_DST, interrupts=False)
    a = h.regs[A]
    print(f'   returned A=${a:02X} after {steps} instructions, {t} T-states')
    print(f'   $C000 before ${before:02X}, after ${m[0xC000]:02X}  '
          f'({"restored" if m[0xC000] == before else "CLOBBERED"})')
    print(f'   port writes: ' +
          ' '.join(f'${p:04X}<-${v:02X}' for _, p, v in h.ports.writes))
    verdict = '48K (nothing paged)' if a == 0xD1 else '128K'
    print(f'   CP $D1 -> {"Z" if a == 0xD1 else "NZ"}  => {verdict}')
    return a


def check_sequence():
    """$C22D..$C245: the copy, the call, the compare and the store."""
    print('\n2. THE COPY / CALL / COMPARE / STORE, $C22D..$C245')
    h = stage_a()
    show(h, 'before')
    h.regs[PC] = COPY_PC
    reason, n = h.run_until((AFTER_PC,), limit=200_000, interrupts=False)
    print(f'   ran to ${h.pc():04X} ({reason}, {n} instructions)')
    show(h, ' after')
    v = h.memobj.m[FFFD]
    print(f'   => ($FFFD) = ${v:02X}  '
          f'({"48K / BEEPER" if v == 0 else "128K / AY"})')
    return v


def check_frontend(limit=40_000_000):
    """The whole front end from $C1F2, as the loader calls it, with a write
    watch on $FFFD.  Reports every write to it and where it came from.

    $C220 LD IX,$C8FB / $C224 CALL $C8EA / BIT 0,(IX+7) / JR nz -- $C8EA is a
    second copy of the eight-half-row keyboard scanner writing to $C8FB, and
    (IX+7) is the LAST half-row, $7F, whose bit 0 is SPACE.  So the front end
    sits on "STOP TAPE AND PRESS SPACE" until SPACE is held.  Hold it.
    """
    print('\n3. THE FRONT END FROM $C1F2 (as $FF0F calls it), SPACE held')
    h = stage_a()
    h.ports.press(0x7F, 0xFE)          # SPACE: half-row $7F, bit 0
    show(h, 'before')
    h.memobj.watch(FFFD, FFFD + 1)
    reason, n = h.run_until((STORE_PC,), limit=limit, interrupts=True)
    print(f'   reached ${h.pc():04X} after {n} instructions ({reason})')
    if reason == 'target':
        h.step(1, interrupts=False)
    h.memobj.unwatch()
    for pc, addr, val in h.memobj.log:
        print(f'   write ($FFFD) <- ${val:02X}  from PC=${pc:04X}')
    show(h, ' after')
    v = h.memobj.m[FFFD]
    print(f'   => ($FFFD) = ${v:02X}  '
          f'({"48K / BEEPER" if v == 0 else "128K / AY"})')
    return v


if __name__ == '__main__':
    a = check_isolated()
    v1 = check_sequence()
    v2 = check_frontend()
    print('\nVERDICT: probe returns $%02X, ($FFFD) = $%02X / $%02X  -> %s'
          % (a, v1, v2, 'THE 48K BRANCH, UNFORCED'
             if a == 0xD1 and v1 == 0 and v2 == 0 else 'NOT 48K -- investigate'))
