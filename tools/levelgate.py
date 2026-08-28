#!/usr/bin/env python3
"""
levelgate.py -- THE LEVEL CLOSURE FILE.

Drives the ORIGINAL Z80 and writes build/levelcheck.json: for every dungeon
that ships, the 32x32 grid the original builds in live RAM at $8000..$83FF,
its player start and its actor list.  `node tools/headless.js` reads that file
and asserts the ENGINE's grid equals it cell for cell.  Nothing in the file
comes from the port; every byte is the original's own output.

  python tools/levelgate.py            write build/levelcheck.json (307 + 24 + 7)
  python tools/levelgate.py --quick    pack 1 only, for a smoke test

THREE SECTIONS, three different things being pinned down
--------------------------------------------------------
"records"  all 307 sub-blocks, expanded by the original's own $97CB with
           IX pointed at the record's TAPE BYTES at $C000.  The two RNG-driven
           passes are switched OFF on the original's side, so the comparison
           is exact rather than statistical:
             * flags bit 2 is cleared in the record copy, which is the game's
               own gate on $9B5F ("keep one random $36") -- $988F BIT 2,(IX+1)
             * $8403 is set to 1, so $989C CP 8 / RET c skips both mirrors and
               $9BB7 returns at its own CP 8
           Everything else -- the vector table, the fixed border, the RLE, the
           wall connectivity, the actor list and the player start -- is the
           full build.

"mirrors"  the level>=8 arms, which the records section deliberately excludes:
           $8403 = 8, $847E bit 6 (bonus) SET so that $9BB7 returns at $9BDD
           before its object loop, the RNG at $B575 replaced with LD A,0 / RET
           so the one draw $9BB7 does make cannot place anything, and $84C7
           forced to each of 0..3 -- the byte $91B5 draws, whose bit 1 runs
           $9C06 and bit 0 runs $9C69.  Six records x 4 orientations.

"levels"   the real thing: $B3D0 for dungeons 1..7, i.e. the tape load, the
           pack's own stub, $91D7's record-list restore, $9175's record
           selection, $97CB and the placement -- and then the globals the
           level start leaves behind ($84A0/$84BD/$84BE/$84BF from $B401..$B428
           and the contact-damage row $AB6F installs at $8437).

VERIFY: python tools/packgate.py all -> 307/307 is the same comparison run the
other way round (against tools/packdecode.py rather than against the engine).
"""
import base64
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, TAPE_CALL_PC              # noqa: E402
import packdecode as PD                                    # noqa: E402
import packbuild                                           # noqa: E402

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
OUT = os.path.join(ROOT, 'build', 'levelcheck.json')
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


def expand_on_z80(h, base, rec, level=1, c7=0, bonus=False, kill_rng=False):
    """$97CB over exactly the bytes that ship, and nothing else."""
    h.load_state(base)
    m = h.memobj.m
    m[BUF:BUF + 0x1000] = bytes(0x1000)
    m[BUF:BUF + len(rec)] = bytes(rec)
    m[0x8403] = level
    m[0x84C7] = c7
    m[0x847E] = 0x40 if bonus else 0x00
    if kill_rng:
        # LD A,0 / RET over $B575.  The state is reloaded from the pickle for
        # every record, so nothing is patched for longer than one build.
        m[0xB575], m[0xB576], m[0xB577] = 0x3E, 0x00, 0xC9
    r = h.regs
    r[8], r[9] = BUF >> 8, BUF & 0xFF                      # IX = the record
    sp = r[12]
    for s in reversed(h.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        m[sp] = s & 0xFF
        m[sp + 1] = s >> 8
    r[12] = sp
    r[PC] = EXPANDER
    reason, steps = run_nb(h, set(h.SENTINELS))
    assert reason == 'target', reason
    nact = m[0x8496]
    return dict(
        grid=base64.b64encode(bytes(m[0x8000:0x8400])).decode('ascii'),
        player=[m[0x8492], m[0x8493]],
        actors=[list(m[0x5C00 + 4 * i:0x5C00 + 4 * i + 3]) for i in range(nact)],
    )


def pack_records(n):
    """Every sub-block of pack n as (sub, bytes), from the pack's own table."""
    pack = PD.load_pack(n)
    lens = PD.sub_lengths(pack)
    off = PD.HDR
    out = []
    for s, ln in enumerate(lens):
        if ln:
            out.append((s, bytes(pack[off:off + ln])))
        off += ln
    return out


def main():
    quick = '--quick' in sys.argv
    h = Harness()
    base = pickle.load(open(STATE, 'rb'))

    recs = []
    for n in range(1, 2 if quick else 32):
        for s, body in pack_records(n):
            rec = bytearray(body)
            rec[1] &= ~0x04                    # $988F BIT 2,(IX+1) -> no $9B5F
            r = expand_on_z80(h, base, rec, level=1)
            r.update(pack=n, sub=s)
            recs.append(r)
        print('pack %2d: %d records' % (n, len(pack_records(n))))

    # ---- the level >= 8 mirrors ----------------------------------------
    mirrors = []
    if not quick:
        for n, s in ((16, 5), (13, 3), (3, 0), (7, 0), (25, 8), (31, 9)):
            body = dict(pack_records(n))[s]
            rec = bytearray(body)
            rec[1] &= ~0x04
            for c7 in range(4):
                r = expand_on_z80(h, base, rec, level=8, c7=c7, bonus=True,
                                  kill_rng=True)
                r.update(pack=n, sub=s, c7=c7)
                mirrors.append(r)
        print('mirrors: %d orientations' % len(mirrors))

    # ---- the whole $B3D0 path, dungeons 1..7 ---------------------------
    levels = []
    if not quick:
        h2 = Harness()
        h2.load_state(pickle.load(open(STATE, 'rb')))
        first = True
        for level in range(1, 8):
            packbuild.build(h2, level, rewind=first, watch=False)
            first = False
            m = h2.memobj.m
            nact = m[0x8496]
            levels.append(dict(
                level=level,
                grid=base64.b64encode(bytes(m[0x8000:0x8400])).decode('ascii'),
                start=[m[0x8492], m[0x8493]],       # the record's player start
                px=m[0x8420], py=m[0x8421],         # where $9689 actually put him
                actors=[list(m[0x5C00 + 4 * i:0x5C00 + 4 * i + 3])
                        for i in range(nact)],
                spawn_base=m[0x84A0],               # $B401  50 + level/2
                obj_prob=m[0x84BD],                 # $B41F  180 + level
                obj_count=m[0x84BE],                # $B40B  5 + level/12
                obj_type=m[0x84BF],                 # $B415  25 + level/2
                damage=list(m[0x8437:0x843D]),      # $AB6F's armour row
                p12=m[0x842C], p13=m[0x842D], p14=m[0x842E],
                f11=m[0x842B],
            ))
            print('level %d built: player (%d,%d), start (%d,%d), %d actors' %
                  (level, m[0x8420], m[0x8421], m[0x8492], m[0x8493], nact))

    data = dict(records=recs, mirrors=mirrors, levels=levels)
    json.dump(data, open(OUT, 'w'), separators=(',', ':'))
    print('wrote %s: %d records, %d mirrors, %d levels' %
          (OUT, len(recs), len(mirrors), len(levels)))


if __name__ == '__main__':
    main()
