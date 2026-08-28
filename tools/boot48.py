#!/usr/bin/env python3
"""
boot48.py -- boot the game the way the LOADER does, so that the 48K/128K sound
branch is chosen by the game's OWN probe instead of by a stale byte.

=============================================================================
WHY
=============================================================================
tools/harness.py loads blocks A, B and C in that order and jumps to $8400.
Block C covers $8400..$FB76 and block A lives at $8600..$D80F, so block A is
gone before a single instruction of it runs -- and the loader's $FF0F CALL
$C1F2, which is where the 128K paging probe lives, never happens.  RAM $FFFD
therefore holds the loader stub's padding byte $2A, which is non-zero, so
$BEB9 CALL $BF21 takes the 128K/AY arm.  EVERY MEASUREMENT THIS PROJECT MADE
BEFORE PHASE 11 WAS ON THE AY BRANCH BY ACCIDENT.

This tool restores the loader's real ORDER:

    block A -> $8600            (mkimage.py --stage a)
    run the probe $C22D..$C245  (LDIR $CE50->$61A8, CALL it, CP $D1, store)
    block B -> $73DA
    block C -> $8400            (which overwrites block A, as on the tape)
    JP $8400

The probe is NOT patched, NOT stubbed and its answer is NOT forced: the
harness is a flat 64K whose OUT ($7FFD) goes nowhere, which is exactly what a
48K does, so the read-back is the $D1 that was just written and the probe
stores 0 by itself.  `python tools/probe48.py` is the standalone proof.

`--mode ay` runs the identical script with the probe SKIPPED, which is the
control: it reproduces today's $FFFD = $2A machine through the same code path,
so the two saved states differ in the boot-time branch and in nothing else
that this tool does.

=============================================================================
THE KEY SCRIPT
=============================================================================
A cold boot of block C does not start a game by itself.  $B35A NEW GAME ->
$B374 CALL $B470, the ATTRACT loop, which polls $9432/$9440 for FIRE; then
$B48F BIT 7,(IY+$4D) is clear on a cold boot, so $B494 prints "REWIND TAPE TO
START OF SIDE 2" and $B4AD spins until SPACE.  Holding Z (player 1 FIRE) and
SPACE from the first instruction satisfies both, and both are released at the
first main-loop top so that neither perturbs play (holding FIRE freezes the
player).  Measured, not assumed: without them the game sits in the attract
loop for ever and the tape deck is never asked for a pack.

=============================================================================
THE ANCHOR
=============================================================================
build/state_charsel.pkl is PC=$ABA1 with ($8491) = $42, i.e. 66 main-loop
passes into dungeon 1, one tape block served.  Both states this tool writes
are anchored at THE SAME PLACE -- the first visit to $ABA1 in pass $42 -- so
that tools/sim_move.py's opening step_to_loop_top() lands on the same pass top
from all three files and the per-pass tables line up row for row.

Usage:
    python tools/boot48.py                     write build/state_48k.pkl
    python tools/boot48.py --mode ay           write build/state_48k_ay.pkl
    python tools/boot48.py --both --compare    both, plus the diff against
                                               build/state_charsel.pkl
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, tape_blocks, rom48, SIDE1,          # noqa: E402
                     PC, T, R, SP, IFF, IM, I, FRAME_T, TAPE_CALL_PC,
                     BLOCK_A, BLOCK_B, BLOCK_C, ENTRY, STUB_SRC, STUB_DST,
                     STUB_LEN, PROG)
from keyprobe import KEYS, keymask                                # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}

PROBE_COPY_PC = 0xC22D          # LD HL,$CE50 / LD DE,$61A8 / LDIR
PROBE_END_PC = 0xC245           # the instruction after LD ($FFFD),A
LOOP_TOP = 0x8503
ANCHOR_PC = 0xABA1              # where build/state_charsel.pkl was saved
PASS_CTR = 0x8491
ANCHOR_PASS = 0x42

# the ten bytes $BF21 rewrites, measured last cycle
PATCHED = (0xB8B5, 0xB8CC, 0xBA01, 0xBA2B, 0xBA2C, 0xBA2D, 0xBADB,
           0xBBA7, 0xBBBC, 0xFFFD)


def payloads():
    by = {}
    for flag, data in tape_blocks(SIDE1):
        by.setdefault(flag, []).append(data)
    return by


def make(mode='48k', verbose=True):
    """A harness sitting at $8400 with the sound branch chosen by `mode`.

    mode '48k' runs the probe out of block A before block C lands on top of
    it; mode 'ay' skips the probe, which is what the harness does today.
    """
    h = Harness()                                   # ROM + deck + ports
    by = payloads()
    raw = bytearray(0x10000)
    raw[0:0x4000] = rom48()

    dest, ln, flag = BLOCK_A                        # $8600, $5210, $80
    raw[dest:dest + ln] = by[flag][0]
    basic = by[0xFF][0]
    raw[STUB_DST:STUB_DST + STUB_LEN] = basic[STUB_SRC - PROG:
                                              STUB_SRC - PROG + STUB_LEN]
    h.memobj.m[:] = raw
    r = h.regs
    r[SP] = 0x5C00                                  # $FF00 LD SP,$5C00
    r[IFF] = 0
    r[IM] = 1

    probe_a = None
    if mode == 'menu':
        # THE WHOLE FRONT END, driven.  $C1F2 is CALLed by $FF0F, so push its
        # return address and run until it comes back; SPACE is tapped on and
        # off because the front end waits for the press in one place and for
        # the release in another (see tools/frontend.py).
        import frontend
        r[SP] = (r[SP] - 2) & 0xFFFF
        h.memobj.m[r[SP]], h.memobj.m[r[SP] + 1] = 0x12, 0xFF
        r[PC] = 0xC1F2
        reason, n = frontend.run(h, 40_000_000, targets=(0xC426,),
                                 tap='SPACE')
        assert reason == 'target', 'never reached the character picker'
        frontend.tapn(h, '5', 3)                 # pick character 2, WIZARD
        # THE CONTROL PICKERS, $C4FF and $C514, both CALL $C555: key 6 steps
        # $C808 forward, key 7 back, SPACE selects, and $8560 dispatches the
        # result -- 0 SINCLAIR (67890 / 12345), 1 KEMPSTON ($8680 IN A,($1F)),
        # 2 CURSOR (5678 + 0), 3 the game's own KEYBOARD map (1QSDZ / 8IKLM).
        # The picker starts at 0, so ONE press of 7 wraps it to 3, which is
        # the arm the stale $2A fell through to and the only one this
        # project's key script drives.  Without this the boot deadlocks in the
        # attract loop: measured, PC sits at $B4EE for ever.
        for target in (0xC4FF, 0xC514):
            reason, n = frontend.run(h, 40_000_000, targets=(target,),
                                     tap='SPACE')
            assert reason == 'target', f'never reached ${target:04X}'
            # $C574's test is a LEVEL, not an edge: while 7 is down the loop
            # decrements $C808 every iteration.  So hold it and let go the
            # instant the counter reads 3.
            sel, bit = KM['7']
            h.ports.press(sel, keymask(bit))
            while h.memobj.m[0xC808] != 3:
                frontend._advance(h, 1)
            h.ports.release_all()
            frontend._advance(h, 2)
        reason, n = frontend.run(h, 40_000_000, targets=(0xFF12,),
                                 tap='SPACE')
        assert reason == 'target', 'the front end never returned to $FF12'
        h.ports.release_all()
        m = h.memobj.m
        if verbose:
            print('  front end driven to completion; ' +
                  ' '.join(f'(${a:04X})=${m[a]:02X}' for a in range(0xFFFB, 0x10000)))
    if mode == '48k':
        r[PC] = PROBE_COPY_PC
        reason, n = h.run_until((PROBE_END_PC,), limit=200_000,
                                interrupts=False)
        assert reason == 'target', f'probe did not finish: {reason}'
        probe_a = h.memobj.m[0xFFFD]
        if verbose:
            print(f'  probe ran ({n} instructions) -> ($FFFD) = ${probe_a:02X}')
    elif mode == 'ay' and verbose:
        print(f'  probe SKIPPED -> ($FFFD) = ${h.memobj.m[0xFFFD]:02X} '
              f'(the loader stub\'s padding)')

    for dest, ln, flag in (BLOCK_B, BLOCK_C):       # as $FF20 / $FF2C do
        data = by[flag][0]
        assert len(data) == ln
        h.memobj.m[dest:dest + ln] = data

    r[PC] = ENTRY
    r[SP] = 0xFFFB
    r[IFF] = 0
    r[IM] = 1
    return h


def run_to(h, targets, limit=80_000_000, predicate=None):
    """Step to any PC in `targets` (and, if given, satisfying predicate(h))."""
    if isinstance(targets, int):
        targets = (targets,)
    targets = set(targets)
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0, n = regs[T], 0
    while n < limit:
        pc = regs[PC]
        if n and pc in targets and (predicate is None or predicate(h)):
            return (pc, regs[T] - t0, n)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError(f'no target in {limit} instructions (PC=${regs[PC]:04X})')


def drive(h, verbose=True):
    """FIRE + SPACE held from the first instruction; released at the first
    main-loop top.  Then on to the anchor."""
    for k in ('Z', 'SPACE'):
        sel, bit = KM[k]
        h.ports.press(sel, keymask(bit))
    pc, dt, n = run_to(h, LOOP_TOP)
    h.ports.release_all()
    if verbose:
        print(f'  live at $8503 after {n} instructions, {dt/FRAME_T:.1f} frames'
              f'; dungeon {h.memobj.m[0x8403]}, tape blocks served {h.deck.pos}')
    pc, dt, n = run_to(h, ANCHOR_PC,
                       predicate=lambda x: x.memobj.m[PASS_CTR] == ANCHOR_PASS)
    if verbose:
        print(f'  anchor $ABA1 in pass ${ANCHOR_PASS:02X} after {n} more '
              f'instructions ({dt/FRAME_T:.1f} frames)')
    return h


def report(h, tag):
    m = h.memobj.m
    print(f'  {tag}: ($FFFD)=${m[0xFFFD]:02X}  patched bytes ' +
          ' '.join(f'${a:04X}=${m[a]:02X}' for a in PATCHED[:9]))
    print(f'      p1 ({m[0x8420]},{m[0x8421]})  health ${m[0x8422]:02X}{m[0x8423]:02X}'
          f'  actors {m[0x8496]}  dungeon {m[0x8403]}  pass ${m[PASS_CTR]:02X}'
          f'  PC=${h.pc():04X}  R={h.regs[R]}')


def compare(a, b, na, nb):
    """Byte diff between two saved states, bucketed."""
    ma = a[0] if isinstance(a, tuple) else a
    mb = b[0] if isinstance(b, tuple) else b
    d = [i for i in range(0x4000, 0x10000) if ma[i] != mb[i]]
    print(f'\n  {na} vs {nb}: {len(d)} bytes differ in $4000..$FFFF')
    buckets = {}
    for i in d:
        buckets[i & 0xFF00] = buckets.get(i & 0xFF00, 0) + 1
    for page, c in sorted(buckets.items()):
        print(f'      ${page:04X}xx  {c}')
    return d


def main():
    args = sys.argv[1:]
    mode = '48k'
    both = '--both' in args
    docmp = '--compare' in args
    if '--mode' in args:
        mode = args[args.index('--mode') + 1]

    outs = {}
    modes = ['48k', 'ay'] if both else [mode]
    for md in modes:
        name = {'48k': 'state_48k.pkl', 'ay': 'state_48k_ay.pkl',
                'menu': 'state_48k_menu.pkl'}[md]
        print(f'\n=== mode {md} -> build/{name} ===')
        h = make(md)
        drive(h)
        report(h, md)
        st = h.save_state()
        pickle.dump(st, open(os.path.join(ROOT, 'build', name), 'wb'))
        outs[md] = st
        print(f'  wrote build/{name}')

    if docmp:
        ref = pickle.load(open(os.path.join(ROOT, 'build',
                                            'state_charsel.pkl'), 'rb'))
        for md, st in outs.items():
            d = compare(st[0], ref[0], f'state_48k{"" if md == "48k" else "_ay"}',
                        'state_charsel')
            if len(d) < 80:
                print('      ' + ' '.join(f'${a:04X}' for a in d))
        if len(outs) == 2:
            d = compare(outs['48k'][0], outs['ay'][0], 'state_48k',
                        'state_48k_ay')
            if len(d) < 80:
                print('      ' + ' '.join(f'${a:04X}' for a in d))


if __name__ == '__main__':
    main()
