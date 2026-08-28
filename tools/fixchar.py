#!/usr/bin/env python3
"""
fixchar.py -- repair the captured state's PLAYER SPRITE BANK.

Why it is broken.  The boot code picks the player's sprite set from a byte the
LOADER leaves at $FFFF (and $FFFE for player 2).

CORRECTION, measured: this docstring used to say "$FFFD = 1/2 players".  IT IS
NOT.  $BF21 reads $FFFD and patches either $B8B5/$B8CC (the beeper) or
$BADB/$BA01/$BBA7/$BBBC and $BA2B -> JP $B92B (the AY) to RET; $9D0A reads it
again at runtime and branches between $B8B0/$B8B5 and $BA2B.  It is the
48K-beeper / 128K-AY SOUND select, auto-detected at boot by the $7FFD paging
probe block A copies to $61A8, and there is NO player-count byte anywhere in
the image: a second player is a player who has pressed FIRE.  See
notes/NOTES-engine.md, "Two players".

    $BE53  LD A,($FFFF)        ; player 1 character index
    $BE56  LD DE,$C000 / PUSH DE
    $BE5A  CALL $BEE5
    $BE64  LD A,($FFFE)        ; player 2 character index
    $BE67  LD DE,$C420 / CALL $BEE5
    $BE74  LD DE,$5F00 / POP HL / LD BC,$0840 / LDIR    ; both sets -> $5F00

    $BEE5  PUSH DE / LD E,A / SUB A / LD HL,$5F00 / LD IX,$BF19
           LD BC,$0420                                   ; 1056 = 32 records
      loop CP E / JR z / INC A / ADD HL,BC / INC IX / INC IX / JR loop
           POP DE / LDIR / LD A,(IX) / LD C,(IX+1) / RET

$5F00..$6F7F is at that moment the master table of FOUR character sprite sets
($1080 = 4 x $420), copied there by the LDIR at $BE20 out of block C's own
$C000 region.  The harness never runs the loader's menu, so $FFFF holds a stale
$2A: `ADD HL,$420` executes 42 times, HL wraps to $0C80 in the ROM, and the
"player sprites" become 1,056 bytes of ROM.  THAT is the speckled blob -- the
blitter and the sprite format were right all along.

The repair is exact and needs no boot: character n's set is
    blockC[$3C00 + n*$420 : ... + $420]
written back over $5F00 (player 1) and $6320 (player 2).

Usage:  python tools/fixchar.py [--p1 3] [--p2 0]
                                [--in build/state_charsel.pkl]
                                [--out build/state_elf.pkl]
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import tape_blocks, SIDE1                      # noqa: E402

SET = 0x420                      # 1,056 bytes = 32 records of 33
BANK = 0x5F00
BLOCKC_C000 = 0x3C00             # block C loads at $8400; $C000 - $8400
# ($8433,$8435) pairs the boot reads from $BF19 for each character
ID = {0: (0x00, 0x8E), 1: (0x08, 0xD8), 2: (0x10, 0x32), 3: (0x18, 0x64)}
NAME = {0: 'warrior(red)', 1: 'valkyrie(cyan)', 2: 'wizard(yellow)',
        3: 'elf(green)'}


def master():
    by = {}
    for flag, payload in tape_blocks(SIDE1):
        by.setdefault(flag, []).append(payload)
    C = by[0x82][0]
    return C[BLOCKC_C000:BLOCKC_C000 + 4 * SET]


def fix(mem, p1=3, p2=0):
    mt = master()
    mem[BANK:BANK + SET] = mt[p1 * SET:(p1 + 1) * SET]
    mem[BANK + SET:BANK + 2 * SET] = mt[p2 * SET:(p2 + 1) * SET]
    mem[0x8433], mem[0x8435] = ID[p1]
    mem[0x8453], mem[0x8455] = ID[p2]
    mem[0xFFFF], mem[0xFFFE] = p1, p2
    return mem


def main():
    args = sys.argv[1:]
    p1, p2 = 3, 0
    src = os.path.join(ROOT, 'build', 'state_charsel.pkl')
    dst = os.path.join(ROOT, 'build', 'state_elf.pkl')
    while args:
        if args[0] == '--p1':
            p1 = int(args[1]); del args[:2]
        elif args[0] == '--p2':
            p2 = int(args[1]); del args[:2]
        elif args[0] == '--in':
            src = args[1]; del args[:2]
        elif args[0] == '--out':
            dst = args[1]; del args[:2]
        else:
            del args[:1]
    st = pickle.load(open(src, 'rb'))
    mem = bytearray(st[0])
    fix(mem, p1, p2)
    pickle.dump((bytes(mem), st[1], st[2]), open(dst, 'wb'))
    print(f'{src} -> {dst}: player1={p1} {NAME[p1]}, player2={p2} {NAME[p2]}')


if __name__ == '__main__':
    main()
