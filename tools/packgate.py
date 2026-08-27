#!/usr/bin/env python3
"""
packgate.py -- THE CLOSURE TEST for the dungeon-pack format.

For a given pack and sub-block it

  * pokes the record's TAPE bytes into $C000 (plus the bytes that follow it,
    because $9A5C looks one byte past the entry it is deciding about),
  * points IX at them and calls the original's expander $97CB on the real Z80,
  * decodes the same tape bytes with tools/packdecode.py,
  * compares all 1024 map cells, the player start and the actor list.

Usage
    python tools/packgate.py 1              pack 1, every record
    python tools/packgate.py all            every sub-block of every pack
    python tools/packgate.py 3 --sub 5      one sub-block
    python tools/packgate.py --end2end      the full $B3D0 build, levels 1-7
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, TAPE_CALL_PC          # noqa: E402
import packdecode as PD                                # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')   # the 48K baseline
EXPANDER = 0x97CB
BUF = 0xC000


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


def call_expander(h, base_state, buf, off):
    """Run $97CB over `buf` (a bytes-like record list) with IX at buf+off."""
    h.load_state(base_state)
    m = h.memobj.m
    m[BUF:BUF + 0x1000] = bytes(0x1000)
    n = min(len(buf), 0x1000)
    m[BUF:BUF + n] = bytes(buf[:n])
    m[0x8403] = 1                     # level < 8: skip the $9BB7/$9C06 extras
    r = h.regs
    r[8], r[9] = (BUF + off) >> 8, (BUF + off) & 0xFF      # IX
    sp = r[12]
    for s in reversed(h.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        m[sp] = s & 0xFF
        m[sp + 1] = s >> 8
    r[12] = sp
    r[PC] = EXPANDER
    reason, steps = run_nb(h, set(h.SENTINELS))
    grid = bytes(m[0x8000:0x8400])
    nact = m[0x8496]
    actors = [tuple(m[0x5C00 + 4 * i:0x5C00 + 4 * i + 3]) for i in range(nact)]
    player = (m[0x8492], m[0x8493])
    return reason, grid, player, actors


def compare(pack_n, sub, h, base_state, verbose=False):
    pack = PD.load_pack(pack_n)
    lens = PD.sub_lengths(pack)
    starts = [PD.HDR]
    for x in lens:
        starts.append(starts[-1] + x)
    if sub >= len(lens) or lens[sub] == 0:
        return None
    # The record list the game assembles always has the chosen sub-block at
    # its head; what follows it in memory is the NEXT chosen sub-block.  Here
    # the sub-block is followed by whatever lies after it on the tape, which is
    # what the game gets when the draws happen to be consecutive.  Only the
    # ONE byte past the table matters, and we supply the real one.
    body = pack[starts[sub]:]
    buf = bytearray(PD.FIRST_COPY + PD.SECOND_COPY)
    k = min(len(body), len(buf))
    buf[:k] = body[:k]

    # The deterministic gate: $9B5F's "keep one random $36" pass is the ONE
    # part of the build that is a coin toss, so it is switched off on BOTH
    # sides (flags bit 2) and checked separately below.
    rnd36 = bool(buf[1] & 0x04)
    buf[1] &= ~0x04

    reason, grid, player, actors = call_expander(h, base_state, buf, 0)
    mp, info = PD.expand(buf, 0)
    same = sum(1 for i in range(1024) if grid[i] == mp.cell[i])
    bad = [(i, grid[i], mp.cell[i]) for i in range(1024) if grid[i] != mp.cell[i]]
    ok_p = (player == (mp.player or (0, 0)))
    ok_a = ([a[:3] for a in actors] == [a[:3] for a in mp.actors])
    tagtxt = 'pack %2d sub %d  len %3d flags $%02X hdr %3d rle %3d' % (
        pack_n, sub, info['rec_len'], info['flags'] | (4 if rnd36 else 0),
        info['hdrlen'], info['rle_bytes'])
    status = '%d/1024 cells' % same
    ok36 = True
    note = ''
    if rnd36:
        # switch it back on and check the survivor is one of ours
        buf[1] |= 0x04
        _, g2, _, _ = call_expander(h, base_state, buf, 0)
        kept = [i for i in range(1024) if g2[i] == 0x36]
        cand = mp.thirtysix
        rest = [i for i in range(1024) if g2[i] != mp.cell[i]]
        ok36 = (len(kept) == 1 and kept[0] in cand and
                set(rest) == set(cand) - set(kept))
        note = '  [$9B5F: 1 of %d $36 kept, %s]' % (
            len(cand), 'in candidates' if ok36 else 'NOT IN CANDIDATES')
    if same == 1024 and ok_p and ok_a and ok36:
        print('%s  %s  player %s  %d actors  OK%s' %
              (tagtxt, status, player, len(actors), note))
        return True
    print('%s  %s  playerOK=%s actorsOK=%s (%d vs %d)%s' %
          (tagtxt, status, ok_p, ok_a, len(actors), len(mp.actors), note))
    if verbose:
        for i, a, b in bad[:24]:
            print('    cell (%2d,%2d)  original $%02X  decoded $%02X' %
                  (i // 32, i % 32, a, b))
        if not ok_p:
            print('    player original %s  decoded %s' % (player, mp.player))
        if not ok_a:
            for i in range(max(len(actors), len(mp.actors))):
                x = actors[i] if i < len(actors) else None
                y = mp.actors[i][:3] if i < len(mp.actors) else None
                if x != y:
                    print('    actor %d: original %s decoded %s' % (i, x, y))
                    break
    return False


def end2end():
    """The real thing: $B3D0 for levels 1..7, against the tape-only decode."""
    import packbuild
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    pack = PD.load_pack(1)
    buf = PD.record_list(pack, None)
    offs = PD.record_offsets(buf, 8)
    print('pack 1 record offsets:', ['$%03X' % o for o in offs])
    first = True
    for level in range(1, 8):
        packbuild.build(h, level, rewind=first, watch=False)
        first = False
        m = h.memobj.m
        grid = bytes(m[0x8000:0x8400])
        mp, info = PD.expand(buf, offs[level - 1])
        same = sum(1 for i in range(1024) if grid[i] == mp.cell[i])
        player = (m[0x8492], m[0x8493])
        nact = m[0x8496]
        acts = [tuple(m[0x5C00 + 4 * i:0x5C00 + 4 * i + 3]) for i in range(nact)]
        print('level %d -> record %d ($%03X): %d/1024 cells identical, '
              'player %s vs %s, actors %d vs %d %s' %
              (level, level - 1, offs[level - 1], same, player, mp.player,
               len(acts), len(mp.actors),
               'OK' if (same == 1024 and player == mp.player and
                        [a[:3] for a in acts] == [a[:3] for a in mp.actors])
               else 'MISMATCH'))


def main():
    args = sys.argv[1:]
    if '--end2end' in args:
        end2end()
        return
    verbose = '--verbose' in args or '-v' in args
    pos = [a for a in args if not a.startswith('-')]
    h = Harness()
    base = pickle.load(open(STATE, 'rb'))
    if pos and pos[0] == 'all':
        tot = ok = 0
        for n in range(1, 32):
            for s in range(10):
                r = compare(n, s, h, base, verbose)
                if r is None:
                    continue
                tot += 1
                ok += 1 if r else 0
        print('\n%d/%d sub-blocks identical' % (ok, tot))
        return
    n = int(pos[0]) if pos else 1
    if '--sub' in args:
        subs = [int(args[args.index('--sub') + 1])]
    else:
        subs = range(10)
    tot = ok = 0
    for s in subs:
        r = compare(n, s, h, base, verbose)
        if r is None:
            continue
        tot += 1
        ok += 1 if r else 0
    print('%d/%d sub-blocks identical' % (ok, tot))


if __name__ == '__main__':
    main()
