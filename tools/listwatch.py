#!/usr/bin/env python3
"""
listwatch.py -- the lifecycle of the ACTOR LIST at $5C00.

Every claim in notes/NOTES-actors.md that begins "measured" is one of the modes
below.  Nothing here reads the list's meaning off the artwork; the meaning comes
from the routines that write it, and this tool is what checks the reading.

MODES
    watch  [dir] [passes]   per-pass write census + record-level diff of the list
                            while a direction key is held.  Shows that the list
                            is INCREMENTALLY updated, never rebuilt: passes with
                            no visible actor write nothing at all.
    stats  [passes]         long varied run: writes/pass distribution, the
                            $8494 == $5C00 + 4*$8496 invariant, type/flag census.
    scan                    isolation-calls $A97F to pin the 7x7 box, the
                            FIRST-MATCH rule and the x-wraps/y-does-not quirk.
    cull                    isolation-calls $B0FE: which records are eligible
                            (flags bit 2), the $38/$32 distance thresholds and
                            the "B is decremented twice per removal" quirk.
    build                   isolation-calls $9A8C (level-build append) for a
                            range of map cell values and checks
                            type == ((v & $38) << 2) | ((v & 3) << 3);
                            and $9B0E, the class-$A0-to-the-front partition.
    vis                     samples (dx,dy) from the camera at the instant
                            $A1DA is entered from the actor loop and reports
                            whether the update hook $ABFF was then reached.
                            This is the OFF-SCREEN FREEZE: dy >= 40 is never
                            updated.

ADDRESSES (all read from build/live_cs.bin, i.e. after self-relocation)
    $5C00   base of the list, 4-byte records: +0 x, +1 y, +2 type, +3 flags
    $8494   END pointer, one past the last record (16-bit)
    $8496   live record count (IY+$17, IY = $847F); cap $C0 = 192 at $AA0E
    $9A8C   level-build append           $9B0E/$9B1B  class-$A0 partition
    $AA26.. generator spawn append       $A9C2  the generator sweep ($853D)
    $AB94   the update+draw pass ($851E); $ABFF is the per-actor update, hooked
            into the draw routine by patching $A21E to CALL $ABFF
    $A97F   the 7x7 actor scan           $B0FE  the off-screen cull ($853A)
    $A641   death compaction (player melee)     $AE7E  self-removal / potion
    $B135   cull compaction
"""
import collections
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import Harness, PC, T, IFF, A, B, C, D, E, H, L, F, IXh, IXl, TAPE_CALL_PC  # noqa: E402
from filmstrip import run_frames                                                          # noqa: E402
from keyprobe import KEYS, keymask                                                        # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
LIST, END, COUNT = 0x5C00, 0x8494, 0x8496
FRAME_T = 69888

WRITERS = {
    0x9AB5: 'level build: count++',
    0xAA3D: 'generator spawn: x',      0xAA3F: 'generator spawn: y',
    0xAA44: 'generator spawn: type',   0xAA46: 'generator spawn: flags=4',
    0xAA49: 'generator spawn: count++', 0xAA4C: 'generator spawn: end ptr',
    0xAC73: 'move: zero own y before the self-scan',
    0xAC7C: 'move: commit x',          0xAC7F: 'move: commit y',
    0xAC82: 'move ok: RES 5 flags',
    0xAC89: 'move blocked: restore y (operand self-patched at $AC70)',
    0xAC9E: 'move blocked: flags |= $20, XOR $10 if already blocked',
    0xACEF: 'flags: animation phase += $40',
    0xACF2: 'type write-back (direction bits only)',
    0xAEEF: 'attack: RES 3 flags',     0xAF06: 'attack: RES 5 flags',
    0xAF15: 'attack: SET 1 flags',
    0xA609: 'player melee: type -= 8',
    0xA648: 'death compaction +3',     0xA64C: 'death compaction +2',
    0xA650: 'death compaction +1',     0xA654: 'death compaction +0',
    0xA655: 'death: end ptr -= 4',     0xA659: 'death: count--',
    0xAE83: 'self-removal +3',         0xAE88: 'self-removal +2',
    0xAE8D: 'self-removal +1',         0xAE92: 'self-removal +0',
    0xAE95: 'self-removal: end ptr',   0xAE98: 'self-removal: count--',
    0xB13B: 'cull compaction',         0xB13F: 'cull compaction',
    0xB143: 'cull compaction',         0xB147: 'cull compaction',
    0xB148: 'cull: end ptr -= 4',      0xB14D: 'cull: count--',
}


def boot():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def install_watch(h, log):
    """Log every write to the list region and to $8494..$8496.  Mem uses
    __slots__, so patch the class rather than the instance."""
    memobj = h.memobj
    orig = memobj.__class__.__setitem__
    sim = memobj.sim

    def setter(self_, i, v):
        if i < 0x4000:
            return
        if LIST <= i < 0x5F00 or END <= i <= COUNT:
            log.append((sim.registers[PC], i, v))
        self_.m[i] = v

    memobj.__class__.__setitem__ = setter
    return lambda: setattr(memobj.__class__, '__setitem__', orig)


def records(m):
    n = m[COUNT]
    return [tuple(m[LIST + 4 * i:LIST + 4 * i + 4]) for i in range(n)]


def fmt(r):
    return '--' if r is None else f'{r[0]},{r[1]},${r[2]:02X},${r[3]:02X}'


# --------------------------------------------------------------------------
def mode_watch(direction='down', npass=32):
    h = boot()
    m = h.memobj.m
    if direction in DIRKEY:
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    log = []
    off = install_watch(h, log)
    prev = records(m)
    print('pass   px  py   cam    cnt  end    wr  writers / record changes')
    for p in range(npass):
        log.clear()
        run_frames(h, 4)
        cur = records(m)
        by = collections.Counter(pc for pc, a, v in log)
        ch = [f'[{i}]{fmt(prev[i] if i < len(prev) else None)}->'
              f'{fmt(cur[i] if i < len(cur) else None)}'
              for i in range(max(len(prev), len(cur)))
              if (prev[i] if i < len(prev) else None) != (cur[i] if i < len(cur) else None)]
        w = ' '.join(f'${pc:04X}x{k}' for pc, k in sorted(by.items()))
        print(f'{p+1:>4}{m[0x8420]:>5}{m[0x8421]:>4}  {m[0x848B]:>3},{m[0x848C]:<3}'
              f'{m[COUNT]:>4} ${m[END+1]*256+m[END]:04X}{len(log):>6}  {w}')
        if ch:
            print(' ' * 39 + '  ' + '  '.join(ch[:6]))
        prev = cur
    off()


def mode_stats(npass=420):
    h = boot()
    m = h.memobj.m
    log = []
    off = install_watch(h, log)
    seq = [('Q', 80), ('D', 60), ('1', 40), ('S', 40), ('Q', 80), ('D', 60), ('Q', 60)]
    wr, per = collections.Counter(), collections.Counter()
    types, flags = collections.Counter(), collections.Counter()
    ok = bad = 0
    counts = []
    done = 0
    for key, n in seq:
        h.ports.release_all()
        sel, bit = KM[key]
        h.ports.press(sel, keymask(bit))
        for _ in range(n):
            if done >= npass:
                break
            log.clear()
            run_frames(h, 4)
            done += 1
            wr.update(pc for pc, a, v in log)
            per[len(log)] += 1
            c = m[COUNT]
            counts.append(c)
            if m[END + 1] * 256 + m[END] == LIST + 4 * c:
                ok += 1
            else:
                bad += 1
            for r in records(m):
                types[r[2]] += 1
                flags[r[3]] += 1
    off()
    tot = sum(per.values())
    print(f'passes {tot}   count min {min(counts)} max {max(counts)} final {counts[-1]}')
    print(f'passes with ZERO list writes: {per[0]} ({100*per[0]/tot:.0f}%)')
    print(f'writes/pass: max {max(per)} mean {sum(k*v for k, v in per.items())/tot:.1f}'
          f'   (a wholesale rebuild would be {4*counts[0]} EVERY pass)')
    print(f'$8494 == $5C00 + 4*$8496 : ok {ok}  broken {bad}')
    print('writer census:')
    for pc, c in sorted(wr.items()):
        print(f'  ${pc:04X} {c:>7}  {WRITERS.get(pc, "?")}')
    print('types:', {f'${k:02X}': v for k, v in sorted(types.items())})
    print('classes (type >> 5):', sorted({k >> 5 for k in types}))
    print('flags:', {f'${k:02X}': v for k, v in sorted(flags.items())})


def _setlist(h, recs, cam=None):
    for i, r in enumerate(recs):
        h.poke(LIST + 4 * i, *r)
    h.poke(COUNT, len(recs))
    e = LIST + 4 * len(recs)
    h.poke(END, e & 0xFF, e >> 8)
    if cam:
        h.poke(0x848B, cam[0])
        h.poke(0x848C, cam[1])


def mode_scan():
    h = boot()
    m = h.memobj.m
    base = h.save_state()

    def probe(cx, cy):
        r = h.sim.registers
        r[C], r[B] = cx, cy
        h.call(0xA97F, interrupts=False)
        return r[F] & 1, r[L] + 256 * r[H]

    h.load_state(base)
    _setlist(h, [(40, 40, 0x10, 0)])
    st = h.save_state()
    print('$A97F, one record at (40,40): candidates that HIT')
    for cy in range(35, 46):
        row = ''
        for cx in range(35, 46):
            h.load_state(st)
            row += '#' if probe(cx, cy)[0] else '.'
        print(f'  cy={cy:3d} {row}      cx 35..45')
    print('  => 7x7, offsets -3..+3 on both axes')
    for order in (0, 1):
        recs = [(40, 40, 0x10, 0), (41, 41, 0xA0, 0)]
        if order:
            recs.reverse()
        h.load_state(base)
        _setlist(h, recs)
        c, hl = probe(40, 40)
        print(f'  order {recs}: carry {c}, matched slot {(hl-LIST)//4}'
              f' type ${m[hl+2]:02X}   <- FIRST match wins, order is significant')
    for tag, rec, cand in (('x wraps', (2, 2, 0x10, 0), (127, 5)),
                           ('y does not wrap', (2, 2, 0x10, 0), (2, 1))):
        h.load_state(base)
        _setlist(h, [rec])
        print(f'  {tag}: record {rec[:2]} candidate {cand} -> hit={probe(*cand)[0]}')


def mode_cull():
    h = boot()
    m = h.memobj.m
    NEAR, FAR = (34, 22), (34, 80)          # camera (2,2) => centre (34,22)
    print('$B0FE, camera (2,2): removes records with flags bit 2 set that are')
    print('  |dx| >= $38 (56) or |dy| >= $32 (50) from (cam_x+$20, cam_y+$14)')
    h.poke(0x8420, 0, 0)
    _setlist(h, [NEAR + (0x10, 0x04), FAR + (0x11, 0x04), NEAR + (0x12, 0x00),
                 FAR + (0x13, 0x00), FAR + (0x14, 0x04), NEAR + (0x15, 0x04)], (2, 2))
    print('  before:', ' '.join(fmt(r) for r in records(m)))
    h.call(0xB0FE, interrupts=False)
    print('  after :', ' '.join(fmt(r) for r in records(m)))
    print('  (slot 1 removed and refilled from the END; slot 4 was far and')
    print('   cullable but escaped -- B is decremented TWICE per removal)')
    _setlist(h, [FAR + (0x10 + i, 0x04) for i in range(8)], (2, 2))
    n0 = m[COUNT]
    h.call(0xB0FE, interrupts=False)
    print(f'  8 far cullable records: one call removed {n0 - m[COUNT]}'
          f' (cap is min(N/2, 5) -- C starts at 5 at $B113)')
    for axis in (0, 1):
        row = ''
        for d in range(0, 68, 2):
            xy = ((34 + d) & 0x7F, 22) if axis == 0 else (34, (22 + d) & 0x7F)
            _setlist(h, [xy + (0x10, 0x04)], (2, 2))
            h.call(0xB0FE, interrupts=False)
            row += '1' if m[COUNT] == 0 else '0'
        print(f'  {"xy"[axis]} offset 0,2,..66 culled? {row}')


def mode_build():
    h = boot()
    m = h.memobj.m
    print('$9A8C level-build append: map cell value -> record')
    stub = 0xFE20
    for v in (0x3F, 0x40, 0x41, 0x42, 0x43, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78):
        _setlist(h, [])
        h.poke(stub, 0xD9, 0x0E, v, 0xD9, 0xCD, 0x8C, 0x9A, 0xC9)   # EXX/LD C,v/EXX/CALL/RET
        r = h.sim.registers
        r[H], r[L] = 0x80, 5 * 32 + 7          # map cursor: row 5, column 7
        r[A] = v
        h.call(stub, interrupts=False)
        exp = (((v & 0x38) << 2) | ((v & 3) << 3)) & 0xFF
        got = records(m)
        print(f'  ${v:02X} -> {"(rejected, < $40)" if not got else fmt(got[0])}'
              f'   predicted type ${exp:02X}'
              + ('  MATCH' if got and got[0][2] == exp else ''))
    print('  world coords = (column*4, row*4); flags = $B575 random AND $C0')
    print()
    print('$9B0E/$9B1B partition: every class-$A0 record moves to the FRONT')
    _setlist(h, [(10, 10, 0x10, 0), (20, 20, 0xA0, 0), (30, 30, 0x00, 0),
                 (40, 40, 0xA3, 0), (50, 50, 0x40, 0), (60, 60, 0xA5, 0)])
    print('  before:', ' '.join(fmt(r) for r in records(m)))
    h.call(0x9B0E, interrupts=False)
    print('  after :', ' '.join(fmt(r) for r in records(m)))


def mode_vis():
    """The update hook $ABFF is inside the DRAW routine ($A21E is patched to
    CALL $ABFF for the duration of the actor pass), so an actor that fails the
    clip test at $A1DA is not updated at all.  Sample (dx,dy) at the instant
    $A1DA is entered and see whether $ABFF follows."""
    h = boot()
    m = h.memobj.m
    sim = h.sim
    regs, ops, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    obs = collections.defaultdict(lambda: [0, 0])
    pending = None
    for key, npass in (('Q', 60), ('D', 70), ('1', 40), ('S', 40), ('Q', 60)):
        h.ports.release_all()
        sel, bit = KM[key]
        h.ports.press(sel, keymask(bit))
        for _ in range(npass):
            tgt = regs[T] + 4 * FRAME_T
            while regs[T] < tgt:
                pc = regs[PC]
                if pc == TAPE_CALL_PC:
                    h._tape()
                    continue
                if mem[pc] == 0x76 and regs[IFF]:
                    h._fast_halt()
                    continue
                if pc == 0xA1DA:
                    if pending is not None:
                        obs[pending][1] += 1
                        pending = None
                    ix = regs[IXl] + 256 * regs[IXh]
                    if LIST <= ix < 0x5F00:
                        dx = (regs[C] - m[0x848B]) & 0x7F
                        dy = (regs[B] - m[0x848C]) & 0x7F
                        pending = (dx - 128 if dx >= 64 else dx,
                                   dy - 128 if dy >= 64 else dy)
                elif pc == 0xABFF and pending is not None:
                    obs[pending][0] += 1
                    pending = None
                ops[mem[pc]]()
                if regs[IFF] and regs[T] % fd < ia:
                    sim.accept_interrupt(regs, mem, pc)
    hits = [k for k, v in obs.items() if v[0]]
    mixed = [k for k, v in obs.items() if v[0] and v[1]]
    print(f'{sum(v[0] for v in obs.values())} updates / {sum(v[1] for v in obs.values())} skips')
    print(f'updated dx {min(k[0] for k in hits)}..{max(k[0] for k in hits)}'
          f'  dy {min(k[1] for k in hits)}..{max(k[1] for k in hits)}')
    print(f'(dx,dy) that were BOTH updated and skipped: {len(mixed)}  <- 0 means a pure gate')
    cols = list(range(-8, 70, 2))
    print('    dx ' + ''.join(str(c % 10) for c in cols))
    for dy in range(-8, 46, 2):
        line = ''
        for dx in cols:
            v = obs.get((dx, dy))
            line += ' ' if not v else ('?' if v[0] and v[1] else ('U' if v[0] else '.'))
        print(f'  dy{dy:4d} {line}')
    print('U = updated, . = frozen.  Read from $A1DA: dx in [-3,64), dy in [-3,40).')


if __name__ == '__main__':
    args = sys.argv[1:] or ['watch']
    mode = args[0]
    if mode == 'watch':
        mode_watch(args[1] if len(args) > 1 else 'down',
                   int(args[2]) if len(args) > 2 else 32)
    elif mode == 'stats':
        mode_stats(int(args[1]) if len(args) > 1 else 420)
    elif mode == 'scan':
        mode_scan()
    elif mode == 'cull':
        mode_cull()
    elif mode == 'build':
        mode_build()
    elif mode == 'vis':
        mode_vis()
    else:
        print(__doc__)
