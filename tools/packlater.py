#!/usr/bin/env python3
"""
packlater.py -- drive the ORIGINAL through the $C0 (level >= 8) pack path and
check which sub-block each level actually gets.

The pack's own stub at $C019 draws FOUR distinct indices with $C090 --
call them d1..d4 -- stores d4,d3,d2 at $C0CD..$C0CF and concatenates those
three sub-blocks at $4000 before copying them to $6F80.  d1 is used only to
point IX at a sub-block of the RAW pack still sitting at $C000, and that is
the level the game builds on the very pass the pack is loaded.

So one $C0 pack yields FOUR levels: d1 (immediately), then d4, d3, d2 as the
$84CC mask (initialised to 7 by $C078) is consumed a bit at a time.

The map is read at $9896 -- after the RLE and $9B5F, before the level >= 8
extras ($9BB7's random objects and $9C06/$9C69's mirrors), which are RNG-driven
and cannot be reproduced byte for byte.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, TAPE_CALL_PC   # noqa: E402
import packdecode as PD                         # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')


def run_nb(h, stop, limit=8_000_000):
    sim = h.sim
    regs = sim.registers
    opcodes = sim.opcodes
    mem = sim.memory
    n = 0
    while n < limit:
        pc = regs[PC]
        if pc in stop:
            return ('target', n)
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        if mem[pc] == 0x76:
            regs[PC] = (pc + 1) & 0xFFFF
            n += 1
            continue
        opcodes[mem[pc]]()
        n += 1
    return ('limit', n)


def one_level(h, level, load, deckpos=None):
    """Run $B3D0 for `level`.  Stops at $9896, i.e. with the deterministic map
    built.  Returns (grid, player, actors, chosen indices or None)."""
    m = h.memobj.m
    if deckpos is not None:
        h.deck.pos = deckpos
    m[0x8403] = level
    m[0x84CD] = 0xC0
    if load:
        m[0x84CC] = 0x00                 # mask empty -> $91BD loads a pack
    r = h.regs
    sp = r[12]
    for s in reversed(h.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        m[sp] = s & 0xFF
        m[sp + 1] = s >> 8
    r[12] = sp
    r[PC] = 0xB3D0
    chosen = None
    mask_before = m[0x84CC] & 0x7F
    if load:
        run_nb(h, {0xC04E})
        chosen = (m[0xC0CD], m[0xC0CE], m[0xC0CF], m[0xC071])
    # $9896 CALL $9BB7 sprinkles RANDOM extra objects on a level >= 8; step
    # over it so that what is left is the deterministic build, then let
    # $9899 CALL $9B0E (the actor partition) run and stop before the
    # $9C06/$9C69 mirrors.
    run_nb(h, {0x9896})
    h.regs[PC] = 0x9899
    run_nb(h, {0x989C})
    grid = bytes(m[0x8000:0x8400])
    nact = m[0x8496]
    actors = [tuple(m[0x5C00 + 4 * i:0x5C00 + 4 * i + 3]) for i in range(nact)]
    player = (m[0x8492], m[0x8493])
    run_nb(h, set(h.SENTINELS))
    used = mask_before ^ (m[0x84CC] & 0x7F)
    rec = {1: 0, 2: 1, 4: 2}.get(used)
    return grid, player, actors, chosen, m[0x84CC], rec


def check(name, grid, player, actors, pack_n, sub):
    pack = PD.load_pack(pack_n)
    lens = PD.sub_lengths(pack)
    starts = [PD.HDR]
    for x in lens:
        starts.append(starts[-1] + x)
    body = pack[starts[sub]:]
    buf = bytearray(PD.FIRST_COPY + PD.SECOND_COPY)
    k = min(len(body), len(buf))
    buf[:k] = body[:k]
    mp, info = PD.expand(buf, 0)
    same = sum(1 for i in range(1024) if grid[i] == mp.cell[i])
    diff36 = all(grid[i] == 0 and mp.cell[i] == 0x36
                 for i in range(1024) if grid[i] != mp.cell[i])
    tail = ''
    if same != 1024:
        tail = ' (%d diffs, all $36->$00: %s)' % (1024 - same, diff36)
    print('  %-28s vs sub %d: %d/1024%s  player %s/%s  actors %d/%d  %s' %
          (name, sub, same, tail, player, mp.player, len(actors),
           len(mp.actors),
           'OK' if (same == 1024 or diff36) and player == mp.player and
           [a[:3] for a in actors] == [a[:3] for a in mp.actors] else 'MISMATCH'))
    return (same == 1024 or diff36)


def main():
    packs = [int(a) for a in sys.argv[1:] if not a.startswith('-')] or [2, 3, 4]
    h = Harness()
    base = pickle.load(open(STATE, 'rb'))
    good = tot = 0
    for pn in packs:
        h.load_state(base)
        # the deck serves side-2 blocks in order; block index pn-1 is pack pn
        r = one_level(h, 8, True, deckpos=pn - 1)
        grid, player, actors, chosen, mask, _ = r
        recs = chosen[:3]                     # $C0CD, $C0CE, $C0CF
        d1 = chosen[3]
        print('pack %d: stub -> records 0,1,2 = sub-blocks %s;  IX on the '
              'loading pass = sub-block %d;  $84CC=$%02X'
              % (pn, list(recs), d1, mask))
        tot += 1
        good += check('level 8 (the loading pass)', grid, player, actors, pn, d1)
        for lvl in (9, 10, 11):
            grid, player, actors, _, mask, rec = one_level(h, lvl, False)
            tot += 1
            good += check('level %d -> record %s' % (lvl, rec),
                          grid, player, actors, pn, recs[rec])
    print('\n%d/%d levels reproduced from the tape bytes' % (good, tot))


if __name__ == '__main__':
    main()
