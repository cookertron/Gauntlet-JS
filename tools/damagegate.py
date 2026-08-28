#!/usr/bin/env python3
"""
damagegate.py -- drive the REAL Z80 and print every number in the damage and
score model.  Nothing here loads the JS engine; every row is the original
answering.

Subcommands
    melee      $A5F0's arms: walk the player into an actor of each class/tier
    contact    $AEA0's arms: walk an actor of each class/tier into the player
    armour     the six $7D34 records installed at $8437, contact damage measured
    genmelee   $A6B5/$A964: melee a generator, cell by cell, per FIGHT POWER
    genshot    $8F79: shoot a generator, per SHOT POWER
    shotactor  $90E6: shoot an actor of each class/tier
    potion     $AF5D: the potion sweep, per MAGIC POWER
    items      $A65D's eight consumers, priced; the thief; the generator
    tally      $8BCA: what a thrown potion pays out
    attrs      $8435's four 2-bit attributes and the $AB6F armour install
    walkinto   IN SITU melee: hold right into a pinned monster
    shotplayer $9009: a shot lands on a player
    death      $93CD: health BCD 0000
    drain      $AF26: the class-5 drain accumulator
    score      every $B807/$B7E9/$913C site, driven
    fire       drive the running game with FIRE held and watch a real shot
    all        everything above

The player block is $8420 (32-byte stride); the actor list is 4-byte records
at $5C00 with the count at $8496 and the tail pointer at $8494.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, A, B, C, D, E, H, L, PC, T, IFF,   # noqa: E402
                     TAPE_CALL_PC, FRAME_T)
from keyprobe import KEYS, keymask                               # noqa: E402
from sim_move import step_to_loop_top                            # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')

P1 = 0x8420
LIST = 0x5C00
COUNT = 0x8496
TAIL = 0x8494
CTR = 0x8491


def fresh():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def hp(m):
    return f'{m[P1 + 2]:02X}{m[P1 + 3]:02X}'


def score(m):
    return f'{m[P1 + 4]:02X}{m[P1 + 5]:02X}{m[P1 + 6]:02X}'


def bcd(s):
    """The health/score fields are PACKED BCD, so the decimal value is the
    hex digits read as decimal.  int(s, 16) is the classic way to get this
    wrong (1997 - 1987 would print 16, not 10)."""
    return int(s, 10)


def one_actor(h, x, y, state, flags=0x00):
    """Wipe the actor list down to a single record and return its address."""
    m = h.memobj.m
    m[LIST + 0] = x
    m[LIST + 1] = y
    m[LIST + 2] = state
    m[LIST + 3] = flags
    m[COUNT] = 1
    m[TAIL] = (LIST + 4) & 0xFF
    m[TAIL + 1] = (LIST + 4) >> 8
    return LIST


# --------------------------------------------------------------------------
def melee():
    """$A5F0: the player's move finds an actor in the 7x7 box.

    Called in isolation with BC = the candidate square, IX = the player.
    The class-0 arm ($A627) damages the PLAYER and destroys the actor; the
    class 1-4 arm ($A606) subtracts 8 from the state byte and scores 25; the
    class-5 arm returns without doing anything at all."""
    print('=== MELEE: player walks into an actor  ($A5F0)')
    print('cls tier  ctr  hp before -> after   score    count  state\'  hits-to-kill')
    for cls in range(6):
        for tier in range(4):
            h = fresh()
            m = h.memobj.m
            px, py = m[P1], m[P1 + 1]
            st = (cls << 5) | (tier << 3)
            one_actor(h, px, py, st)
            m[CTR] = 0x40                      # $8491 & 3 == 0, bit0 = 0
            hp0, sc0 = hp(m), score(m)
            h.call(0xA5F0, regs={'IX': P1, 'C': px, 'B': py})
            hp1, sc1 = hp(m), score(m)
            cnt = m[COUNT]
            st1 = m[LIST + 2]
            # how many calls to destroy it?
            hits = 1 if cnt == 0 else None
            if cnt:
                for k in range(2, 40):
                    h.call(0xA5F0, regs={'IX': P1, 'C': px, 'B': py})
                    if m[COUNT] == 0:
                        hits = k
                        break
            dhp = bcd(hp0) - bcd(hp1)
            print(f'{cls:>3}{tier:>5}  ${m[CTR]:02X}  {hp0} -> {hp1} (-{dhp:>2})'
                  f'  {sc0}->{sc1}  {cnt:>3}   ${st1:02X}     {hits}')
    # the pass-counter gate
    print('\n  $A600 gate: class 1 tier 0, one call at each $8491 & 3')
    for ctr in range(4):
        h = fresh()
        m = h.memobj.m
        px, py = m[P1], m[P1 + 1]
        one_actor(h, px, py, 0x20)
        m[CTR] = ctr
        s0 = score(m)
        h.call(0xA5F0, regs={'IX': P1, 'C': px, 'B': py})
        print(f'   $8491={ctr}  count={m[COUNT]}  state=${m[LIST+2]:02X}'
              f'  score {s0}->{score(m)}')


# --------------------------------------------------------------------------
def contact():
    """$AEA0: an actor's own move lands on the player's square."""
    print('=== CONTACT: an actor walks into the player  ($AEA0)')
    print('cls tier   hp before -> after    delta   actor survives  sentinel')
    for cls in range(6):
        for tier in range(4):
            h = fresh()
            m = h.memobj.m
            px, py = m[P1], m[P1 + 1]
            st = (cls << 5) | (tier << 3)
            one_actor(h, px, py, st)
            m[CTR] = 0
            hp0 = hp(m)
            idx, _, _ = h.call(0xAEA0, regs={'IX': LIST, 'C': px, 'B': py,
                                             'E': st, 'D': 0})
            hp1 = hp(m)
            print(f'{cls:>3}{tier:>5}   {hp0} -> {hp1}'
                  f'   -{bcd(hp0)-bcd(hp1):<4} count={m[COUNT]}'
                  f'  flags=${m[LIST+3]:02X}   {idx}')
    print('\n  the (IX+3)&3 countdown gate, class 1 tier 0:')
    for f in range(4):
        h = fresh()
        m = h.memobj.m
        px, py = m[P1], m[P1 + 1]
        one_actor(h, px, py, 0x20, flags=f)
        hp0 = hp(m)
        h.call(0xAEA0, regs={'IX': LIST, 'C': px, 'B': py, 'E': 0x20, 'D': 0})
        print(f'   flags in ${f:02X} -> hp {hp0} -> {hp(m)}'
              f'  flags out ${m[LIST+3]:02X}')


# --------------------------------------------------------------------------
ARMOUR_TABLE = 0x7D34


def armour():
    """$8437..$843C is 6 bytes copied out of $7D34 + 8*armour."""
    print('=== ARMOUR: $7D34 + 8*n installed at $8437, damage measured')
    src = fresh().memobj.m
    for n in range(6):
        rec = [src[ARMOUR_TABLE + 8 * n + i] for i in range(6)]
        got_c0, got_c1 = [], []
        for tier in range(4):
            for arm, out in ((0x00, got_c0), (0x20, got_c1)):
                h = fresh()
                m = h.memobj.m
                for i in range(6):
                    m[0x8437 + i] = rec[i]
                px, py = m[P1], m[P1 + 1]
                st = arm | (tier << 3)
                one_actor(h, px, py, st)
                m[CTR] = 0
                hp0 = hp(m)
                h.call(0xAEA0, regs={'IX': LIST, 'C': px, 'B': py,
                                     'E': st, 'D': 0})
                out.append(bcd(hp0) - bcd(hp(m)))
        print(f'  armour {n}: table {" ".join("%02X" % b for b in rec)}'
              f'   class0 by tier {got_c0}   class1-4 by tier {got_c1}')


# --------------------------------------------------------------------------
def gen_cell():
    """An address in the map that is safe to plant a generator in."""
    return 0x8000 + 0x20 * 20 + 20          # row 20, col 20


def genmelee():
    """$A6B5 -> $A964 -> $A6C0.  Damage per hit and hits to destroy."""
    print('=== GENERATOR MELEE  ($A6B5 / $A964 / $A6C0)')
    print('the $A964 lookup, enumerated over every reachable input:')
    print('  fight inv5  t=$7D70[i]   d on $8491 bit1=0 / =1')
    for fight in range(4):
        for inv5 in (0, 1):
            ds = []
            for b1 in (0, 2):
                h = fresh()
                m = h.memobj.m
                m[0x8435] = (m[0x8435] & ~0x0C) | (fight << 2)
                m[0x8434] = (m[0x8434] & ~0x20) | (inv5 << 5)
                m[CTR] = b1
                h.call(0xA964, regs={'IX': P1, 'DE': 0x7D70})
                ds.append(h.sim.registers[A])
            idx = ((m[0x8435] >> 1) & 6) + (4 if inv5 else 0)
            print(f'  {fight:>5}{inv5:>5}   ${m[0x7D70 + idx]:02X}'
                  f'         {ds[0]} / {ds[1]}')
    print('\nthe whole chain, planted cell $22 (class 0 tier 2):')
    for fight in (0, 2, 3):
        for inv5 in (0, 1):
            h = fresh()
            m = h.memobj.m
            cell = gen_cell()
            m[0x8435] = (m[0x8435] & ~0x0C) | (fight << 2)
            m[0x8434] = (m[0x8434] & ~0x20) | (inv5 << 5)
            seq = []
            for p in range(12):
                m[CTR] = p
                m[cell] = m[cell] if p else 0x22
                h.call(0xA6B5, regs={'IX': P1, 'HL': cell})
                seq.append(f'{p}:${m[cell]:02X}')
                if m[cell] == 0:
                    break
            print(f'  fight {fight} inv5 {inv5}: ' + ' '.join(seq))
    print('\ncell value after damage d, planted $20..$2E (d applied directly):')
    for v in (0x20, 0x21, 0x22, 0x23, 0x24, 0x2E):
        row = []
        for d in range(4):
            h = fresh()
            m = h.memobj.m
            cell = gen_cell()
            m[cell] = v
            h.call(0xA6C0, regs={'IX': P1, 'HL': cell, 'A': d})
            row.append(f'd={d}->${m[cell]:02X}')
        print(f'  ${v:02X}: ' + '  '.join(row))


# --------------------------------------------------------------------------
def genshot():
    """$8F79: the shot's own generator arm -- score 10 then $7D65 damage."""
    print('=== GENERATOR, SHOT  ($8F79, table $7D65)')
    print('  shot inv3   t=$7D65[i]   d on $8491 bit0=0 / =1')
    m0 = fresh().memobj.m
    for shot in range(4):
        for inv3 in (0, 1):
            idx = 2 * (shot + 2 * inv3)
            ds = []
            for b0 in (0, 1):
                t = m0[0x7D65 + idx]
                d = t >> 1
                if (t & 1) and b0 == 0:
                    d += 1
                ds.append(d)
            print(f'  {shot:>5}{inv3:>5}   ${m0[0x7D65 + idx]:02X}'
                  f'         {ds[0]} / {ds[1]}   (derived)')
    print('  ...and the same numbers taken off the machine, via $9115:')
    for shot in range(4):
        for inv3 in (0, 1):
            h = fresh()
            m = h.memobj.m
            m[0x8435] = (m[0x8435] & ~3) | shot
            m[0x8434] = (m[0x8434] & ~0x08) | (inv3 << 3)
            m[0x8432] = 0x20                 # a shot owned by player 1
            h.call(0x9115, regs={'IX': 0x8430, 'DE': 0x7D65})
            print(f'  {shot:>5}{inv3:>5}   ${h.sim.registers[A]:02X}')


# --------------------------------------------------------------------------
def shotactor():
    """$90E6: a shot lands on an actor.  The tier bits ARE the hit points."""
    print('=== SHOT vs ACTOR  ($90E6, table $7D64)')
    print('  the $9115 lookup:')
    for shot in range(4):
        for inv3 in (0, 1):
            h = fresh()
            m = h.memobj.m
            m[0x8435] = (m[0x8435] & ~3) | shot
            m[0x8434] = (m[0x8434] & ~0x08) | (inv3 << 3)
            m[0x8432] = 0x20
            h.call(0x9115, regs={'IX': 0x8430, 'DE': 0x7D64})
            print(f'   shot power {shot} inv bit3 {inv3} -> t=${h.sim.registers[A]:02X}')
    print('\n  hits to destroy, per class/tier, at each shot power'
          ' (bit7 of the shot flags CLEAR = a player shot):')
    for shot in range(4):
        for cls in (0, 1, 4):
            row = []
            for tier in range(4):
                h = fresh()
                m = h.memobj.m
                m[0x8435] = (m[0x8435] & ~3) | shot
                m[0x8432] = 0x20
                m[0x8433] = 0x20
                st = (cls << 5) | (tier << 3)
                one_actor(h, 40, 40, st)
                n = None
                for k in range(1, 20):
                    m[CTR] = k & 0xFF
                    h.call(0x90E6, regs={'IX': 0x8430, 'HL': LIST + 2})
                    if m[COUNT] == 0:
                        n = k
                        break
                row.append(f'T{tier}:{n}')
            print(f'   power {shot} class {cls}: ' + '  '.join(row))
    print('\n  a MONSTER shot (flags bit 7 set) is a flat $08:')
    for tier in range(4):
        h = fresh()
        m = h.memobj.m
        m[0x8433] = 0x80
        one_actor(h, 40, 40, tier << 3)
        n = None
        for k in range(1, 20):
            h.call(0x90E6, regs={'IX': 0x8430, 'HL': LIST + 2})
            if m[COUNT] == 0:
                n = k
                break
        print(f'   tier {tier}: {n} hits')


# --------------------------------------------------------------------------
def potion():
    """$AF5D: the potion sweep applied to one actor."""
    print('=== POTION  ($A518 arms $84A4/$84A5 from $7D1C; $AF5D applies it)')
    m0 = fresh().memobj.m
    print('  magic inv2  ->  $84A3 $84A4 $84A5   (table $7D1C + 4*i)')
    for magic in range(4):
        for inv2 in (0, 1):
            i = magic + inv2
            a = 0x7D1C + 4 * i
            print(f'  {magic:>5}{inv2:>5}  ->   ${m0[a]:02X}   ${m0[a+1]:02X}'
                  f'    ${m0[a+2]:02X}')
    print('\n  outcome per class/tier for each ($84A4,$84A5) pair the table'
          ' can produce:')
    pairs = sorted({(m0[0x7D1C + 4 * i + 1], m0[0x7D1C + 4 * i + 2])
                    for i in range(5)})
    for p4, p5 in pairs:
        out = []
        for cls in (0, 1, 5):
            for tier in range(4):
                h = fresh()
                m = h.memobj.m
                m[0x84A4], m[0x84A5] = p4, p5
                st = (cls << 5) | (tier << 3)
                one_actor(h, 40, 40, st)
                h.call(0xAF5D, regs={'IX': LIST, 'E': st})
                out.append(f'c{cls}t{tier}:'
                           + ('dead' if m[COUNT] == 0 else f'${m[LIST+2]:02X}'))
        print(f'  $84A4=${p4:02X} $84A5=${p5:02X}: ' + ' '.join(out))
    print('\n  the counters $84A2 (kills) and $84B2 (class-5 kills):')
    h = fresh()
    m = h.memobj.m
    m[0x84A4], m[0x84A5] = 1, 1
    for cls in (0, 1, 5):
        one_actor(h, 40, 40, cls << 5)
        a2, b2 = m[0x84A2], m[0x84B2]
        h.call(0xAF5D, regs={'IX': LIST, 'E': cls << 5})
        print(f'   class {cls}: $84A2 ${a2:02X}->${m[0x84A2]:02X}'
              f'  $84B2 ${b2:02X}->${m[0x84B2]:02X}  count={m[COUNT]}')


# --------------------------------------------------------------------------
def drain():
    """$AF26: class 5 takes 4 health a pass and kills itself at $C8."""
    print('=== CLASS 5 DRAIN  ($AF26, accumulator at $5B60 + slot/2)')
    h = fresh()
    m = h.memobj.m
    px, py = m[P1], m[P1 + 1]
    one_actor(h, px, py, 0xA0)
    for a in range(0x5B60, 0x5BE0):
        m[a] = 0
    # $AF26: L = $60 + (IXL>>1), then INC L for player 1 -- so the drain
    # accumulator is TWO BYTES PER SLOT, odd = player 1, and it aliases
    # every 64 slots because IXL wraps.
    ACC = 0x5B60 + ((LIST & 0xFF) >> 1) + 1
    n = 0
    hp0 = hp(m)
    while n < 80:
        n += 1
        idx, _, _ = h.call(0xAEA0, regs={'IX': LIST, 'C': px, 'B': py,
                                         'E': 0xA0, 'D': 0})
        if n <= 3 or m[COUNT] == 0:
            print(f'   contact {n}: hp {hp0} -> {hp(m)}'
                  f'  acc(${ACC:04X})=${m[ACC]:02X}  count={m[COUNT]}'
                  f'  exit {idx}')
        hp0 = hp(m)
        if m[COUNT] == 0:
            break
    print(f'   destroyed itself after {n} contacts')


# --------------------------------------------------------------------------
def score_sites():
    """Every scoring site, driven, with the BCD arithmetic shown."""
    print('=== SCORE')
    tests = [
        ('$B807 pickup  DE=$0100', 0xB807, {'IX': P1, 'DE': 0x0100}),
        ('$B807 melee   DE=$0025', 0xB807, {'IX': P1, 'DE': 0x0025}),
        ('$B7E9 treasure DE=$0100', 0xB7E9, {'IX': P1, 'DE': 0x0100}),
    ]
    for name, addr, regs in tests:
        h = fresh()
        m = h.memobj.m
        s0 = score(m)
        hp0 = hp(m)
        h.call(addr, regs=regs)
        print(f'  {name}: score {s0} -> {score(m)}   health {hp0} -> {hp(m)}')
    for e in (0x10, 0x05, 0x01, 0x0A):
        h = fresh()
        m = h.memobj.m
        s0 = score(m)
        h.call(0x913C, regs={'IX': 0x8430, 'E': e})
        print(f'  $913C E=${e:02X}: score {s0} -> {score(m)}')
    print('  ...and from 999900, to show the wrap into $84C8/$84C9:')
    h = fresh()
    m = h.memobj.m
    m[P1 + 4], m[P1 + 5], m[P1 + 6] = 0x99, 0x99, 0x00
    m[0x84C8] = m[0x84C9] = 0
    h.call(0xB807, regs={'IX': P1, 'DE': 0x0100})
    print(f'   score -> {score(m)}   $84C8=${m[0x84C8]:02X}'
          f' $84C9=${m[0x84C9]:02X}')


# --------------------------------------------------------------------------
def fire():
    """Drive the running game with FIRE (Z) held and watch a real shot."""
    print('=== FIRE, in the running game (control method $FFFC=$B3 -> $862E:'
          ' 1/Q/S/D + Z)')
    h = fresh()
    m = h.memobj.m
    for k in ('D', 'Z'):
        sel, bit = KM[k]
        h.ports.press(sel, keymask(bit))
    step_to_loop_top(h)
    print('pass  dirbyte  shot(x,y,state,flags)  count  score    hp')
    for i in range(24):
        step_to_loop_top(h)
        print(f'{i+1:>4}    ${m[0x8427]:02X}     '
              f'{m[0x8430]:>4},{m[0x8431]:>4},${m[0x8432]:02X},${m[0x8433]:02X}'
              f'    {m[COUNT]:>3}  {score(m)}  {hp(m)}')


def firehit():
    """Drive the running game, FIRE + right, with something planted in the
    shot's path.  This is the in-situ check on the isolated numbers above."""
    print('=== FIRE, with a target planted in the path')
    print('  The target is pinned in place (its x,y are rewritten after every'
          '\n  pass) so that it cannot reach the player and the ONLY thing that'
          '\n  can touch it is the shot.  health is printed to prove that: a'
          '\n  class-0 CONTACT would cost 10, a shot costs nothing.')
    print('  target                 score      health       state trace')
    for label, cls, tier in [('actor class 0 tier 0', 0, 0),
                             ('actor class 0 tier 2', 0, 2),
                             ('actor class 1 tier 0', 1, 0),
                             ('actor class 1 tier 3', 1, 3),
                             ('actor class 4 tier 0', 4, 0),
                             ('actor class 5 tier 0', 5, 0)]:
        h = fresh()
        m = h.memobj.m
        px, py = m[P1], m[P1 + 1]
        tx, ty = px + 20, py
        one_actor(h, tx, ty, (cls << 5) | (tier << 3))
        for k in ('D', 'Z'):
            sel, bit = KM[k]
            h.ports.press(sel, keymask(bit))
        step_to_loop_top(h)
        s0, h0 = score(m), hp(m)
        trace = []
        for i in range(16):
            step_to_loop_top(h)
            if m[COUNT] == 0:
                trace.append(f'p{i+1}:DEAD')
                break
            m[LIST], m[LIST + 1] = tx, ty        # pin it
            if not trace or trace[-1][-3:] != f'${m[LIST+2]:02X}'[-3:]:
                trace.append(f'p{i+1}:${m[LIST+2]:02X}')
        print(f'  {label:<20}  {s0}->{score(m)}  {h0}->{hp(m)}  '
              + ' '.join(trace[:8]))
    for v in (0x20, 0x22):
        h = fresh()
        m = h.memobj.m
        px, py = m[P1], m[P1 + 1]
        m[COUNT] = 0
        cell = 0x8000 + 0x20 * (py >> 2) + ((px + 12) >> 2)
        m[cell] = v
        for k in ('D', 'Z'):
            sel, bit = KM[k]
            h.ports.press(sel, keymask(bit))
        step_to_loop_top(h)
        s0 = score(m)
        seq = []
        for i in range(16):
            step_to_loop_top(h)
            seq.append(f'${m[cell]:02X}')
            if m[cell] == 0:
                break
        print(f'  generator cell ${v:02X}        {s0}->{score(m)}    -'
              f'     ' + ' '.join(seq))


CELL = 0x8000 + 0x20 * 20 + 20


def plant(h, v):
    """Plant a map value AND the pending-interaction slot that $A65D reads.

    $A65D takes the cell address from (IX+$1E)/(IX+$1F), NOT from HL -- passing
    HL and forgetting the slot makes every consumer write to $0000, which the
    harness discards as a ROM write, and every measurement then reads 'the cell
    was not cleared'."""
    m = h.memobj.m
    m[CELL] = v
    m[0x843E], m[0x843F] = CELL & 0xFF, CELL >> 8
    m[0x843D] = v
    return m


def items():
    """$A65D's eight consumers, priced."""
    print('=== ITEM / INTERACTION SCORES  ($A65D)')
    print('  value  score            health           cell after')
    for v, label in ((0x11, 'door'), (0x13, 'treasure'), (0x14, 'food'),
                     (0x16, 'potion'), (0x18, 'power-up'), (0x19, 'item'),
                     (0x1F, 'key'), (0x2F, 'map sweep'), (0x32, 'hoard')):
        h = fresh()
        m = plant(h, v)
        s0, h0 = score(m), hp(m)
        h.call(0xA65D, regs={'IX': P1, 'A': v})
        print(f'  ${v:02X} {label:<10} {s0}->{score(m)}   {h0}->{hp(m)}'
              f'   ${m[CELL]:02X}')
    print('\n  the thief ($31) -- 99 health only when there is nothing to steal:')
    for inv, label in ((0x00, 'inventory EMPTY'), (0x3F, 'inventory FULL')):
        h = fresh()
        m = plant(h, 0x31)
        m[0x8434] = inv
        s0, h0 = score(m), hp(m)
        h.call(0xA65D, regs={'IX': P1, 'A': 0x31})
        print(f'  {label:<16} {h0}->{hp(m)}  score {s0}->{score(m)}'
              f'  inventory ${inv:02X}->${m[0x8434]:02X}')
    print('\n  a generator meleed through $A65D -- damage but NO SCORE:')
    h = fresh()
    m = plant(h, 0x22)
    m[0x8435] = (m[0x8435] & ~0x0C) | 0x0C
    m[CTR] = 3
    s0 = score(m)
    h.call(0xA65D, regs={'IX': P1, 'A': 0x22})
    print(f'   cell $22 -> ${m[CELL]:02X}   score {s0}->{score(m)}')


def tally():
    """$8BCA: the score a thrown potion pays out when its sweep finishes."""
    print('=== POTION PAY-OUT  ($8BCA)')
    print('  $84A2 (ordinary kills) x 10, plus $84B3 hundreds per class-5 kill')
    for kills, c5, b3 in ((0x01, 0, 0), (0x07, 0, 0), (0x12, 0, 0),
                          (0x00, 1, 0x30), (0x12, 2, 0x50)):
        h = fresh()
        m = h.memobj.m
        m[0x847E] = (m[0x847E] | 0x08) & ~0x01
        m[0x84A6], m[0x84A7] = 0x20, 0x84
        m[0x84A2], m[0x84B2], m[0x84B3] = kills, c5, b3
        s0 = score(m)
        h.call(0x8BCA)
        print(f'   kills=${kills:02X} class5=${c5:02X} $84B3=${b3:02X}'
              f' : score {s0} -> {score(m)}')
    print('  ($84B3 is set at $8D82 when a SHOT hits a class 5: (rand & $70)'
          ' + $10,\n   so it is 1000..8000 a head and ZERO until one is shot.)')


def attrs():
    """$8435 is four 2-bit attributes; $8434 is the six power-up items."""
    print('=== THE ATTRIBUTE BYTE $8435 and the armour install $AB6F')
    print('  bits 1:0 SHOT POWER   ($9115)   bits 3:2 FIGHT POWER ($A964)')
    print('  bits 5:4 MAGIC POWER  ($A544)   bits 7:6 ARMOUR      ($AB7B)')
    print('  $8434 inventory: bit0 ARMOUR+2, bit1 PICKUP POWER, bit2 MAGIC+1,')
    print('                   bit3 SHOT POWER+2, bit4 SHOT SPEED+1,'
          ' bit5 FIGHT POWER+2')
    boot = open(os.path.join(ROOT, 'build', 'image.bin'), 'rb').read()
    print('\n  $BF19, the per-character (shot tag, attributes) pairs'
          ' -- from build/image.bin,\n  because $BE2D relocates over $BF19'
          ' before play:')
    for n, name in enumerate(('WARRIOR', 'VALKYRIE', 'WIZARD', 'ELF')):
        t, at = boot[0xBF19 + 2 * n], boot[0xBF19 + 2 * n + 1]
        print(f'   {n} {name:<9} tag=${t:02X} attr=${at:02X}  ->  shot {at&3}'
              f'  fight {(at>>2)&3}  magic {(at>>4)&3}  armour {at>>6}')
    live = fresh().memobj.m
    print(f'\n  the CAPTURED state has $FFFF=${live[0xFFFF]:02X} (stale), so'
          f' $BEE5 indexed\n  {live[0xFFFF]} entries into $BF19 and left'
          f' $8433=${live[0x8433]:02X} $8435=${live[0x8435]:02X}'
          ' -- GARBAGE, the same\n  boot bug that made the player a speckled'
          ' blob.  The elf is ($18,$64).')
    print('\n  $AB6F installs $7D34 + 8*(armour + 2*inv bit0) at $8437:')
    for att in range(4):
        for inv0 in (0, 1):
            h = fresh()
            m = h.memobj.m
            m[0x8435] = (m[0x8435] & 0x3F) | (att << 6)
            m[0x8434] = (m[0x8434] & ~1) | inv0
            for i in range(6):
                m[0x8437 + i] = 0xEE
            h.call(0xAB6F, regs={'IX': P1})
            print(f'   armour bits {att}, inv bit0 {inv0} -> '
                  + ' '.join('%02X' % m[0x8437 + i] for i in range(6)))


def walkinto():
    """IN SITU: hold right into a pinned monster in the running game, so the
    isolated $A5F0 numbers above are checked against the whole loop."""
    print('=== MELEE, in the running game (hold right into a pinned monster)')
    for cls, tier in ((0, 0), (1, 0), (1, 3), (5, 0)):
        h = fresh()
        m = h.memobj.m
        px, py = m[P1], m[P1 + 1]
        tx, ty = px + 6, py
        one_actor(h, tx, ty, (cls << 5) | (tier << 3))
        sel, bit = KM['D']
        h.ports.press(sel, keymask(bit))
        step_to_loop_top(h)
        s0, h0 = score(m), hp(m)
        rows = []
        for i in range(14):
            step_to_loop_top(h)
            dead = m[COUNT] == 0
            rows.append(f'p{i+1}(${m[CTR]:02X}):'
                        + ('DEAD' if dead else f'${m[LIST+2]:02X}')
                        + f'/{score(m)}')
            if dead:
                break
            m[LIST], m[LIST + 1] = tx, ty
        print(f'  class {cls} tier {tier}: {h0}->{hp(m)} {s0}->{score(m)}')
        print('     ' + ' '.join(rows))


def shotplayer():
    """$9009 -> $908E: a shot lands on a player.  The damage is a CONSTANT,
    chosen by two bits of the shot's own flag byte, and armour does not
    enter into it."""
    print('=== SHOT vs PLAYER  ($9009 / $908E)')
    print('  shot flags   health          delta   $847E bits 4/5')
    for flags in (0x00, 0x20, 0x80, 0xC0, 0x81):
        for extra in (0x00, 0x10, 0x20):
            h = fresh()
            m = h.memobj.m
            px, py = m[P1], m[P1 + 1]
            m[0x847E] = (m[0x847E] & ~0x30) | extra
            m[0x8430], m[0x8431] = px, py
            m[0x8432], m[0x8433] = 0xA0, flags
            hp0 = hp(m)
            h.call(0x9009, regs={'IX': 0x8430, 'C': px, 'B': py})
            print(f'  ${flags:02X}          {hp0} -> {hp(m)}   '
                  f'-{bcd(hp0)-bcd(hp(m)):<4}  $847E=${m[0x847E]:02X}'
                  f'   (IX+$1D)=${m[P1+0x1D]:02X}')


def death():
    """$93CD: what happens when the packed-BCD health reaches exactly 0000."""
    print('=== DEATH  ($93C2 -> $93CD)')
    for keys in (0, 1, 3):
        h = fresh()
        m = h.memobj.m
        m[P1 + 2] = m[P1 + 3] = 0
        m[P1 + 8] = keys
        px, py = m[P1], m[P1 + 1]
        cell = 0x8000 + 0x20 * (py >> 2) + (px >> 2)
        before = m[cell]
        h.call(0x93C2)
        print(f'  keys={keys}: cell (${cell:04X}) ${before:02X} -> ${m[cell]:02X}'
              f'   (IX+$14)=${m[P1+0x14]:02X}  (IX+11)=${m[P1+11]:02X}'
              f'  (IX+14)=${m[P1+14]:02X}  $84C0=${m[0x84C0]:02X}')
    h = fresh()
    m = h.memobj.m
    m[P1 + 2], m[P1 + 3] = 0x00, 0x01
    h.call(0x93C2)
    print(f'  health 0001 (not zero): (IX+$14)=${m[P1+0x14]:02X}  -- no death')


CMDS = {
    'melee': melee, 'contact': contact, 'armour': armour, 'firehit': firehit,
    'shotplayer': shotplayer, 'death': death, 'walkinto': walkinto,
    'items': items, 'tally': tally, 'attrs': attrs,
    'genmelee': genmelee, 'genshot': genshot, 'shotactor': shotactor,
    'potion': potion, 'drain': drain, 'score': score_sites, 'fire': fire,
}


def main():
    args = sys.argv[1:] or ['all']
    names = list(CMDS) if args[0] == 'all' else args
    for n in names:
        CMDS[n]()
        print()


if __name__ == '__main__':
    main()
