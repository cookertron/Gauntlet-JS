#!/usr/bin/env python3
"""
exitgate.py -- THE EXIT SEQUENCE, $94E1 and the sprite swap at $9555.

Stepping on a $36 sets (IX+11) bit 6 and (IX+$16) = $18 ($A693).  From then on
$8537's $94AE falls through to $94E1 once a pass and the player walks to the
centre of the exit cell while $9544 INC (IX+13) rotates him.  The part this
port was missing is the tail:

    $954B  DEC (IX+$16) / JR nz,$9555
    $9550  SET 7,(IX+11)              the sequence is over
    $9555  LD A,(IX+$16) / CP 7 / RET nz
    $955B  LD DE,$6218 / JP $9595

$9595 stamps EIGHT pointers -- DE, DE+$21, DE+$42, ... -- into the master
record table at $7C9E (player 1, id $D0) or $7CCE (player 2, id $E8).  $6218
is $5F00 + $318, i.e. RECORD 24 of the player's own 32-record bank, so the
last seven passes draw records 24..31: a SECOND animation, the shrink.
$9691 installs the walk set ($5F00) the same way at level start.

    python tools/exitgate.py          measure and write build/_exit.json
    node tools/headless.js            compares the built engine against it

WHAT IS COMPARED: the whole per-pass table -- position, (IX+11), the countdown,
the rotating slot, and WHICH RECORD INDEX the pointer table names -- so the
swap is pinned by the record the original actually installs, not by this
tool's arithmetic.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, IFF, TAPE_CALL_PC          # noqa: E402
from keyprobe import keymask                                # noqa: E402
from sim_move import KM, DIRKEY, LOOP_TOP                   # noqa: E402
import fixchar                                              # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
BANK1 = 0x5F00
PTAB = 0x7C9E                       # id $D0's two bytes in the $7B00 table


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
            clk[0] = mem[0x8497]           # the drain's own clock, handed over
        ops[mem[pc]]()
        n += 1
        if regs[IFF] and regs[25] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    raise RuntimeError('no main-loop top')


def run(char=0):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    one_pass(h)
    fixchar.fix(m, char, char)
    px, py = m[0x8420], m[0x8421]
    m[0x8000 + (py >> 2) * 32 + ((px >> 2) + 1)] = 0x36     # an exit alongside
    sel, bit = KM[DIRKEY['right']]
    h.ports.press(sel, keymask(bit))
    # $849F is the drain's expected phase and $8497 its frame counter; the
    # tick fires when ($8497 AND $C0) == $849F, so handing over the clock
    # WITHOUT the phase still leaves the tick on a different pass.  p2gate.py
    # seeds both for the same reason.
    seed = {'phase': m[0x849F], 'frame': m[0x8497], 'ctr': m[0x8491],
            'hurry': m[0x84B8], 'f11': m[0x842B],
            'hp': (m[0x8422] << 8) | m[0x8423]}
    rows, clock = [], []
    for _ in range(40):
        clk = [0]
        one_pass(h, clk)
        clock.append(clk[0])
        ptr = m[PTAB] | (m[PTAB + 1] << 8)
        # the pointer table names a RECORD; report its index, not its address,
        # so the comparison does not depend on where the bank happens to sit
        rec = (ptr - BANK1) // 0x21 if (ptr - BANK1) % 0x21 == 0 else -1
        rows.append({'x': m[0x8420], 'y': m[0x8421], 'f11': m[0x842B],
                     'ctr': m[0x8436], 'slot': m[0x842D], 'rec0': rec})
        if m[0x842B] & 0x80:
            break
    return rows, clock, seed


def main():
    char = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    rows, clock, seed = run(char)
    out = os.path.join(ROOT, 'build', '_exit.json')
    # The drain is clocked by $8497, not by passes, and this engine charges a
    # flat four frames a pass where the original's cost 3.92..5.03 -- so
    # $842B bit 0 drifts by a pass over a 24-pass sequence.  Measured and
    # handed over, the same substitution p2gate.py and potiongate.py make:
    # what is under test here is the EXIT, not the clock.
    json.dump({'char': char, 'rows': rows, 'clock': clock, 'seed': seed},
              open(out, 'w'))
    print('pass  x,y     f11  ctr  slot  record set at id $D0')
    for i, r in enumerate(rows, 1):
        print('%4d  %3d,%-3d  %02X   %02X   %02X    %d'
              % (i, r['x'], r['y'], r['f11'], r['ctr'], r['slot'], r['rec0']))
    swap = [i for i, r in enumerate(rows, 1) if r['rec0'] == 24]
    print('\nthe swap to record 24 happens on pass %s, at countdown $%02X'
          % (swap[0] if swap else 'NEVER', rows[swap[0] - 1]['ctr'] if swap else 0))
    print('wrote %s -- %d passes' % (out, len(rows)))


if __name__ == '__main__':
    main()
