#!/usr/bin/env python3
"""
actors.py -- dump the ACTOR TABLE in a readable form, one row per actor.

THE TABLE, recovered by disassembly and confirmed by measurement.  The update
rule itself lives in tools/actormodel.py, which differential-tests against the
real Z80 (13,447/13,447 records exact over five drives).

    base    $5C00
    stride  4 bytes                 <-- the whole actor state is 4 bytes.
    count   ($8496) == (IY+$17)     0..192
    tail    ($8494)                 == $5C00 + 4*count, ALWAYS.  Appending
                                    writes at the tail; removal is a
                                    SWAP-REMOVE from the tail ($A641/$AE7E).
    max     192, gated at $AA0E  CP $C0  --  192*4 = 768 = $5C00..$5EFF,
                                which is exactly the gap below the sprite
                                bank at $5F00.

    +0  x       world x, 7-bit (0..127).  4 units = one map cell = 16 px.
    +1  y       world y, 7-bit.
    +2  state   bits 7:5  class   (0..7)   selects the 24-record sprite set
                bits 4:3  tier    (0..3)   hit points / pickup tier
                bits 2:0  facing  (0..7)   index into the $8418 mask table
    +3  flags   bits 7:6  animation phase counter, decoded 0,1,0,2
                bit 5     "was blocked / re-choose direction"
                bit 4     blocked-direction memory (toggled with bit 5)
                bit 3     INVISIBLE -- $AD15 pops the return address, so the
                          actor is updated but never drawn
                bit 2     generator-spawned (the $B10E area-kill needs it)
                bits 1:0  a small per-actor countdown

    sprite id = 64 + 24*class + facing + 8*phase           ($ACF5..$AD13)
                ids 64..255 = 8 classes x 8 facings x 3 phases, and classes 6
                and 7 (bases 208, 232) ARE the two players' character sets.

WHO WRITES THE TABLE
    $9A8C   level build.  Every map cell holding a value >= $40 becomes an
            actor and the cell is zeroed:  class = (v>>3)&7, tier = v&3,
            facing = 0, flags = random & $C0.  ("values >= $40 left in the
            live map: []" -- none survive.)
    $AA3A   a GENERATOR spawns.  Map values $20..$2E are generators ($A9E4);
            they pick a random free neighbour cell and append
            (x, y, class|tier from $7CFE + facing, $04).  Capped at 192
            actors ($AA0E CP $C0) with the rate falling as the population
            grows ($AA12: threshold = (IY+$21) - count/8).
    $AC73.. the update commits a move.
    $A641   the player walked into one: removed.
    $AE7E   it walked into the player: removed.
    $B14D   an area effect removed it (needs flags bit 2).
    All three removals are the SAME SWAP-REMOVE: copy the record at
    ($8494)-4 over the dead one, back the tail up 4, DEC the count.  $ABE8
    then skips the IX advance so the swapped-in record is not missed.

AUXILIARY, not part of the record
    $5B60..$5B8F   48 bytes = 2 per actor for the first 24 slots: the class-5
                   ($A0) drain counter, one per player ($AF26 adds 4 per
                   contact, removes the actor at 200).  Cleared by $9B0E,
                   which also compacts every class-5 actor to the head of the
                   list -- which is what makes the 24-slot indexing safe.
    $5B90..$5BCF   8 slots x 8 bytes, a deferred-spawn ring keyed on
                   pass & 7; filled with $FF each pass by $AB94 and drained
                   by $8FC5 into the object queue at $5B20.

Usage:
    python tools/actors.py                       # the first dungeon, as saved
    python tools/actors.py --passes 30 --key Q   # after 30 passes holding down
    python tools/actors.py --live build/live_cs.bin      # from a memory dump
    python tools/actors.py --passes 40 --key Q --trace   # per-pass deltas
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

ACTORS = 0x5C00
TAIL = 0x8494              # (IY+$15)  16-bit, next free record
COUNT = 0x8496             # (IY+$17)  8-bit
MAXACTORS = 192            # $AA0E  CP $C0
CAM_X, CAM_Y = 0x848B, 0x848C
PASS = 0x8491              # (IY+$12)
P1, P2 = 0x8420, 0x8440

# $8418..$841F, read at $ADF8/$AA5E: bit0 up, bit1 down, bit2 left, bit3 right
DIRMASK = 0x8418
DIRNAME = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def maskname(m):
    s = ''
    if m & 1:
        s += 'U'
    if m & 2:
        s += 'D'
    if m & 4:
        s += 'L'
    if m & 8:
        s += 'R'
    return s or '-'


def phase(f):
    """$AD03: bit6 adds 8, bit7 adds a further 8 -- so 00,40,80,C0 -> 0,1,2,2?
    No: bit6 alone -> +8, bit6+bit7 -> +16, bit7 alone -> +0.  The counter runs
    $00 $40 $80 $C0, which decodes 0,1,0,2 -- the same three-frame walk the
    player uses at $A49F/$A4A7."""
    b6, b7 = (f >> 6) & 1, (f >> 7) & 1
    return 0 if not b6 else (2 if b7 else 1)


def sprite_id(state, flags):
    return 64 + 24 * (state >> 5) + (state & 7) + 8 * phase(flags)


def read_table(mem):
    n = mem[COUNT]
    tail = mem[TAIL] | (mem[TAIL + 1] << 8)
    rows = []
    for k in range(n):
        a = ACTORS + 4 * k
        x, y, st, fl = mem[a], mem[a + 1], mem[a + 2], mem[a + 3]
        rows.append(dict(slot=k, addr=a, x=x, y=y, state=st, flags=fl,
                         cls=st >> 5, tier=(st >> 3) & 3, face=st & 7,
                         mask=mem[DIRMASK + (st & 7)],
                         phase=phase(fl), sid=sprite_id(st, fl),
                         blocked=(fl >> 5) & 1, bmem=(fl >> 4) & 1,
                         invis=(fl >> 3) & 1, gen=(fl >> 2) & 1,
                         cdown=fl & 3,
                         col=(x >> 2) & 31, row=(y >> 2) & 31))
    return n, tail, rows


def onscreen(r, cx, cy):
    """$A1DA's cull, read to its RET nc's:

        A = (x+3-cam_x) & $7F;  CP $44 / RET nc          -> A <= $43
            A < 3 ? clip left : SUB $43 / RET nc          -> A <= $42
        A = (y+3-cam_y) & $7F;  CP $44 / RET nc
            A < 3 ? clip top  : SUB $2B / RET nc          -> A <= $2A

    An actor outside this window is neither drawn NOR UPDATED, because the
    update ($ABFF) is a patched CALL inside the draw routine.  That is why
    nothing in the first dungeon moves until the camera brings it into view."""
    dx = (r['x'] + 3 - cx) & 0x7F
    dy = (r['y'] + 3 - cy) & 0x7F
    return dx < 0x43 and dy < 0x2B


def dump(mem, out=sys.stdout, limit=None):
    n, tail, rows = read_table(mem)
    cx, cy = mem[CAM_X], mem[CAM_Y]
    exp = ACTORS + 4 * n
    print(f'actor table  base ${ACTORS:04X}  stride 4  count ${COUNT:04X}={n}'
          f'  tail ${TAIL:04X}=${tail:04X}'
          f'  (expected ${exp:04X} {"OK" if tail == exp else "MISMATCH"})'
          f'  max {MAXACTORS}', file=out)
    print(f'camera ({cx},{cy})   pass ${mem[PASS]:02X}   '
          f'p1 ({mem[P1]},{mem[P1+1]})  p2 ({mem[P2]},{mem[P2+1]})', file=out)
    print(file=out)
    print('slot  addr   x   y  cell   raw       cls tier face mask  ph '
          'sid  flags     vis gen blk cd  seen', file=out)
    for r in rows[:limit]:
        raw = f'{r["x"]:02X} {r["y"]:02X} {r["state"]:02X} {r["flags"]:02X}'
        print(f'{r["slot"]:>4} ${r["addr"]:04X} {r["x"]:>3} {r["y"]:>3} '
              f'{r["col"]:>2},{r["row"]:<2} {raw}  '
              f'{r["cls"]:>3} {r["tier"]:>4} {DIRNAME[r["face"]]:>4} '
              f'{maskname(r["mask"]):>4} {r["phase"]:>3} {r["sid"]:>4} '
              f'{r["flags"]:08b} '
              f'{"-" if r["invis"] else "Y":>3} {r["gen"]:>3} '
              f'{r["blocked"]:>3} {r["cdown"]:>2}  '
              f'{"*" if onscreen(r, cx, cy) else " "}', file=out)
    if limit and n > limit:
        print(f'... {n - limit} more', file=out)
    print(file=out)
    cls = {}
    for r in rows:
        cls[(r['cls'], r['tier'])] = cls.get((r['cls'], r['tier']), 0) + 1
    print('census by (class, tier): ' +
          '  '.join(f'{c}/{t}:{k}' for (c, t), k in sorted(cls.items())),
          file=out)
    vis = sum(1 for r in rows if onscreen(r, cx, cy))
    print(f'on-screen (and therefore UPDATED this pass): {vis} of {n}', file=out)
    return rows


def main():
    args = sys.argv[1:]
    state = os.path.join(ROOT, 'build', 'state_charsel.pkl')
    live = None
    passes = 0
    poke = None
    key = None
    trace = False
    limit = None
    while args:
        if args[0] == '--state':
            state = args[1]; del args[:2]
        elif args[0] == '--live':
            live = args[1]; del args[:2]
        elif args[0] == '--passes':
            passes = int(args[1]); del args[:2]
        elif args[0] == '--key':
            key = args[1]; del args[:2]
        elif args[0] == '--limit':
            limit = int(args[1]); del args[:2]
        elif args[0] == '--poke':
            poke = tuple(int(v) for v in args[1].split(',')); del args[:2]
        elif args[0] == '--trace':
            trace = True; del args[:1]
        else:
            del args[:1]

    if live:
        dump(bytearray(open(live, 'rb').read()), limit=limit)
        return

    from harness import Harness                                # noqa: E402
    from filmstrip import run_frames                            # noqa: E402
    from keyprobe import KEYS, keymask                          # noqa: E402
    km = {n: (s, b) for n, s, b in KEYS}

    h = Harness()
    h.load_state(pickle.load(open(state, 'rb')))
    if poke:
        h.memobj.m[P1], h.memobj.m[P1 + 1] = poke
    if key:
        sel, bit = km[key.upper()]
        h.ports.press(sel, keymask(bit))
    m = h.memobj.m
    prev = None
    for i in range(passes):
        before = bytes(m[ACTORS:ACTORS + 4 * MAXACTORS]), m[COUNT]
        run_frames(h, 4)
        if trace:
            after = bytes(m[ACTORS:ACTORS + 4 * MAXACTORS]), m[COUNT]
            ch = [k for k in range(4 * MAXACTORS) if before[0][k] != after[0][k]]
            if ch or before[1] != after[1]:
                slots = sorted({k // 4 for k in ch})
                print(f'pass {i+1:>3}  count {before[1]}->{after[1]}  '
                      f'slots touched {slots}')
        prev = before
    del prev
    dump(m, limit=limit)


if __name__ == '__main__':
    main()
