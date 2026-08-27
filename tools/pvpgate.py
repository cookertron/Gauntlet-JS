#!/usr/bin/env python3
"""
pvpgate.py -- drive the REAL Z80 through the PLAYER-VERSUS-PLAYER rules and
print the numbers a port must reproduce.  Same shape as deathgate.py /
shotgate.py / actorgate.py: it never loads the engine, it only asks the
original.

    python tools/pvpgate.py all
    python tools/pvpgate.py join      player 2 joins mid-game with FIRE (M)
    python tools/pvpgate.py overlap   $AAC4, the 7x7 other-player gate
    python tools/pvpgate.py box       $AAC4 ENUMERATED over a 128x128 grid
    python tools/pvpgate.py push      $AAF5/$AB06, the shove
    python tools/pvpgate.py order     $A39B, the update-order swap
    python tools/pvpgate.py modes     $847E bits 4/5 -- STUN and HURT
    python tools/pvpgate.py shot      a shot into the other player, both modes
    python tools/pvpgate.py item      item competition, both players one cell
    python tools/pvpgate.py death     one player dies, the other plays on
    python tools/pvpgate.py stun      the STUN duration, in passes
    python tools/pvpgate.py thief     $A6F6 and the potion, one vs two players
    python tools/pvpgate.py quirks    the family-C idioms, incl. a patch diff

PLAYER 2 IS REACHED WITHOUT POKING ANY STATE.  In build/state_charsel.pkl his
block is (IX+$14)=$80 (DEAD) and (IX+$0E)=0, which is exactly the state
$9440 leaves a player who has not joined:

    $9440  BIT 7,(IX+$14) / RET z      a LIVE player has nothing to do here
    $9445  LD A,(IX+14) / OR A / JP nz,$92A6      a dead player mid-NAME ENTRY
    $944C  BIT 4,(IX+7) / RET z        ... otherwise he is waiting on FIRE

so pressing M (player 2's fire, $8447 bit 4) runs the rejoin at $9451..$94AB
and puts him in the game.  That is the arcade's drop-in join and it is the
same code path a two-player game starts through.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, F, IFF, SP, FRAME_T, TAPE_CALL_PC   # noqa: E402
from keyprobe import KEYS, keymask                                   # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LOOP_TOP = 0x8503

P1 = dict(up='1', down='Q', left='S', right='D', fire='Z', shift='CAPS')
P2 = dict(up='8', down='I', left='K', right='L', fire='M', shift='SPACE')

P1B, P2B = 0x8420, 0x8440


def boot():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def press(h, *names):
    h.ports.release_all()
    for n in names:
        sel, bit = KM[n]
        h.ports.press(sel, keymask(bit))


def run_until_pc(h, targets, limit=20_000_000):
    if isinstance(targets, int):
        targets = (targets,)
    targets = set(targets)
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t0, n = regs[T], 0
    while n < limit:
        pc = regs[PC]
        if n and pc in targets:
            return (pc, regs[T] - t0, n)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return (None, regs[T] - t0, n)


def pass_top(h):
    return run_until_pc(h, LOOP_TOP)


def join2(h, at=2, settle=0):
    """Bring player 2 into the game with FIRE.  Returns after `settle` more
    passes with no keys held."""
    pass_top(h)
    for i in range(at):
        pass_top(h)
    press(h, P2['fire'])
    m = h.memobj.m
    for i in range(6):
        pass_top(h)
        if m[0x8454] & 0x80 == 0:
            break
    press(h)
    for i in range(settle):
        pass_top(h)
    return h


def row(m, ctr=True):
    return ('%3d  p1=(%3d,%3d) hp=%02X%02X sc=%02X%02X%02X d=%02X f14=%02X i1D=%02X | '
            'p2=(%3d,%3d) hp=%02X%02X sc=%02X%02X%02X d=%02X f14=%02X i1D=%02X'
            % (m[0x8491],
               m[0x8420], m[0x8421], m[0x8422], m[0x8423], m[0x8424], m[0x8425], m[0x8426],
               m[0x8427], m[0x842E], m[0x843D],
               m[0x8440], m[0x8441], m[0x8442], m[0x8443], m[0x8444], m[0x8445], m[0x8446],
               m[0x8447], m[0x844E], m[0x845D]))


# ------------------------------------------------------------------- join
def cmd_join():
    print("""
=============================================================================
PLAYER 2 JOINS MID-GAME WITH FIRE -- $9440 .. $94AB
=============================================================================
$8509 CALL $A38A -> $A38A CALL $9432 -> $9435 IX=$8420 CALL $9440, then
$943C LD IX,$8440 and FALL THROUGH into $9440 a second time.  (The routine is
CALLed for player 1 and fallen into for player 2; its RET serves both.)
""")
    h = boot(); m = h.memobj.m
    pass_top(h)
    print('  p2 before: +00..+02 %02X %02X %02X  +0B=%02X +0E=%02X +14=%02X'
          % (m[0x8440], m[0x8441], m[0x8442], m[0x844B], m[0x844E], m[0x8454]))
    before = bytes(m[0x8440:0x8460])
    print()
    print('ctr  p1                                          p2')
    for i in range(9):
        if i == 2:
            press(h, P2['fire'])
        if i == 5:
            press(h)
        pass_top(h)
        print(row(m))
    after = bytes(m[0x8440:0x8460])
    print()
    print('  p2 block diff (+off  before -> after):')
    for i in range(32):
        if before[i] != after[i]:
            print('     +%02X  %02X -> %02X' % (i, before[i], after[i]))
    print("""
  $9451 RES 7,(IX+$14)            no longer dead
  $9455 CALL $9689                the placement search picks the cell
  $946E..$9483 zeroes +3 +4 +5 +6 +8 +9 +10   health low, SCORE, keys,
                                  potions, and the power-up timer
  $9484 LD (IX+$10),$FF           writes the SHOT'S X, not its state (+$12):
                                  the rejoin does not free the shot
  $9488 LD (IX+2),$20             health := BCD 2000
  $948C (IX+$14) &= $80           the inventory is wiped
  $9494 LD A,IXL / AND $20        picks $5080 (p1) or $5091 (p2) and clears
                                  $84C8 / $84C9, then JP $B5E8 repaints
""")


# ---------------------------------------------------------------- overlap
def cmd_overlap():
    print("""
=============================================================================
$AAC4 -- THE OTHER-PLAYER OVERLAP GATE ($A616 CALL $AAC4 / RET c)
=============================================================================
$AAC4  LD A,IXL / AND $20 / JR nz,$AAD5     IXL is $20 for p1 and $40 for p2,
                                            so the SET bit selects the OTHER
$AACA  BIT 6,(IY-$54)=$842B / RET nz        p2 moving -> p1 out of play?
$AACF  LD DE,($8420)                        ... the other player's (x,y)
$AAD5  BIT 6,(IY-$34)=$844B / RET nz        p1 moving -> p2 out of play?
$AADA  LD DE,($8440)
$AADE  LD A,E / ADD 3 / SUB C / AND $7F / CP 7 / RET nc
$AAE7  LD A,D / ADD 3 / SUB B / AND $7F / CP 7 / RET nc
$AAF0  SET 3,(IX+14) / RET                  carry is SET by the last CP
""")
    h = boot(); m = h.memobj.m
    join2(h, settle=2)
    print('  p1 (%d,%d)  p2 (%d,%d)   -- one cell apart on x' %
          (m[0x8420], m[0x8421], m[0x8440], m[0x8441]))
    print()
    print('  PLAYER 1 HOLDS RIGHT INTO PLAYER 2:')
    print('ctr  p1                                          p2')
    press(h, P1['right'])
    for i in range(10):
        pass_top(h)
        print(row(m))
    print("""
  f14 bit 3 ($08) is $AAC4's flag.  The pair settles into a TWO-PASS LIMIT
  CYCLE and both advance 2 units every 2 passes -- HALF the normal 2/pass:

    ODD  $8491: p1's flag is clear, so $A3A1 runs p1 FIRST; his candidate is
                inside the box, $AAF0 sets the flag and $A619 RET c refuses
                the commit.  p2 has no direction (nothing pushed him) and
                does not move.  NOTHING MOVES.
    EVEN $8491: p1's flag is set, so $A3BE runs $AAF5 (which gives p2 RIGHT)
                and then updates p2 FIRST.  p2 steps 2 units clear.  Only
                then does p1 move, and $AAC4 now measures against p2's NEW
                position, 4 units away -- outside the box -- so p1 commits.

  So the shove is not p1 displacing p2: it is the ORDER SWAP letting p2 out
  of the way before p1's own gate is evaluated.  Getting $A39B's order
  backwards makes the pair lock solid instead of walking.
""")
    # and the same test with player 2 marked out of play
    h = boot(); m = h.memobj.m
    join2(h, settle=2)
    h.poke(0x844B, m[0x844B] | 0x40)
    press(h, P1['right'])
    x0 = m[0x8420]
    for i in range(6):
        pass_top(h)
        h.poke(0x844B, m[0x844B] | 0x40)
    print('  WITH $844B BIT 6 FORCED SET (p2 "out of play"):  p1 x %d -> %d'
          % (x0, m[0x8420]))
    print('  -- $AAD9 RET nz fires and p1 walks straight through him.')


# -------------------------------------------------------------------- box
def cmd_box():
    """ENUMERATE $AAC4 over the whole 128x128 world (manual: enumerate the
    reachable outputs, do not read the algebra)."""
    print("""
=============================================================================
$AAC4 ENUMERATED -- every (dx,dy) in the 128x128 world
=============================================================================
Called in isolation with IX=$8420, BC = the candidate, and player 2's block
planted at every offset.  Reports the exact set of offsets that set bit 3.
""")
    h = boot()
    m = h.memobj.m
    h.poke(0x844B, m[0x844B] & ~0x40)          # p2 in play
    hits = set()
    sp0 = h.regs[SP]
    for dy in range(128):
        for dx in range(128):
            cx, cy = 40, 40
            h.regs[SP] = sp0
            h.poke(0x8440, (cx + dx) & 0x7F)
            h.poke(0x8441, (cy + dy) & 0x7F)
            h.poke(0x842E, 0)
            h.call(0xAAC4, regs=dict(IX=0x8420, BC=(cy << 8) | cx, IY=0x847F))
            if m[0x842E] & 0x08:
                hits.add((dx, dy))
    h.regs[SP] = sp0
    xs = sorted({d for d, _ in hits})
    ys = sorted({d for _, d in hits})
    sx = sorted(x - 128 if x > 63 else x for x in xs)
    sy = sorted(y - 128 if y > 63 else y for y in ys)
    print('  offsets that set bit 3 : %d' % len(hits))
    print('  dx (signed, short way) : %s' % sx)
    print('  dy (signed, short way) : %s' % sy)
    print('  is it the full product : %s' % (len(hits) == len(xs) * len(ys)))
    print()
    print('  -> the box is |other.x - candidate.x| <= 3 AND')
    print('               |other.y - candidate.y| <= 3, wrapped mod 128;')
    print('     i.e. 7x7 UNITS = 1.75 cells = 28 screen pixels each way.')
    # carry discipline
    h.regs[SP] = sp0
    h.poke(0x8440, 40); h.poke(0x8441, 40)
    h.call(0xAAC4, regs=dict(IX=0x8420, BC=(40 << 8) | 40, IY=0x847F, F=0))
    print('  carry on contact       : %d   (the RET c at $A619 refuses the move)'
          % (h.regs[F] & 1))
    h.regs[SP] = sp0
    h.poke(0x8440, 100)
    h.call(0xAAC4, regs=dict(IX=0x8420, BC=(40 << 8) | 40, IY=0x847F, F=0))
    print('  carry on no contact    : %d' % (h.regs[F] & 1))
    h.poke(0x8440, 40)
    h.poke(0x844B, m[0x844B] | 0x40)
    for want in (0, 1):
        h.regs[SP] = sp0
        h.regs[F] = want
        h.call(0xAAC4, regs=dict(IX=0x8420, BC=(40 << 8) | 40, IY=0x847F))
        print('  other player OUT OF PLAY, entered carry=%d -> returned carry=%d'
              % (want, h.regs[F] & 1))
    print('  -- the two RET nz exits look like a carry LEAK (BIT does not touch')
    print('     carry), but $AAC6 AND $20 has already cleared it, so the routine')
    print('     really does return carry SET only on contact.  A port may write')
    print('     "return true iff both axes are within 3" with no caveat.')
    h.poke(0x844B, m[0x844B] & ~0x40)


# ------------------------------------------------------------------- push
def cmd_push():
    print("""
=============================================================================
$AAF5 / $AB06 -- THE SHOVE
=============================================================================
$AAF5  RES 3,(IY-$51)=$842E  BC=($8420) IX=$8440 A=($8427)   p1 pushes p2
$AB06  RES 3,(IY-$31)=$844E  BC=($8440) IX=$8420 A=($8447)   p2 pushes p1
$AB15  BIT 0,(IY+$12) / RET nz          $8491 EVEN passes only
$AB1A  LD E,A / AND 15 / RET z          the PUSHER must hold a direction
$AB1E  LD A,(IX+7) / AND 15 / RET nz    the PUSHED must hold NONE
$AB24  SET 4,(IX+14)                    ... the fire-freeze exemption bit
$AB28  BIT 0,E: (B-4)&$7F == (IX+1) -> SET 0,(IX+7)     up
$AB3A  BIT 1,E: (B+4)&$7F == (IX+1) -> SET 1,(IX+7)     down
$AB4C  BIT 2,E: (C-4)&$7F == (IX)   -> SET 2,(IX+7)     left
$AB5E  BIT 3,E: (C+4)&$7F == (IX)   -> SET 3,(IX+7)     right
Note each arm tests ONLY the axis it pushes along: the perpendicular
coordinate is not consulted at all.
""")
    h = boot(); m = h.memobj.m
    join2(h, settle=2)
    print('  p1 (%d,%d) holds RIGHT; p2 (%d,%d) holds NOTHING'
          % (m[0x8420], m[0x8421], m[0x8440], m[0x8441]))
    print('ctr  p1                                          p2')
    press(h, P1['right'])
    for i in range(16):
        pass_top(h)
        print(row(m))
    print("""
  p2's d= byte shows $08 (right) appearing on the pushed passes and p2's x
  advancing 2 units on each of them.  p1 stays put: his own move is refused
  by $AAC4 every pass.  The shove is therefore ONE-WAY and HALF RATE.
""")
    # the pushed player resisting
    h = boot(); m = h.memobj.m
    join2(h, settle=2)
    press(h, P1['right'], P2['left'])
    x1, x2 = m[0x8420], m[0x8440]
    for i in range(12):
        pass_top(h)
    print('  RESISTING: p1 holds RIGHT and p2 holds LEFT for 12 passes')
    print('    p1 x %d -> %d     p2 x %d -> %d' % (x1, m[0x8420], x2, m[0x8440]))
    print('    -- $AB1E RET nz: a player holding ANY direction cannot be pushed.')


# ------------------------------------------------------------------ order
def cmd_order():
    print("""
=============================================================================
$A39B -- THE CONTACT BIT SWAPS THE UPDATE ORDER
=============================================================================
$A39B  BIT 3,(IY-$51)=$842E / JR nz,$A3BE       p1 touched p2 last pass
$A3A1  BIT 3,(IY-$31)=$844E / CALL nz,$AB06     ... CONDITIONAL
$A3A8  IX=$8420 HL=$84C1 CALL $A4DD             p1 moves FIRST
$A3B2  IX=$8440 HL=$84C2 CALL $A4DD             then p2
$A3BC  JR $A3DC
$A3BE  CALL $AAF5                               p1 pushes p2
$A3C1  IX=$8440 HL=$84C2 CALL $A4DD             p2 moves FIRST
$A3CB  BIT 3,(IY-$31)                           <-- flags computed and DROPPED
$A3CF  CALL $AB06                               ... UNCONDITIONAL here
$A3D2  IX=$8420 HL=$84C1 CALL $A4DD             then p1
""")
    h = boot()
    join2(h, settle=2)
    press(h, P1['right'])
    seen = {}
    for i in range(12):
        # hook: which of $A3A8 / $A3C1 is reached first this pass
        pc, _, _ = run_until_pc(h, (0x8503,))
        pc2, _, _ = run_until_pc(h, (0xA3A8, 0xA3C1))
        seen[pc2] = seen.get(pc2, 0) + 1
        run_until_pc(h, (0x8503,))
    print('  first player updated, over 12 passes of p1 walking into p2:')
    for k, v in sorted(seen.items()):
        print('    %s : %d' % ({0xA3A8: '$A3A8  p1 first', 0xA3C1: '$A3C1  p2 first'}[k], v))


# ------------------------------------------------------------------ modes
def cmd_modes():
    print("""
=============================================================================
"OTHER  PLAYERS / SHOTS NOW STUN" and "... HURT"  --  $847E bits 4 and 5
=============================================================================
The strings are LENGTH-PREFIXED records in the table at $7E00..$7F1B:
    $7EDD 10 "FIND THE  POTION"     $7EEE 0E "OTHER  PLAYERS"
    $7EFD 0E "SHOTS NOW STUN"       $7F0C 0E "SHOTS NOW HURT"
and the level-intro screen picks them at $8B37:
    $8B37  LD A,($847E) / AND $30 / JR z,$8B5C     neither bit -> no banner
    $8B3E  IX=$7EEE ("OTHER  PLAYERS") DE=$1020 CALL $8A08
    $8B48  DE=$1000 / IX=$7EFD ("SHOTS NOW STUN")
    $8B4F  BIT 4,(IY-1) / JR nz,$8B59              bit 4 -> STUN
    $8B55  IX=$7F0C ("SHOTS NOW HURT")             else   -> HURT
THE STATE IS SET BY THE DUNGEON RECORD ITSELF, at the top of $97CB:
    $97CB  BIT 0,(IX+1) / JR z / SET 5,(IY-1)      record flags b0 -> HURT
    $97D5  BIT 1,(IX+1) / JR z / SET 4,(IY-1)      record flags b1 -> STUN
""")
    d = open(os.path.join(ROOT, 'build', 'live_cs.bin'), 'rb').read()
    # every RES/SET on $847E in the whole image
    kind = {0x60: 'BIT4', 0x68: 'BIT5', 0xE6: 'SET4', 0xEE: 'SET5',
            0xA6: 'RES4', 0xAE: 'RES5'}
    print('  every instruction in the 64K image that touches $847E bit 4 or 5:')
    for i in range(len(d) - 3):
        if d[i] == 0xFD and d[i + 1] == 0xCB and d[i + 2] == 0xFF and d[i + 3] in kind:
            print('     $%04X  %s' % (i, kind[d[i + 3]]))
    for i in range(len(d) - 2):
        if d[i] in (0x3A, 0x32) and d[i + 1] == 0x7E and d[i + 2] == 0x84:
            print('     $%04X  %s' % (i, 'LD A,($847E)' if d[i] == 0x3A else 'LD ($847E),A'))
    print("""  -- there is no RES 4 and no RES 5 anywhere, but the mode is NOT sticky:
     $B3D4 sits inside the PER-LEVEL load $B3D0, three instructions before
     $B3DD CALL $9175 and $B3E0 CALL $97CB.  So every level zeroes $847E and
     then re-derives bits 4/5 from the incoming record's own flags byte.
     $B382 CALL $B3D0 / $B38E CALL $8B27 (the banner) / $B391 CALL $8503.
""")
    # how many of the 307 dungeon records ask for each mode
    import base64
    import json
    p = os.path.join(ROOT, 'build', 'packdata.json')
    if not os.path.exists(p):
        return
    pd = json.load(open(p))
    blob = base64.b64decode(pd['blob'])
    offs, o = [], 0
    for L in pd['lens']:
        offs.append((o, L)); o += L
    recs = [blob[offs[i][0]:offs[i][0] + offs[i][1]] for pk in pd['packs'] for i in pk]
    print('  the flags byte over all %d dungeon records on the tape.  b2/b6/b7')
    print('  are the counts NOTES-engine.md already records (114 / 7 / 52), so')
    print('  they validate that this is the right byte:')
    for b in range(8):
        n = sum(1 for r in recs if (r[1] >> b) & 1)
        note = {0: '  <- HURT ($847E bit 5)', 1: '  <- STUN ($847E bit 4)',
                2: '  (the $9B5F pass)', 6: '  (suppress $5A3E clear)',
                7: '  (no left wall column)'}.get(b, '')
        print('     b%d : %3d of %d%s' % (b, n, len(recs), note))
    print()
    print('  ** NO RECORD ON THE TAPE SETS BIT 0. **  "SHOTS NOW HURT" and the')
    print('     5-point friendly fire at $9074 are unreachable from the shipped')
    print('     dungeon data; only STUN ever fires.  A port may implement HURT')
    print('     for completeness but it will never be entered.')
    print('  STUN dungeons, per pack:',
          [sum(1 for i in pk if (blob[offs[i][0] + 1] & 2)) for pk in pd['packs']])
    print('  dungeons 1-7 (pack 0) flags: %s  -- no mode in the fixed dungeons'
          % [r[1] & 3 for r in recs[:7]])


# ------------------------------------------------------------------- shot
def cmd_shot():
    print("""
=============================================================================
$9009 -- A SHOT HITS A PLAYER, AND WHAT THE TWO MODES DO
=============================================================================
$9009  BIT 6,(IY-$54)=$842B / JR nz,$9035   p1 out of play -> skip him
$900F  ($8420)+2-C & $7F < 5   and  ($8421)+2-B & $7F < 5       a 5x5 box
$9027  CALL $908E  -> D = the damage:  monster shot 10, or 3 if flags bit 6
                     A PLAYER'S OWN SHOT LEAVES D = 0
$902A  PUSH IX / IX = the victim's block / HL = his health
$905C  JR nz,$9076                          (D != 0: a monster shot)
$905E  BIT 4,(IY-1) / JR z,$906E            $847E bit 4 -- STUN
$9064  SET 6,(IX+$14) / LD (IX+$1D),$1E     ... and D stays 0: NO DAMAGE
$906E  BIT 5,(IY-1) / JR z,$9076            $847E bit 5 -- HURT
$9074  LD D,5                               ... 5 BCD points
$9076  LD A,D / OR A / JR z,$9085           D==0 -> no health write at all
$907A  SET 0,(IX+11) / CALL $B852           health -= D, clamped at 0000
$9081  LD (IY+$39),0                        $84B8, the hurry-up, is reset
$9085  POP IX / LD A,8 / CALL $BA2B / SCF / RET     sound 8, and CARRY SET
                                            -> the shot is destroyed either way
""")
    for name, bits in (('neither bit ($847E &= ~$30)', 0x00),
                       ('STUN  ($847E bit 4)', 0x10),
                       ('HURT  ($847E bit 5)', 0x20),
                       ('BOTH  (bits 4 and 5)', 0x30)):
        h = boot(); m = h.memobj.m
        join2(h, settle=2)
        h.poke(0x847E, (m[0x847E] & ~0x30) | bits)
        # park p2 four cells to the right of p1 on the same row, in the open
        h.poke(0x8420, 12); h.poke(0x8421, 40)
        h.poke(0x8440, 32); h.poke(0x8441, 40)
        for i in range(30):          # let the camera settle
            pass_top(h)
            h.poke(0x847E, (m[0x847E] & ~0x30) | bits)
        hp0 = (m[0x8442], m[0x8443])
        f14 = m[0x8454]; i1d = m[0x845D]
        press(h, P1['fire'], P1['right'])
        hits = 0
        log = []
        for i in range(40):
            pass_top(h)
            h.poke(0x847E, (m[0x847E] & ~0x30) | bits)
            if (m[0x8442], m[0x8443]) != hp0 or m[0x8454] != f14 or m[0x845D] != i1d:
                log.append((m[0x8491], m[0x8442], m[0x8443], m[0x8454], m[0x845D]))
                hp0 = (m[0x8442], m[0x8443]); f14 = m[0x8454]; i1d = m[0x845D]
        press(h)
        print('  --- %-28s p2 health %02X%02X  p2 +$14 %02X  p2 +$1D %02X'
              % (name, m[0x8442], m[0x8443], m[0x8454], m[0x845D]))
        for c, a, b, f, j in log[:8]:
            print('        ctr %3d  hp %02X%02X  +$14 %02X  +$1D %02X' % (c, a, b, f, j))
        if not log:
            print('        (nothing changed in 40 passes)')


# ------------------------------------------------------------------- item
CONTEND_HOOKS = {0xA919: 'REC', 0xA65D: 'USE', 0xA6CA: 'DMG',
                 0xA3A8: 'ORDER1', 0xA3C1: 'ORDER2'}


def contend(h, cellval, npass=6, keys=0, melee=False, force_contact=False):
    """Both players probe map cell (4,10) on the SAME pass.

    p1 stands at (12,40) and holds RIGHT: his cursor is on the OLD x, so his
    first probe (the BIT 1 gate passes on candidate 14) reads column 4.
    p2 stands at (16,36) and holds DOWN: candidate 38 passes the gate and his
    probe reads row 10.  They are 4 units apart on x and 4 on y, which is
    OUTSIDE $AAC4's 7x7 box, so neither blocks the other.
    """
    m = h.memobj.m
    h.poke(0x8420, 12); h.poke(0x8421, 40)
    h.poke(0x8440, 16); h.poke(0x8441, 36)
    h.poke(0x8424, 0, 0, 0); h.poke(0x8444, 0, 0, 0)
    if keys:
        h.poke(0x8428, keys); h.poke(0x8448, keys)
    if melee:
        h.poke(0x8434, m[0x8434] | 0x20); h.poke(0x8454, m[0x8454] | 0x20)
    cell = 0x8144
    h.poke(cell, cellval)
    sim = h.sim
    regs, opc, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    press(h, P1['right'], P2['down'])
    from harness import IXh, IXl
    out = []
    for p in range(npass):
        if force_contact:
            h.poke(0x842E, m[0x842E] | 0x08)
        ctr = m[0x8491]; ev = []; order = '-'; n = 0
        while n < 400_000:
            pc = regs[PC]
            if n and pc == LOOP_TOP:
                break
            if pc in CONTEND_HOOKS:
                ix = regs[IXh] * 256 + regs[IXl]
                who = 'p1' if ix == 0x8420 else 'p2' if ix == 0x8440 else hex(ix)
                if pc == 0xA3A8:
                    order = 'p1 first'
                elif pc == 0xA3C1:
                    order = 'p2 FIRST'
                else:
                    ev.append(CONTEND_HOOKS[pc] + who)
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape(); n += 1; continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt(); n += 1; continue
            opc[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
            n += 1
        out.append((ctr, order, ' '.join(ev) or '-', m[cell],
                    m[0x8428], m[0x8448],
                    (m[0x8424], m[0x8425], m[0x8426]), (m[0x8444], m[0x8445], m[0x8446]),
                    (m[0x8422], m[0x8423]), (m[0x8442], m[0x8443])))
    press(h)
    return out


def show_contend(rows, title):
    print('  --- %s' % title)
    print('      ctr order      events                   cell k1 k2 score1 score2 hp1  hp2')
    for c, o, e, cell, k1, k2, s1, s2, h1, h2 in rows:
        print('      %3d %-10s %-24s %02X   %d  %d  %02X%02X%02X %02X%02X%02X %02X%02X %02X%02X'
              % (c, o, e, cell, k1, k2, s1[0], s1[1], s1[2], s2[0], s2[1], s2[2],
                 h1[0], h1[1], h2[0], h2[1]))


def cmd_item():
    print("""
=============================================================================
ITEM COMPETITION -- both players probing the same cell on the same pass
=============================================================================
Each player's pending interaction is his OWN (IX+$1D/$1E/$1F): $A919 records
it during his own move and $A65D consumes it inside the SAME $A4DD, three
instructions later.  There is no arbitration and no queue.  What the two
players share is the MAP, and the consumer that runs first has already
rewritten the cell before the loser's probe reads it.

WHICH PLAYER RUNS FIRST IS $A39B, and $A39B reads $842E bit 3 -- the flag
$AAC4 set when p1's candidate overlapped p2 LAST pass.  So the player who
touched the other loses the next race.
""")
    for val, name in ((0x13, 'TREASURE $13 -- the consumer ZEROES the cell'),
                      (0x1F, 'KEY $1F      -- the consumer ZEROES the cell'),
                      (0x12, 'DOOR $12     -- the consumer does NOT clear the cell'),
                      (0x22, 'GENERATOR $22 (neither player has the melee bit)')):
        h = boot(); join2(h, settle=2)
        for i in range(25):
            pass_top(h)
        show_contend(contend(h, val, npass=4, keys=5), name)
    h = boot(); join2(h, settle=2)
    for i in range(25):
        pass_top(h)
    show_contend(contend(h, 0x22, npass=8, melee=True),
                 'GENERATOR $22 with BOTH players holding inventory bit 5')
    print("""
  MEASURED:
    $13 / $1F   ONE player only.  The loser's $A919 never fires -- by the time
                his probe runs the cell already reads 0.  He is not even
                charged the refused pass.
    $12 DOOR    BOTH record and BOTH consume, and both are refused the pass --
                but only ONE key is spent.  $A6D4 opens with
                    LD A,($849E) / AND $88 / RET nz
                and there is exactly one global door-animation slot
                ($849A/$849C/$849E), so the second player finds it armed and
                returns before $A6DF DEC (IX+8).
    $20..$2E    BOTH melee it in the same pass and BOTH points of damage land:
                two players kill a generator in half the passes.  Without
                inventory bit 5 the cell BLOCKS instead ($A8FC), so neither
                player records anything and both simply stop.
""")
    print('  PROOF THAT THE ORDER IS THE WHOLE MECHANISM -- the same treasure')
    print('  scenario twice, with $842E bit 3 forced set on the second run:')
    for force, lab in ((False, '$842E bit 3 clear  -> $A3A1 arm, p1 first'),
                       (True, '$842E bit 3 forced -> $A3BE arm, p2 FIRST')):
        h = boot(); join2(h, settle=2)
        for i in range(25):
            pass_top(h)
        show_contend(contend(h, 0x13, npass=3, force_contact=force), lab)


# ------------------------------------------------------------------ death
def cmd_death():
    print("""
=============================================================================
ONE PLAYER DIES, THE OTHER PLAYS ON -- $93C2/$93CD and $94AE
=============================================================================
$93C2  LD IX,$8420 / CALL $93CD / LD IX,$8440 / [fall through]
$93CD is per-player throughout: nothing it writes is shared except the map
cell it drops the loot on and $5BE8/$84C0.
$94AE  BIT 3,(IY-2) / JR nz,$94D1
$94B4  LD A,($842B) / AND (IY-$34)=($844B) / RLA / JR nc,$94D6
       -- BOTH players' (IX+11) bit 7 must be set before the level can end
$94C3  LD A,($842C) / CP (IY-$33)=($844C) / JR nc / LD A,($844C)
$94CE  LD ($8403),A          the next dungeon is the MAX of the two
""")
    h = boot(); m = h.memobj.m
    join2(h, settle=2)
    print('  killing player 1 by poking his health to $0000; p2 walks RIGHT.')
    h.poke(0x8422, 0x00); h.poke(0x8423, 0x00)
    press(h, P2['right'])
    print('ctr  p1                                          p2         $847D $842B $844B')
    for i in range(10):
        pc, _, _ = run_until_pc(h, (LOOP_TOP, 0x855C))
        print(row(m) + '   %02X   %02X   %02X %s'
              % (m[0x847D], m[0x842B], m[0x844B],
                 '  <-- MAIN LOOP RETURNED' if pc == 0x855C else ''))
        if pc == 0x855C:
            break
    print("""
  p2 keeps moving and the loop keeps running.  p1's +$0E goes 00 -> 80, which
  is $93E2 LD (IX+14),$80 -- and $9445 LD A,(IX+14) / OR A / JP nz,$92A6 then
  sends him into HIS OWN name entry ($92A6 uses $5086 for p1 and $5097 for
  p2), on his own half of the panel, while p2 is still playing.
""")
    # now confirm the level does not end
    print('  $847D bit 7 (levelDone) after 10 passes with only p1 dead: %d'
          % (1 if m[0x847D] & 0x80 else 0))
    # and the rejoin
    press(h, P1['fire'])
    for i in range(8):
        pass_top(h)
    press(h)
    print('  p1 presses FIRE (Z): +$14=%02X  health=%02X%02X  score=%02X%02X%02X  (%d,%d)'
          % (m[0x8434], m[0x8422], m[0x8423], m[0x8424], m[0x8425], m[0x8426],
             m[0x8420], m[0x8421]))


# ------------------------------------------------------------------ thief
def cmd_thief():
    print("""
=============================================================================
THE THIEF ($A6F6) AND THE POTION IN TWO-PLAYER PLAY
=============================================================================
$A6F6  SET 7,(IY+$3A)=$84B9        the banner selector
$A6FA  LD ($84B4),IX               ... and WHICH PLAYER it happened to
$A6FE  LD A,(IX+$14) / AND $3F / JR nz,$A720      holding any of the 6 items?
$A705  LD A,(IX+9) / OR A / JR z,$A718             no items: steal a POTION
$A70B  BIT 5,(IX+11) / JR nz,$A718                 ... unless the HUD flag is up
$A711  DEC (IX+9) / SET 5,(IX+11)
$A718  CALL $A7C3 / LD D,$99 / JP $B83C            ... AND FALLS THROUGH: -99
$A720  ... has items: $B575 picks one of 6 and clears it, and never reaches
       $A718, so an item theft costs no health at all
""")
    h = boot(); m = h.memobj.m
    join2(h, settle=2)
    for i in range(10):
        pass_top(h)
    sp0 = h.regs[SP]
    print('  $A6F6 in isolation (IX = the victim, HL = his cell):')
    print('   inv  pot | health          inv   pot  $84B4  $84B9')
    for inv, pot in ((0x00, 0), (0x00, 3), (0x3F, 0), (0x01, 2)):
        h.regs[SP] = sp0
        h.poke(0x8422, 0x19, 0x95)
        h.poke(0x8434, (m[0x8434] & 0xC0) | inv)
        h.poke(0x8429, pot)
        h.poke(0x842B, m[0x842B] & ~0x20)
        h.poke(0x84B4, 0, 0); h.poke(0x84B9, 0)
        h.poke(0x8144, 0x31)
        h.call(0xA6F6, regs=dict(IX=0x8420, IY=0x847F, HL=0x8144))
        print('   %02X   %d   | 1995 -> %02X%02X    %02X    %d   $%04X  %02X'
              % (inv, pot, m[0x8422], m[0x8423], m[0x8434] & 0x3F, m[0x8429],
                 m[0x84B4] | (m[0x84B5] << 8), m[0x84B9]))
    h.regs[SP] = sp0
    print("""
  $A705..$A717 FALLS THROUGH into $A718.  With no inventory item you lose a
  potion AND 99 health -- not one or the other.  (NOTES-engine.md's "health
  $1997 -> $1898 (or one item stolen)" should read "and".)

  IN TWO-PLAYER PLAY the thief is per-player except for one thing: $A7CE
  LD (HL),0 clears the cell, so only the player whose $A65D runs first is
  ever robbed -- the same $A39B order that decides every other item race.
  $84B4/$84B9 are a ONE-SLOT global announcement channel ($8935 reads $84B9,
  $893E LD IX,($84B4)); two announcements in one pass keep only the last.
  ($AAC4 aside: LD ($84B4),IX is a 16-bit store, but $84B5 is read by nothing
  else in the image -- it really is just the pointer's high byte.)
""")
    print('  THE POTION IS ALSO A SINGLE GLOBAL SLOT:')
    print('    $A566 LD ($84A6),IX      the THROWER, so $8BCA pays him the score')
    print('    $A56A SET 3,(IY-1)       $847E bit 3 = a potion is in flight')
    print('    $A524 BIT 3,(IY-1) / JR nz,$A57E')
    print('    -> while EITHER potion is live NEITHER player can throw one.')
    print('    $A51E LD A,($84A1) / OR A / JR nz,$A57E is a second global gate.')


# ----------------------------------------------------------------- quirks
def cmd_quirks():
    print("""
=============================================================================
THE FAMILY-C IDIOMS IN THE TWO-PLAYER PATHS
=============================================================================
1. FALL-THROUGH INSTEAD OF A SECOND CALL.  Three routines are CALLed for
   player 1 and FALLEN INTO for player 2, the single RET serving both:
       $9439 CALL $9440 / $943C LD IX,$8440 / $9440 ...   the dead handler
       $93C6 CALL $93CD / $93C9 LD IX,$8440 / $93CD ...   death
       $94DA CALL $94E1 / $94DD LD IX,$8440 / $94E1 ...   the exit walk
2. TWO ENTRY POINTS, ONE BODY.  $AAF5 and $AB06 differ only in which block
   goes into BC/IX/A; both fall into the shared body at $AB15.
3. FLAGS CARRIED ACROSS UNRELATED INSTRUCTIONS.  $9009's two arms end
       CALL $908E / PUSH IX / LD IX,$84x0 / LD HL,$84x3 / $905C JR nz
   and the Z tested at $905C is still $908E's ($9094 RET z when D=0, else
   $909F INC D).  PUSH and LD rr,nn do not touch flags.  A port that inserts
   any flag-affecting statement between the damage lookup and the branch
   turns every player shot into a monster shot.
4. A CONDITIONAL TEST WHOSE RESULT IS DISCARDED -- and it is observable.
""")

    def walk(patch, n=8):
        h = boot(); m = h.memobj.m
        join2(h, settle=2)
        for i in range(20):
            pass_top(h)
        if patch:
            h.poke(0xA3CF, 0xC4)             # CALL -> CALL nz, same length
        h.poke(0x8420, 60); h.poke(0x8421, 46)
        h.poke(0x8440, 32); h.poke(0x8441, 40)
        press(h, P2['down'])
        out = []
        for i in range(n):
            h.poke(0x842E, m[0x842E] | 0x08)
            pass_top(h)
            out.append((m[0x8491], m[0x8420], m[0x8421], m[0x8427],
                        m[0x8440], m[0x8441]))
        press(h)
        return out
    a, b = walk(False), walk(True)
    print('   $A3CB BIT 3,(IY-$31) / $A3CF CALL $AB06  -- the CALL is')
    print('   UNCONDITIONAL, while the mirror-image site $A3A5 is CALL nz.')
    print('   So on the swapped branch $AB06 runs whether or not p2 ever')
    print("   touched p1.  And $AB06's DOWN arm tests ONLY the y axis:")
    print()
    print('   p1 stands at (60,46) pressing NOTHING.  p2 walks DOWN from')
    print('   (32,40) -- 28 units away in x, nowhere near any box.')
    print('   ctr | shipped: p1(x,y) dir | $A3CF as CALL nz: p1(x,y) dir')
    for r, s2 in zip(a, b):
        print('   %3d |          (%2d,%2d)  %02X |                   (%2d,%2d)  %02X'
              % (r[0], r[1], r[2], r[3], s2[1], s2[2], s2[3]))
    print('   differing rows: %d of %d'
          % (sum(1 for x, y in zip(a, b) if x[1:4] != y[1:4]), len(a)))
    print('   -> the shipped game shoves p1 two units south from 28 units away.')
    print('      A port that tidies $A3CF into a conditional loses that.')
    print()
    print('5. NO STACK TRICKS.  Every two-player routine returns cleanly.  The')
    print('   harness pushes six distinct sentinels and index 0 means a normal')
    print('   RET (deathgate.py `stack`, same method):')
    h = boot(); join2(h, settle=2)
    sp0 = h.regs[SP]
    for addr, name, rg in (
            (0xAAC4, '$AAC4 overlap gate', dict(IX=0x8420, BC=(40 << 8) | 40, IY=0x847F)),
            (0xAAF5, '$AAF5 p1 pushes p2', dict(IY=0x847F)),
            (0xAB06, '$AB06 p2 pushes p1', dict(IY=0x847F)),
            (0xAB15, '$AB15 push body', dict(IX=0x8440, BC=(40 << 8) | 40, IY=0x847F, A=0x08)),
            (0x908E, '$908E shot damage', dict(IX=0x8430, IY=0x847F)),
            (0x93CD, '$93CD death', dict(IX=0x8420, IY=0x847F)),
            (0x9440, '$9440 dead handler', dict(IX=0x8440, IY=0x847F)),
            (0x94E1, '$94E1 exit walk', dict(IX=0x8420, IY=0x847F)),
            (0xA6F6, '$A6F6 thief', dict(IX=0x8420, IY=0x847F, HL=0x8144))):
        h.regs[SP] = sp0
        idx, t, n = h.call(addr, regs=rg)
        print('      %-20s exit=%s  %6d T  %5d steps' % (name, idx, t, n))


# ------------------------------------------------------------------- stun
def cmd_stun():
    print("""
=============================================================================
THE STUN DURATION -- $9068 arms it, $A506 spends it
=============================================================================
$9064  SET 6,(IX+$14) / LD (IX+$1D),$1E     the shot lands
$A506  BIT 6,(IX+$14) / JR z,$A514          at the TOP of the victim's move
$A50C  DEC (IX+$1D) / RET nz                frozen: no move, no fire, nothing
$A510  RES 6,(IX+$14)
$A514  LD (IX+$1D),0                        ... and fall into the normal path
The stun counter IS the pending-interaction slot, reused as a countdown.
$A46F BIT 6,(IX+$14) / JR nz,$A48D freezes his walk animation with it.
""")
    h = boot(); m = h.memobj.m
    join2(h, settle=2)
    h.poke(0x8420, 12); h.poke(0x8421, 40)
    h.poke(0x8440, 32); h.poke(0x8441, 40)

    def force():
        h.poke(0x847E, (m[0x847E] & ~0x30) | 0x10)
    for i in range(40):
        pass_top(h); force()
    press(h, P1['fire'], P1['right'])
    for i in range(8):
        pass_top(h); force()
        if m[0x8432] != 0xFF:
            break
    press(h)
    for i in range(50):
        pass_top(h); force()
        if m[0x8454] & 0x40:
            break
    print('  p1 (12,40) fires ONE shot east at p2 (32,40), STUN mode forced.')
    print('  hit at ctr %d: p2 +$14 = %02X, +$1D = %02X'
          % (m[0x8491], m[0x8454], m[0x845D]))
    press(h, P2['up'])
    n, first, y0 = 0, None, m[0x8441]
    while n < 60:
        pass_top(h); force(); n += 1
        if not (m[0x8454] & 0x40):
            first = (m[0x8491], m[0x8441])
            break
    press(h)
    print('  frozen passes with $8454 bit 6 set : %d' % (n - 1))
    print('  p2 holds UP throughout; he first moves again at ctr %d, y %d -> %d'
          % (first[0], y0, first[1]))
    print('  -> $1E is loaded, 29 decrements return nz, the 30th reaches 0 and')
    print('     he moves on that pass.  29 passes, about 2.3 s at 12.52 Hz.')


def cmd_all():
    for f in (cmd_join, cmd_overlap, cmd_push, cmd_order, cmd_modes,
              cmd_shot, cmd_stun, cmd_item, cmd_death, cmd_thief, cmd_quirks):
        f()
        print()


CMDS = dict(all=cmd_all, join=cmd_join, overlap=cmd_overlap, box=cmd_box,
            push=cmd_push, order=cmd_order, modes=cmd_modes, shot=cmd_shot,
            stun=cmd_stun, item=cmd_item, death=cmd_death, thief=cmd_thief,
            quirks=cmd_quirks)

if __name__ == '__main__':
    a = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if a not in CMDS:
        print(__doc__)
        sys.exit(1)
    CMDS[a]()
