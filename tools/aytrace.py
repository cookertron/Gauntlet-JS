#!/usr/bin/env python3
"""
aytrace.py -- THE AY-3-8912 REGISTER TRACE.  Phase 11 of PORTING-ZX-TO-JS.txt,
the AY branch.  Nothing here loads the JS engine: every row is the original
Z80 answering, driven through scripted play with the port tracer armed
(manual 6.5).

    python tools/aytrace.py inventory        which of the 14 registers, ever
    python tools/aytrace.py dump SCENARIO    the per-50Hz-frame register dump
    python tools/aytrace.py effects          each effect id $00..$11, isolated
    python tools/aytrace.py table            the $73DA effect-pointer table
    python tools/aytrace.py verify           THE GATE: model vs recorded writes
    python tools/aytrace.py clock            what clock the periods imply
    python tools/aytrace.py switch           the $BF21 / $FFFD sound switch
    python tools/aytrace.py stall            where the 103.8-frame stall really is
    python tools/aytrace.py save             write build/aytrace.json
    python tools/aytrace.py all

=============================================================================
WHAT IS BEING DECODED
=============================================================================
Every AY write in the game goes through ONE helper, $BB96, and it writes both
ports back to back:

    $BB96  PUSH AF / PUSH BC / LD B,C / PUSH BC / LD BC,$FFFD
    $BB9D  OUT (C),A          ; A = REGISTER NUMBER   (port $FFFD, select)
    $BB9F  LD B,$BF / POP AF  ; A = the pushed B = C on entry = THE VALUE
    $BBA2  OUT (C),A          ; port $BFFD, data
    $BBA4  POP BC / POP AF / RET

so the calling convention is A = register, C = value, and decoding the port
trace is a straight state machine: remember the last value written to $FFFD,
pair it with the next write to $BFFD.  Writes are bucketed by 50 Hz video
frame (T // 69888), which is the same bucketing a .psg or .ay file uses.

NOTE THE NAME COLLISION.  $FFFD is BOTH the AY register-select PORT and a RAM
address this game reads (`LD A,($FFFD)` at $BF21 and $9D0A).  An OUT to the
port and a read of the address are different things; see the `switch`
subcommand for what the RAM byte does.

=============================================================================
THE FRAME IS THE UNIT, NOT THE PASS
=============================================================================
The driver tick $BADB is called once per video frame from the interrupt path,
not once per main-loop pass, so the natural bucket is the 50 Hz frame and NOT
the ~12.5 Hz main-loop pass.  Pass boundaries ($8503) are recorded alongside
anyway, because the port's onePass() is what will replay this trace and it
needs to know which frames belong to which pass.
"""
import collections
import json
import math
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, A, B, C, D, E, H, L, PC, T, IFF, SP,  # noqa: E402
                     TAPE_CALL_PC, FRAME_T, CPU_HZ)
from keyprobe import KEYS, keymask                                 # noqa: E402
from sim_move import step_to_loop_top                              # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
OUT = os.path.join(ROOT, 'build', 'aytrace.json')

AY_SEL = 0xFFFD                 # port: register select
AY_DAT = 0xBFFD                 # port: data
SPEAKER = 0x10                  # bit 4 of a $FE write

P1 = 0x8420
LIST = 0x5C00
COUNT = 0x8496
TAIL = 0x8494
CTR = 0x8491
SND_DISPATCH = 0xBA2B
SND_TICK = 0xBADB
SND_TABLE = 0x73DA              # $BAB7's base: id -> 16-bit offset -> data

DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D', 'fire': 'Z'}

REGNAME = {
    0: 'A tone lo', 1: 'A tone hi', 2: 'B tone lo', 3: 'B tone hi',
    4: 'C tone lo', 5: 'C tone hi', 6: 'noise per', 7: 'MIXER',
    8: 'A vol', 9: 'B vol', 10: 'C vol', 11: 'env lo', 12: 'env hi',
    13: 'env shape', 14: 'ioA', 15: 'ioB',
}

# The two candidate AY clocks.  WHICH ONE IS RIGHT IS A MEASUREMENT, NOT AN
# ASSUMPTION -- see the `clock` subcommand and the `switch` subcommand, which
# is the one that actually settles it.
CLK_128 = 1_773_400             # 128K/+2/+3: CPU 3,546,900 / 2
CLK_48 = 1_750_000              # a 48K-clocked add-on: CPU 3,500,000 / 2
CLK_FULLER = 1_500_000          # some Fuller Box units
CANDIDATES = (('128K CPU/2', CLK_128), ('48K CPU/2', CLK_48),
              ('Fuller 1.5MHz', CLK_FULLER))


# ---------------------------------------------------------------------------
# the rig
# ---------------------------------------------------------------------------
def fresh(quiet_actors=False):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if quiet_actors:
        h.memobj.m[COUNT] = 0
    return h


def press(h, *names):
    for n in names:
        sel, bit = KM[DIRKEY.get(n, n)]
        h.ports.press(sel, keymask(bit))


def one_actor(h, x, y, state, flags=0x00):
    m = h.memobj.m
    m[LIST + 0], m[LIST + 1], m[LIST + 2], m[LIST + 3] = x, y, state, flags
    m[COUNT] = 1
    m[TAIL], m[TAIL + 1] = (LIST + 4) & 0xFF, (LIST + 4) >> 8
    return LIST


def arm(h):
    """Start recording.  Returns the T-state origin."""
    h.ports.record_writes = True
    h.ports.writes = []
    return h.regs[T]


def decode(writes, t0):
    """(T,port,value) -> (dt, frame, register, value), plus everything that is
    not an AY write.  The register-select state machine is the whole decode."""
    sel = None
    ev, other, speaker = [], [], []
    last_fe = None
    base = t0 // FRAME_T
    for t, p, v in writes:
        if p == AY_SEL:
            sel = v
        elif p == AY_DAT:
            ev.append((t - t0, t // FRAME_T - base, sel, v))
        elif (p & 0xFF) == 0xFE:
            if last_fe is not None and (last_fe ^ v) & SPEAKER:
                speaker.append((t - t0, v))
            last_fe = v
        else:
            other.append((t - t0, p, v))
    return ev, other, speaker


def frames(ev):
    """events -> {frame: [(reg,val), ...]} in write order."""
    out = collections.OrderedDict()
    for _, f, r, v in ev:
        out.setdefault(f, []).append((r, v))
    return out


# ---------------------------------------------------------------------------
# scenarios -- each returns (name, description, harness, marks)
# `marks` is a list of (frame, label) so the dump can say what happened when.
# ---------------------------------------------------------------------------
LOOP_TOP = 0x8503
AFTER_LOOP = 0xB394              # $B391 CALL $8503 -- where death comes out


def step_until(h, targets, deadline_frames=400):
    """Step to any of `targets`, or give up after a deadline in video frames.
    Returns the PC hit, or None.

    The plain sim_move sampler raises after 8M instructions, which is right for
    a movement table and wrong here: DEATH LEAVES THE MAIN LOOP ($8556 BIT 7,
    (IY-2) -> $B394) and never comes back, and the sound of dying is exactly
    what that scenario is for."""
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    t_end = regs[T] + deadline_frames * FRAME_T
    n = 0
    while regs[T] < t_end:
        pc = regs[PC]
        if n and pc in targets:
            return pc
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        opcodes[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    return None


def step_frames(h, n):
    """Drive n whole video frames, whatever the PC is doing."""
    step_until(h, (), n)


def _run(h, passes, marks, t0, per_pass=None, after_exit_frames=80):
    base = t0 // FRAME_T
    for i in range(passes):
        hit = step_until(h, (LOOP_TOP, AFTER_LOOP), 400)
        if hit != LOOP_TOP:
            marks.append((h.regs[T] // FRAME_T - base,
                          'LEFT THE MAIN LOOP'
                          + (' at $B394' if hit == AFTER_LOOP else ' (deadline)')))
            step_frames(h, after_exit_frames)
            break
        if per_pass:
            lbl = per_pass(h, i)
            if lbl:
                marks.append((h.regs[T] // FRAME_T - base, lbl))
    return marks


def sc_idle(passes=24):
    h = fresh(quiet_actors=True)
    step_to_loop_top(h)
    t0 = arm(h)
    return h, t0, _run(h, passes, [], t0)


def sc_walk(passes=40):
    h = fresh(quiet_actors=True)
    press(h, 'right')
    step_to_loop_top(h)
    t0 = arm(h)
    return h, t0, _run(h, passes, [], t0)


def sc_wall(passes=30):
    """Hold UP from the start square: the dungeon-1 start is walled to the
    north, so every pass is a refused move."""
    h = fresh(quiet_actors=True)
    press(h, 'up')
    step_to_loop_top(h)
    t0 = arm(h)
    m = h.memobj.m
    y0 = m[P1 + 1]
    marks = []

    def hook(hh, i):
        return None
    _run(h, passes, marks, t0, hook)
    marks.append((0, f'y {y0} -> {m[P1+1]} (0 = never moved)'))
    return h, t0, marks


def sc_fire(passes=30):
    h = fresh(quiet_actors=True)
    press(h, 'right', 'fire')
    step_to_loop_top(h)
    t0 = arm(h)
    return h, t0, _run(h, passes, [], t0)


def sc_melee(passes=20):
    """Hold right into a pinned class-1 monster and kill it."""
    h = fresh()
    m = h.memobj.m
    px, py = m[P1], m[P1 + 1]
    tx, ty = px + 6, py
    one_actor(h, tx, ty, 0x20)
    press(h, 'right')
    step_to_loop_top(h)
    t0 = arm(h)
    marks = []

    def hook(hh, i):
        mm = hh.memobj.m
        if mm[COUNT] == 0:
            return 'monster DEAD'
        mm[LIST], mm[LIST + 1] = tx, ty
        return None
    _run(h, passes, marks, t0, hook)
    return h, t0, marks


def sc_shotkill(passes=20):
    h = fresh()
    m = h.memobj.m
    px, py = m[P1], m[P1 + 1]
    tx, ty = px + 20, py
    one_actor(h, tx, ty, 0x20)
    press(h, 'right', 'fire')
    step_to_loop_top(h)
    t0 = arm(h)
    marks = []

    def hook(hh, i):
        mm = hh.memobj.m
        if mm[COUNT] == 0:
            return 'target DEAD'
        mm[LIST], mm[LIST + 1] = tx, ty
        return None
    _run(h, passes, marks, t0, hook)
    return h, t0, marks


def sc_damage(passes=20):
    """A class-0 actor walks into the player: contact damage ($AEA0)."""
    h = fresh()
    m = h.memobj.m
    px, py = m[P1], m[P1 + 1]
    one_actor(h, px + 8, py, 0x00)
    step_to_loop_top(h)
    t0 = arm(h)
    marks = []
    hp0 = (m[P1 + 2], m[P1 + 3])

    def hook(hh, i):
        mm = hh.memobj.m
        if (mm[P1 + 2], mm[P1 + 3]) != hp0:
            return f'health {hp0[0]:02X}{hp0[1]:02X} -> {mm[P1+2]:02X}{mm[P1+3]:02X}'
        return None
    _run(h, passes, marks, t0, hook)
    return h, t0, marks


def _plant_walk(cell_value, passes=20, keys=0, health=None):
    """Put the player one square north of a planted cell and hold DOWN into
    it.  The whole interaction chain then runs in situ."""
    h = fresh(quiet_actors=True)
    m = h.memobj.m
    row, col = 12, 8
    m[0x8000 + row * 32 + col] = cell_value
    m[P1], m[P1 + 1] = col * 4, (row - 2) * 4
    if keys:
        m[P1 + 8] = keys
    if health is not None:
        m[P1 + 2], m[P1 + 3] = health
    press(h, 'down')
    step_to_loop_top(h)
    t0 = arm(h)
    marks = []
    seen = {}

    def hook(hh, i):
        mm = hh.memobj.m
        v = mm[0x8000 + row * 32 + col]
        if v != cell_value and 'gone' not in seen:
            seen['gone'] = 1
            return f'cell ${cell_value:02X} -> ${v:02X}'
        return None
    _run(h, passes, marks, t0, hook)
    return h, t0, marks


def sc_food():
    return _plant_walk(0x14, passes=14)


def sc_treasure():
    return _plant_walk(0x13, passes=14)


def sc_key():
    return _plant_walk(0x1F, passes=14)


def sc_potion():
    return _plant_walk(0x16, passes=14)


def sc_door():
    return _plant_walk(0x11, passes=14, keys=3)


def sc_exit():
    """$1D/$1E is the level exit -- it reloads, which is the 271-frame stall."""
    return _plant_walk(0x1B, passes=6)


def isolated_call(h, addr, regs=None):
    """Call a routine INSIDE a running machine and put the machine back.

    h.call is built for one-shot measurement and leaves two things behind:

      * PC on a SENTINEL ($FE00), not where the game was.  Let the machine
        run on from there and it executes whatever bytes live at $FE00.
      * SP ten bytes low -- five of the six sentinel words are never claimed.

    Both are invisible if you stop and read memory, and both are fatal if the
    machine keeps running.  They were caught here, twice: first as a write to
    AY register $A1 (the game executing data at $F9C0 with the stack out from
    under it), then as effect $0F's driver going silent after 66 of its 85
    frames -- the game had fallen into the ROM at $192B with interrupts off,
    so the ISR stopped ticking the sound.  BOTH SYMPTOMS WERE IN THE SOUND
    TRACE AND NEITHER WAS A SOUND BUG."""
    pc0, sp0 = h.regs[PC], h.regs[SP]
    out = h.call(addr, regs=regs, interrupts=True)
    h.regs[PC], h.regs[SP] = pc0, sp0
    return out


def sc_tune1(frames_n=220):
    """$BBA7 -- one of the two three-voice TUNES.  Called from $8B88."""
    h = fresh(quiet_actors=True)
    step_until(h, (LOOP_TOP,), 20)
    t0 = arm(h)
    isolated_call(h, 0xBBA7)
    step_frames(h, frames_n)
    return h, t0, [(0, 'tune 1 started ($BBA7, data $6C82/$6CA0/$6CBC)')]


def sc_tune2(frames_n=220):
    """$BBBC -- the other tune.  Called from $8AE0."""
    h = fresh(quiet_actors=True)
    step_until(h, (LOOP_TOP,), 20)
    t0 = arm(h)
    isolated_call(h, 0xBBBC)
    step_frames(h, frames_n)
    return h, t0, [(0, 'tune 2 started ($BBBC, data $6B0E/$6B18/$6B46)')]


def sc_three(frames_n=60):
    """Three effects at once -- the only way to see the third voice, because
    $BA2B allocates one of THREE channel records at $BDB3 and one effect can
    only ever occupy one of them."""
    h = fresh(quiet_actors=True)
    step_until(h, (LOOP_TOP,), 20)
    t0 = arm(h)
    marks = []
    base = t0 // FRAME_T
    for eid in (0x06, 0x0B, 0x09):
        isolated_call(h, SND_DISPATCH, regs={'A': eid})
        marks.append((h.regs[T] // FRAME_T - base, f'triggered effect ${eid:02X}'))
        step_frames(h, 2)
    step_frames(h, frames_n)
    return h, t0, marks


def sc_death(passes=26):
    """Health 0002 and a class-0 actor in contact: the player dies in situ."""
    h = fresh()
    m = h.memobj.m
    m[P1 + 2], m[P1 + 3] = 0x00, 0x02
    px, py = m[P1], m[P1 + 1]
    one_actor(h, px + 8, py, 0x00)
    step_to_loop_top(h)
    t0 = arm(h)
    marks = []
    dead = {}

    def hook(hh, i):
        mm = hh.memobj.m
        if not dead and (mm[P1 + 2], mm[P1 + 3]) == (0, 0):
            dead['x'] = 1
            return 'health 0000 -- DEAD'
        return None
    _run(h, passes, marks, t0, hook)
    return h, t0, marks


SCENARIOS = collections.OrderedDict([
    ('idle', (sc_idle, 'standing still, no actors')),
    ('walk', (sc_walk, 'hold right, no actors')),
    ('wall', (sc_wall, 'hold up into the wall north of the start')),
    ('fire', (sc_fire, 'hold right + fire')),
    ('melee', (sc_melee, 'hold right into a pinned class-1 monster')),
    ('shotkill', (sc_shotkill, 'shoot a pinned class-1 monster')),
    ('damage', (sc_damage, 'a class-0 actor walks into the player')),
    ('food', (sc_food, 'walk onto a $14 food cell')),
    ('treasure', (sc_treasure, 'walk onto a $13 treasure cell')),
    ('key', (sc_key, 'walk onto a $1F key cell')),
    ('potion', (sc_potion, 'walk onto a $16 potion cell')),
    ('door', (sc_door, 'walk into a $11 door with keys')),
    ('death', (sc_death, 'health 0002, a class-0 actor kills him')),
    ('three', (sc_three, 'three effects at once: $06, $0B, $09')),
    ('tune1', (sc_tune1, 'the $BBA7 tune, three voices, 220 frames')),
    ('tune2', (sc_tune2, 'the $BBBC tune, three voices, 220 frames')),
])


_CACHE = {}


def collect(name):
    """Drive one scenario and decode it.  Memoised: `all` would otherwise
    re-simulate every scenario once per subcommand."""
    if name in _CACHE:
        return _CACHE[name]
    fn, desc = SCENARIOS[name]
    h, t0, marks = fn()
    ev, other, spk = decode(h.ports.writes, t0)
    _CACHE[name] = dict(name=name, desc=desc, events=ev, other=other,
                        speaker=spk, t_span=h.regs[T] - t0, marks=marks)
    return _CACHE[name]


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------
def cmd_inventory(args=()):
    print('=== REGISTER INVENTORY over every scenario')
    print('  (register, value) pairs counted; "other" is any non-AY port write')
    total = collections.Counter()
    per_reg_vals = collections.defaultdict(collections.Counter)
    rows = []
    for name in SCENARIOS:
        d = collect(name)
        regs = collections.Counter(r for _, _, r, _ in d['events'])
        for _, _, r, v in d['events']:
            total[r] += 1
            per_reg_vals[r][v] += 1
        oth = collections.Counter(p for _, p, _ in d['other'])
        rows.append((name, len(d['events']), d['t_span'] / FRAME_T,
                     sorted(regs), len(d['speaker']),
                     {('$%04X' % p): n for p, n in oth.items()}))
        for f, lbl in d['marks']:
            pass
    print(f'{"scenario":<10} {"writes":>7} {"frames":>8}  registers touched'
          f'                 speaker-bit changes')
    for name, n, fr, regs, spk, oth in rows:
        rs = ' '.join('R%d' % r for r in regs)
        print(f'{name:<10} {n:>7} {fr:>8.1f}  {rs:<34} {spk}'
              + (f'   other={oth}' if oth else ''))
    print('\n  register    name           writes   distinct values')
    for r in sorted(total):
        vals = per_reg_vals[r]
        vs = ' '.join('$%02X' % v for v in sorted(vals))
        if len(vs) > 74:
            vs = vs[:71] + '...'
        print(f'  R{r:<2} {REGNAME.get(r,""):<14} {total[r]:>8}   '
              f'{len(vals):>3}: {vs}')
    never = [r for r in range(14) if r not in total]
    print(f'\n  NEVER WRITTEN in any scenario: '
          + (' '.join('R%d' % r for r in never) if never else '(none)'))
    return total, per_reg_vals


def cmd_dump(args):
    name = args[0] if args else 'fire'
    n_max = int(args[1]) if len(args) > 1 else 200
    d = collect(name)
    print(f'=== PER-FRAME REGISTER DUMP: {name} -- {d["desc"]}')
    print(f'    {len(d["events"])} AY writes over {d["t_span"]/FRAME_T:.1f} '
          f'video frames; {len(d["speaker"])} speaker-bit changes')
    marks = collections.defaultdict(list)
    for f, lbl in d['marks']:
        marks[f].append(lbl)
    fr = frames(d['events'])
    print('  frame | writes (register=value), in the order the game made them')
    for f in list(fr)[:n_max]:
        s = ' '.join(f'R{r}=${v:02X}' for r, v in fr[f])
        print(f'  {f:>5} | {s}')
        for lbl in marks.get(f, []):
            print(f'        + {lbl}')
    for f in sorted(marks):
        if f not in fr:
            print(f'  {f:>5} | (no AY writes)   + ' + '; '.join(marks[f]))


def cmd_effects(args=()):
    """Trigger each effect id in situ and record what the driver then plays.

    $BA2B is the ONE entry point: 23 call sites, each `LD A,id` first.  Calling
    it does the voice allocation; the actual register writes are made by the
    per-frame tick $BADB over the following frames, so the isolation has to run
    the machine on afterwards -- calling and reading registers back proves
    nothing."""
    print('=== EACH EFFECT ID, TRIGGERED IN SITU  ($BA2B, A = id)')
    ids = list(range(0x00, 0x12))
    print(f'{"id":>4} {"call cost":>10} {"frames":>7} {"writes":>7}  '
          f'registers        first 3 frames of the dump')
    out = {}
    for eid in ids:
        h = fresh(quiet_actors=True)
        step_until(h, (LOOP_TOP,), 20)
        step_until(h, (LOOP_TOP,), 20)
        t0 = arm(h)
        idx, cost, _ = isolated_call(h, SND_DISPATCH, regs={'A': eid})
        # drive WHOLE FRAMES, not passes: some ids leave the main loop and
        # some block for a hundred frames, and the driver tick is per frame
        # anyway.
        step_frames(h, 60)
        ev, other, spk = decode(h.ports.writes, t0)
        fr = frames(ev)
        regs = sorted({r for _, _, r, _ in ev})
        head = []
        for f in list(fr)[:3]:
            head.append(f'{f}:' + ','.join(f'R{r}=${v:02X}' for r, v in fr[f]))
        span = (h.regs[T] - t0) / FRAME_T
        print(f'${eid:02X} {cost/FRAME_T:>10.1f}f {span:>7.1f} {len(ev):>7}  '
              f'{" ".join("R%d" % r for r in regs):<16} {" | ".join(head)[:78]}')
        out[eid] = dict(call_frames=cost / FRAME_T, writes=len(ev),
                        regs=regs, frames={str(k): v for k, v in fr.items()})
    return out


def cmd_table(args=()):
    """$BAB7: id*2 indexes a table of 16-bit offsets at $73DA (block B)."""
    h = fresh()
    m = h.memobj.m
    print('=== THE EFFECT TABLE at $73DA (block B, the 2,881-byte data block)')
    print('  $BAB7: HL = $73DA + 2*id; HL = ($73DA + peek16) -- an offset, not')
    print('  an address, so the whole table is position independent.')
    print('   id   offset   data at    first bytes')
    for eid in range(0x12):
        off = m[SND_TABLE + 2 * eid] | (m[SND_TABLE + 2 * eid + 1] << 8)
        addr = (SND_TABLE + off) & 0xFFFF
        by = ' '.join('%02X' % m[addr + i] for i in range(10))
        print(f'  ${eid:02X}   ${off:04X}    ${addr:04X}     {by}')


NOTE_TABLE = 0x6A4E             # $BD52: HL = $6A4E + 2*note -> 16-bit period


def note_table(m, n=64):
    return [m[NOTE_TABLE + 2 * i] | (m[NOTE_TABLE + 2 * i + 1] << 8)
            for i in range(n)]


def effect_rows(m, eid, limit=400):
    """The effect's own data, decoded by the model $BB35 implies.

        byte 0   high nibble = VOLUME 0..15
                 low  nibble = NOISE PERIOD / 2   (0 = no noise on this channel)
        byte 1   PITCH, and the 12-bit tone period is ((pitch + 1) & $FF) * 4

    THE 8-BIT WRAP IS NOT COSMETIC.  $BB71 does `INC A` on the pitch byte in
    A -- eight bits -- BEFORE $BB72 widens it into HL, so pitch $FF gives
    period 0, not 1024.  The first version of this model wrote (b1+1)*4 in
    Python and `verify` failed on exactly the 14 rows whose pitch is $FF
    (effects $0F and $11).  The recorded writes were right and the arithmetic
    was wrong: the same class of bug as the case study's DJNZ B=0 wrap.
        byte 0 == 0 ends the effect (the driver writes volume 0 and stops)
    """
    off = m[SND_TABLE + 2 * eid] | (m[SND_TABLE + 2 * eid + 1] << 8)
    addr = (SND_TABLE + off) & 0xFFFF
    rows = []
    for i in range(limit):
        b0, b1 = m[addr + 2 * i], m[addr + 2 * i + 1]
        if b0 == 0:
            break
        rows.append((b0 >> 4, (b0 & 15) * 2, (((b1 + 1) & 0xFF) * 4) & 0xFFF))
    return addr, rows


def cmd_verify(args=()):
    """THE GATE (manual 11.5): the model against the recorded writes, over the
    whole of every effect, not at one point.

    For each id the data at $73DA is decoded into (volume, noise period, tone
    period) rows and compared, row for row, with what the machine ACTUALLY put
    on the bus when that id was triggered.  Nothing is fitted."""
    print('=== MODEL vs RECORDED, every effect, every frame')
    print('  model: 2 bytes per 50 Hz frame, b0 = vol<<4 | noise/2,'
          ' TP = (b1+1)*4')
    m = fresh().memobj.m
    total = bad = 0
    print(f'{"id":>4} {"addr":>7} {"rows":>5} {"frames cmp":>11} {"ch":>3}'
          f'  verdict')
    for eid in range(0x12):
        addr, rows = effect_rows(m, eid)
        h = fresh(quiet_actors=True)
        step_until(h, (LOOP_TOP,), 20)
        step_until(h, (LOOP_TOP,), 20)
        t0 = arm(h)
        isolated_call(h, SND_DISPATCH, regs={'A': eid})
        step_frames(h, 2 * len(rows) + 20)
        ev, _, _ = decode(h.ports.writes, t0)
        fr = frames(ev)
        # which channel did the allocator hand it?  Take it from the trace.
        ch = None
        for f in fr:
            for r, v in fr[f]:
                if r in (0, 2, 4) and ch is None:
                    ch = r // 2
        got = []
        for f in fr:
            d = dict(fr[f])
            if ch is None or (2 * ch) not in d:
                continue
            tp = d[2 * ch] | ((d.get(2 * ch + 1, 0) & 0x0F) << 8)
            got.append((d.get(8 + ch), d.get(6), tp))
        n = min(len(rows), len(got))
        diff = [i for i in range(n) if rows[i] != got[i]]
        total += n
        bad += len(diff)
        verdict = ('MATCH' if not diff and len(got) >= len(rows)
                   else f'{len(diff)} differing rows')
        if len(got) < len(rows):
            verdict += f' (only {len(got)} of {len(rows)} frames observed)'
        print(f'${eid:02X} ${addr:04X} {len(rows):>5} {n:>11} {"ABC"[ch or 0]:>3}'
              f'  {verdict}')
        for i in diff[:3]:
            print(f'      row {i}: model {rows[i]}  recorded {got[i]}')
    print(f'\n  {total - bad}/{total} frames match the model.')
    return total, bad


def call_sites(m):
    """Every place the game asks for a sound.  $BA2B is the only entry point
    and A is always loaded immediately before, so the sites are found by byte
    pattern and then CONFIRMED by decoding the two bytes in front of the
    CALL/JP (manual 11.2): `3E nn` LD A,n, or `97` SUB A for id 0."""
    blob = bytes(m)
    out = []
    for op, kind in ((0xCD, 'CALL'), (0xC3, 'JP')):
        pat = bytes((op, 0x2B, 0xBA))
        i = blob.find(pat, 0x8000)
        while i >= 0:
            if blob[i - 2] == 0x3E:
                out.append((blob[i - 1], i - 2, kind))
            elif blob[i - 1] == 0x97:
                out.append((0x00, i - 1, kind))
            else:
                out.append((None, i, kind))
            i = blob.find(pat, i + 1)
    return sorted(out, key=lambda r: (255 if r[0] is None else r[0], r[1]))


def cmd_sites(args=()):
    print('=== WHO ASKS FOR WHICH SOUND  (LD A,id / CALL $BA2B)')
    m = fresh().memobj.m
    byid = collections.defaultdict(list)
    for eid, addr, kind in call_sites(m):
        byid[eid].append(f'${addr:04X}({kind})')
    _, rows0 = effect_rows(m, 0)
    for eid in range(0x12):
        _, rows = effect_rows(m, eid)
        sites = ' '.join(byid.get(eid, [])) or '-- NEVER TRIGGERED --'
        print(f'  ${eid:02X}  {len(rows):>3} frames  {sites}')


def cmd_switch(args=()):
    """THE SOUND SWITCH.  $BF21 reads the RAM byte $FFFD -- not the port."""
    h = fresh()
    m = h.memobj.m
    print('=== THE SOUND SWITCH: RAM $FFFD, tested at $BF21 (boot) and $9D0A')
    print("""
  $BF21  LD A,($FFFD) / OR A / LD A,$C9        ; $C9 = RET
  $BF27  JR z,$BF30
         ; NON-ZERO -> AY.  Blow away the BEEPER:
  $BF29  LD ($B8B5),A / LD ($B8CC),A / RET
  $BF30  ; ZERO -> BEEPER.  Blow away the AY:
         LD ($BADB),A   ; the per-frame AY tick
         LD ($BA01),A
         LD ($BBA7),A   ; the AY init (mixer $38 etc.)
         LD ($BBBC),A
         LD HL,$B92B / LD ($BA2C),HL / LD A,$C3 / LD ($BA2B),A
         ; $BA2B := JP $B92B -- the whole effect dispatcher is re-pointed at
         ; the beeper player.  RET.
""")
    patched = {a: m[a] for a in (0xB8B5, 0xB8CC, 0xBADB, 0xBA01, 0xBBA7,
                                 0xBBBC, 0xBA2B, 0xBA2C, 0xBA2D)}
    print('  in build/state_charsel.pkl:  $FFFD = $%02X' % m[0xFFFD])
    print('   ' + '  '.join(f'${a:04X}=${v:02X}' for a, v in patched.items()))
    ay = (m[0xB8B5] == 0xC9 and m[0xB8CC] == 0xC9 and m[0xBA2B] != 0xC3)
    print(f'  => the captured state is in {"AY" if ay else "BEEPER"} mode.')
    print("""
  WHERE $FFFD COMES FROM -- and it is NOT a menu option.  In the front end
  (block A, which the harness never runs) $C22D copies 100 bytes from $CE50
  to $61A8 and calls them.  Disassembled at $61A8:

      DI / EX AF,AF' / LD A,($C000) / EX AF,AF'      save the byte at $C000
      SUB A / LD ($C000),A                           write 0 there
      LD BC,$7FFD / LD A,$D1 / OUT (C),A             page RAM BANK 1 in
      LD ($C000),A                                   write $D1 into bank 1
      LD A,$D0 / OUT (C),A                           page RAM bank 0 back
      LD A,($C000)                                   read it back
      EX AF,AF' / LD ($C000),A / EX AF,AF' / RET     restore, return the byte

  $C23B  CP $D1 / LD A,1 / JR nz,$C242 / SUB A / LD ($FFFD),A

  It is a 128K PAGING TEST, and it has to live at $61A8 because $4000-$7FFF is
  the one window a 128K never pages.  If the write through bank 1 is still
  visible after paging bank 0 back ($D1 came back) there is no paging: 48K,
  $FFFD = 0, BEEPER.  If it is not, $7FFD paged: 128K, $FFFD = 1, AY.

  CONSEQUENCE FOR THE CLOCK: the game only ever reaches its AY code on a
  machine whose $7FFD paging works -- a 128K/+2/+3 -- and on that machine the
  AY is clocked at CPU/2 = 3,546,900 / 2 = 1,773,400 Hz.  A Melodik or Fuller
  box on a plain 48K answers $D1 and gets the beeper instead.
""")
    print(f'  Only zero/non-zero is ever tested ($BF21 OR A, $9D0A OR A,'
          f'\n  $C25F AND A, $C82D AND A), so the harness\'s stale $FFFD=$'
          f'{m[0xFFFD]:02X}\n  should drive exactly the same code as a real'
          f' 128K\'s $FFFD=$01.  MEASURED, not\n  argued:')

    def one(force):
        h = fresh(quiet_actors=True)
        if force is not None:
            h.memobj.m[0xFFFD] = force
        press(h, 'right', 'fire')
        step_until(h, (LOOP_TOP,), 20)
        t0 = arm(h)
        for _ in range(30):
            step_until(h, (LOOP_TOP,), 400)
        return decode(h.ports.writes, t0)
    a, b = one(None), one(0x01)
    print(f'    30 passes of right+fire, stale $2A : {len(a[0])} AY writes, '
          f'{len(a[2])} speaker changes')
    print(f'    the same, with $FFFD forced to $01 : {len(b[0])} AY writes, '
          f'{len(b[2])} speaker changes')
    print(f'    the two register traces are identical: {a[0] == b[0]}')
    print('    (forcing $FFFD to $00 in the CAPTURED state proves nothing --'
          '\n     $BF21 runs once at boot and the patches are already in place.)')


def cmd_stall(args=()):
    """WHERE THE PICKUP STALL ACTUALLY IS.  This corrects a note in the
    project's own record, which had $BA2B -- the sound dispatcher -- blocking
    for 103.8 video frames on an inventory pickup.

    It does not.  $BA2B has no wait in it at all (three DJNZ loops of three
    iterations and no HALT), `effects` measures its call cost as 0.0 frames
    for all eighteen ids, and the stall belongs to the ATTRIBUTE FILLER at
    $A2F8, which is a different subsystem that happens to run on the same
    pass."""
    print('=== WHERE THE 103.8-FRAME PICKUP STALL IS')
    print('  walk onto each interactive cell and time every pass:')
    for v, label in ((0x13, 'treasure'), (0x14, 'food'), (0x16, 'potion'),
                     (0x18, 'power-up'), (0x19, 'INVENTORY ITEM'),
                     (0x1F, 'key'), (0x2F, 'map sweep'), (0x11, 'door')):
        h = fresh(quiet_actors=True)
        m = h.memobj.m
        row, col = 12, 8
        m[0x8000 + row * 32 + col] = v
        m[0x8420], m[0x8421] = col * 4, (row - 2) * 4
        m[0x8428] = 3
        press(h, 'down')
        step_until(h, (LOOP_TOP,), 20)
        worst, hot = 0.0, None
        sim = h.sim
        regs, opc, mem = sim.registers, sim.opcodes, sim.memory
        fd, ia = h.frame_duration, h.int_active
        for i in range(12):
            t0 = regs[T]
            hist = collections.Counter()
            n = 0
            while n < 6_000_000:
                pc = regs[PC]
                if n and pc == LOOP_TOP:
                    break
                hist[pc] += 1
                if h.deck is not None and pc == TAPE_CALL_PC:
                    h._tape(); n += 1; continue
                if mem[pc] == 0x76 and regs[IFF]:
                    h._fast_halt(); n += 1; continue
                opc[mem[pc]]()
                if regs[IFF] and regs[T] % fd < ia:
                    sim.accept_interrupt(regs, mem, pc)
                n += 1
            d = (regs[T] - t0) / FRAME_T
            if d > worst:
                worst, hot = d, hist.most_common(4)
        tail = ''
        if worst > 6:
            tail = '   hot: ' + ' '.join(f'${p:04X}x{c}' for p, c in hot)
        print(f'  ${v:02X} {label:<15} worst pass {worst:>6.1f} frames{tail}')
    print('\n  $A2F8 is the attribute filler -- LD (HL),A / INC L / DJNZ, 13'
          '\n  bytes by 3 rows -- and $BA2B is nowhere in the histogram.')


def cmd_clock(args=()):
    """WHICH CLOCK.  Enumerate the evidence; do not assume 1,773,400.

    Three independent things are printed and only the first settles anything:
    the hardware the game demands before it will use the AY at all; the game's
    own chromatic note table, fitted; and what the recorded periods become
    under each candidate clock.
    """
    print('=== THE CLOCK')
    print("""
  1. WHAT HARDWARE THE AY PATH REQUIRES.  This is the decisive one, and it is
     not a frequency argument at all.  The front end runs a $7FFD PAGING TEST
     ($CE50, copied to $61A8 and called from $C238) and stores 1 or 0 in the
     RAM byte $FFFD; $BF21 then patches the beeper OUT of the game when it is
     non-zero, and the whole AY driver out when it is zero.  So the AY code can
     only ever run on a machine whose $7FFD paging works -- a 128K/+2/+3 -- and
     on those machines the AY-3-8912 is clocked at CPU/2:

           3,546,900 / 2 = 1,773,400 Hz.

     A Melodik or Fuller box on a plain 48K is NOT detected: it answers the
     paging test like a 48K and gets the beeper.  Run `aytrace.py switch`.
""")
    m = fresh().memobj.m
    tab = note_table(m)
    print("  2. THE GAME'S OWN NOTE TABLE at $6A4E (read by $BD52), 64 entries.")
    xs = [i for i in range(len(tab)) if i != 12]
    ys = [math.log2(tab[i]) for i in xs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
             / sum((x - mx) ** 2 for x in xs))
    inter = my - slope * mx
    resid = [(y - (inter + slope * x)) * 1200 for x, y in zip(xs, ys)]
    rms = (sum(r * r for r in resid) / len(resid)) ** 0.5
    print(f'     a CHROMATIC table: {slope*12:+.4f} semitones per entry'
          f' (exact = -1.0000),')
    print(f'     residual {rms:.1f} cents rms, '
          f'{max(abs(r) for r in resid):.1f} cents worst.  Entry 12 is the one')
    print(f'     outlier ($0D39 where the scale wants $0CB0) and is excluded.')
    print(f'     range: TP {max(tab)} .. {min(tab)}')
    print('')
    print('     under each candidate clock the table reads:')
    for lbl, clk in CANDIDATES:
        f0 = clk / (16 * tab[0])
        offs = [cents_off(clk / (16 * tab[i])) for i in xs]
        print(f'       {lbl:<16} entry 0 = {f0:8.2f} Hz = {note_name(f0):<6}'
              f'   whole table {sum(offs)/len(offs):+6.1f} cents off A440')
    print("""
     THE TABLE CANNOT PICK THE CLOCK, and it is worth saying why rather than
     quoting whichever number flatters the answer.  The table is
     TP_i = round(K * 2^(i/12)) and K = clock / (16 * f_reference), so EVERY
     candidate clock fits it exactly as well, with a different implied
     reference pitch.  What the table does prove is that the composer had a
     real 12-TET generator.  At 1,773,400 Hz the tune plays about a
     quarter-tone flat of A440 -- in tune with itself, which is all a listener
     can hear.
""")
    print('  3. THE RECORDED PERIODS, and both candidate frequencies.')
    per = collections.Counter()
    for name in SCENARIOS:
        d = collect(name)
        st = {}
        for _, f, r, v in d['events']:
            st[r] = v
            if r in (1, 3, 5):
                ch = r // 2
                tp = st.get(2 * ch, 0) | ((v & 0x0F) << 8)
                per[(ch, tp)] += 1
    print(f'     {len(per)} distinct (channel, period) pairs.  The ten most'
          f' written:')
    print(f'     {"ch":>3} {"TP":>5} {"n":>5}  '
          + '  '.join(f'{lbl:>20}' for lbl, _ in CANDIDATES))
    for (ch, tp), n in per.most_common(10):
        cols = []
        for _, clk in CANDIDATES:
            if tp:
                f = clk / (16 * tp)
                cols.append(f'{f:>10.1f} Hz {note_name(f):>8}')
            else:
                cols.append(f'{"period 0 (= 1)":>20}')
        print(f'     {"ABC"[ch]:>3} {tp:>5} {n:>5}  ' + '  '.join(cols))
    print('')
    print('     noise periods written; the noise clock is clock/(16*NP):')
    nz = collections.Counter()
    for name in SCENARIOS:
        for _, f, r, v in collect(name)['events']:
            if r == 6:
                nz[v] += 1
    for v, n in sorted(nz.items()):
        if v:
            print(f'       NP={v:<3} n={n:<4} ' + '   '.join(
                f'{lbl}: {clk/(16*v):>8.1f} Hz' for lbl, clk in CANDIDATES))
    print(f'       NP=0  n={nz.get(0,0)}    (the AY treats 0 as 1)')
    print("""
  CONCLUSION.  1,773,400 Hz, on the strength of point 1 and nothing else.
  Points 2 and 3 are consistent with it and cannot distinguish it -- which is
  exactly why the manual wants a REGISTER-LEVEL DIFFERENTIAL rather than a
  frequency round-trip: the port has to reproduce the PERIODS, and the clock
  only decides what they sound like.  Note also that a 128K frame is 70,908
  T-states against the 48K's 69,888, so a real 128K plays this trace at
  50.01 Hz where the harness measures 50.08 Hz: a 0.14% tempo difference,
  declared and not modelled.""")



NOTES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def _semi(f):
    return 12 * math.log2(f / 440.0) + 69


def note_name(f):
    n = int(round(_semi(f)))
    return f'{NOTES[n % 12]}{n // 12 - 1}'


def cents_off(f):
    s = _semi(f)
    return (s - round(s)) * 100


def cmd_save(args=()):
    """Write the reference trace.  THIS FILE IS THE GROUND TRUTH the port's
    differential runs against."""
    doc = dict(
        _format='gauntlet AY-3-8912 register trace, one entry per scenario',
        _source='the original Z80 under tools/harness.py, port tracer armed',
        _decode='OUT $FFFD selects a register, OUT $BFFD writes it; helper $BB96',
        _frame_t=FRAME_T,
        _cpu_hz=CPU_HZ,
        _ay_clock=CLK_128,
        _ay_clock_why=('the game only enables its AY path when the $7FFD '
                       'paging test at $61A8 says 128K, and a 128K clocks the '
                       'AY at CPU/2 = 1,773,400 Hz'),
        _fields=('events: [dt_tstates, frame, register, value] in write order, '
                 'dt and frame both relative to the start of the scenario'),
        _switch=dict(ram_byte=0xFFFD, zero='beeper', nonzero='AY',
                     patched_by=0xBF21),
        scenarios=collections.OrderedDict(),
        effects=collections.OrderedDict(),
    )
    for name in SCENARIOS:
        d = collect(name)
        doc['scenarios'][name] = dict(
            desc=d['desc'],
            frames=round(d['t_span'] / FRAME_T, 2),
            n_writes=len(d['events']),
            speaker_bit_changes=len(d['speaker']),
            marks=[[f, l] for f, l in d['marks']],
            events=[[dt, f, r, v] for dt, f, r, v in d['events']],
        )
        print(f'  {name:<10} {len(d["events"]):>5} writes  '
              f'{d["t_span"]/FRAME_T:>7.1f} frames')
    eff = cmd_effects()
    m = fresh().memobj.m
    sites = call_sites(m)
    for eid, v in eff.items():
        addr, rows = effect_rows(m, eid)
        v['data_addr'] = addr
        v['rows'] = rows            # (volume, noise period, tone period)/frame
        v['trigger_sites'] = ['$%04X %s' % (a, kind)
                              for i, a, kind in sites if i == eid]
    doc['effects'] = {('$%02X' % k): v for k, v in eff.items()}
    doc['note_table'] = dict(
        addr=NOTE_TABLE,
        note='16-bit AY tone periods, chromatic, read by $BD52 as $6A4E+2*note',
        periods=note_table(m))
    doc['driver'] = dict(
        tick=SND_TICK, tick_caller=0xA2A5, isr=0xA29F,
        dispatch=SND_DISPATCH, write_helper=0xBB96,
        effect_table=SND_TABLE, channels=3, channel_records=0xBDB3,
        row_model=('per 50 Hz frame and per active channel: b0 = vol<<4 | '
                   'noise/2, b1 = pitch, TP = ((pitch+1)&$FF)*4; b0 = 0 ends '
                   'the effect with a volume-0 write'),
        mixer=('R7 = $38 with bit (3+channel) CLEARED for any channel whose '
               'noise nibble is non-zero; the three tone-enable bits are '
               'always 0, so all three tones are always enabled'),
        envelope='R11/R12/R13 are NEVER written -- volume is per-frame software')
    with open(OUT, 'w') as f:
        json.dump(doc, f, separators=(',', ':'))
    print(f'\n  wrote {OUT}  ({os.path.getsize(OUT)} bytes)')


CMDS = collections.OrderedDict([
    ('inventory', cmd_inventory), ('dump', cmd_dump), ('effects', cmd_effects),
    ('table', cmd_table), ('sites', cmd_sites), ('verify', cmd_verify),
    ('switch', cmd_switch), ('stall', cmd_stall), ('clock', cmd_clock),
    ('save', cmd_save),
])


def main():
    args = sys.argv[1:] or ['inventory']
    if args[0] == 'all':
        for n, fn in CMDS.items():
            if n == 'dump':
                continue
            print()
            fn([])
        return
    fn = CMDS[args[0]]
    fn(args[1:])


if __name__ == '__main__':
    main()
