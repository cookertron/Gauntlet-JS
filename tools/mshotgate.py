#!/usr/bin/env python3
"""
mshotgate.py -- MONSTER SHOTS, $ACC9 / $AF8F / $B04B / $8FC5 / $8C0B.

Classes 2 and 3 shoot back.  Class 2 first appears on level 5 -- 23 of them --
so an engine without this is markedly easier than the original from there on.

    python tools/mshotgate.py            measure, write build/_mshot.json
    node tools/headless.js               compare the built engine against it

THE RULES THIS GATE CARRIES.

  $ACC2  AND $E0 / CP $40 / JR c   |  $ACC9  CP $80 / CALL c,$AF8F
         classes 2 and 3 only.

  $AF8F  BIT 4,(IY+$29) / RET nz   at 16 live shots nobody may fire
         CP $60 / JP z,$B060       class 3 takes a DIFFERENT routine

  CLASS 2 fires only when ALIGNED with a living player, and which alignment
  depends on the low two bits of its facing:
         facing AND 3 == 0   ($AFE7)  |dx| < 3      -- it faces up or down
         facing AND 3 == 2   ($B019)  |dy| < 3      -- left or right
         otherwise           ($AFA4)  |dy|-|dx| in [-2,2]  -- a diagonal
  Player 1 is tried first and player 2 only if player 1 is dead or not lined
  up ($842B / $844B bit 7).  The distance $AFC2 computes is DEAD -- $B04B's
  LD A,E overwrites it -- and only the range test's flags matter.

  $B04B  THE RING, and it is what limits the rate.  A hit does not create a
         shot, it claims one of SIXTEEN slots at $5B90 (eight facings x two
         players; $B049 SET 5,L puts player 2's at $5BB0).  The slot index is
         also its priority and $B056 CP (HL) / RET nc never overwrites an
         occupied slot, so the FIRST actor to claim a facing keeps it.
  $AB94  refills all 64 bytes with $FF at the top of every actor pass, so a
         claim lives for exactly one pass.
  $8FC5  drains only TWO slots a pass, chosen by $8491 & 7, appending
         (x+1, y+1, facing | $90, $80) to the array at $5B20.  $90 sets the
         BORN bit so the first step skips both collision scans; $80 is the
         flag every "is this a monster shot" test reads.
  $8C0B  walks the array through the SAME body the players use, removing with
         the swap-with-last idiom ($8C1B copies the cursor record over the
         dead one and does NOT advance IX).

  $8C69  BIT 7,(IX+3) / RET nz after $8C60's CP $20 matched -- so an exploded
         monster shot returns with CARRY CLEAR, and that is the ONLY thing
         that ever removes one.  Miss it and the array fills to its cap of 16
         with dead $FF records and no monster can fire again.

  In the shared body a monster shot: skips the whole spawn block ($8C6E),
  never reads (IX+4) for the speed bump so it always steps 2 ($8CFB), deals a
  flat 8 rather than consulting the power table ($90E7), and scores nothing
  ($8D67).

NOT COVERED HERE: class 3's own routine at $B060, which aims with a computed
sub-cell vector and writes STRAIGHT to the array with flags bit 6 set, taking
the fine-grained velocity encoding at $8CBC.  Class 3 first appears on level 8.

NO BOUNDARY SCENES HERE, and the reason is structural: $AF8F is reached from
actorTail, which runs AFTER the actor has moved and turned, so the facing that
fires is the POST-TURN facing and $AC25 is LD A,R.  A scene sitting on any of
the three windows' edges therefore flips with the coin.  What survives in the
matrix are scenes whose answer is the same for EVERY facing -- `skew` is
rejected by all three arms, the aligned ones are accepted by the arm they use
and no other -- and the edges are measured instead by GRID mode, which calls
$AF8F directly with the registers set and no simulation around it.

    python tools/mshotgate.py grid     the routine-level differential
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, IFF, TAPE_CALL_PC          # noqa: E402
from sim_move import LOOP_TOP                               # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
RING, ARRAY = 0x5B90, 0x5B20

# (dx, dy, facing) around the player.  Facings 0..7 are up, upright, right,
# downright, down, downleft, left, upleft.
# EVERY actor must be ON CAMERA, because $AF8F is reached from actorTail and
# actorTail only runs for an actor that survived $A1DA's four RET nc.  The
# player is at (12,8) with the camera at (2,2), so the window is roughly
# x 2..68 and y 2..44 -- which means "above" and "left of" the player are OFF
# it.  A first version of this matrix put three of the five scenes up there
# and measured no shots at all, which looks exactly like a rule and is not.
# Each scene is a list of (dx, dy, facing) plus options:
#   join   press player 2's FIRE on pass 1, so his eight ring slots at $5BB0
#          and his own 5x5 hit box come into play
#   p14    force player 1's $8434, whose bit 4 is the SHOT SPEED bump -- a
#          monster shot must NOT take it ($8CFB guards the read)
#   prey   plant a class-0 actor in the line of fire, so a monster shot hits
#          another MONSTER and takes the flat-8 damage path at $90E7
SCENES = {
    # aligned VERTICALLY: below the player, facing up (facing 0, AND 3 == 0)
    'vert':    {'a': [(0, 12, 0), (0, 20, 0)]},
    # aligned HORIZONTALLY: right of the player, facing left (6, AND 3 == 2)
    'horiz':   {'a': [(12, 0, 6), (20, 0, 6)]},
    # the DIAGONAL arm, $AFA4: down-right of him, facing upleft (7)
    'diag':    {'a': [(12, 12, 7), (20, 20, 7)]},
    # the diagonal window's OWN boundary.  |v|-|h| is 2 (the last accepted)
    # and 3 (the first rejected); without these a mutation that widened
    # $AFBC's CP 5 to CP 9 was caught by nothing.
    # NOT aligned on ANY axis, whatever it turns to face
    'skew':    {'a': [(14, 4, 0), (20, 6, 2)]},
    # four actors all claiming the SAME facing slot -- only one may hold it
    'contend': {'a': [(0, 12, 0), (0, 16, 0), (0, 20, 0), (0, 24, 0)]},
    # TWO PLAYERS: player 2 joins, so $B049 SET 5,L and his own hit box matter
    # Player 2 materialises ON player 1, so actors lined up with one are lined
    # up with both -- which is what makes $B049's SET 5,L visible: the same
    # facing claims a slot in HIS half of the ring at $5BB0.  The shooters are
    # kept on the cardinal axes; one at (12,8) drifted into a diagonal facing
    # while he was still appearing and fired in one engine and not the other,
    # which is $AC25's LD A,R and not a rule.
    'twoplayer': {'a': [(0, 12, 0), (12, 0, 6), (0, 20, 0)], 'join': True},
    # PLAYER 2 ALONE.  He materialises on player 1, so while they overlap an
    # actor aligned with one is aligned with both -- and $AFA4 returns after
    # the FIRST success, so player 1 always takes the slot and $B049's
    # SET 5,L is never exercised.  Moving him clear (a poke both engines get)
    # is what makes his half of the ring at $5BB0 reachable at all.
    # NO 'p2only' SCENE.  Player 2 cannot be held alive across passes by
    # poking $844B: something re-marks him, and a scene that means to test
    # $B049 ends up testing that instead.  His half of the ring is covered by
    # GRID mode below, where $AF8F is called on its own and nothing can undo
    # the poke.
    # A monster shot hitting a MONSTER.  $90E7 gives it a flat 8 and never
    # consults $7D64, and to SEE that the prey must survive one value and not
    # the other: $9102's AND $18 quantises damage to whole tiers, so 8 and
    # $7D64's 9 both come out 8 and the two paths are indistinguishable.
    # Power 2 gives $10 -- a whole tier more -- and a TIER-1 prey ($08) lives
    # through the monster's 8 and dies to the table's $10.
    'friendly2': {'a': [(0, 20, 0)], 'prey': [(0, 12)], 'preystate': 0x08,
                  'p15': 0x02},
    # CLASS 1, placed exactly where a class 2 fires.  $ACC2's CP $40 must
    # refuse it: without this scene a mutation that let class 1 shoot was
    # caught by nothing, because every other scene is class 2.
    'class1':  {'a': [(0, 12, 0), (12, 0, 6), (12, 12, 7)], 'cls': 0x20},
    # ...and CLASS 4, the other side of $ACC9's CP $80, placed the same way.
    'class4':  {'a': [(0, 12, 0), (12, 0, 6), (12, 12, 7)], 'cls': 0x80},
    # NO 'cap' SCENE.  $AF8F's BIT 4,(IY+$29) needs SIXTEEN live shots and
    # only two are born a pass against a life of about three, so the guard is
    # not reachable by playing; prefilling $84A8 alone would hand the Z80
    # sixteen records of whatever $5B20 happens to hold and the port none.
    # It is checked as a UNIT instead, in tools/headless.js, and labelled.
    # player 1 carrying the SHOT-SPEED bit.  A monster shot must still step 2.
    'fast':    {'a': [(0, 12, 0), (12, 0, 6)], 'p14': 0x10},
    # a class-0 actor standing in the line of fire, so the shot hits a MONSTER
    'friendly': {'a': [(0, 20, 0)], 'prey': [(0, 12)]},
    # a CROWD on every facing, to fill the array towards its cap of 16 and to
    # make the swap-with-last removal matter
    'crowd':   {'a': [(0, 12, 0), (0, 16, 0), (12, 0, 6), (16, 0, 6),
                      (12, 12, 7), (16, 16, 7), (2, 20, 0), (20, 2, 6),
                      (0, 24, 0), (24, 0, 6), (8, 8, 7), (20, 20, 7)]},
}


def one_pass(h, clk=None):
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < 20_000_000:
        pc = regs[PC]
        if n and pc == LOOP_TOP:
            return
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        if clk is not None and pc == 0xB6DA:
            clk[0] = mem[0x8497]
        ops[mem[pc]]()
        n += 1
        if regs[IFF] and regs[25] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    raise RuntimeError('no main-loop top')


# EIGHT passes, not sixteen, and the length is a measurement not a taste:
# $AC25 is LD A,R, so an actor's facing is not reproducible by any port, and
# a scene that sits on a boundary (edge is at |dx| = 2 and 3) flips the moment
# one unit of drift accumulates.  Eight passes is comfortably more than the
# whole life of a shot -- born, flown, hit, removed -- and short enough that
# the scene is still the scene.  Sixteen showed the drift and nothing else.
def run(scene, passes=12):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)
    opt = SCENES[scene]
    px, py = m[0x8420], m[0x8421]
    if 'p14' in opt:
        m[0x8434] |= opt['p14']
    if 'p15' in opt:
        m[0x8435] = opt['p15']
    base = len(opt['a'])

    def plant():
        for i, (dx, dy, f) in enumerate(opt['a']):
            a = 0x5C00 + 4 * i
            m[a] = (px + dx) & 0x7F
            m[a + 1] = (py + dy) & 0x7F
            m[a + 2] = opt.get('cls', 0x40) | f
            m[a + 3] = 0x04
        for j, (dx, dy) in enumerate(opt.get('prey', [])):
            a = 0x5C00 + 4 * (base + j)
            m[a] = (px + dx) & 0x7F
            m[a + 1] = (py + dy) & 0x7F
            m[a + 2] = opt.get('preystate', 0x00)   # something to shoot
            m[a + 3] = 0x04
    plant()
    # The settle pass can itself drain, so the two sides must START from the
    # same health or every row differs by one BCD unit for a reason that has
    # nothing to do with monster shots.  Sampled AFTER the settle, exactly as
    # p2gate.py seeds its own.
    seed = {'phase': m[0x849F], 'frame': m[0x8497],
            'hp': (m[0x8422] << 8) | m[0x8423], 'hurry': m[0x84B8],
            'f11': m[0x842B]}
    from keyprobe import keymask
    from sim_move import KM
    rows, clock = [], []
    for n_ in range(passes):
        h.ports.release_all()
        if opt.get('join') and n_ == 0:          # $8ADA -- player 2 joins
            sel, bit = KM['M']
            h.ports.press(sel, keymask(bit))
        if opt.get('p2at'):
            # Player 2 placed DIRECTLY rather than joined: $8ADA's materialise
            # walks him over several passes and where he is on the way is not
            # something the two engines agree on, which made a scene meant to
            # test $B049 test the materialise instead.  Both sides are handed
            # the same three bytes.
            m[0x8440] = (px + opt['p2at'][0]) & 0x7F
            m[0x8441] = (py + opt['p2at'][1]) & 0x7F
            m[0x844B] &= ~0xC0                  # not dead, not exiting
        # REPAINT the actors every pass, on both sides.  $AC25 is LD A,R, so
        # an actor's facing and position drift apart between the two engines
        # within a few passes and the scene stops being the scene.  Pinning
        # them is what isolates the SHOT machinery from the actor RNG -- the
        # same reason shotgate.py holds fire to freeze the player.
        plant()
        clk = [0]
        one_pass(h, clk)
        clock.append(clk[0])
        rows.append({
            'ring': list(m[RING:RING + 64]),
            'n': m[0x84A8],
            'cur': m[0x84A9],
            'shots': [list(m[ARRAY + 4 * k: ARRAY + 4 * k + 4])
                      for k in range(m[0x84A8])],
            'hp': (m[0x8422] << 8) | m[0x8423],
            'hp2': (m[0x8442] << 8) | m[0x8443],
            'score': m[0x8426] | (m[0x8425] << 8) | (m[0x8424] << 16),
            'nact': m[0x8496],
        })
    return {'rows': rows, 'clock': clock, 'seed': seed,
            'actors': opt['a'], 'prey': opt.get('prey', []),
            'join': bool(opt.get('join')), 'p14': opt.get('p14', 0),
            'p15': opt.get('p15', None), 'p2at': opt.get('p2at', None),
            'cls': opt.get('cls', 0x40),
            'preystate': opt.get('preystate', 0x00)}


def flight3(vectors, passes=20):
    """Plant a CLASS-3 shot in the $5B20 array and let the main loop fly it.

    This is the other half of class 3: $8CBC's sub-cell step, $90AD's lifetime
    and curve, and the expiry that $8D31 turns into "skip the collision
    scans".  The record is planted directly because $B060's own trigger is
    LD A,R -- what is under test is the FLIGHT, not when it starts."""
    out = []
    for state, flags in vectors:
        h = Harness()
        h.load_state(pickle.load(open(STATE, 'rb')))
        m = h.memobj.m
        one_pass(h)
        px, py = m[0x8420], m[0x8421]
        m[ARRAY], m[ARRAY + 1] = (px + 10) & 0x7F, (py + 10) & 0x7F
        m[ARRAY + 2], m[ARRAY + 3] = state, flags
        m[0x84A8] = 1
        m[0x84A9], m[0x84AA] = 0x24, 0x5B
        # the drain is clocked by $8497, not by passes -- measured and handed
        # over, the same substitution every other gate here makes
        seed = {'phase': m[0x849F], 'frame': m[0x8497],
                'hp': (m[0x8422] << 8) | m[0x8423]}
        rows, clock = [], []
        for _ in range(passes):
            clk = [0]
            one_pass(h, clk)
            clock.append(clk[0])
            rows.append({'n': m[0x84A8],
                         'rec': list(m[ARRAY:ARRAY + 4]),
                         'hp': (m[0x8422] << 8) | m[0x8423]})
            if not m[0x84A8]:
                break
        out.append({'state': state, 'flags': flags, 'rows': rows,
                    'clock': clock, 'seed': seed,
                    'x0': (px + 10) & 0x7F, 'y0': (py + 10) & 0x7F})
    return out


def grid3():
    """$B060 called DIRECTLY over a grid of offsets, recording the RECORD it
    writes.  Class 3 aims rather than aligning, so what matters is the
    sub-cell vector it computes -- and $B060 opens with LD A,R, which is not
    reproducible, so the entry is made at $B065 with the coin supplied."""
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)
    px, py = m[0x8420], m[0x8421]
    sp0 = h.sim.registers[12]
    m[0x842A] = 0                                   # player 1's timer clear
    rows = []
    for dx in range(-20, 21, 2):
        for dy in range(-20, 21, 2):
            m[0x84A8] = 0
            m[0x84A9], m[0x84AA] = 0x20, 0x5B
            for k in range(16):
                m[ARRAY + k] = 0
            h.sim.registers[12] = sp0
            x, y = (px - dx) & 0x7F, (py - dy) & 0x7F
            # enter at $B065 with A = 0, i.e. the coin already drawn and even
            h.call(0xB065, {'A': 0, 'BC': (y << 8) | x, 'DE': 0x60})
            rows.append([dx, dy, m[0x84A8],
                         list(m[ARRAY:ARRAY + 4])])
    return rows


def grid():
    """Call $AF8F DIRECTLY over a grid of (dx, dy, facing) and record which
    ring slot it claims.  No actor loop, no movement, no LD A,R -- so the
    three alignment windows can be pinned at their exact edges."""
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)
    px, py = m[0x8420], m[0x8421]
    sp0 = h.sim.registers[12]
    rows = []
    # who = 0: player 1 alive, as captured.  who = 1: player 1 marked DEAD and
    # player 2 alive at the same spot, which is the only way to reach $AFC7 /
    # $B001 / $B033 -- the arms that end in $B049 SET 5,L and put the claim in
    # his half of the ring at $5BB0.  Nothing runs but $AF8F, so the poke
    # cannot be undone underneath the measurement.
    for who in (0, 1):
        if who:
            m[0x842B] |= 0x80                       # player 1 dead
            m[0x844B] &= ~0x80                      # player 2 alive
            m[0x8440], m[0x8441] = px, py           # ...and in his place
        for f in range(8):
            for dx in range(-6, 7):
                for dy in range(-6, 7):
                    for k in range(64):
                        m[RING + k] = 0xFF
                    m[0x84A8] = 0
                    h.sim.registers[12] = sp0
                    x, y = (px - dx) & 0x7F, (py - dy) & 0x7F
                    h.call(0xAF8F, {'A': 0x40, 'BC': (y << 8) | x,
                                    'DE': 0x40 | f})
                    hit = [k for k in range(64) if m[RING + k] != 0xFF]
                    rows.append([who, f, dx, dy, hit[0] if hit else -1])
    return rows


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'flight3':
        # a spread of magnitudes, both signs on both axes, odd and even
        # counters (only ODD ones curve, $90C3 BIT 0,E)
        VEC = [(0x01, 0xC4), (0x02, 0xCF), (0x23, 0xC8), (0x45, 0xC3),
               (0x67, 0xCE), (0x8A, 0xC5), (0xAB, 0xC1), (0xCD, 0xC7),
               (0xEF, 0xC2), (0x11, 0xC9), (0x76, 0xCB), (0x99, 0xCC)]
        out = flight3(VEC)
        path = os.path.join(ROOT, 'build', '_mshot3f.json')
        json.dump({'flights': out}, open(path, 'w'))
        for f in out:
            last = f['rows'][-1]
            print('state $%02X flags $%02X: %2d passes, ends %s hp %04X'
                  % (f['state'], f['flags'], len(f['rows']),
                     last['rec'], last['hp']))
        print('wrote', path)
        return
    if len(sys.argv) > 1 and sys.argv[1] == 'grid3':
        rows = grid3()
        n = sum(1 for r in rows if r[2])
        path = os.path.join(ROOT, 'build', '_mshot3.json')
        json.dump({'rows': rows}, open(path, 'w'))
        print('%d offsets, %d produced a shot' % (len(rows), n))
        got = {}
        for r in rows:
            if r[2]:
                got[(r[3][2], r[3][3])] = got.get((r[3][2], r[3][3]), 0) + 1
        print('  %d distinct (state, flags) vectors' % len(got))
        for k in sorted(got)[:8]:
            print('    state $%02X flags $%02X  x%d' % (k[0], k[1], got[k]))
        print('wrote', path)
        return
    if len(sys.argv) > 1 and sys.argv[1] == 'grid':
        rows = grid()
        fired = sum(1 for r in rows if r[4] >= 0)
        path = os.path.join(ROOT, 'build', '_mshotgrid.json')
        json.dump({'rows': rows}, open(path, 'w'))
        print('%d (facing, dx, dy) probes, %d claimed a slot' % (len(rows), fired))
        for who in (0, 1):
            slots = sorted({r[4] for r in rows if r[0] == who and r[4] >= 0})
            n = sum(1 for r in rows if r[0] == who and r[4] >= 0)
            print('  player %d: %3d fire, ring slots %s'
                  % (who + 1, n, slots))
        print('wrote', path)
        return
    out = {}
    for scene in SCENES:
        out[scene] = run(scene)
        rows = out[scene]['rows']
        born = sum(1 for r in rows if r['n'])
        print('%-10s %2d passes, shots live on %d, peak %d, hp %04X hp2 %04X '
              'score %06X'
              % (scene, len(rows), born, max(r['n'] for r in rows),
                 rows[-1]['hp'], rows[-1]['hp2'], rows[-1]['score']))
    path = os.path.join(ROOT, 'build', '_mshot.json')
    json.dump(out, open(path, 'w'))
    print('wrote', path)


if __name__ == '__main__':
    main()
