#!/usr/bin/env python3
"""
joingate.py -- drive the REAL Z80 through PLAYER 2's JOIN-IN and print the
numbers a port must reproduce.  Same shape as deathgate.py / shotgate.py: it
never loads the engine, it only asks the original.

    python tools/joingate.py all
    python tools/joingate.py flags     where the ACTIVE/INACTIVE bits live
    python tools/joingate.py title     Z starts player 1, M starts player 2
    python tools/joingate.py join      the mid-dungeon join, before/after diff
    python tools/joingate.py origin    $9689 spawns him next to the OTHER player
    python tools/joingate.py search    the $B2B6 ring search under blocked cells
    python tools/joingate.py sprites   the six materialise frames at $F45E
    python tools/joingate.py play      he is fully simulated once he is in
    python tools/joingate.py sound     $BF21 is the 48K/128K SOUND switch
    python tools/joingate.py stun      $847E bits 4/5 come from the DUNGEON

=============================================================================
THE MODEL, IN ONE PARAGRAPH
=============================================================================
There is no one-player/two-player switch in the game block.  The game ALWAYS
carries two 32-byte player blocks and both start marked NOT IN THE GAME --
$8434 and $8454 (offset +$14) bit 7, and $842B / $844B (offset +$0B) bit 7 --
and those four bytes are INITIAL DATA in tape block C, not written by any
code.  $9432 (main loop $8509 -> $A38A, and the title loop $B47B) calls $9440
once per player per pass; $9440 tests BIT 7,(IX+$14) and FIRE, and $9451
RES 7,(IX+$14) is the single instruction in the whole image by which a player
enters the game.  So "one player" and "two players" are emergent: whoever
pressed FIRE is in, and the other can join at ANY time, at the title screen or
mid-dungeon, with exactly the same code that revives a dead player.

$BF21's read of RAM $FFFD, long suspected of being the player-count switch, is
the 48K-beeper / 128K-AY selector -- see `sound`.

EVERY MEASUREMENT BELOW IS SAMPLED AT THE MAIN-LOOP TOP $8503, once per pass,
per NOTES-engine.md.  A four-video-frame window is not a sampler.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, TAPE_CALL_PC          # noqa: E402
from keyprobe import KEYS, keymask                             # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LOOP_TOP = 0x8503

# control method 3 (the harness's stale $FFFC/$FFFB = $2A falls through to it):
#   player 1 -> $862E, player 2 -> $8657
P1 = dict(up='1', down='Q', left='S', right='D', fire='Z', shift='CAPS')
P2 = dict(up='8', down='I', left='K', right='L', fire='M', shift='SPACE')


def boot():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def press(h, name):
    sel, bit = KM[name]
    h.ports.press(sel, keymask(bit))


def step(h, limit=8_000_000):
    """Advance to the NEXT visit of $8503 -- THE per-pass sampler."""
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < limit:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('loop top not reached')


def run_raw(h, n, ints=True):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    for _ in range(n):
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            continue
        ops[mem[pc]]()
        if ints and regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)


def run_until(h, target, limit=4_000_000):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    n = 0
    while n < limit:
        if n and regs[PC] == target:
            return
        if h.deck is not None and regs[PC] == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        ops[mem[regs[PC]]]()
        n += 1
    raise RuntimeError('$%04X not reached' % target)


def blk(m, base):
    return ' '.join('%02X' % m[base + i] for i in range(32))


def cell(x, y):
    return 0x8000 + (y >> 2) * 32 + (x >> 2)


# --------------------------------------------------------------------------
def cmd_flags():
    print('THE TWO FLAGS, and the tape\'s own initial values')
    print('  +$14 bit 7  $8434 / $8454   NOT IN THE GAME (never joined, or dead)')
    print('              tested $93CD $9440 $9689 $9707 $B5EA $899F')
    print('              SET    $93EE (death)  $9460 (join refused, no free cell)')
    print('              RES    $9451  <- the ONLY way into the game')
    print('  +$0B bit 7  $842B / $844B   OUT OF PLAY FOR THIS LEVEL')
    print('              tested $A3E9/$A3F2 $A92A/$A93A $A94A/$A95A $AA8C/$AAA9')
    print('                     $ADDE/$ADE9 $AEA0/$AEC5 $AFA4/$AFC7 $AFE7/$B001')
    print('                     $B019/$B033 $B068/$B076 $94B4(level over)')
    print('              SET    $9550 (exit walk done)  $93E9 OR $C3 (death)')
    print('              RES    $970D  LD A,(IX+11) / AND $3F  (placement)')
    print('  +$0B bit 6  $842B / $844B   NOT PARTICIPATING (exiting or dead)')
    print('              tested $9009/$9035 $AACA/$AAD5 $B2EF/$B2FC $B6EE/$B6FE')
    print()
    im = open(os.path.join(ROOT, 'build', 'image.bin'), 'rb').read()
    print('  tape block C, player 1 $8420: %s' % ' '.join('%02X' % b for b in im[0x8420:0x8440]))
    print('  tape block C, player 2 $8440: %s' % ' '.join('%02X' % b for b in im[0x8440:0x8460]))
    print('  -> both +$14 = $80 and both +$0B = $C0 BEFORE ANY CODE RUNS')
    h = Harness()
    run_raw(h, 900_000)
    m = h.memobj.m
    print('  at the title screen: 8434=%02X 8454=%02X 842B=%02X 844B=%02X'
          % (m[0x8434], m[0x8454], m[0x842B], m[0x844B]))


def cmd_title():
    print('THE TITLE SCREEN IS THE JOIN: $B470 -> $B47B CALL $9432, and')
    print('$B47E LD A,($8434) / AND (IY-$2B)=$8454 / RLA / JR nc  leaves the')
    print('attract loop as soon as EITHER player is alive.')
    for key, who in (('Z', 'player 1 FIRE'), ('M', 'player 2 FIRE'), (None, 'nothing')):
        h = Harness()
        run_raw(h, 900_000)
        m = h.memobj.m
        b4 = (m[0x8434], m[0x8454])
        if key:
            press(h, key)
        run_raw(h, 400_000)
        print('  %-14s: $8434 %02X->%02X  $8454 %02X->%02X  PC=$%04X'
              % (who, b4[0], m[0x8434], b4[1], m[0x8454], h.regs[PC]))


def cmd_join():
    print('THE MID-DUNGEON JOIN, from build/state_charsel.pkl (dungeon 1).')
    h = boot()
    step(h)
    m = h.memobj.m
    before = bytes(m[0x4000:0x10000])
    print('  before  P1 %s' % blk(m, 0x8420))
    print('          P2 %s' % blk(m, 0x8440))
    press(h, P2['fire'])
    step(h)
    print('  +1 pass P2 %s' % blk(m, 0x8440))
    h.ports.release_all()
    for _ in range(8):
        step(h)
    print('  +9      P2 %s' % blk(m, 0x8440))
    after = bytes(m[0x4000:0x10000])
    print('  player-block diffs ($8400..$845F):')
    for a in range(0x8400, 0x8460):
        b, c = before[a - 0x4000], after[a - 0x4000]
        if b != c:
            print('    %04X  %02X -> %02X' % (a, b, c))
    print('  READ IT AS: +0/+1 position (next to player 1), +2/+3 health BCD 2000,')
    print('  +4..+6 score 000000, +8 keys 0, +9 potions 0, +$0B $C0->$00,')
    print('  +$0C = ($8403), +$0D/+$0E the materialise, +$14 $80->$00 (IN),')
    print('  +$15 the character attribute from $8455, +$17..+$1C the $AB6F armour row.')


def cmd_origin():
    print('$9689 SPAWNS HIM NEXT TO THE *OTHER* PLAYER, not at the level start.')
    print('  $96B4 LD A,IXL / AND $20 / LD IX,$8420 / JR z / LD IX,$8440')
    print('  -- IXL & $20 is NON-ZERO for player 1 ($8420) -- so the block it')
    print('  loads is the OTHER player\'s, and $96D0 falls back to ($8492), the')
    print('  level\'s own start cell, only when that player is still at (0,0).')
    for warp in (None, (40, 40), (72, 24), (12, 60)):
        h = boot()
        step(h)
        m = h.memobj.m
        start = (m[0x8492], m[0x8493])
        if warp:
            m[0x8420], m[0x8421] = warp
            for _ in range(40):
                step(h)
        p1 = (m[0x8420], m[0x8421])
        press(h, P2['fire'])
        step(h)
        h.ports.release_all()
        print('    level start %-8s player 1 %-9s -> player 2 at %-9s (d=%+d,%+d)'
              % (start, p1, (m[0x8440], m[0x8441]),
                 m[0x8440] - p1[0], m[0x8441] - p1[1]))


def cmd_search():
    print('$B2E3 + $B2B6: the start cell is rejected when it is non-zero OR')
    print('within 7 units of a player whose +$0B bit 6 is clear, and the ring')
    print('search then tries the eight neighbours; $96ED AND $55 keeps only the')
    print('four CARDINALS, and $96EF SCF / JR z returns FAILURE if none is free.')
    for block in ([], ['E'], ['E', 'S'], ['E', 'S', 'W'], ['E', 'S', 'W', 'N']):
        h = boot()
        step(h)
        m = h.memobj.m
        m[0x8420], m[0x8421] = 40, 40
        for _ in range(40):
            step(h)
        b = cell(40, 40)
        for k in block:
            m[b + {'E': 1, 'W': -1, 'S': 32, 'N': -32}[k]] = 0x01
        press(h, P2['fire'])
        step(h)
        h.ports.release_all()
        print('    walled %-12s -> player 2 at (%d,%d)'
              % (','.join(block) or 'nothing', m[0x8440], m[0x8441]))
    print('  and the REFUSAL arm, $9458 JR nc / $945A LD A,($8403) / DEC A / JR z:')
    for lvl in (1, 2, 7):
        h = boot()
        step(h)
        m = h.memobj.m
        m[0x8420], m[0x8421] = 40, 40
        for _ in range(40):
            step(h)
        b = cell(40, 40)
        for o in (1, -1, 32, -32):
            m[b + o] = 0x01
        m[0x8403] = lvl
        press(h, P2['fire'])
        step(h)
        step(h)
        h.ports.release_all()
        print('    dungeon %d, all four cardinals walled: (%d,%d) health %02X%02X '
              '+$14=%02X  %s'
              % (lvl, m[0x8440], m[0x8441], m[0x8442], m[0x8443], m[0x8454],
                 'JOINS ANYWAY (the $8403==1 short circuit)' if lvl == 1
                 else 'REFUSED, stays out, sound 13'))


def cmd_sprites():
    print('THE MATERIALISE.  $9689 arms it ($96A7 (IX+13)=$FF, (IX+14)=1) and')
    print('$9694 CALL $9579 repoints SIX sprite-table entries at $F45E+n*$21.')
    print('$A4F9 BIT 0,(IX+14) short-circuits the whole move routine to $A4B5')
    print('INC (IX+13); at 6 it latches (IX+13)=4 (compass slot 4 = SOUTH),')
    print('RES 0,(IX+14), and $A4C8 restores the pointers -- $5F00 for player 1')
    print('(IXL&$20 NON-zero) and $6320 for player 2.')
    h = boot()
    step(h)
    m = h.memobj.m

    def ptrs(base, n):
        return ' '.join('%04X' % (m[base + 2 * i] | (m[base + 2 * i + 1] << 8))
                        for i in range(n))
    print('  player 1 table $7C9E (sprite ids 208+, (IX+15)=$D0): %s' % ptrs(0x7C9E, 6))
    print('  player 2 table $7CCE (sprite ids 232+, (IX+15)=$E8): %s' % ptrs(0x7CCE, 6))
    press(h, P2['fire'])
    step(h)
    h.ports.release_all()
    for i in range(8):
        print('    +%d pass  (IX+13)=%02X (IX+14)=%02X   $7CCE: %s'
              % (i + 1, m[0x844D], m[0x844E], ptrs(0x7CCE, 6)))
        step(h)


def cmd_play():
    print('ONCE IN, HE IS FULLY SIMULATED: $A3A8/$A3B2 call $A4DD for both')
    print('blocks every pass, and $A39B BIT 3,(IY-$51) SWAPS THE ORDER when')
    print('$AAC4 has flagged a player-versus-player overlap.')
    h = boot()
    step(h)
    m = h.memobj.m
    press(h, P2['fire'])
    step(h)
    h.ports.release_all()
    for _ in range(6):
        step(h)
    print('  ctr  player 1        player 2        (both keys held)')
    press(h, P1['left'])
    press(h, P2['right'])
    for _ in range(8):
        step(h)
        print('   %02X   (%3d,%3d) %02X%02X   (%3d,%3d) %02X%02X'
              % (m[0x8491], m[0x8420], m[0x8421], m[0x8422], m[0x8423],
                 m[0x8440], m[0x8441], m[0x8442], m[0x8443]))


def cmd_sound():
    print('$BF21 IS THE 48K-BEEPER / 128K-AY SWITCH, NOT THE PLAYER COUNT.')
    print('  $BF21  LD A,($FFFD) / OR A / LD A,$C9 / JR z,$BF30')
    print('  $BF29  non-zero: LD ($B8B5),A / LD ($B8CC),A     kill the BEEPER')
    print('  $BF30  zero:     LD ($BADB),A / LD ($BA01),A / LD ($BBA7),A /')
    print('                   LD ($BBBC),A / ($BA2C)=$B92B / ($BA2B)=$C3')
    print('                                                  kill the AY player')
    print('  It is called ONCE, from $BEB9, and $BEE2\'s LD DE,$BE32 / JP $B358')
    print('  then LDIRs $1CE bytes of map over $BE32..$BFFF -- which is why the')
    print('  live image disassembles as graphics there.  Use build/image.bin.')
    print('  $FFFD is written by BLOCK A at $C242, from the 100-byte probe')
    print('  $CE50 (copied to $61A8 and called): it writes $C000 through the')
    print('  $7FFD paging latch and reads it back.  $C23B CP $D1 -> 48K -> 0.')
    sites = [0xB8B5, 0xB8CC, 0xBADB, 0xBA01, 0xBBA7, 0xBBBC, 0xBA2B, 0xBA2C, 0xBA2D]
    for val in (0, 1, 0x2A):
        h = Harness()
        run_until(h, 0xBEB9)
        b4 = {a: h.memobj.m[a] for a in sites}
        h.poke(0xFFFD, val)
        regs = h.sim.registers
        ret = 0x7000
        regs[12] = (regs[12] - 2) & 0xFFFF
        h.memobj.m[regs[12]] = ret & 0xFF
        h.memobj.m[regs[12] + 1] = ret >> 8
        regs[PC] = 0xBF21
        for _ in range(2000):
            if regs[PC] == ret:
                break
            h.sim.opcodes[h.sim.memory[regs[PC]]]()
        ch = ['%04X %02X->%02X' % (a, b4[a], h.memobj.m[a])
              for a in sites if b4[a] != h.memobj.m[a]]
        print('  $FFFD=%02X patches %s' % (val, ', '.join(ch)))
    for val in (0, 1):
        h = Harness()
        run_until(h, 0xBEB9)
        h.poke(0xFFFD, val)
        sim = h.sim
        regs, ops, mem = sim.registers, sim.opcodes, sim.memory
        fd, ia = h.frame_duration, h.int_active
        outs = {}
        for _ in range(3_000_000):
            pc = regs[PC]
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape()
                continue
            op = mem[pc]
            if op == 0xD3:
                p = mem[(pc + 1) & 0xFFFF]
                outs[p] = outs.get(p, 0) + 1
            elif op == 0xED and mem[(pc + 1) & 0xFFFF] == 0x79:
                p = (regs[2] << 8) | regs[3]
                outs[p] = outs.get(p, 0) + 1
            ops[op]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
        print('  $FFFD=%02X, 3M instructions: OUT ports %s   -> %s'
              % (val, {('$%04X' % p if p > 0xFF else '$%02X' % p): c
                       for p, c in sorted(outs.items())},
                 'BEEPER' if val == 0 else 'AY-3-8912'))


def cmd_stun():
    print('THE TWO-PLAYER-ONLY SHOT RULES COME OUT OF THE DUNGEON RECORD.')
    print('  $97CB  BIT 0,(IX+1) -> SET 5,(IY-1)     $847E bit 5')
    print('  $97D5  BIT 1,(IX+1) -> SET 4,(IY-1)     $847E bit 4')
    print('  $B3D4  LD ($847E),A with A=0 at every level start -- per dungeon.')
    print('  $8B37  banner: AND $30 -> print "OTHER  PLAYERS" then')
    print('         "SHOTS NOW STUN" if bit 4 else "SHOTS NOW HURT"')
    print('  $905E  bit 4 -> SET 6,(IX+$14) and (IX+$1D)=$1E   the STUN')
    print('  $906E  bit 5 -> D = 5                             5 points of hurt')
    print('         neither -> D stays 0: a player shot passes straight through.')
    import base64
    import collections
    import json
    p = json.load(open(os.path.join(ROOT, 'build', 'packdata.json')))
    blob = base64.b64decode(p['blob'])
    lens, packs = p['lens'], p['packs']
    off, offs = 0, []
    for L in lens:
        offs.append(off)
        off += L
    c = collections.Counter()
    n = 0
    for pk in packs:
        for idx in pk:
            f = blob[offs[idx] + 1]
            c[(f & 1, (f >> 1) & 1)] += 1
            n += 1
    print('  across the 307 shipped dungeons:')
    print('    record flags bit 0 ($847E bit 5, SHOTS NOW HURT): %d'
          % sum(v for k, v in c.items() if k[0]))
    print('    record flags bit 1 ($847E bit 4, SHOTS NOW STUN): %d'
          % sum(v for k, v in c.items() if k[1]))
    print('    neither: %d of %d' % (c[(0, 0)], n))


CMDS = dict(flags=cmd_flags, title=cmd_title, join=cmd_join, origin=cmd_origin,
            search=cmd_search, sprites=cmd_sprites, play=cmd_play,
            sound=cmd_sound, stun=cmd_stun)

if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    names = list(CMDS) if what == 'all' else [what]
    for nm in names:
        print('=' * 74)
        print('== %s' % nm)
        print('=' * 74)
        CMDS[nm]()
        print()
