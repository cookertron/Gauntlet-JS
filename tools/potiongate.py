#!/usr/bin/env python3
"""
potiongate.py -- THE POTION DIFFERENTIAL.

The potion is the one player action with no gate of its own, and it is the one
that touches the most subsystems: the player block, the global sweep flag, the
actor list, the MAP, the HUD's icon round robin and the sound driver.  This
tool drives the same key script through the real Z80 and through the built
engine and prints the same table from each.

    python tools/potiongate.py            the full matrix, both sides
    python tools/potiongate.py z80        the real Z80 only
    python tools/potiongate.py port       the built engine only
    python tools/potiongate.py row        just the $7D1C row selection
    python tools/potiongate.py swarm      just the actor-damage scenarios
    python tools/potiongate.py p2-         just the two-player pay-out
    python tools/potiongate.py unhelped    without the measured drain clock
    python tools/potiongate.py controls    the controls behind the exemptions

THE RULES THIS GATE CARRIES.  All measured; the addresses are where.

  THE THROW, $A518, and it ARMS A GLOBAL SWEEP rather than acting at once:
    $A518  BIT 5,(IX+7)                   the MAGIC bit of the direction byte
    $A51E  ($84A1) must be 0              a sweep is already counting down
    $A524  BIT 3,(IY-1) must be clear     one is already armed
    $A52A  (IX+9) potions -- ZERO takes $A530: sound 13 AND NOTHING ELSE, so
           an empty player still makes a noise every pass he holds the key
    $A537  BIT 5,(IX+11) the F_POT_SPEND debounce -- one throw per press
    $A53D  DEC (IX+9) / $A540 SET 5,(IX+11)
    $A544  LD A,(IX+$15) / RRCA / RRCA / AND 12          $8435, the character
    $A54B  BIT 2,(IX+$14) -> ADD A,4                     $8434, the INVENTORY
    $A553  LD HL,$7D1C / ADD A,L        so the STRIDE IS 4 and the row is 3
           bytes: $84A3, then (IY+$25) and (IY+$26)
    $A566  LD ($84A6),IX                a 16-bit block POINTER, not an index
    $A56A  SET 3,(IY-1) / $A56E zero $84A2 and $84B2 / $A575 ($84A1) = 5
    $A579  sound 5

  THE DISARM, $A38D RES 3,(IY-1), AT THE TOP OF EVERY PASS, re-armed only
  while $847E bit 0 is set ($A391/$A397).  So a potion kills for the REMAINDER
  OF THE PASS IT WAS THROWN ON and $84A1's five passes gate only the re-throw.

  THE ACTOR HALF, $AC07 BIT 3,(IY-1) / $AC0B CALL nz,$AF5D.  $AF5D itself has
  no radius test, but the call site is reached only through $A1DA's four
  RET nc's, so the reach is the ACTOR CULL WINDOW.  From the captured state
  all 63 actors are off camera and $AF5D is entered ZERO times; parking six on
  the player gives six entries on the throwing pass and none afterwards.
    $AF5E  E >= $A0 -> $84B2 = BCD+1, remove, and UNWIND THE STACK
    $AF72  else     -> $84A2 = BCD+1, D = (IY+$25) if E < $20 else (IY+$26),
                       (E AND $18) - D  ->  carry kills, else E -= D
  The call site is installed by SELF-MODIFYING CODE: $ABB1 writes CALL $ABFF
  into $A21E for the actor walk and $ABF5 writes three zeros back over it.

  THE GENERATOR HALF, $A9EE, inside the GENERATOR SWEEP and therefore limited
  to the sweep's 16/17 x 10/11 window:
    $A9F4  A = (cell - $20) mod 3, the tier inside the type
    $A9FD  tier <  $84A3  ->  $AA02 LD (HL),0, destroyed, AND NO SPAWN ROLL
           tier >= $84A3  ->  $AA06 cell -= $84A3, weakened, roll still happens
  $84A3 is 0 on row 0, so the weakest potion does not touch generators at all.

  THE DETONATION, $8FAE, and it is what SETS $847E bit 0.  A SHOT landing on
  a $16 (a potion) or on $19..$1E (the six carried items) runs:
    $8FAE  SET 0,(IY-1)              the SUSTAIN arm -- $A391 re-arms the
                                     sweep at the top of the NEXT pass
    $8FB2  $84A3=0, (IY+$25)=1, (IY+$26)=1     one point, no generator damage
    $8FBE  sound 5, then $8FAA clears the cell
  It sets neither $84A6 nor $84A1 and does not zero the tallies, so $8BD4 sees
  bit 0 still up, skips the pay-out and $8C01 clears the bit: the borrowed
  sweep lasts EXACTLY ONE PASS and pays nothing.  $14 is cleared WITHOUT
  detonating, which is this gate's negative control.

  THE WHITE FLASH IS THE BORDER, and it is set from the FRAME INTERRUPT:
    $A2A8  SUB A / $A2A9 BIT 3,(IY-1) / JR z / $A2AF LD A,7
    $A2B1  CALL $B4F9   ->   LD ($84CA),A / OUT ($FE),A
  So it is not an effect drawn anywhere: the Spectrum's border is held WHITE
  for exactly as long as the sweep is armed -- the remainder of the throwing
  pass -- and it flashes for a $8FAE detonation too, because that re-arms the
  same bit.  MEASURED: OUT ($FE) at $B4FC carries 7 on the throw passes, 0 on
  every other pass, and never 7 at all when the player has no potions.  The
  `bord` column is $84CA on the Z80 side and g.border in the port.

  THE DEBOUNCE IS CLEARED BY THE HUD, $B7B1 RES 5,(IX+11), LAST in the icon
  priority chain -- so it is cleared only on a pass where the round robin
  ($8491 bits 0 and 1) picks this player, his health and score are both clean,
  and no key-gain or key-spend icon outranks it.  $B756 is a SEPARATE exit:
  with potion-gain and potion-spend both up the HUD clears BOTH and draws
  nothing, which releases the debounce a pass earlier.
  So the re-throw interval is NOT the countdown's 5 and it is not a constant 6
  either.  The rule is  next_throw = max(P+5, Q+1)  for a throw on pass P
  cleared on pass Q; measured gaps from one start are 6,5,5,6,5,7,5 and they
  shift with the phase of the round robin if the first press is delayed.

  THE PAY-OUT, $8BCA, reached from $8524 -- a MAIN-LOOP STEP, not part of the
  throw, and the head of the routine whose tail ($8C05) is the shot subsystem:
    $8BD6  LD IX,($84A6)             the THROWER is paid, not player 1
    $8BDA  DE = BCD($84A2) x 10      ten points an actor TOUCHED
    $8BED  CALL $B807                CALLED EVEN WHEN THE TALLY IS ZERO, and
                                     $B823 SET 1,(IX+11) is its tail
    $8BF6  $84B2 times, add $84B3 in the HUNDREDS (DJNZ reads the BCD tally
           as binary; reproduced rather than corrected)
    $8C01  RES 0,(IY-1)              clears the sustain bit on every path

WHAT THIS GATE IS WORTH.  A gate that cannot fail is not a gate, so eighteen
mutations of the port's potion rules were run against it
(`scratchpad/mutate.py`): 17 were CAUGHT.  The one that was not is PROVABLY
EQUIVALENT rather than a hole -- moving $AF7F's CP $20 to CP $30 changes the
outcome for no actor state at all, because for E in $20..$2F the mask at $AF86
can only yield $00 or $08 while every damage byte in the shipped $7D1C table is
at least $10, so both branches borrow and kill.  ((IY+$26) itself IS
observable, but only on row 2, where lo $18 and hi $10 differ, and only for
states $30..$9F -- which is what swarm-mid covers.)

WHAT IS NOT GATED HERE.

  * What SETS $847E bit 0.  $8FAE is the only SET 0,(IY-1) in the image and no
    scenario has reached it, so the sustain arm is modelled in the port and
    never taken on either side.  While bit 0 is clear the pay-out runs and
    $8C01 clears it again, which is the only behaviour these 320 rows see.

  * Three scenarios exempt a column, and each exemption is backed by a CONTROL
    that is a scenario of this tool rather than by an argument:
      gen-k0 / gen-k1  nact      generators left standing keep rolling for
                                 spawns, and `gen-control` plants the same six
                                 cells with no potion at all and still parts
                                 company on pass 1.
      p2-swarm         nact      `p2join-control` runs the same join with the
                                 same six actors and no potion, and the PORT
                                 KILLS MORE OF THEM than the original does.
    A fourth divergence needed no exemption, only honest scoping: swarm-weak
    is four passes rather than twelve because the state-$08 actors a row-0
    potion creates diverge from pass 5 -- `weak-control` plants state $08
    directly, throws nothing, and parts company on that same pass.
    NONE of these are potion bugs, and none of them are fixed here.

  * ULA CONTENTION RE-ROLLS ALL OF IT, and the count here depends on which
    harness is running.  MEASURED: 0 mismatching of 438 with
    GAUNTLET_CONTENDED=0, and 8 of 438 contended -- swarm-big-tough (5 of its
    6 passes) and swarm-weak (3 of 4), every one of them the `nact` column and
    every one off by exactly ONE actor.
    The cause is the REFRESH REGISTER.  The actor update's two coins ($AC25,
    $AC4C) and the generator roll read `LD A,R`, and R counts instruction
    FETCHES: contention adds no fetches by itself, but it makes a pass cost
    five video frames instead of four, which runs the interrupt handler an
    extra time and moves R by a few hundred.  Measured at the loop top over
    eight passes: 66 74 108 101 122 118 15 8 uncontended against
    15 63 4 4 59 68 6 6 contended -- not a shift, a different sequence.
    The port substitutes its own entropy for R and can follow NEITHER.  It
    happened to agree with the uncontended sequence on all 438 rows; against
    the contended one, two borderline actors fall the other side of the cull.
    So the 8 are the SAME declared divergence as the three above, re-rolled --
    and they are left VISIBLE rather than exempted, because a permanently
    hidden row cannot report a regression.  If this number is not 8, something
    has changed that is worth reading.
"""
import json
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402  -- for harness.CONTENDED in the summary
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')

# --- the addresses this gate samples ---------------------------------------
A_X, A_Y = 0x8420, 0x8421
A_POTIONS = 0x8429                     # (IX+9)
A_F11 = 0x842B                         # (IX+11)
A_P14, A_P15 = 0x8434, 0x8435          # (IX+$14) / (IX+$15)
A_FLAGS = 0x847E                       # (IY-1); bit 3 is ARMED
A_CTR = 0x8491
A_COUNT = 0x8496                       # live actors
A_T = 0x84A1                           # (IY+$22), the countdown
A_KILL = 0x84A2
A_K = 0x84A3                           # (IY+$24), generator tier damage
A_LO, A_HI = 0x84A4, 0x84A5            # (IY+$25) / (IY+$26)
A_GEN = 0x84B2
A_SCORE = 0x8424                       # (IX+4)..(IX+6), 24-bit packed BCD
P2 = 0x8440                            # player 2's block
A2_POTIONS, A2_SCORE = 0x8449, 0x8444
A_BORDER = 0x84CA                      # $B4F9's shadow of the last OUT ($FE)
ACTORS = 0x5C00                        # 4 bytes each: x, y, state, flags
MAP = 0x8000
SFX_CALL = 0xBA2B                      # the driver entry; A is the effect id


def cell(c, r):
    return MAP + ((r & 31) * 32) + (c & 31)


# ---------------------------------------------------------------------------
# THE MATRIX.  Each scenario is a setup plus one character per pass:
#   'M' hold MAGIC (CAPS on the Z80, {potion:true} in the port)
#   'Z' hold FIRE  (Z on the Z80, {fire:true}), which is how a cell DETONATES
#   '.' release
# `actors` repaints live records, `cells` plants map values, `watch` names the
# cells sampled every pass.
# ---------------------------------------------------------------------------
def scn(label, pattern, **kw):
    d = dict(label=label, pattern=pattern, pattern2=None, potions=3, potions2=0,
             potions2_at=0, f11_set=None, p15=None, p14=None, actors=[],
             cells=[], watch=[], ignore=())
    d.update(kw)
    # player 2's own script: '.' idle, 'F' FIRE (which is how he JOINS), 'M'
    # MAGIC.  His keys are M and SPACE where player 1's are Z and CAPS.
    d['pattern2'] = d['pattern2'] or ('.' * len(d['pattern']))
    assert len(d['pattern2']) == len(d['pattern']), d['label']
    return d


# six actors parked ON the player, so they are inside $A1DA's window
def parked(state, n=6):
    return [(i, 14 + 2 * i, 8, state) for i in range(n)]


MATRIX = [
    # -- the throw's own gating, the countdown and the re-throw cadence ------
    scn('hold-3', 'M' * 24),
    scn('hold-0', 'M' * 10, potions=0),        # $A52A: sound 13 every pass
    scn('hold-1', 'M' * 16, potions=1),
    scn('tap', ('M...' * 6)),                  # the debounce, released each time
    scn('tap-tight', ('M.' * 12)),
    scn('late', ('.' * 4) + ('M' * 16)),

    # -- the actor half, $AC07 / $AF5D --------------------------------------
    scn('swarm-lo', 'M' * 12, actors=parked(0x00)),   # state < $20 -> (IY+$25)
    scn('swarm-mid', 'M' * 12, actors=parked(0x40)),  # $20..$9F  -> (IY+$26)
    scn('swarm-hi', 'M' * 12, actors=parked(0xA0)),   # >= $A0 -> $84B2, unwind
    scn('swarm-mix', 'M' * 12,
        actors=[(0, 14, 8, 0x00), (1, 16, 8, 0x10), (2, 18, 8, 0x40),
                (3, 20, 8, 0x60), (4, 22, 8, 0xA0), (5, 24, 8, 0xC0)]),
    # $1F and $20 straddle $AF7F's CP $20, the only place the two damage bytes
    # are told apart.  Nothing else in this matrix has a state in $20..$2F, and
    # a mutation that moved the threshold to $30 went unnoticed without them.
    scn('swarm-edge', 'M' * 12, p15=0x20,       # row 2: lo $18, hi $10
        actors=[(0, 14, 8, 0x18), (1, 16, 8, 0x1F), (2, 18, 8, 0x20),
                (3, 20, 8, 0x28), (4, 22, 8, 0x2F), (5, 24, 8, 0x38)]),
    # A BIG SWARM, and it is the reason this scenario exists: with only six
    # parked actors a removal swaps in an actor from the far END of the list,
    # which is off camera and would have been stepped over anyway -- so a bug
    # that SKIPS the swapped-in actor is completely invisible.  With 48 the
    # swapped-in actor is usually another parked one, and the skip shows up
    # immediately: the port killed 32 of 48 and scored $0320 where the
    # original killed all 48 and scored $0480.  Reported from play as "the
    # potion leaves a band of ghosts standing".
    scn('swarm-big', 'M' * 6,
        actors=[(i, x, y, 0x00) for i, (x, y) in enumerate(
            [(x, y) for y in (8, 14, 20, 26, 32, 38)
             for x in (8, 16, 24, 32, 40, 48, 56, 64)])]),
    # the same crowd at a TIER the warrior's row-0 potion cannot kill outright
    # ($18 & $18 = $18, minus $10, no borrow) -- they must all SURVIVE, which
    # is the other half of the rule.
    scn('swarm-big-tough', 'M' * 6, p15=0x00,
        actors=[(i, x, y, 0x18) for i, (x, y) in enumerate(
            [(x, y) for y in (8, 14, 20, 26, 32, 38)
             for x in (8, 16, 24, 32, 40, 48, 56, 64)])]),
    scn('swarm-far', 'M' * 8, actors=[(i, 14 + 2 * i, 100, 0x00)
                                      for i in range(6)]),   # OUTSIDE $A1DA
    # the ONLY scenario in which actors SURVIVE the potion: row 0 deals $10 and
    # (AND $18) - $10 does not borrow for state $18, so the six drop to $08.
    # nact and f11 are ignored here and the reason is MEASURED, not assumed:
    # `python tools/potiongate.py controls` plants state $08 directly and never
    # throws, and the two sides still part company on the same pass.  See
    # WHAT IS NOT GATED HERE at the top of this file.
    # FOUR passes, not twelve, and the length is the point: what this scenario
    # gates is a SINGLE-PASS event -- six actors touched, damaged and NOT
    # removed -- and the divergence that starts on pass 5 belongs to the actor
    # model, not to the potion.  `python tools/potiongate.py controls` plants
    # state $08 directly with no potion at all and parts company on that same
    # pass 5, so running longer here would only re-report someone else's bug.
    scn('swarm-weak', 'M' * 4, p15=0x00, actors=parked(0x18)),

    # -- the row selection, $A544 / $A54B -----------------------------------
    scn('row0', 'M' * 6, p15=0x00, p14=0x00),
    scn('row1', 'M' * 6, p15=0x10, p14=0x00),
    scn('row2', 'M' * 6, p15=0x20, p14=0x00),
    scn('row3', 'M' * 6, p15=0x30, p14=0x00),
    scn('row0+4', 'M' * 6, p15=0x00, p14=0x04),
    scn('row1+4', 'M' * 6, p15=0x10, p14=0x04),
    scn('row2+4', 'M' * 6, p15=0x20, p14=0x04),
    scn('row3+4', 'M' * 6, p15=0x30, p14=0x04),

    # -- $B756: GAIN AND SPEND IN THE SAME WINDOW CANCEL --------------------
    #    $B759 CPL / AND $30 / JR nz / RES 4,C / RES 5,C -- if BOTH potion
    #    bits are set the HUD clears BOTH and draws no icon, so $B7B1 is
    #    never reached and the debounce is released a pass EARLIER than the
    #    icon path would release it.  Bit 4 (potion GAINED) is set by $A772
    #    when the player walks onto a $16, which holding MAGIC from a standing
    #    start never does -- so it is FORCED here.  Forcing is honest in a
    #    differential: both sides are handed the same byte and must agree on
    #    what the HUD does with it.  Without this scenario a mutant that
    #    deleted the whole cancel arm was caught by nothing.
    scn('cancel', 'M' * 14, f11_set=(1, 0x10)),
    scn('cancel-late', 'M' * 14, f11_set=(3, 0x10)),
    scn('cancel-gain-only', 'M' * 14, potions=0, f11_set=(1, 0x10)),

    # -- THE DETONATION, $8FAE: shooting a cell arms $847E bit 0 -----------
    #    The player is at (12,8) = cell (3,2) and holding FIRE FREEZES him
    #    ($A57E), so the cells are planted all round him and whichever the
    #    shot reaches must be reached identically on both sides.  $16 is a
    #    potion, $1A one of the six carried items -- both detonate -- and $14
    #    is the NEGATIVE CONTROL: $8F5E clears it without detonating.
    scn('deto-16', 'Z' * 16, potions=0,
        cells=[(5, 2, 0x16), (3, 4, 0x16), (1, 2, 0x16), (3, 0, 0x16)],
        watch=[(5, 2), (3, 4), (1, 2), (3, 0)]),
    scn('deto-1a', 'Z' * 16, potions=0,
        cells=[(5, 2, 0x1A), (3, 4, 0x1A), (1, 2, 0x1A), (3, 0, 0x1A)],
        watch=[(5, 2), (3, 4), (1, 2), (3, 0)]),
    scn('deto-14', 'Z' * 16, potions=0,
        cells=[(5, 2, 0x14), (3, 4, 0x14), (1, 2, 0x14), (3, 0, 0x14)],
        watch=[(5, 2), (3, 4), (1, 2), (3, 0)]),
    #    with actors ON CAMERA, so the one-pass sweep the detonation borrows
    #    is actually observable: $84A4/$84A5 are 1, not the table's $10/$18.
    scn('deto-swarm', 'Z' * 16, potions=0,
        actors=[(i, 40 + 2 * i, 30, 0x00) for i in range(6)],
        cells=[(5, 2, 0x16), (3, 4, 0x16), (1, 2, 0x16), (3, 0, 0x16)],
        watch=[(5, 2), (3, 4), (1, 2), (3, 0)]),

    # -- TWO PLAYERS: $8BD6 LD IX,($84A6) pays the THROWER ------------------
    #    Player 2 joins on his FIRE ($8ADA materialises him over the next few
    #    passes) and then throws.  Without a scenario like this the whole of
    #    $84A6 is invisible -- a mutant that paid player 1 unconditionally was
    #    caught by nothing else in this matrix.
    scn('p2-throw', '.' * 22, potions=0, potions2=3, potions2_at=10,
        pattern2='F' + '.' * 9 + 'M' * 12),
    scn('p2-both', 'M' * 22, potions=3, potions2=3, potions2_at=10,
        pattern2='F' + '.' * 9 + 'M' * 12),
    # the pay-out with a NON-ZERO tally going to player 2 -- p2-throw alone
    # cannot see $84A6, because with nothing in range both scores stay 0.
    # nact is exempt and the control is `p2join-control`: the same six parked
    # actors and the same join with NO potion anywhere still part company on
    # pass 2, and the port kills MORE of them than the original does.
    # the six sit at (40..50, 30) rather than on the player: ON CAMERA, so
    # $A1DA still lets the potion reach them, but clear of the spot player 2
    # MATERIALISES on -- parking them on the player instead let the join kill
    # some of them first, and the port kills a different number than the
    # original does ($ python tools/potiongate.py controls, p2join-control).
    scn('p2-swarm', '.' * 18, potions=0, potions2=3, potions2_at=8,
        actors=[(i, 40 + 2 * i, 30, 0x00) for i in range(6)],
        pattern2='F' + '.' * 7 + 'M' * 10, ignore=('nact',)),

    # -- the generator half, $A9EE ------------------------------------------
    #    K = 3 destroys tiers 0..2 outright; K = 1 destroys tier 0 and weakens
    #    tiers 1 and 2; K = 0 (row 0) must leave every cell alone.
    scn('gen-k3', 'M' * 6, p15=0x30,
        cells=[(5, 5, 0x20), (6, 5, 0x21), (7, 5, 0x22),
               (5, 6, 0x26), (6, 6, 0x2D), (7, 6, 0x2E)],
        watch=[(5, 5), (6, 5), (7, 5), (5, 6), (6, 6), (7, 6)]),
    # k1 and k0 leave generators STANDING, so they keep rolling for spawns and
    # the live count is contaminated by a divergence that has nothing to do
    # with the potion -- `python tools/potiongate.py controls` plants the same
    # six cells with potions=0 and never throws, and nact parts company on the
    # very first pass.  k3 destroys all six ($AA02 JR $AA51 skips the roll) and
    # therefore needs no exemption at all, which is the cross-check.
    scn('gen-k1', 'M' * 4, p15=0x10, ignore=('nact',),
        cells=[(5, 5, 0x20), (6, 5, 0x21), (7, 5, 0x22),
               (5, 6, 0x26), (6, 6, 0x2D), (7, 6, 0x2E)],
        watch=[(5, 5), (6, 5), (7, 5), (5, 6), (6, 6), (7, 6)]),
    scn('gen-k0', 'M' * 4, p15=0x00, ignore=('nact',),
        cells=[(5, 5, 0x20), (6, 5, 0x21), (7, 5, 0x22)],
        watch=[(5, 5), (6, 5), (7, 5)]),
    #    OUTSIDE the sweep window: the sweep starts at (camX>>2, camY>>2) and
    #    covers 17 x 11 cells, so column 25 is never reached.
    scn('gen-far', 'M' * 6, p15=0x30,
        cells=[(25, 20, 0x20), (26, 20, 0x21)],
        watch=[(25, 20), (26, 20)]),
]

COLS = ('potions', 'T', 'armed', 'sus', 'f11', 'kill', 'gen', 'nact',
        'K', 'lo', 'hi', 'score', 'p2pot', 'p2score', 'bord')

# The two CONTROLS behind the `ignore` exemptions above.  Neither throws a
# potion at all -- one plants the same six generator cells, the other plants
# the survivors' post-damage state $08 directly.  Both still diverge, which is
# what makes "pre-existing" a measurement rather than an excuse.  Run them with
# `python tools/potiongate.py controls`.
CONTROLS = [
    scn('gen-control', '.' * 8, potions=0,
        cells=[(5, 5, 0x20), (6, 5, 0x21), (7, 5, 0x22),
               (5, 6, 0x26), (6, 6, 0x2D), (7, 6, 0x2E)],
        watch=[(5, 5), (6, 5), (7, 5), (5, 6), (6, 6), (7, 6)]),
    scn('weak-control', '.' * 12, potions=0, p15=0x00,
        actors=[(i, 14 + 2 * i, 8, 0x08) for i in range(6)]),
    scn('p2join-control', '.' * 10, potions=0, potions2=0,
        actors=[(i, 14 + 2 * i, 8, 0x00) for i in range(6)],
        pattern2='F' + '.' * 9),
]


# ===========================================================================
# THE REAL Z80
# ===========================================================================
def run_z80(only=None):
    from harness import Harness, PC, IFF, TAPE_CALL_PC       # noqa: E402
    from keyprobe import keymask                             # noqa: E402
    from sim_move import KM, LOOP_TOP                        # noqa: E402

    def one_pass(h, sfx, clk=None):
        """sim_move.step_to_loop_top with a hook on the sound driver entry."""
        sim = h.sim
        regs = sim.registers
        opcodes = sim.opcodes
        mem = sim.memory
        fd, ia = h.frame_duration, h.int_active
        if clk is None:
            clk = [0]
        n = 0
        while n < 8_000_000:
            pc = regs[PC]
            if n and pc == LOOP_TOP:
                return
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape()
                n += 1
                continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt()
                n += 1
                continue
            if pc == SFX_CALL:
                sfx.append(regs[0])                   # A, the effect id
            elif pc == 0xB6DA:
                clk[0] = mem[0x8497]                  # the drain's own clock
            opcodes[mem[pc]]()
            n += 1
            if regs[IFF] and regs[25] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
        raise RuntimeError('no main-loop top in 8M instructions')

    # player 1 MAGIC is CAPS ($857E BIT 0,(IY+0)); player 2's is SPACE
    # ($85A7 BIT 0,(IY+7)) and his FIRE is M -- the same map tools/p2gate.py
    # uses.  Ports.press ANDs into the half-row it already holds, so two keys
    # that share one half-row (SPACE and M both live in $7F) combine.
    K1M, K1F, K2M, K2F = KM['CAPS'], KM['Z'], KM['SPACE'], KM['M']
    out = []
    for s in MATRIX:
        if only and only not in s['label']:
            continue
        h = Harness()
        h.load_state(pickle.load(open(STATE, 'rb')))
        m = h.memobj.m
        one_pass(h, [])                               # settle on a loop top
        m[A_POTIONS] = s['potions']
        # player 2's potions are poked AFTER the join, not at setup: $8ADA
        # rebuilds his whole block when he materialises, so anything written
        # to $8449 beforehand is wiped -- the first version of these scenarios
        # gave him three potions and watched him play sound 13 for ten passes.
        if not s['potions2_at']:
            m[A2_POTIONS] = s['potions2']
        if s['p15'] is not None:
            m[A_P15] = s['p15']
        if s['p14'] is not None:
            m[A_P14] = s['p14']
        for i, x, y, st in s['actors']:
            a = ACTORS + 4 * i
            m[a], m[a + 1], m[a + 2] = x & 0xFF, y & 0xFF, st & 0xFF
        for c, r, v in s['cells']:
            m[cell(c, r)] = v
        rows = []
        clock = []
        for n, (ch, ch2) in enumerate(zip(s['pattern'], s['pattern2']), 1):
            if n == s['potions2_at']:
                m[A2_POTIONS] = s['potions2']
            if s['f11_set'] and n == s['f11_set'][0]:
                m[A_F11] |= s['f11_set'][1]
            h.ports.release_all()
            if ch == 'M':
                h.ports.press(K1M[0], keymask(K1M[1]))
            elif ch == 'Z':
                h.ports.press(K1F[0], keymask(K1F[1]))
            if ch2 == 'M':
                h.ports.press(K2M[0], keymask(K2M[1]))
            elif ch2 == 'F':
                h.ports.press(K2F[0], keymask(K2F[1]))
            sfx = []
            clk = [0]
            one_pass(h, sfx, clk)
            clock.append(clk[0])
            rows.append(dict(
                v=[m[A_POTIONS], m[A_T], 1 if m[A_FLAGS] & 8 else 0,
                   m[A_FLAGS] & 1, m[A_F11],
                   m[A_KILL], m[A_GEN], m[A_COUNT], m[A_K], m[A_LO], m[A_HI],
                   # (IX+4..6), and (IX+6) is the LOW byte -- $B807 adds E to
                   # it first.  Sampled because $8BDA's pay-out is otherwise
                   # invisible: a mutation test that changed its 10 to a 9 went
                   # completely unnoticed until this column existed.
                   m[A_SCORE + 2] | (m[A_SCORE + 1] << 8) | (m[A_SCORE] << 16),
                   m[A2_POTIONS],
                   m[A2_SCORE + 2] | (m[A2_SCORE + 1] << 8)
                   | (m[A2_SCORE] << 16),
                   # $84CA -- the BORDER, which is the white flash a potion
                   # makes.  $A2A9 BIT 3,(IY-1) picks 7 or 0 on EVERY frame
                   # interrupt, so it is up for exactly as long as the sweep.
                   m[A_BORDER] & 7],
                cells=[m[cell(c, r)] for c, r in s['watch']],
                sfx=[e for e in sfx if e in (5, 13)]))
        out.append((s['label'], rows, clock))
    return out


# ===========================================================================
# THE BUILT ENGINE
# ===========================================================================
PORT_JS = r'''
'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm');
const BUILT=path.join(__dirname,'..','web','gauntlet.html');
const ctxStub={set fillStyle(v){this._f=v;},get fillStyle(){return this._f;},fillRect(){}};
function makeEl(id){const s=new Set();return{id,_text:'',innerHTML:'',
 get textContent(){return this._text;},set textContent(v){this._text=String(v);},
 getContext(){return ctxStub;},
 classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},
 width:256,height:192};}
const els=new Map();
const sandbox={console,atob:s=>Buffer.from(s,'base64').toString('binary'),
 document:{getElementById(id){if(!els.has(id))els.set(id,makeEl(id));return els.get(id);}},
 addEventListener(){},requestAnimationFrame(){return 1;},
 Math,JSON,Uint8Array,Buffer,String,Number,Array,Object,Error};
sandbox.globalThis=sandbox;vm.createContext(sandbox);
const html=fs.readFileSync(BUILT,'utf8');
const jm=html.match(/<script type="application\/json" id="assets">([\s\S]*?)<\/script>/);
els.set('assets',Object.assign(makeEl('assets'),
  {_text:jm[1].split(String.fromCharCode(60,92,47)).join('</')}));
const cm=html.match(/<script>([\s\S]*?)<\/script>\s*$/);
vm.runInContext(cm[1],sandbox,{filename:'gauntlet.html'});
const G=sandbox.globalThis.__GAUNTLET__;
const MATRIX=JSON.parse(process.argv[2]);
const out=[];
for(const s of MATRIX){
  /* char $2A is what the captured machine's loader left at $FFFF, so this is
     the same player block the Z80 side starts from -- p15 = $20. */
  /* $FFFF and $FFFE are what the captured machine's loader left for the two
     players, so both blocks start where the Z80 side starts.  Player 2's
     matters as soon as he throws: his $8455 picks his own $7D1C row. */
  const g=G.seed({char:0x2A,char2:0x2A});
  /* THE DRAIN'S CLOCK, measured on the original and handed over -- the same
     substitution p2gate.py makes, and for the same reason.  This engine
     charges a flat four video frames a pass where the original's cost
     3.92..5.03, so over the sixteen passes between two drain ticks the tick
     moves by one pass, and $842B bit 0 (health dirty) then shifts the pass on
     which $B7B1 clears the potion-spend debounce.  What is under test here is
     the POTION, not the clock. */
  if(s.clock && s.clock.length) g.clockOverride=s.clock.slice();
  g.potions=s.potions;
  if(!s.potions2_at) g.players[1].potions=s.potions2;
  if(s.p15!==null) g.p15=s.p15;
  if(s.p14!==null) g.p14=s.p14;
  for(const a of s.actors){ const t=g.actors[a[0]];
    t.x=a[1]&0xFF; t.y=a[2]&0xFF; t.state=a[3]&0xFF; }
  for(const c of s.cells) g.map[c[1]&31][c[0]&31]=c[2];
  /* the sound TRIGGERS, not the driver's register writes: shadow sfx() on the
     instance so $A530's 13 and $A579's 5 are visible the way $BA2B's A is. */
  const sfx=[]; const real=g.sfx.bind(g);
  g.sfx=function(n){ sfx.push(n); return real(n); };
  const rows=[];
  for(let pi=0;pi<s.pattern.length;pi++){
    const ch=s.pattern[pi], ch2=s.pattern2[pi];
    if(pi+1===s.potions2_at) g.players[1].potions=s.potions2;
    if(s.f11_set && pi+1===s.f11_set[0]) g.f11|=s.f11_set[1];
    sfx.length=0;
    g.onePass({potion:ch==='M', fire:ch==='Z',
               p2:{potion:ch2==='M', fire:ch2==='F'}});
    rows.push({v:[g.potions,g.potionT,g.potionArmed?1:0,g.f847E_bit0?1:0,g.f11,
                  g.killTally,g.genTally,g.actors.length,
                  g.potionK,g.potionLo,g.potionHi,g.score,
                  g.players[1].potions,g.players[1].score,g.border&7],
               cells:s.watch.map(c=>g.map[c[1]&31][c[0]&31]),
               sfx:sfx.filter(n=>n===5||n===13)});
  }
  out.push([s.label,rows]);
}
console.log(JSON.stringify(out));
'''


def run_port(only=None, clocks=None):
    js = os.path.join(ROOT, 'build', '_potiongate_port.js')
    open(js, 'w').write(PORT_JS)
    spec = []
    for s in MATRIX:
        if only and only not in s['label']:
            continue
        d = dict(s)
        d['clock'] = (clocks or {}).get(s['label'], [])
        spec.append(d)
    r = subprocess.run(['node', js, json.dumps(spec)],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        print(r.stderr)
        raise SystemExit(1)
    return [(lbl, rows) for lbl, rows in json.loads(r.stdout)]


# ===========================================================================
def fmt(row):
    s = ' '.join('%02X' % v for v in row['v'])
    if row['cells']:
        s += ' |' + ''.join(' %02X' % c for c in row['cells'])
    if row['sfx']:
        s += ' sfx' + ','.join(str(n) for n in row['sfx'])
    return s


def show(side, table):
    for label, rows in table:
        print('\n%s  %s' % (side, label))
        print('  pass  ' + ' '.join('%2s' % c[:2] for c in COLS))
        for i, r in enumerate(rows, 1):
            print('  %4d  %s' % (i, fmt(r)))


def differs(s, a, b):
    """Row a vs row b, honouring the scenario's `ignore` list of column names."""
    keep = [i for i, c in enumerate(COLS) if c not in s['ignore']]
    return ([a['v'][i] for i in keep] != [b['v'][i] for i in keep]
            or a['cells'] != b['cells'] or a['sfx'] != b['sfx'])


def tally(z, p, quiet=False):
    """Compare and (unless quiet) print.  Returns (differing rows, total)."""
    by_label = {s['label']: s for s in MATRIX}
    bad = total = exempt = 0
    for (zl, zr, _clk), (pl, pr) in zip(z, p):
        assert zl == pl, (zl, pl)
        s = by_label[zl]
        diffs = []
        for i, (a, b) in enumerate(zip(zr, pr), 1):
            total += 1
            if differs(s, a, b):
                bad += 1
                diffs.append((i, a, b))
        if quiet:
            continue
        note = ''
        if s['ignore']:
            exempt += 1
            note = '   [ignoring %s -- see `potiongate.py controls`]' % (
                ','.join(s['ignore']))
        print('%s %-12s %3d passes, %d differing%s'
              % ('ok      ' if not diffs else 'MISMATCH',
                 zl, len(zr), len(diffs), note))
        for i, a, b in diffs[:6]:
            print('    pass %-3d  Z80  %s' % (i, fmt(a)))
            print('             PORT %s' % fmt(b))
        if len(diffs) > 6:
            print('    ... and %d more' % (len(diffs) - 6))
    if not quiet and exempt:
        print()
        print('%d scenario(s) carry a column exemption; the controls '
              'that justify them are scenarios of this tool too:'
              % exempt)
        print('    python tools/potiongate.py controls')
    return bad, total


def cmd_dump():
    """Write build/potiongate.json: the SCENARIOS plus the real Z80's answer.

    tools/headless.js then drives the built engine through the same scenarios
    and compares, so the potion has an always-on regression check anchored on
    measurement rather than on this engine's own output -- the same shape as
    build/telecensus.json."""
    z = run_z80()
    doc = {'scenarios': MATRIX,
           'cols': list(COLS),
           'z80': [{'label': l, 'rows': r, 'clock': c} for l, r, c in z]}
    out = os.path.join(ROOT, 'build', 'potiongate.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, separators=(',', ':'))
    n = sum(len(x['rows']) for x in doc['z80'])
    print('wrote %s -- %d scenarios, %d passes' % (out, len(doc['z80']), n))


def main():
    global MATRIX
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if what == 'dump':
        cmd_dump()
        return
    only = None
    helped = True
    if what == 'controls':
        MATRIX, what = CONTROLS, 'all'
        print('THE CONTROLS.  None of these throws a potion at all.  They are')
        print('EXPECTED TO MISMATCH -- that is what makes the three column')
        print('exemptions in the main matrix a measurement and not an excuse.')
        print()
    elif what == 'unhelped':
        helped, what = False, 'all'
    elif what not in ('all', 'z80', 'port'):
        only, what = what, 'all'

    z = run_z80(only) if what in ('all', 'z80') else None
    if what == 'port':
        show('PORT', [(l, r) for l, r in run_port(only)])
        return
    if what == 'z80':
        show('Z80', [(l, r) for l, r, _ in z])
        return
    if not z:
        print('no scenario matches %r' % only)
        raise SystemExit(2)

    clocks = {l: c for l, _r, c in z}
    p = run_port(only, clocks if helped else None)
    bad, total = tally(z, p, quiet=False)
    print()
    print('%d mismatching rows of %d   (%s)'
          % (bad, total, ' '.join(COLS)))
    if getattr(harness, 'CONTENDED', False):
        print('   the harness is CONTENDED, and EIGHT of these are'
              ' expected and declared:')
        print('   swarm-big-tough and swarm-weak, the `nact` column,'
              ' off by one actor.')
        print('   The actor coins read LD A,R and contention moves R.'
              '  0 of 438 with GAUNTLET_CONTENDED=0.')

    if helped:
        # the house rule (see p2gate.py): print what the substitution is worth,
        # so a gate can never quietly hide behind it.
        ub, _ = tally(z, run_port(only, None), quiet=True)
        print('the drain clock was measured on the original and handed '
              'to the engine; without it')
        print('the same matrix reports %d differing rows (`potiongate.py unhelped`)' % ub)
    raise SystemExit(1 if bad else 0)


if __name__ == '__main__':
    main()
