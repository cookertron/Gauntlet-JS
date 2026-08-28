#!/usr/bin/env python3
"""
magicgate.py -- A POTION IS ONLY AS STRONG AS THE CHARACTER, $A544 / $AF86.

Reported from play: "using a potion still doesn't attack all visible enemies."
It is not a defect.  $AF86 kills an actor ONLY ON BORROW:

    $AF7C  LD D,(IY+$25) / CP $20 / JR c / LD D,(IY+$26)   two damage rows
    $AF86  AND $18 / SUB D / JR c,$AF6B                    dies only if it borrows
    $AF8B  SUB D                                           else it is knocked DOWN

and the row is chosen by the CHARACTER's magic level -- $A544 takes bits 5:4 of
$8435 and indexes $7D1C with stride 4:

    magic 0  WARRIOR   K=$00 lo=$10 hi=$10
    magic 1  VALKYRIE  K=$01 lo=$10 hi=$10
    magic 2  ELF       K=$02 lo=$18 hi=$10
    magic 3  WIZARD    K=$03 lo=$18 hi=$18

Dungeon 1 is 60 of 63 actors at state $10 -- tier field $10.  For a WARRIOR
$10 - $10 = 0, no borrow, so the monster SURVIVES, knocked to tier 0 and left
standing exactly where it was.  For an ELF $10 - $18 borrows and it dies.

WHY NOTHING IN THIS PROJECT EVER CAUGHT IT.  build/state_48k.pkl holds $8435 =
$20, the loader's stale byte, which is magic 2 -- ELF strength.  Every gate in
tools/ has therefore only ever thrown an elf's potion, at which dungeon 1's
ghosts do die.  Meanwhile CHAR_MENU_DEFAULT is 0: a player who taps through
the shipped menu plays the WARRIOR, whose potion cannot kill them.

AND WHY IT LOOKS LIKE A NO-OP.  $ACF5's sprite id is $40 + 24*class + facing;
the tier bits 4:3 never reach it.  A ghost knocked $10 -> $00 is drawn with
the SAME frame in the SAME ink, so a survivor is indistinguishable on screen
from a monster the potion missed entirely.

    python tools/magicgate.py          capture, write build/_magic.json
    python tools/magicgate.py show     ...and print each character's table

THE TRAP THIS GATE AVOIDS.  $AF72 bumps the kill tally $84A2 BEFORE $AF86
chooses the damage, so the tally counts TOUCHES, not kills -- a warrior is
paid for monsters still standing.  The `kill`, `gen` and `score` columns are
therefore identical whether the potion kills everything or nothing, and a
check built on them passes with the bug reinstated.  Only the LIVE COUNT and
the per-actor tier can see this, and those are what is compared.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, IFF, TAPE_CALL_PC             # noqa: E402
from keyprobe import KEYS, keymask                             # noqa: E402
from sim_move import LOOP_TOP                                  # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
KM = {n: (s, b) for n, s, b in KEYS}
P15, PX, PY, POTS = 0x8435, 0x8420, 0x8421, 0x8429
NACT, AEND, ACTORS = 0x8496, 0x8494, 0x5C00
TALLY, ROWK, ROWLO, ROWHI = 0x84A2, 0x84A3, 0x84A4, 0x84A5
N = 24
CHARS = [('warrior', 0x8E), ('valkyrie', 0xD8), ('wizard', 0x32), ('elf', 0x64)]


def one_pass(h):
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
        ops[mem[pc]]()
        n += 1
        if regs[IFF] and regs[25] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    raise RuntimeError('no main-loop top')


def run(p15):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)
    m[P15] = p15                                   # $8435 -- the character
    m[POTS] = 4
    px, py = m[PX], m[PY]
    # park N tier-$10 ghosts around the player, all on camera
    for k in range(N):
        a = ACTORS + k * 4
        m[a] = (px + ((k % 6) - 3) * 4) & 0x7F
        m[a + 1] = (py + (k // 6 - 2) * 4) & 0x7F
        m[a + 2] = 0x10                            # class 0, tier field $10
        m[a + 3] = 0x00
    m[NACT] = N
    end = ACTORS + 4 * N
    m[AEND], m[AEND + 1] = end & 0xFF, end >> 8
    sel, bit = KM['CAPS']                          # $857E -- player 1's MAGIC
    h.ports.press(sel, keymask(bit))
    one_pass(h)
    live = m[NACT]
    tiers = {}
    for k in range(live):
        t = m[ACTORS + k * 4 + 2] & 0x18
        tiers['$%02X' % t] = tiers.get('$%02X' % t, 0) + 1
    return {'K': m[ROWK], 'lo': m[ROWLO], 'hi': m[ROWHI],
            'tally': m[TALLY], 'live': live, 'tiers': tiers}


def main():
    out = {}
    for name, p15 in CHARS:
        r = run(p15)
        out[name] = dict(r, p15=p15, magic=(p15 >> 4) & 3, planted=N)
        print('%-9s p15=$%02X magic=%d  K=%d lo=$%02X hi=$%02X  tally=$%02X  '
              'live %d -> %d  survivors %s'
              % (name, p15, (p15 >> 4) & 3, r['K'], r['lo'], r['hi'],
                 r['tally'], N, r['live'], r['tiers']))

    # the gate must SEE the difference, and must not rest on the tally
    weak = [k for k in out if out[k]['live'] > 0]
    strong = [k for k in out if out[k]['live'] == 0]
    assert weak and strong, \
        'every character behaved the same -- the planted tier does not ' \
        'discriminate and this gate would pass a potion that did nothing'
    assert len({out[k]['tally'] for k in out}) == 1, \
        'the kill tally differed between characters; $AF72 counts TOUCHES, ' \
        'so if this ever varies the reading of $AF72 is wrong'
    path = os.path.join(ROOT, 'build', '_magic.json')
    json.dump(out, open(path, 'w'))
    print('\nweak (monsters survive): %s' % ', '.join(sorted(weak)))
    print('strong (all die):        %s' % ', '.join(sorted(strong)))
    print('the kill tally is $%02X for ALL of them -- it counts touches'
          % out[weak[0]]['tally'])
    print('wrote', path)


if __name__ == '__main__':
    main()
