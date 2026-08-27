#!/usr/bin/env python3
"""
shotgate.py -- drive the REAL Z80 and print every number about SHOTS.

Never loads the engine; everything here is the original answering.  Follows
tools/actorgate.py and tools/doorgate.py.  The per-pass sampler is the main
loop top $8503 (NEVER a frame count -- see tools/sim_move.py's docstring).

Subcommands
    firekey            press each of the 40 keys; report $8427 and the shot
    steps  <keys> <n>  hook $8D2B, the per-STEP commit; histogram steps/pass
    dirs               fire in all 8 compass slots from an open arena
    plant              plant every map value in the shot's path; what stops it
    box                isolate $8EEB / $9009 and ENUMERATE the hit geometry
    window             isolate $8D97 and ENUMERATE the viewport cull
    probe              isolate $8F36 and enumerate which map cell it reads
    sprite             trap the two blitters; report the sprite record
    blit               trace one shot blit: source row -> screen row
    gates              the fire gates: inventory bits, one-shot-at-a-time
    chars              drive $BE53 with $FFFF poked: the four ($8433,$8435)
    collide            the $20 state collision the captured $8433 produces
    cadence            fire cadence with the camera SETTLED first
    parity             $90E6's bit-0 bonus: how big, and on which parity
    freeze             does holding FIRE stop the player?
    contact            the pass-24 ghost contact, with the ELF armour row
    death              $93CD: the trigger, the drop, the pass count
    genmelee           refused passes walking into $21/$22, ELF fight power
    all                every section above

THE SHOT DIFFERENTIAL
    table <dir> <n> [--walk W] [--char C]
        prints one row per MAIN-LOOP PASS, sampled at $8503:
            pass  px py  sx sy st  act  score
        FIRE is held from pass W+1 (default 1); `dir` is held throughout.
        `node tools/headless.js --shots <dir> <n> [--walk W] [--char C]`
        prints the identical table from the BUILT engine, so the two can be
        diffed line for line the way tools/sim_move.py's is.
    diff               run both sides for five directions and report mismatches

NOTE ON THE SAVED STATE.  build/state_charsel.pkl carries the game's own
$FFFF boot bug ($FFFF = $2A, see NOTES-engine.md "a BOOT bug, not a decode
bug").  $BE53 reads it and $BEE5 indexes a FOUR-entry table at $BF19 with it,
so player 1's shot-flags byte $8433 lands on garbage ($20) instead of one of
$00/$08/$10/$18.  Anything that depends on $8433 is therefore measured BOTH
ways: as the state has it, and with $8433 repaired.
"""
import os
import pickle
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, A, B, C, D, E, H, L,   # noqa
                     IXh, IXl, TAPE_CALL_PC, FRAME_T)
from keyprobe import KEYS, keymask                                   # noqa

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')   # the 48K baseline
LOOP_TOP = 0x8503

P1 = 0x8420
SHOT1 = 0x8430               # player 1's shot record = player block + $10
FLAGS1 = 0x8433              # its flags byte -- $BE5D writes it
CHAR_FLAGS = (0x00, 0x08, 0x10, 0x18)     # the real table at $BF19
NAMES = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def base_state():
    return pickle.load(open(STATE, 'rb'))


def boot(flags=None, arena=False, px=64, py=64, actors=None):
    h = Harness()
    h.load_state(base_state())
    if arena:
        for a in range(0x8000, 0x8400):
            h.poke(a, 0)
        h.poke(P1, px)
        h.poke(P1 + 1, py)
    if actors is not None:
        h.poke(0x8496, actors)
    if flags is not None:
        h.poke(FLAGS1, flags)
    return h


def press(h, *names):
    for n in names:
        sel, bit = KM[n]
        h.ports.press(sel, keymask(bit))


def step_to(h, addrs, limit=4_000_000):
    """Run until PC is in addrs.  A custom stepper, so it handles the tape
    breakpoint and a HALT with interrupts enabled, as the harness's does."""
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    fd, ia = h.frame_duration, h.int_active
    n = 0
    while n < limit:
        pc = regs[PC]
        if n and pc in addrs:
            return pc
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape()
            n += 1
            continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            n += 1
            continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return None


def rec(m, b=SHOT1):
    return tuple(m[b + i] for i in range(4))


# ------------------------------------------------------------------ firekey
def cmd_firekey():
    print('# which key fires:  $8427 is the direction byte, bit 4 = FIRE')
    st = base_state()
    for name, sel, bit in KEYS:
        h = Harness()
        h.load_state(st)
        press(h, name)
        step_to(h, {LOOP_TOP})
        dirs, shots = set(), set()
        for _ in range(6):
            step_to(h, {LOOP_TOP})
            m = h.memobj.m
            dirs.add(m[0x8427])
            shots.add(rec(m))
        if dirs != {0} or len(shots) > 1:
            d = ' '.join(f'{v:02X}' for v in sorted(dirs))
            s = ' '.join('/'.join(f'{v:02X}' for v in r)
                         for r in sorted(shots)[:3])
            print(f'  {name:<6} $8427={d:<6} shot {s}')


# -------------------------------------------------------------------- steps
def cmd_steps(keys=('D',), n=12):
    print('# $8D2B is the per-STEP commit LD (IX),C / LD (IX+1),B')
    for label, inv in (('inventory $00', 0x00),
                       ('inventory $10 SHOT SPEED', 0x10),
                       ('inventory $40', 0x40)):
        h = boot(flags=0x10)
        h.poke(0x8434, inv)
        press(h, 'Z', *keys)
        step_to(h, {LOOP_TOP})
        per, cur, passes = Counter(), 0, 0
        while passes < n:
            pc = step_to(h, {LOOP_TOP, 0x8D2B})
            if pc == LOOP_TOP:
                per[cur] += 1
                passes += 1
                cur = 0
            else:
                cur += 1
        print(f'  {label:<26} steps/pass {dict(sorted(per.items()))}')


# --------------------------------------------------------------------- dirs
def cmd_dirs():
    """Open arena, camera settled, compass slot forced -- so the only thing
    that can end the shot is the viewport cull."""
    print('# open arena, player (64,64), camera settles at (34,46)')
    print('  slot dir  state  first      last       passes  ends because')
    for flags in (0x20, 0x10):
        print(f'  --- $8433 = ${flags:02X} '
              f'{"(as saved: the BOOT BUG value)" if flags == 0x20 else ""}')
        for slot in range(8):
            h = boot(flags=flags, arena=True, actors=0)
            m = h.memobj.m
            for _ in range(60):
                step_to(h, {LOOP_TOP})
                h.poke(0x842D, slot)
            cam = (m[0x848B], m[0x848C])
            press(h, 'Z')
            path, started = [], False
            for _ in range(40):
                step_to(h, {LOOP_TOP})
                h.poke(0x842D, slot)
                if m[0x8432] != 0xFF:
                    started = True
                    path.append((m[0x8430], m[0x8431]))
                elif started:
                    break
            if not path:
                print(f'  {slot}    {NAMES[slot]:<3}  --     no shot')
                continue
            lx, ly = path[-1]
            print(f'  {slot}    {NAMES[slot]:<3}  ${flags + slot:02X}    '
                  f'{str(path[0]):<10} {str(path[-1]):<10} {len(path):<7}'
                  f' cam={cam} (dx,dy)=({(lx - cam[0]) & 0x7F},'
                  f'{(ly - cam[1]) & 0x7F})')


# -------------------------------------------------------------------- plant
def cmd_plant():
    """Plant one map value in the path of a shot fired DOWN from the level
    start and report whether the FIRST shot is stopped by it."""
    CELL = 0x8000 + 32 * 5 + 3          # cell (col 3, row 5): y 20..23
    print('# player (12,8) fires DOWN; cell (3,5) planted; a stopped shot '
          'never reaches y=24')
    stop, thru, changed, scored = [], [], [], []
    for v in list(range(0x00, 0x40)) + [0x7F, 0x80, 0x81]:
        h = boot(flags=0x10)
        h.poke(CELL, v)
        press(h, 'Z')
        m = h.memobj.m
        step_to(h, {LOOP_TOP})
        s0 = tuple(m[0x8424 + i] for i in range(3))
        ys = []
        for _ in range(9):
            step_to(h, {LOOP_TOP})
            if m[0x8432] == 0xFF:
                break
            # Z is held, so a dead shot is re-fired on the very next pass;
            # a y that goes BACKWARDS is the second shot, not this one.
            if ys and m[0x8431] < ys[-1]:
                break
            ys.append(m[0x8431])
        (stop if 24 not in ys else thru).append(v)
        if m[CELL] != v:
            changed.append((v, m[CELL]))
        if tuple(m[0x8424 + i] for i in range(3)) != s0:
            scored.append(v)
    print('  STOPS  :', ' '.join(f'${x:02X}' for x in stop))
    print('  PASSES :', ' '.join(f'${x:02X}' for x in thru))
    print('  cell changed:',
          ' '.join(f'${a:02X}->${b:02X}' for a, b in changed))
    print('  scored :', ' '.join(f'${x:02X}' for x in scored))


# ---------------------------------------------------------------------- box
def _iso(h, entry, exits, inside, regs_in, budget=200):
    """Single-step a routine in isolation with SP restored, returning the PC
    it left through.  PC breakpoints only -- never a byte patch (6.6b)."""
    r = h.sim.registers
    mem, ops = h.sim.memory, h.sim.opcodes
    sp0 = r[SP]
    for k, v in regs_in.items():
        if k == 'BC':
            r[B], r[C] = v >> 8, v & 0xFF
        elif k == 'IX':
            r[IXh], r[IXl] = v >> 8, v & 0xFF
        else:
            r[k] = v
    r[PC] = entry
    out = None
    for _ in range(budget):
        pc = r[PC]
        if pc in exits:
            out = pc
            break
        if pc not in inside:
            break
        ops[mem[pc]]()
    r[SP] = sp0
    return out


def cmd_box():
    print('# $8EEB shot-vs-ACTOR and $9009 shot-vs-PLAYER, enumerated')
    h = boot()
    m, regs = h.memobj.m, h.sim.registers
    h.poke(0x8496, 1)
    for i, v in enumerate((64, 64, 0x00, 0x00)):
        h.poke(0x5C00 + i, v)
    hits = set()
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            h.call(0x8EEB, regs={'BC': (((64 + dy) & 0x7F) << 8)
                                 | ((64 + dx) & 0x7F)})
            if regs[1] & 1:
                hits.add((dx, dy))
    print('  actor at (64,64), shot at (64+dx,64+dy):')
    for dy in range(-6, 7):
        print('    dy=%+d ' % dy, ''.join('#' if (dx, dy) in hits else '.'
                                          for dx in range(-6, 7)))
    print('    dx', sorted({d[0] for d in hits}),
          'dy', sorted({d[1] for d in hits}))
    h.poke(0x5C03, 0x08)
    h.call(0x8EEB, regs={'BC': (64 << 8) | 64})
    print('    same actor with flags bit 3 (invisible): carry =', regs[1] & 1)
    h.poke(0x5C03, 0x00)
    h.poke(P1, 64)
    h.poke(P1 + 1, 64)
    h.poke(0x842B, m[0x842B] & ~0x40)
    h.poke(0x844B, m[0x844B] | 0x40)
    hits2 = set()
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            h.poke(SHOT1, 0)
            h.call(0x9009, regs={'BC': (((64 + dy) & 0x7F) << 8)
                                 | ((64 + dx) & 0x7F), 'IX': SHOT1})
            if regs[1] & 1:
                hits2.add((dx, dy))
    print('  $9009 player at (64,64):  dx', sorted({d[0] for d in hits2}),
          'dy', sorted({d[1] for d in hits2}))


# ------------------------------------------------------------------- window
CULL_IN = set(range(0x8D97, 0x8DAB)) | set(range(0xAD37, 0xAD47))


def cmd_window():
    print('# $8D97, the viewport cull, enumerated over a 140x140 offset grid')
    h = boot()
    ok = set()
    for dy in range(-70, 70):
        for dx in range(-70, 70):
            h.poke(0x848B, 10)
            h.poke(0x848C, 10)
            r = _iso(h, 0x8D97, {0x8DAB}, CULL_IN,
                     {'BC': (((10 + dy) & 0x7F) << 8) | ((10 + dx) & 0x7F),
                      'IX': SHOT1})
            if r == 0x8DAB:
                ok.add((dx % 128, dy % 128))
    xs = sorted({p[0] for p in ok})
    ys = sorted({p[1] for p in ok})
    print(f'  survives for (x-camx)&$7F in {xs[0]}..{xs[-1]} '
          f'and (y-camy)&$7F in {ys[0]}..{ys[-1]}'
          f'   ({len(ok)} offsets, rectangle: '
          f'{len(ok) == len(xs) * len(ys)})')


PROBE_IN = set(range(0x8F36, 0x8F55)) | set(range(0xAE6D, 0xAE7E))


def cmd_probe():
    print('# $8F36, which map CELL a step reads, by compass slot')
    h = boot(flags=0x10)
    r = h.sim.registers
    for s in range(8):
        seen = set()
        for a in range(4):
            for b in range(4):
                x, y = 40 + a, 40 + b
                h.poke(0x8432, 0x10 + s)
                out = _iso(h, 0x8F36, {0x8F54}, PROBE_IN,
                           {'BC': (y << 8) | x, 'IX': SHOT1})
                if out is None:
                    continue
                own = 0x8000 + 32 * (y >> 2) + (x >> 2)
                seen.add(((r[H] << 8) | r[L]) - own)
        print(f'  slot {s} {NAMES[s]:<3} cell deltas {sorted(seen)}'
              '   (+1 right, +32 down)')


# ------------------------------------------------------------------- sprite
def cmd_sprite():
    print('# which sprite record the blitter is handed '
          '($8DF9 16-wide OR, $8EBD 8-wide opaque)')
    for flags in (0x20, 0x10):
        for slot in range(8):
            h = boot(flags=flags, arena=True, actors=0)
            m, regs = h.memobj.m, h.sim.registers
            for _ in range(30):
                step_to(h, {LOOP_TOP})
                h.poke(0x842D, slot)
            press(h, 'Z')
            got = None
            for _ in range(30):
                pc = step_to(h, {LOOP_TOP, 0x8DF9, 0x8EBD})
                h.poke(0x842D, slot)
                if pc == LOOP_TOP:
                    continue
                got = (pc, regs[L] | (regs[H] << 8), m[0x8432])
                break
            if got:
                pc, hl, st = got
                blank = all(m[hl + i] == 0 for i in range(33))
                print(f'  $8433=${flags:02X} slot {slot} {NAMES[slot]:<3} '
                      f'state ${st:02X}  blitter ${pc:04X}  record ${hl:04X}'
                      f'  attr ${m[hl]:02X} {"BLANK" if blank else ""}')


# --------------------------------------------------------------------- blit
POPS = {0x8E37, 0x8E40, 0x8E49, 0x8E52, 0x8E5B, 0x8E64,
        0x8E7B, 0x8E84, 0x8E8D, 0x8E96, 0x8E9F, 0x8EA8}


def cmd_blit():
    print('# one shot blit: source row -> screen row (the $8DFD 16-wide OR)')
    h = boot(flags=0x10)
    press(h, 'Z', 'D')
    step_to(h, {LOOP_TOP})
    for _ in range(3):
        step_to(h, {LOOP_TOP})
    step_to(h, {0x8DFD})
    regs, mem, ops = h.sim.registers, h.sim.memory, h.sim.opcodes

    def py(a):
        a -= 0xC000
        return ((a >> 11) & 3) * 64 + ((a >> 5) & 7) * 8 + ((a >> 8) & 7)
    by = py((regs[D] << 8) | regs[E])
    src = 0
    while regs[PC] != 0x8EB0:
        pc = regs[PC]
        if pc in POPS:
            sp = regs[SP]
            print(f'   source row {src:2} -> screen row +'
                  f'{py((regs[D] << 8) | regs[E]) - by:2}'
                  f'   bytes L=${mem[sp]:02X} R=${mem[sp + 1]:02X}')
            src += 1
        ops[mem[pc]]()


# -------------------------------------------------------------------- gates
def cmd_gates():
    print('# the fire gates at $8C74..$8C8E, one at a time')
    for label, poke in (('nothing (control)', None),
                        ('inventory bit 6 ($8434 |= $40)', (0x8434, 0x40)),
                        ('phase byte ($842E |= 1)', (0x842E, 0x01)),
                        ('flags bit 6 ($842B |= $40)', (0x842B, 0x40))):
        h = boot(flags=0x10, arena=True, actors=0)
        press(h, 'Z', 'D')
        step_to(h, {LOOP_TOP})
        fires, passes = 0, 0
        while passes < 20:
            if poke:
                h.poke(poke[0], h.memobj.m[poke[0]] | poke[1])
            pc = step_to(h, {LOOP_TOP, 0x8C94})
            if pc == LOOP_TOP:
                passes += 1
            else:
                fires += 1
        print(f'  {label:<34} {fires} shots in {passes} passes')


# ======================================================================== the
# ======================================================== CHARACTER, MEASURED
def cmd_chars():
    """$BE53 LD A,($FFFF) driven FOR REAL on a fresh boot, with $FFFF poked to
    each legal index, reading $8433/$8435 back after $BE61 has written them.

    This settles a claim one report made and its own $BF19 dump contradicted:
    the tape-fresh $8435 is NOT $00 -- $00 is not in the table at all."""
    print('# fresh boot, $FFFF poked at $BE53, $8433/$8435 read after $BE61')
    for idx in (0, 1, 2, 3, 0x2A):
        h = Harness()
        pc = step_to(h, {0xBE53}, limit=20_000_000)
        assert pc == 0xBE53, pc
        h.poke(0xFFFF, idx)
        step_to(h, {0xBE64}, limit=2_000_000)
        m = h.memobj.m
        p = m[0x8435]
        print(f'  $FFFF=${idx:02X} -> $8433=${m[0x8433]:02X} $8435=${p:02X}'
              f'   shot {p & 3} fight {(p >> 2) & 3} magic {(p >> 4) & 3} '
              f'armour {(p >> 6) & 3}'
              + ('   <- the CAPTURED state: $2A is the stale $FFFF, and $BEE5'
                 ' walks 42 entries past a FOUR-entry table'
                 if idx == 0x2A else ''))


CHARS = {0: (0x00, 0x8E), 1: (0x08, 0xD8), 2: (0x10, 0x32), 3: (0x18, 0x64)}


def elf(h, idx=3):
    """Install character `idx` the way the boot would, on the real Z80.

    $BE5D/$BE61 write the $BF19 pair into $8433/$8435, and then $96AF calls
    $AB6F at LEVEL START to copy the six armour bytes.  The row is therefore
    computed by the GAME'S OWN ROUTINE here -- called in isolation on a saved
    state, read back, and poked into the restored machine, so that the call's
    register damage cannot leak into the run.  idx None leaves the capture
    exactly as it is (the $FFFF boot bug's $20/$20)."""
    if idx is None or idx < 0 or idx > 3:
        return
    tag, powers = CHARS[idx]
    snap = h.save_state()
    h.poke(0x8435, powers)
    h.call(0xAB6F, regs={'IX': P1})
    row = [h.memobj.m[a] for a in range(0x8437, 0x843D)]
    h.load_state(snap)
    h.poke(FLAGS1, tag)
    h.poke(0x8435, powers)
    for i, v in enumerate(row):
        h.poke(0x8437 + i, v)


def cmd_collide():
    """The captured $8433 = $20 makes a NORTH shot's state exactly $20, which
    is the routine's own EXPLODING sentinel ($8C60/$8DAB)."""
    print('# fire + up from the level start, 8 passes, by $8433')
    for tag in (0x20, 0x00, 0x08, 0x10, 0x18):
        h = boot(flags=tag)
        m = h.memobj.m
        press(h, 'Z', '1')
        step_to(h, {LOOP_TOP})
        path = []
        for _ in range(8):
            step_to(h, {LOOP_TOP})
            path.append(f'({m[0x8430]},{m[0x8431]})${m[0x8432]:02X}')
        print(f'  $8433=${tag:02X}  ' + ' '.join(path)
              + ('   <- PINNED' if tag == 0x20 else ''))


def arena(h, px=64, py=64):
    """An empty map with ONE actor parked far away.  $8496 = 0 would make the
    DJNZ in $A97F wrap to 256 iterations over whatever sits at $5C00, which
    both blocks the player's move and manufactures spurious shot expiries."""
    for a in range(0x8000, 0x8400):
        h.poke(a, 0)
    h.poke(P1, px)
    h.poke(P1 + 1, py)
    for i, v in enumerate((4, 4, 0x00, 0x00)):
        h.poke(0x5C00 + i, v)
    h.poke(0x8496, 1)
    h.poke(0x8494, 0x04)
    h.poke(0x8495, 0x5C)


def cmd_cadence(tag=0x18):
    """Fire cadence with the camera SETTLED FIRST.  Taking this measurement
    before the camera converges measures the CAMERA, not the gun: the shot is
    culled on its very first step while camy is still 30 units from the player
    and every gap reads 1."""
    print(f'# open arena (64,64), 60 settling passes, $8433=${tag:02X}, '
          f'then fire held for 120')
    for slot in range(8):
        h = boot(flags=tag)
        arena(h)
        m = h.memobj.m
        for _ in range(60):
            step_to(h, {LOOP_TOP})
            h.poke(0x842D, slot)
        cam = (m[0x848B], m[0x848C])
        press(h, 'Z')
        fires, passes = [], 0
        while passes < 120:
            pc = step_to(h, {LOOP_TOP, 0x8C94})
            if pc == LOOP_TOP:
                passes += 1
                h.poke(0x842D, slot)
            else:
                fires.append(passes)
        gaps = Counter(b - a for a, b in zip(fires, fires[1:]))
        print(f'  slot {slot} {NAMES[slot]:<3} cam={cam}  {len(fires):>3} fires'
              f'  gaps {dict(gaps)}')


def cmd_parity():
    """$90E6's bit-0 bonus.  Table byte $09 (SHOT POWER 1) against a repainted
    actor at every tier and every pass-counter phase, in isolation."""
    print('# $90E6 isolated, $8435 SHOT POWER 1 -> $7D64[2] = $09')
    print('  ctr tier  state -> state   destroyed')
    h = boot()
    m = h.memobj.m
    h.poke(0x8435, (m[0x8435] & ~3) | 1)
    h.poke(0x8434, m[0x8434] & ~0x08)
    for ctr in range(4):
        for tier in range(4):
            h.poke(0x8491, ctr)
            st = tier << 3
            for i, v in enumerate((64, 64, st, 0x00)):
                h.poke(0x5C00 + i, v)
            h.poke(0x8494, 0x04)
            h.poke(0x8495, 0x5C)
            h.poke(0x8496, 1)
            h.poke(0x8432, m[FLAGS1])
            h.call(0x90E6, regs={'IX': SHOT1, 'HL': 0x5C02})
            print(f'  {ctr:>3} {tier:>4}   ${st:02X} -> ${m[0x5C02]:02X}'
                  f'      {"YES" if m[0x8496] == 0 else "no"}')
    print('  -> the bonus is a WHOLE TIER (+8) and it lands on ODD $8491')


def cmd_freeze():
    """$A57E BIT 4,A / $A58B SUB A -- fire zeroes the direction byte and the
    move routine returns.  $A48D is RES 4,(IX+14), NOT "write 0 to $842E"."""
    print('# 12 passes each, sampled at $8503')
    for keys, label in ((('Q',), 'Q alone'), (('Z', 'Q'), 'Z+Q'),
                        (('D',), 'D alone'), (('Z', 'D'), 'Z+D')):
        h = boot()
        m = h.memobj.m
        press(h, *keys)
        step_to(h, {LOOP_TOP})
        path = []
        for _ in range(12):
            step_to(h, {LOOP_TOP})
            path.append((m[0x8420], m[0x8421]))
        print(f'  {label:<8} {path[0]} .. {path[-1]}   $842D={m[0x842D]} '
              f'$842E=${m[0x842E]:02X}')
    h = boot()
    m = h.memobj.m
    press(h, 'D')
    step_to(h, {LOOP_TOP})
    seq = []
    for _ in range(12):
        step_to(h, {LOOP_TOP})
        seq.append(m[0x842E])
    print('  $842E while WALKING: ' + ' '.join('%02X' % v for v in seq)
          + '   (the walk phase, not a zero)')


def cmd_contact():
    """THE PASS-24 GHOST CONTACT, re-measured with the ELF character installed
    by the game's own $AB6F.  This is the expected value the headless check
    uses; the port repairs $FFFF, so the armour row is 1 and not 0."""
    for idx, label in ((None, 'as captured ($8435=$20, armour 0)'),
                       (3, 'ELF ($8435=$64, armour 1)')):
        h = boot()
        elf(h, idx)
        m = h.memobj.m
        press(h, 'Q')
        step_to(h, {LOOP_TOP})
        hp0 = (m[0x8422] << 8) | m[0x8423]
        n0 = m[0x8496]
        for _ in range(24):
            step_to(h, {LOOP_TOP})
        hp1 = (m[0x8422] << 8) | m[0x8423]
        print(f'  {label:<34} $8437..$843C '
              f'{" ".join("%02X" % m[a] for a in range(0x8437, 0x843D))}'
              f'  health ${hp0:04X} -> ${hp1:04X}  actors {n0} -> {m[0x8496]}')


def cmd_genmelee():
    """Refused passes walking into a generator, in the SAME scenario the
    headless check drives: one generator planted at cell (3,10), the player
    started three cells above it at (12,28) with inventory bit 5 set, holding
    down for 14 passes.  Repeated for the captured stat byte and the ELF's,
    because $8435's bits 3:2 are FIGHT POWER and $A964 indexes $7D70 with
    them -- so the character changes how many passes a generator costs."""
    CELL = 0x8000 + 32 * 10 + 3
    for idx, label in ((None, 'as captured ($8435=$20, fight 0)'),
                       (3, 'ELF ($8435=$64, fight 1)')):
        for v in (0x20, 0x21, 0x22):
            h = boot()
            elf(h, idx)
            m = h.memobj.m
            h.poke(0x8496, 0)                   # the actors OFF, as the check
            h.poke(0x8494, 0x00)                # drives it
            h.poke(0x8495, 0x5C)
            h.poke(P1, 12)
            h.poke(P1 + 1, 28)
            h.poke(CELL, v)
            h.poke(0x8434, m[0x8434] | 0x20)    # inventory bit 5: melee allowed
            press(h, 'Q')
            step_to(h, {LOOP_TOP})
            ys, cells = [], []
            for _ in range(14):
                step_to(h, {LOOP_TOP})
                ys.append(m[0x8421])
                cells.append(m[CELL])
            stalls = sum(1 for i in range(1, len(ys)) if ys[i] == ys[i - 1])
            seen = [c for i, c in enumerate(cells) if i == 0 or c != cells[i-1]]
            print(f'  {label:<34} ${v:02X}: {stalls} refused passes, cell '
                  + ' -> '.join('$%02X' % c for c in seen))


# ======================================================================== the
# ===================================================================== DEATH
def cmd_death():
    """$8540 CALL $93C2 -- driven on the real Z80, printing the numbers the
    port's death check asserts.  The trigger, the pass count of the sequence,
    what the drop is and what the state looks like either side."""
    P14, F11, CTL = 0x8434, 0x842B, 0x842E

    def run(hp, keys=None, label=''):
        h = boot()
        elf(h, 3)
        m = h.memobj.m
        step_to(h, {LOOP_TOP})
        if keys is not None:
            h.poke(0x8428, keys)
        h.poke(0x8422, hp >> 8, hp & 0xFF)
        cell = 0x8000 + 32 * ((m[0x8421] & 0x7C) >> 2) + ((m[0x8420] & 0x7C) >> 2)
        before = (m[P14], m[F11], m[CTL], m[cell], m[0x8496], m[0x84C0])
        rows = []
        for i in range(4):
            pc = step_to(h, {LOOP_TOP, 0x855C})
            rows.append((i + 1, m[P14], m[F11], m[CTL], m[cell], m[0x8496],
                         m[0x84C0], m[0x847D],
                         'MAIN LOOP RETURNS' if pc == 0x855C else ''))
            if pc == 0x855C:
                break
        print(f'  {label}  health ${hp:04X}  keys {m[0x8428]}  '
              f'cell ${cell:04X}')
        print(f'      before   $8434=${before[0]:02X} $842B=${before[1]:02X} '
              f'$842E=${before[2]:02X} cell=${before[3]:02X} '
              f'actors={before[4]} $84C0={before[5]}')
        for r in rows:
            print(f'      pass {r[0]}   $8434=${r[1]:02X} $842B=${r[2]:02X} '
                  f'$842E=${r[3]:02X} cell=${r[4]:02X} actors={r[5]} '
                  f'$84C0={r[6]} $847D=${r[7]:02X}  {r[8]}')
        # the score and the health SURVIVE -- nothing resets them here
        print(f'      after    score {m[0x8424]:02X}{m[0x8425]:02X}'
              f'{m[0x8426]:02X}  health ${m[0x8422]:02X}{m[0x8423]:02X}  '
              f'$5BE8 {" ".join("%02X" % b for b in m[0x5BE8:0x5BEB])}')

    print('# THE TRIGGER is exactly BCD 0000 -- $93D2 LD A,(IX+2)/OR (IX+3)')
    run(0x0001, label='not dead:')
    run(0x0000, keys=0, label='0 keys :')
    run(0x0000, keys=1, label='1 key  :')
    run(0x0000, keys=3, label='3 keys :')


# ======================================================================== the
# ============================================================ SHOT DIFFERENTIAL
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D', 'none': None}


def cmd_table(direction='right', passes=24, walk=0, char=3):
    """One row per pass, sampled at $8503 -- see tools/sim_move.py's docstring
    for why the sampler is a PC and never a frame count."""
    h = boot()
    elf(h, char)
    m = h.memobj.m
    k = DIRKEY[direction]
    if k:
        press(h, k)
    step_to(h, {LOOP_TOP})
    print('pass  px  py   sx  sy  st  act  score')
    for i in range(passes):
        if i == walk:
            press(h, 'Z')                 # FIRE from this pass on
        step_to(h, {LOOP_TOP})
        print(f'{i+1:>4}{m[0x8420]:>4}{m[0x8421]:>4}  {m[0x8430]:>3}{m[0x8431]:>4}'
              f'  {m[0x8432]:02X} {m[0x8496]:>4}  '
              f'{m[0x8424]:02X}{m[0x8425]:02X}{m[0x8426]:02X}')


def cmd_diff():
    import re
    import subprocess
    ROW = re.compile(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+'
                     r'([0-9A-F]{2})\s+(\d+)\s+([0-9A-F]{6})\s*$')

    def rows(cmd):
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                             cwd=ROOT).stdout
        return [m.groups() for m in (ROW.match(l) for l in out.splitlines()) if m]

    total, actonly = 0, 0
    def noact(r):
        return r[:6] + r[7:]
    for d in ('none', 'up', 'down', 'left', 'right'):
        for n, w in ((30, 0), (60, 0), (60, 20), (100, 30)):
            a = rows(f'python tools/shotgate.py table {d} {n} --walk {w}')
            b = rows(f'node tools/headless.js --shots {d} {n} --walk {w}')
            if len(a) != n or len(b) != n:
                print(f'{d:>6} {n:>3} walk {w}: WRONG ROW COUNT '
                      f'orig={len(a)} engine={len(b)}')
                total += max(n - len(a), n - len(b), 1)
                continue
            bad = [(x, y) for x, y in zip(a, b) if x != y]
            # The ACTOR COUNT column is the one quantity in this table that the
            # LD A,R entropy can move: a contact the substitute coin does not
            # reproduce removes a ghost on a different pass.  It is reported
            # separately rather than dropped, because a divergence in it is a
            # real fact about the port and a divergence in any other column
            # would be a rule error.
            ao = [p for p in bad if noact(p[0]) == noact(p[1])]
            tag = f'  ({len(ao)} of them the ACTOR COUNT only)' if ao else ''
            print(f'{d:>6} {n:>3} rows walk {w:>2} -> '
                  f'{len(bad) - len(ao)} mismatching{tag}')
            for x, y in bad[:4]:
                if noact(x) != noact(y):
                    print(f'        orig {x}\n        engi {y}')
            total += len(bad) - len(ao)
            actonly += len(ao)
    print(f'\nTOTAL MISMATCHING SHOT ROWS: {total}'
          f'   (plus {actonly} rows where only the actor count differs)')
    try:
        import harness
        if harness.CONTENDED and total:
            print('   the harness is CONTENDED.  MEASURED: 0 mismatching with'
                  ' GAUNTLET_CONTENDED=0 and 32 with it, ALL of them in the'
                  ' one')
            print('   scenario `down 60 rows walk 20`.  The SHOT columns still'
                  ' agree there; what differs is which ACTOR it meets and so'
                  ' the score.')
            print('   Cause: the actor coins read LD A,R, and contention makes'
                  ' a pass cost five video frames instead of four, which runs'
                  ' the')
            print('   handler an extra time and gives R a DIFFERENT sequence'
                  ' (66 74 108 101 ... against 15 63 4 4 ...).  The port'
                  ' substitutes its')
            print('   own entropy and can follow neither.  Left visible rather'
                  ' than exempted: if this is not 32, read it.')
    except Exception:
        pass
    return total


TABLE = {'firekey': cmd_firekey, 'steps': cmd_steps, 'dirs': cmd_dirs,
         'plant': cmd_plant, 'box': cmd_box, 'window': cmd_window,
         'probe': cmd_probe, 'sprite': cmd_sprite, 'blit': cmd_blit,
         'gates': cmd_gates, 'chars': cmd_chars, 'collide': cmd_collide,
         'cadence': cmd_cadence, 'parity': cmd_parity, 'freeze': cmd_freeze,
         'contact': cmd_contact, 'genmelee': cmd_genmelee, 'diff': cmd_diff,
         'death': cmd_death}


def opt(name, default):
    a = sys.argv
    return type(default)(a[a.index(name) + 1]) if name in a else default


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    cmd = args[0] if args else 'all'
    if cmd == 'all':
        for name in ('chars', 'firekey', 'freeze', 'steps', 'probe', 'plant',
                     'box', 'window', 'dirs', 'cadence', 'parity', 'collide',
                     'sprite', 'blit', 'gates', 'contact', 'genmelee', 'death'):
            print(f'\n===== {name} =====')
            TABLE[name]()
    elif cmd == 'steps':
        cmd_steps(args[1].split('+') if len(args) > 1 else ('D',),
                  int(args[2]) if len(args) > 2 else 12)
    elif cmd == 'table':
        cmd_table(args[1] if len(args) > 1 else 'right',
                  int(args[2]) if len(args) > 2 else 24,
                  opt('--walk', 0), opt('--char', 3))
    elif cmd in TABLE:
        TABLE[cmd]()
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
