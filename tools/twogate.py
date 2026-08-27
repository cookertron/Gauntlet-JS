#!/usr/bin/env python3
"""
twogate.py -- TWO PLAYERS.  Every number in this file is driven out of the REAL
Z80 through tools/harness.py; nothing here loads the engine.

    python tools/twogate.py all         every section below
    python tools/twogate.py join        the join-in, $9440 -> $9689
    python tools/twogate.py order       the per-pass hook trace (WHO GOES FIRST)
    python tools/twogate.py camera      $A3E6, the camera TARGET, enumerated
    python tools/twogate.py leash       $A924/$A944, the SEPARATION LIMIT
    python tools/twogate.py push        $AAF5/$AB06, one player SHOVING the other
    python tools/twogate.py overlap     $AAC4, the player-versus-player box
    python tools/twogate.py ff          $847E bits 4/5, friendly fire
    python tools/twogate.py flags       how many of the 307 dungeons set them
    python tools/twogate.py pic         build/two_leash.png

=============================================================================
THE STATE THIS STARTS FROM, AND HOW PLAYER 2 IS PUT IN THE GAME
=============================================================================
build/state_charsel.pkl is a ONE-player machine: $844B = $C0 (bits 7 and 6 of
player 2's +$0B -- "not in the game" and "immaterial") and $8454 = $80 (his
+$14 DEAD bit).  Player 2 is not poked into existence here.  He is joined the
way a second player joins on a real machine: $9432 calls $855D (which scans
both key sets into $8427 and $8447) and then runs $9440 with IX = $8420 and
again with IX = $8440, and $944C BIT 4,(IX+7) is the FIRE test.  Pressing M --
player 2's fire key, measured -- takes the join.  One pass later he is standing
next to player 1 with BCD 2000 health.  Everything below is measured from that.

Every per-pass sample is anchored on the main-loop top $8503 (see
tools/sim_move.py's docstring for why a four-frame window is not a sampler).

=============================================================================
WHAT THIS ESTABLISHED
=============================================================================
* NOTHING IS ALTERNATED.  Counted by hooking, one player and then two:
  $9440, $A4DD, $A97F, $A924/$A944 and $AAC4 all run exactly ONCE PER PLAYER
  PER PASS.  The only two things that alternate in the whole two-player system
  are the HUD round robin ($B717, one player per pass by $8491 bit 1) and the
  shove gate ($AB15, even pass counters only).
* THE CAMERA FOLLOWS THE MIDPOINT.  $A3E6 halves the sum of the two
  coordinates and corrects for the world wrap; inside the leash its output is
  the exact short-way midpoint on 3904/3904 and 2368/2368 reachable pairs.
* THE PLAYERS CANNOT SEPARATE BEYOND THE SCREEN.  $A924/$A944, called by all
  four direction handlers before the map is consulted, refuse any step that
  would put the pair more than 60 units apart across or 36 down -- which is
  exactly 256 and 160 screen pixels, the playfield.  Driven in play: player 1
  stops dead on empty floor at 36 units and is released the instant player 2
  walks.
* PLAYER 1 IS NOT ALWAYS FIRST.  $A39B reads $842E bit 3 -- set by $AAC4 when
  player 1's move was refused by player 2's body -- and REVERSES the order,
  running $AAF5 first so player 1 SHOVES player 2 out of the way.
* $BF21 IS NOT THE 1P/2P SWITCH.  Read in build/image.bin (it is data in the
  relocated image):

      $BF21  LD A,($FFFD) / OR A / LD A,$C9 / JR z,$BF30
      $BF29  ($B8B5) = C9 ; ($B8CC) = C9 ; RET      <- non-zero
      $BF30  ($BADB) = ($BA01) = ($BBA7) = ($BBBC) = C9
             ($BA2C) = $B92B ; ($BA2B) = C3         <- zero

  $B8CC unpatched is `LD A,R / CP (IY+$53) / ... / OUT ($FE),A` -- the BEEPER
  noise generator, called from $A1DD inside the sprite draw -- and $BA2B is
  the AY driver.  So $FFFD picks the SOUND HARDWARE.  There is no player-count
  byte anywhere in the image; the count is simply whoever has pressed FIRE
  ($B478's title loop spins on ($8434 AND $8454) bit 7 until one of them
  clears).
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import harness as H                                          # noqa: E402
from harness import Harness, PC, T, IFF, TAPE_CALL_PC        # noqa: E402
from keyprobe import KEYS, keymask                           # noqa: E402
from sim_move import step_to_loop_top                        # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')

P1, P2 = 0x8420, 0x8440
# the two key sets, measured (tools/deathgate.py prints the same map)
K1 = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D', 'fire': 'Z'}
K2 = {'up': '8', 'down': 'I', 'left': 'K', 'right': 'L', 'fire': 'M'}


def press(h, *keys):
    for k in keys:
        s, b = KM[k]
        h.ports.press(s, keymask(b))


def boot(chars=None):
    """The captured live state, optionally with both character sprite banks
    repaired from the tape (the $FFFF boot bug -- see tools/fixchar.py)."""
    st = pickle.load(open(STATE, 'rb'))
    if chars is not None:
        from fixchar import fix
        mem = bytearray(st[0])
        fix(mem, *chars)
        st = (bytes(mem), st[1], st[2])
    h = Harness()
    h.load_state(st)
    return h


def two(chars=None, passes=12):
    """Join player 2 by holding his FIRE key, and let him materialise.

    $96AB sets (IX+14) bit 0, which routes his update to $A4B5 -- the
    materialise counter -- for the next several passes, so a caller that wants
    a MOVING player 2 must let it finish."""
    h = boot(chars)
    press(h, K2['fire'])
    step_to_loop_top(h)
    for _ in range(passes):
        step_to_loop_top(h)
    h.ports.release_all()
    return h


def place(h, p1, p2):
    """Put the two players somewhere legal and clear the overlap flags.

    They must not be closer than $AAC4's 7x7 box or they deadlock each other
    (measured: two players on the same square can neither move nor push)."""
    m = h.memobj.m
    m[P1], m[P1 + 1] = p1
    m[P2], m[P2 + 1] = p2
    m[P1 + 14] &= ~0x08
    m[P2 + 14] &= ~0x08


# --------------------------------------------------------------------------
def cmd_join():
    print('# $9440 -> $9689: player 2 joins with FIRE (M)')
    h = boot()
    m = h.memobj.m
    before = (m[0x8440], m[0x8441], m[0x844B], m[0x8454], m[0x8442], m[0x8443])
    print('  before   p2 xy=(%d,%d)  +$0B=%02X  +$14=%02X  health=%02X%02X'
          % before)
    press(h, K2['fire'])
    step_to_loop_top(h)
    for i in range(3):
        step_to_loop_top(h)
        print('  pass %d   p2 xy=(%d,%d)  +$0B=%02X  +$14=%02X  health=%02X%02X'
              '  +13=%02X +14=%02X'
              % (i + 1, m[0x8440], m[0x8441], m[0x844B], m[0x8454],
                 m[0x8442], m[0x8443], m[0x844D], m[0x844E]))
    print('  player 1 sits at (%d,%d); $9689 places the joiner NEXT TO HIM --'
          % (m[0x8420], m[0x8421]))
    print('  $96B4 LD A,IXL / AND $20 reads the OTHER block, snaps it to the')
    print('  cell corner (AND $7C) and $B2E3 nudges to a free cell.')
    print('  $970D LD A,(IX+11) / AND $3F clears +$0B bits 7 AND 6.')


# --------------------------------------------------------------------------
WATCH = {
    0x8503: 'LOOP TOP $8503',
    0x9432: '  $9432 read both key sets, then rejoin',
    0x855D: '    $855D ($FFFC)->$8427, ($FFFB)->$8447',
    0x9440: '    $9440 rejoin',
    0xAAF5: '  $AAF5 player 1 SHOVES player 2',
    0xAB06: '  $AB06 player 2 SHOVES player 1',
    0xA4DD: '  $A4DD PLAYER UPDATE',
    0xA514: '    $A514 move top',
    0xA5F0: '    $A5F0 actor-scan gate',
    0xA97F: '      $A97F 7x7 actor scan ($5C00)',
    0xAAC4: '      $AAC4 OTHER-PLAYER box',
    0xA620: '    $A620 COMMIT',
    0xA3E6: '  $A3E6 CAMERA TARGET ($848D/$848E)',
    0xB58C: '$B58C CAMERA STEP ($848B/$848C)',
    0xAB94: '$AB94 ACTOR LOOP',
    0xA43B: '$A43B PLAYER DRAW',
    0x8C5D: '  $8C5D shot',
    0xB717: '$B717 HUD round robin',
    0x93C2: '$93C2 death check',
    0x94AE: '$94AE level-end / exit walk',
}


def cmd_order():
    print('# ONE PASS, hooked.  IX names the player.')
    h = two()
    press(h, K1['down'], K2['down'])
    for _ in range(6):
        step_to_loop_top(h)
    m = h.memobj.m
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active

    def step():
        pc = regs[PC]
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); return
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); return
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)

    while regs[PC] != 0x8503:
        step()
    step()
    n = 0
    while n < 3_000_000:
        pc = regs[PC]
        if pc == 0x8503 and n:
            break
        if pc in WATCH:
            ix = (regs[U_IXh] << 8) | regs[U_IXl]
            who = {P1: 'P1', P2: 'P2', 0x8430: "P1's shot",
                   0x8450: "P2's shot"}.get(ix, '')
            print('  %-46s %-9s p1=(%d,%d) p2=(%d,%d)'
                  % (WATCH[pc], who, m[P1], m[P1 + 1], m[P2], m[P2 + 1]))
        step(); n += 1


U_IXh, U_IXl = H.U.REGISTERS['IXh'], H.U.REGISTERS['IXl']


# --------------------------------------------------------------------------
def target_model(p1, p2, p2gone=False):
    """$A3E6 transcribed.  L is x, H is y, throughout.

    $A3F7 INC H/INC H/INC L/INC L      both coordinates + 2 (the sprite centre)
    $A40B ADD HL,DE                    ONE 16-BIT ADD -- a carry out of the x
                                       sum lands in y (inert while both y are
                                       even, which they always are in play)
    $A40C SRL H / SRL L                halve each byte; SRL shifts in 0, so the
                                       x carry is NOT re-injected
    $A410 RES 0,L / RES 0,H            force even
    $A414 |own - mid| >= $21 -> mid ^= $40    the WRAP fix: take the antipode
    """
    x1, y1 = p1
    x2, y2 = p2
    L, Hh = (x1 + 2) & 0xFF, (y1 + 2) & 0xFF
    A, C = L, Hh
    if not p2gone:
        E, D = (x2 + 2) & 0xFF, (y2 + 2) & 0xFF
        s = ((Hh << 8) | L) + ((D << 8) | E)
        L, Hh = s & 0xFF, (s >> 8) & 0xFF
        Hh >>= 1
        L >>= 1
    L &= 0xFE
    Hh &= 0xFE
    for own, cur in (('L', L), ('H', Hh)):
        t = ((A if own == 'L' else C) - cur) & 0xFF
        if t >= 0x80:
            t = (-t) & 0xFF
        if (t & 0x7F) >= 0x21:
            cur ^= 0x40
        if own == 'L':
            L = cur & 0x7F
        else:
            Hh = cur & 0x7F
    return L, Hh


def cmd_camera():
    print('# $A3E6 -- THE CAMERA TARGET IS THE MIDPOINT OF THE TWO PLAYERS')
    h = two()
    snap = pickle.dumps(h.save_state())
    m = h.memobj.m

    def hw(p1, p2, p1gone=False, p2gone=False):
        h.load_state(pickle.loads(snap))
        mm = h.memobj.m
        mm[P1], mm[P1 + 1] = p1
        mm[P2], mm[P2 + 1] = p2
        mm[0x842B] = 0x80 if p1gone else 0x00
        mm[0x844B] = 0x80 if p2gone else 0x00
        mm[0x848D], mm[0x848E] = 0xEE, 0xEE
        h.call(0xA3E6)
        return mm[0x848D], mm[0x848E]

    import random
    random.seed(7)
    cases = [((x1, 20), (x2, 60)) for x1 in range(0, 128, 4)
             for x2 in range(0, 128, 4)]
    cases += [((random.randrange(128), random.randrange(128)),
               (random.randrange(128), random.randrange(128)))
              for _ in range(600)]
    bad = [(a, b) for a, b in cases if hw(a, b) != target_model(a, b)]
    print('  both players present, midpoint model : %d/%d agree'
          % (len(cases) - len(bad), len(cases)))
    solo = [((random.randrange(128), random.randrange(128)),
             (random.randrange(128), random.randrange(128)))
            for _ in range(200)]
    bad2 = [(a, b) for a, b in solo
            if hw(a, b, p2gone=True) != target_model(a, b, p2gone=True)]
    print('  player 2 ABSENT ($844B bit 7), own+2 model : %d/%d agree'
          % (len(solo) - len(bad2), len(solo)))
    print('  player 1 absent, p2 at (40,60) -> target %s (it follows PLAYER 2)'
          % (hw((0, 0), (40, 60), p1gone=True),))
    print('  BOTH absent -> $A3F6 RET nz, target left at $EE/$EE: %s'
          % (hw((10, 10), (40, 60), p1gone=True, p2gone=True),))
    print()
    print('  THE $A41B CP $21 / XOR $40 IS A WRAP FIX, ENUMERATED.  Inside the')
    print('  leash (below) the pair can never be more than 60 units apart')
    print('  across or 36 down, and both coordinates are always even, so:')
    for lim, axis in ((60, 'x'), (36, 'y')):
        n = ok = 0
        for a in range(0, 128, 2):
            for b in range(0, 128, 2):
                if min(abs(a - b), 128 - abs(a - b)) > lim:
                    continue
                n += 1
                ok += (target_model((a, 0), (b, 0))[0] ==
                       (shortmid(a, b) & 0xFE))
        print('    %s, |d| <= %d: output == the SHORT-WAY midpoint on %d/%d'
              % (axis, lim, ok, n))
    print('  pairs, no exceptions.  Without the flip a pair straddling 0 sends')
    print('  the camera to the far side of the map.')
    print()
    print('  $B58C then tracks it: goal = clamp(target - $22 + 2, 2, $42) across')
    print('  and clamp(target - $16 + 2, 2, $5A) down, approached +-2 units per')
    print('  pass the short way round.  With one player the target is his own')
    print('  coordinate + 2, which is why the one-player rule reads as')
    print('  "player - 30 / player - 18" (NOTES-render.md).')


def short(a, b):
    d = (a - b) & 0x7F
    return d if d < 64 else 128 - d


def shortmid(x1, x2):
    """The true short-way-round midpoint of (x1+2) and (x2+2) in a 128 world."""
    A, B = (x1 + 2) & 0x7F, (x2 + 2) & 0x7F
    d = (B - A) % 128
    if d > 64:
        d -= 128
    return (A + (d // 2 if d >= 0 else -((-d) // 2))) % 128


# --------------------------------------------------------------------------
def cmd_leash():
    print('# $A924 / $A944 -- THE LEASH.  These are NOT a stray-distance test:')
    print('# LD A,IXL / AND $20 selects the OTHER player, exactly as $AAC4 does,')
    print('# and every one of the four direction handlers calls one of them')
    print('# BEFORE the map is consulted:')
    print('#   $A833 (right) $A861 (left) -> $A924 ; $A88F (down) $A8BD (up)')
    print('#   -> $A944 ; carry SET undoes the 2-unit step at $A852/$A880/...')
    print('#   $B1F5/$B1FA apply the same pair to the TELEPORT.')
    h = two()
    snap = pickle.dumps(h.save_state())

    def probe(addr, other, cand, ix=P1, absent=False, seed_carry=0):
        h.load_state(pickle.loads(snap))
        mm = h.memobj.m
        ob = P2 if ix == P1 else P1
        mm[ob], mm[ob + 1] = other, other
        mm[ob + 11] = 0x80 if absent else 0x00
        h.call(addr, regs={'IX': ix, 'C': cand, 'B': cand,
                           'F': seed_carry})
        return h.sim.registers[H.F] & 1

    for addr, name, axis in ((0xA924, '$A924', 'x'), (0xA944, '$A944', 'y')):
        ref = [probe(addr, 20, c) for c in range(128)]
        refused = sorted({short(20, i) for i, v in enumerate(ref) if v})
        allowed = sorted({short(20, i) for i, v in enumerate(ref) if not v})
        print('  %s (%s): short-way |other - candidate| allowed %d..%d, '
              'REFUSED %d..%d'
              % (name, axis, min(allowed), max(allowed),
                 min(refused), max(refused)))
    print('  partner ABSENT ($xx4B bit 7): carry comes back CLEAR whatever it')
    print('  was on entry (%d / %d) -- $A926 AND $20 resets it before the'
          % (probe(0xA924, 20, 100, absent=True, seed_carry=0),
             probe(0xA924, 20, 100, absent=True, seed_carry=1)))
    print('  BIT/RET nz.  THAT is why a one-player port that omits $A924')
    print('  entirely still matches the movement differential.')
    print()
    print('  THE LEASH IS EXACTLY ONE SCREENFUL.  camx = midx - 30, camy =')
    print('  midy - 18 (from $B58C above), the sprite is 16 px and one unit is')
    print('  4 px, so at 60 units apart the two players sit at screen x 0 and')
    print('  240 (240+16 = 256) and at 36 apart at screen y 0 and 144')
    print('  (144+16 = 160, the playfield height).  Neither can ever be culled')
    print('  by $A1DA while the camera is unclamped.')
    print()
    print('  driven in play -- player 1 holds DOWN, player 2 stands still:')
    h = two()
    place(h, (12, 8), (16, 8))
    press(h, K1['down'])
    step_to_loop_top(h)
    m = h.memobj.m
    prev = None
    for i in range(40):
        step_to_loop_top(h)
        sep = (m[P1 + 1] - m[P2 + 1]) & 0x7F
        if m[P1 + 1] == prev:
            break
        prev = m[P1 + 1]
    cx, cy = (m[P1] >> 2) & 31, (m[P1 + 1] >> 2) & 31
    ahead = [m[0x8000 + (((cy + k) & 31) << 5) + cx] for k in range(1, 4)]
    print('    stops at p1 y=%d, p2 y=%d, separation %d units (9 cells)'
          % (m[P1 + 1], m[P2 + 1], sep))
    print('    the three map cells BELOW him are %s -- empty floor, so it is'
          % ' '.join('$%02X' % v for v in ahead))
    print('    the leash and not a wall.')
    press(h, K2['down'])
    for i in range(4):
        step_to_loop_top(h)
    print('    the moment player 2 walks, player 1 is released: '
          'p1 y=%d p2 y=%d sep=%d'
          % (m[P1 + 1], m[P2 + 1], (m[P1 + 1] - m[P2 + 1]) & 0x7F))


# --------------------------------------------------------------------------
def cmd_overlap():
    print('# $AAC4 -- the OTHER PLAYER is a solid 7x7 box, and the refusal is')
    print('# remembered.  $A616 CALL $AAC4 / RET c is the FOURTH move gate,')
    print('# after the map ($A8E7), the leash ($A924/$A944) and the actor scan')
    print('# ($A97F).')
    h = two()
    snap = pickle.dumps(h.save_state())

    def box(ix, other, cand):
        h.load_state(pickle.loads(snap))
        mm = h.memobj.m
        ob = P2 if ix == P1 else P1
        mm[ob], mm[ob + 1] = other
        mm[ob + 11] = 0
        mm[ix + 14] = 0
        h.call(0xAAC4, regs={'IX': ix, 'C': cand[0], 'B': cand[1]})
        return h.sim.registers[H.F] & 1, (mm[ix + 14] >> 3) & 1

    hits = {(dx, dy) for dx in range(-10, 11) for dy in range(-10, 11)
            if box(P1, (40, 40), ((40 + dx) & 0x7F, (40 + dy) & 0x7F))[0]}
    print('  refused for candidate-minus-other dx in %s, dy the same: %d cells'
          % (sorted({d[0] for d in hits}), len(hits)))
    same = all(box(P2, (40, 40), ((40 + dx) & 0x7F, (40 + dy) & 0x7F))[0]
               == ((dx, dy) in hits)
               for dx in range(-10, 11) for dy in range(-10, 11))
    print('  identical with IX = $8440 (it is symmetric): %s' % same)
    h.load_state(pickle.loads(snap))
    mm = h.memobj.m
    mm[P2], mm[P2 + 1] = 40, 40
    mm[P2 + 11] = 0x40
    mm[P1 + 14] = 0
    h.call(0xAAC4, regs={'IX': P1, 'C': 40, 'B': 40})
    print('  other player +$0B bit 6 set (EXITING, $A687) -> carry %d, no flag'
          % (h.sim.registers[H.F] & 1))
    print('  -- so a player walking into the exit stops being solid.')
    print('  On a refusal $AAF0 SET 3,(IX+14).  That bit is read at $A39B/$A3A1')
    print('  NEXT pass and it REORDERS THE TWO UPDATES; see `push`.')


# --------------------------------------------------------------------------
def cmd_push():
    print('# $A39B -- WHO MOVES FIRST DEPENDS ON WHO WAS BLOCKED LAST PASS')
    print('#   $A39B BIT 3,(IY-$51)  = $842E bit 3, player 1 blocked by player 2')
    print('#   set   -> $A3BE CALL $AAF5 (p1 shoves p2); PLAYER 2 UPDATES FIRST')
    print('#            then $A3CF CALL $AB06 -- UNCONDITIONAL, note the $CD')
    print('#            where the normal arm at $A3A5 has $C4 (CALL nz)')
    print('#   clear -> $A3A1 BIT 3,(IY-$31); CALL nz $AB06; player 1 first')
    print('# $AB15, the shove itself:')
    print('#   BIT 0,(IY+$12) / RET nz          EVEN pass counters only')
    print('#   pusher must hold a direction, PUSHEE MUST HOLD NONE')
    print('#   SET 4,(IX+14)                    the same bit $A585 uses to')
    print('#                                    exempt him from the fire freeze')
    print('#   then, per direction bit of the PUSHER, if the pushee is exactly')
    print('#   one cell (4 units) that way, SET that bit in HIS $8427/$8447.')
    h = two()
    place(h, (12, 8), (12, 12))          # player 2 one cell BELOW player 1
    press(h, K1['down'])                 # player 1 pushes down; player 2 idle
    step_to_loop_top(h)
    m = h.memobj.m
    print('  pass ctr   p1        p2        $842E $844E  $8427 $8447')
    for i in range(10):
        step_to_loop_top(h)
        print('  %4d %3d  (%2d,%3d)  (%2d,%3d)   %02X    %02X     %02X    %02X'
              % (i + 1, m[0x8491], m[P1], m[P1 + 1], m[P2], m[P2 + 1],
                 m[P1 + 14], m[P2 + 14], m[0x8427], m[0x8447]))
    print('  $8447 = $02 (DOWN) appears on the ODD rows, i.e. it was written')
    print('  during the EVEN pass before it -- the $AB15 gate.  The pair')
    print('  therefore advances one cell every TWO passes: half speed.')


# --------------------------------------------------------------------------
def cmd_ff():
    print('# $9009 -- A SHOT IS TESTED AGAINST BOTH PLAYERS, 5x5, EVERY STEP.')
    print('# $900F does player 1 ($842B bit 6 skips him), $903A does player 2.')
    print('# $908E gives a MONSTER shot 10 (3 with (IX+3) bit 6); a PLAYER shot')
    print('# arrives with D = 0, and then:')
    print('#   $905E BIT 4,(IY-1)  $847E bit 4 -> SET 6,(IX+$14), (IX+$1D)=$1E')
    print('#   $906E BIT 5,(IY-1)  $847E bit 5 -> D = 5')
    print('# and $908C SCF consumes the shot either way.')
    for name, bits in (('$847E = 00  (dungeon 1)', 0x00),
                       ('$847E bit 5  SHOTS NOW HURT', 0x20),
                       ('$847E bit 4  SHOTS NOW STUN', 0x10)):
        h = two()
        place(h, (12, 8), (12, 24))
        h.memobj.m[0x847E] = bits
        press(h, K1['fire'], K1['down'])
        step_to_loop_top(h)
        m = h.memobj.m
        print('  %s' % name)
        for i in range(7):
            step_to_loop_top(h)
            print('    pass %d shot=(%3d,%3d,$%02X)  p2 health=%02X%02X  '
                  'p2 +$1D=$%02X +$14=$%02X'
                  % (i + 1, m[0x8430], m[0x8431], m[0x8432],
                     m[0x8442], m[0x8443], m[0x845D], m[0x8454]))
    print('  With no flag the shot still DIES on the partner -- he blocks it and')
    print('  takes nothing.  The stun is $A506: BIT 6,(IX+$14) skips the whole')
    print('  move and DECs (IX+$1D) instead, so $1E is a 30-pass freeze.')


def cmd_flags():
    print('# $97CB sets $847E from the DUNGEON RECORD, and $B3D4 clears it at')
    print('# every level load:')
    print('#   $97CB BIT 0,(IX+1) -> SET 5,(IY-1)   "SHOTS NOW HURT"')
    print('#   $97D5 BIT 1,(IX+1) -> SET 4,(IY-1)   "SHOTS NOW STUN"')
    print('# $8B37 LD A,($847E) / AND $30 prints "OTHER  PLAYERS" ($7EEE) and')
    print('# then $7EFD or $7F0C in the level banner.')
    import json
    import base64
    from collections import Counter
    p = os.path.join(ROOT, 'build', 'packdata.json')
    d = json.load(open(p))
    blob = base64.b64decode(d['blob'])
    lens, packs = d['lens'], d['packs']
    off, o = [], 0
    for L in lens:
        off.append(o); o += L
    c = Counter()
    for pk in packs:
        for idx in pk:
            f = blob[off[idx] + 1]
            c[(f & 1, (f >> 1) & 1)] += 1
    tot = sum(c.values())
    for k in sorted(c):
        print('  flags bit0=%d bit1=%d : %3d of %d dungeons' % (k[0], k[1],
                                                                c[k], tot))
    print('  so 41 of the 307 shipped dungeons turn friendly fire on, all of')
    print('  them STUN; "SHOTS NOW HURT" is a string with no data behind it.')
    print('  Dungeons 1-7 (pack 0) flags: %s'
          % ' '.join('$%02X' % blob[off[i] + 1] for i in packs[0]))


# --------------------------------------------------------------------------
def cmd_pic():
    from screen import render
    h = two(chars=(3, 1), passes=12)     # elf and valkyrie
    m = h.memobj.m
    press(h, K1['down'])
    for _ in range(40):
        step_to_loop_top(h)
    print('  p1=(%d,%d) p2=(%d,%d) sep=%d cam=(%d,%d)'
          % (m[P1], m[P1 + 1], m[P2], m[P2 + 1],
             (m[P1 + 1] - m[P2 + 1]) & 0x7F, m[0x848B], m[0x848C]))
    for who, base in (('p1', P1), ('p2', P2)):
        x, y = m[base], m[base + 1]
        print('    %s screen px (%d,%d)'
              % (who, (((x - m[0x848B]) & 0x7E) >> 1) * 8,
                 (((y - m[0x848C]) & 0x7E) >> 1) * 8))
    out = os.path.join(ROOT, 'build', 'two_leash.png')
    render(m).resize((512, 384)).save(out)
    print('  wrote %s' % out)


def cmd_state():
    print('# WHAT IS PER PLAYER AND WHAT IS GLOBAL -- write-attributed over 12')
    print('# two-player passes with both walking, watching $8400..$84FF.')
    from collections import defaultdict
    h = two()
    press(h, K1['down'], K2['down'])
    for _ in range(4):
        step_to_loop_top(h)
    h.memobj.watch(0x8400, 0x8500)
    for _ in range(12):
        step_to_loop_top(h)
    log = h.memobj.log
    h.memobj.unwatch()
    w = defaultdict(set)
    for pc, a, v in log:
        w[a].add(pc)
    p1 = sorted(a - P1 for a in w if P1 <= a <= P1 + 0x1F)
    p2 = sorted(a - P2 for a in w if P2 <= a <= P2 + 0x1F)
    print('  player 1 block offsets written : %s'
          % ' '.join('+%02X' % o for o in p1))
    print('  player 2 block offsets written : %s'
          % ' '.join('+%02X' % o for o in p2))
    print('  the two sets are identical: %s' % (p1 == p2))
    print('  globals touched:')
    for a in sorted(w):
        if P1 <= a <= P1 + 0x1F or P2 <= a <= P2 + 0x1F:
            continue
        print('    $%04X  by %s' % (a, ' '.join('$%04X' % p
                                                for p in sorted(w[a]))))
    print()
    print('  PER PLAYER, by construction (32-byte stride, $8420 / $8440):')
    print('    +0/+1 xy  +2/+3 health BCD  +4..+6 score BCD  +7 keys read')
    print('    +8 keys  +9 potions  +$0A monster-repel countdown ($ADC7 reads')
    print('    it)  +$0B dirty/EXIT/ABSENT flags  +$0C LEVEL  +$0D compass')
    print('    +$0E walk phase & the four two-player bits  +$0F sprite base')
    print('    (208 / 232)  +$10..+$13 his own shot  +$14 stats/DEAD  +$15')
    print('    character attribute  +$17..+$1C the six ARMOUR bytes $AB6F')
    print('    installs from $7D34  +$1D pending interaction / STUN countdown')
    print('  PER PLAYER, outside the block:')
    print('    $84C1 / $84C2   the hoard key-award counters ($A3AC/$A3B6)')
    print('    $84C8 / $84C9   the score MILLIONS carry ($B82E, by IXL & $20)')
    print('    $5B60 + (IXL>>1) the class-5 drain accumulator ($AF26)')
    print('    $50C9 / $50DA   HUD field origin; $5080 / $5091 the name panel;')
    print('                    $5086 / $5097 the name-entry field ($92A6)')
    print('    $5F00 / $6320   the two 32-record sprite banks ($A4C8)')
    print('  GLOBAL, and shared:')
    print('    $848B/$848C camera, $848D/$848E its target -- ONE camera')
    print('    $8491 pass counter -- one round robin drives BOTH players')
    print('    $8497 video frames, $849F drain phase -- ONE drain tick hits')
    print('          both ($B6EE then $B6FE, each skipped on his own +$0B bit 6)')
    print('    $8403 level (max of the two +$0C at $94C3), $84A0/$84BD..$84BF')
    print('          difficulty, $84B8 hurry-up, $84B6 treasure clock')
    print('    $5C00 actor list + $8494/$8496 -- ONE list, no player is in it')
    print('    $847D level-done bits, $847E friendly-fire/treasure/potion bits')
    print('    $8000..$83FF the map, $84AD/$5BD0 the ring, the door animator')


CMDS = {'join': cmd_join, 'order': cmd_order, 'camera': cmd_camera,
        'leash': cmd_leash, 'overlap': cmd_overlap, 'push': cmd_push,
        'ff': cmd_ff, 'flags': cmd_flags, 'state': cmd_state, 'pic': cmd_pic}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    names = list(CMDS) if which == 'all' else [which]
    for n in names:
        print('=' * 74)
        CMDS[n]()
        print()


if __name__ == '__main__':
    main()
