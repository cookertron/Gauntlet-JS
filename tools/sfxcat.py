#!/usr/bin/env python3
"""
sfxcat.py -- THE CATALOGUE OF SOUND EFFECTS: every trigger site, the parameter
it passes, what that plays, how long it lasts and what gates it.  Phase 11 of
PORTING-ZX-TO-JS.txt.  Companion to tools/aytrace.py, which owns the raw
per-frame register dump; this tool owns the TRIGGER side.

    python tools/sfxcat.py sites      the 23 trigger sites, static + dynamic
    python tools/sfxcat.py ay         all 18 effects, AY, isolated
    python tools/sfxcat.py diff       the register-level differential (482/482)
    python tools/sfxcat.py beeper     all 18 effects, 48K beeper, isolated
    python tools/sfxcat.py chan       allocation, stealing, music pre-emption
    python tools/sfxcat.py gates      what gates each effect, by driving
    python tools/sfxcat.py pause      the blocking pause, both sound modes
    python tools/sfxcat.py all

=============================================================================
THE PARAMETER IS A, AND $BA2B IS THE ONLY ENTRY
=============================================================================
Every effect in the game is `LD A,id / CALL $BA2B` (twice `JP $BA2B`, once
`SUB A` for id 0).  There are 23 such sites and the id space is 0..17.

An earlier write-up gave "$A610 LD DE,$25 / JP $B807" as the example of a
sound trigger.  IT IS NOT ONE.  $B807 is the packed-BCD SCORE ADD --
`LD A,(IX+6) / ADD A,E / DAA / LD (IX+6),A / ...` -- which NOTES-engine.md
already names as daaAdd.  Nothing anywhere passes a sound parameter in DE.

=============================================================================
THE SWITCH: ONE ENTRY, TWO DRIVERS
=============================================================================
Block A's 100-byte probe at $CE50 (copied to $61A8 and run there, $C22D) does
a $7FFD paging test and $C242 stores 1 (128K) or 0 (48K) at RAM $FFFD.  The
game's $BF21, called once at boot from $BEB9, then patches itself:

    ($FFFD) != 0   $B8B5 := $C9    kill the beeper tune entry
                   $B8CC := $C9    kill the beeper noise routine
    ($FFFD) == 0   $BADB,$BA01,$BBA7,$BBBC := $C9   kill the WHOLE AY driver
                   $BA2B := JP $B92B                 the beeper dispatcher

So BOTH paths are real and the id means the same thing in both.  $BF21 is a
ONE-SHOT: $BEDF copies $1CE bytes of data over $BE32..$BFFF once it has run,
which is why build/live_cs.bin has graphics at $BF21.

CAUTION: the harness never runs the real menu, and RAM $FFFD is a STALE $2A
left by the BASIC loader stub -- the same byte as the documented $FFFF sprite
bug.  build/state_charsel.pkl is therefore in AY mode BY ACCIDENT.  A real 48K
tape load leaves 0 there and the shipped game is a BEEPER game.
"""
import collections
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, R, A as rA,          # noqa: E402
                     TAPE_CALL_PC, FRAME_T, CPU_HZ)
from keyprobe import KEYS, keymask                                 # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')
STATIC = os.path.join(ROOT, 'build', 'image.bin')
LOOP_TOP = 0x8503
SFX = 0xBA2B                       # the universal entry
TICK = 0xBADB                      # the AY 50 Hz service, called from the ISR
STEP = 0xB8FB                      # the beeper's per-PASS arpeggio step
NOISE = 0xB8CC                     # the beeper's per-SPRITE noise toggle
TABLE = 0x73DA                     # the AY effect-pointer table, 18 entries
CHAN = (0xBDB3, 0xBDB7, 0xBDBB)    # ptr lo, ptr hi, step, id
MASK = 0xBDC0
TUNE = 0xBDE9
P1 = 0x8420

# site -> (id, what it is).  Every site confirmed at an instruction boundary
# by disassembling the window that ends on it, and every id read off the
# `LD A,n` (or `SUB A`) immediately in front.
SITES = [
    (0x8CAD,  4, 'the player FIRES a shot'),
    (0x8FC0,  5, 'a shot DETONATES (it hit a potion or an item)'),
    (0x9089,  8, 'a shot HITS A PLAYER'),
    (0x942E, 15, 'the player DIES (his keys/potions are dropped first)'),
    (0x946A, 13, 'the 2nd player is REFUSED the join (not on dungeon 1)'),
    (0x9757, 16, 'HURRY UP phase 2 -- $84B8 reached $8C'),
    (0x9784, 14, 'HURRY UP phase 1 -- $84B8 reached $17'),
    (0x9D24, 16, 'the MESSAGE fanfare, inside the blocking pause'),
    (0xA4DA,  3, 'the player MATERIALISES ((IX+13) wraps $FF->0)'),
    (0xA532, 13, 'FIRE pressed with NO POTION left'),
    (0xA57B,  5, 'a POTION is used -> detonation'),
    (0xA63E,  0, 'a class-0 ghost touches the player, on HIS move'),
    (0xA6AC,  6, 'the player reaches the EXIT ($36/$37/$38)'),
    (0xA6F2, 14, 'a DOOR opens ($11/$12), one key spent'),
    (0xA783,  4, 'POWER-UP picked up ($18)'),
    (0xA79E,  7, 'KEY picked up ($1F)'),
    (0xA7C5, 17, 'anything else picked up: $13 $14 $15 $16 $17 $19..$1E $31 $32'),
    (0xA7FE,  9, 'the $2F MAP SWEEP'),
    (0xAEFC,  0, "a class-0 ghost touches the player, on THE ACTOR's move"),
    (0xAF1B, 10, 'a non-class-0 monster ATTACKS the player'),
    (0xAF44,  2, 'the class-5 DRAIN tick'),
    (0xB0D3, 12, 'a generator SPAWNS a monster'),
    (0xB212, 11, 'TELEPORT ($30 pad; $84AC = 0 here, so unreachable)'),
]


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def fresh(quiet_actors=False):
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    if quiet_actors:
        h.memobj.m[0x8496] = 0
    return h


def to_beeper(h):
    """Put the machine in the state a 48K boot leaves it in.

    Restores $BF21's own 39 bytes and the two bytes its AY arm patched, pokes
    ($FFFD)=0 and lets THE GAME'S OWN CODE re-patch itself.  Declared: this
    restores original tape bytes, it invents none.
    """
    img = open(STATIC, 'rb').read()
    h.memobj.m[0xBF21:0xBF48] = img[0xBF21:0xBF48]
    h.memobj.m[0xB8B5] = img[0xB8B5]
    h.memobj.m[0xB8CC] = img[0xB8CC]
    h.memobj.m[0xFFFD] = 0
    h.call(0xBF21)
    return h


def walk(h, npass, hooks):
    """Step npass main-loop passes, logging PC hooks and the per-pass cost."""
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    ev, costs = [], []
    done, n, tp = 0, 0, h.sim.registers[T]
    while n < 60_000_000:
        pc = regs[PC]
        if pc in hooks:
            sp = regs[SP]
            ev.append((done + 1, hooks[pc], mem[sp] | (mem[sp + 1] << 8),
                       regs[rA], regs[T]))
        if n and pc == LOOP_TOP:
            costs.append((regs[T] - tp) / FRAME_T)
            tp = regs[T]
            done += 1
            if done >= npass:
                return ev, costs
        if h.deck is not None and pc == TAPE_CALL_PC:
            h._tape(); n += 1; continue
        if mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt(); n += 1; continue
        ops[mem[pc]]()
        if regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
        n += 1
    raise RuntimeError('runaway')


def ay_pairs(writes):
    """(register, value) from the raw port trace: $FFFD selects, $BFFD data."""
    out, sel = [], None
    for _, p, v in writes:
        if p == 0xFFFD:
            sel = v
        elif p == 0xBFFD:
            out.append((sel, v))
    return out


def speaker_edges(writes):
    out, last = [], None
    for t, p, v in writes:
        if (p & 0xFF) == 0xFE:
            lv = (v >> 4) & 1
            if lv != last:
                out.append((t, lv))
                last = lv
    return out


def stream(mem, eid):
    """The 2-byte steps of effect `eid`.  $73DA + 2*id holds an OFFSET from
    $73DA, so the whole thing is position independent."""
    off = mem[TABLE + 2 * eid] | (mem[TABLE + 2 * eid + 1] << 8)
    p = (TABLE + off) & 0xFFFF
    st = []
    while mem[p]:
        st.append((mem[p], mem[p + 1]))
        p += 2
    return (TABLE + off) & 0xFFFF, st, p


def model(mem, eid, ch=0):
    """An INDEPENDENT model of $BADB/$BB35: what registers a lone effect on
    channel `ch` should write on each 50 Hz service call.  Written from the
    format, not from the measured dump, so agreement is evidence."""
    _, st, _ = stream(mem, eid)
    out = []
    for i in range(len(st) + 1):
        f, mixer = [], 0x38
        if i < len(st):
            v, t = st[i]
            f.append((8 + ch, v >> 4))
            lo = v & 0x0F
            if lo:
                mixer &= ~(1 << (3 + ch)) & 0xFF
            f.append((6, lo * 2))
            tp = 0 if t == 0xFF else (t + 1) * 4
            f.append((ch * 2, tp & 0xFF))
            f.append((ch * 2 + 1, tp >> 8))
        else:
            f.append((8 + ch, 0))
        f.append((7, mixer & 0x38))
        out.append(f)
    return out


def chan_snapshot(h):
    m = h.memobj.m
    return ([(m[c] | (m[c + 1] << 8), m[c + 2], m[c + 3]) for c in CHAN],
            m[MASK])


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
def cmd_sites(_=()):
    print('=== THE 23 TRIGGER SITES.  Parameter: A.  Entry: $BA2B, always.')
    m = open(os.path.join(ROOT, 'build', 'live_cs.bin'), 'rb').read()
    ok = 0
    for pc, eid, what in SITES:
        op = 'CALL' if m[pc] == 0xCD else 'JP  '
        pre = ('LD A,%d' % m[pc - 1]) if m[pc - 2] == 0x3E else 'SUB A'
        good = (m[pc + 1] == 0x2B and m[pc + 2] == 0xBA)
        ok += good
        print(f'  ${pc:04X}  {pre:<8} {op} $BA2B   id {eid:2d}  {what}')
    print(f'  ({ok}/{len(SITES)} confirmed to be a real CALL/JP $BA2B)')
    ids = sorted({e for _, e, _ in SITES})
    print(f'\n  ids reachable from a trigger: {ids}')
    print('  id 1 has NO trigger site: it is reached ONLY through the $BAB2')
    print('  LD A,R coin, which turns a requested id 0 into 0 or 1, 50/50.')
    print('\n  DYNAMIC SCAN (hook on $BA2B, driven play):')
    for name, keys, n in (('idle', [], 40), ('down 60', ['Q'], 60),
                          ('right 60', ['D'], 60), ('left 60', ['S'], 60),
                          ('up 60', ['1'], 60)):
        h = fresh()
        for k in keys:
            sel, bit = KM[k]
            h.ports.press(sel, keymask(bit))
        walk(h, 1, {})
        ev, _ = walk(h, n, {SFX: 'sfx'})
        s = ', '.join(f'pass {p} id {a} from ${c-3:04X}' for p, _, c, a, _ in ev)
        print(f'    {name:9s} {s or "NO SOUND AT ALL"}')
    print('\n  NOTE THE NEGATIVE: 60 passes of walking in each of the four')
    print('  directions produce ZERO sound.  Gauntlet has NO FOOTSTEP EFFECT,')
    print('  so nothing in this game is position-gated and no cadence is')
    print('  locked to walking speed.')


def cmd_ay(_=()):
    h = fresh()
    mem = h.memobj.m
    print('=== THE 18 AY EFFECTS, isolated ($BA2B then $BADB frame by frame)')
    print('  a step is 2 bytes: (volume<<4 | noise_period/2, tone_period_byte)')
    print('  TP = (byte+1)*4;  byte $FF -> TP 0, i.e. NOISE ONLY.')
    print('  ONE STEP PER 50 Hz FRAME.  Clock 1,773,400 Hz (128K: the AY is')
    print('  only ever enabled when the $7FFD probe says 128K).')
    print(f'\n{"id":>3} {"data":>7} {"steps":>6} {"ms":>6}  volume  tone Hz    noise')
    for eid in range(18):
        p, st, end = stream(mem, eid)
        vols = [v >> 4 for v, _ in st]
        tps = [0 if t == 0xFF else (t + 1) * 4 for _, t in st]
        hz = [1773400 / (16 * t) for t in tps if t]
        nps = sorted({(v & 15) * 2 for v, _ in st if v & 15})
        rng = (f'{min(hz):.0f}..{max(hz):.0f}' if hz else 'none (pure noise)')
        print(f'{eid:3d}  ${p:04X} {len(st):6d} {len(st)*20:6d}  '
              f'{min(vols):2d}..{max(vols):2d}  {rng:12s} '
              f'NP {nps if nps else "-"}')
    # the streams partition their region exactly -- a wrong format would not
    starts = sorted(stream(mem, i)[0] for i in range(18))
    ends = {stream(mem, i)[0]: stream(mem, i)[2] for i in range(18)}
    # each stream ends on its own $00 terminator, so the next one starts at
    # end+1.  A wrong reading of the step size would leave gaps or overlaps.
    gaps = [(a, b) for a, b in zip(starts, starts[1:]) if ends[a] + 1 != b]
    print(f'\n  the 18 streams tile ${starts[0]:04X}..${ends[starts[-1]]:04X} '
          f'with {len(gaps)} gaps or overlaps '
          f'(0 = the format reading is self-validating)')


def cmd_diff(_=()):
    """The manual's strongest AY check: a register-level differential."""
    h = fresh()
    st = h.save_state()
    mem = h.memobj.m
    print('=== REGISTER-LEVEL DIFFERENTIAL: independent model vs the real Z80')
    tot = ok = 0
    for eid in range(18):
        pred = model(mem, eid)
        h.load_state(st)
        h.ports.record_writes = False
        h.call(0xBA01)
        h.call(SFX, regs={'A': eid})
        got = []
        h.ports.record_writes = True
        for _ in range(len(pred)):
            h.ports.writes = []
            h.call(TICK)
            got.append(ay_pairs(h.ports.writes))
        h.ports.record_writes = False
        bad = [i for i, (a, b) in enumerate(zip(pred, got)) if a != b]
        tot += len(pred)
        ok += len(pred) - len(bad)
        print(f'  {"OK " if not bad else "XX "}id {eid:2d}: '
              f'{len(pred)-len(bad)}/{len(pred)} per-frame register dumps match')
    print(f'\n  TOTAL {ok}/{tot}')


def cmd_beeper(_=()):
    h = to_beeper(fresh())
    m = h.memobj.m
    print('=== THE 48K BEEPER BRANCH')
    print(f'  $BA2B..2D = {m[SFX:SFX+3].hex()} (C3 2B B9 = JP $B92B)   '
          f'$BADB=${m[0xBADB]:02X} $BA01=${m[0xBA01]:02X} '
          f'$BBA7=${m[0xBBA7]:02X} $BBBC=${m[0xBBBC]:02X}  ($C9 = RET)')
    st = h.save_state()
    h.load_state(st)
    h.ports.record_writes = True
    h.ports.writes = []
    _, dt, _ = h.call(STEP)
    print(f'\n  $B8FB with nothing playing: {dt} T = {dt/CPU_HZ*1000:.2f} ms of'
          f' DEAD DELAY ($B901, DEC HL from $01EB).  It runs EVERY PASS on'
          f'\n  every machine, so the game costs the same with sound or'
          f' without.')
    h.ports.record_writes = False
    print('\n  ids with an arpeggio table ($B995..$BA00), ONE STEP PER PASS:')
    print(f'  {"id":>3} {"steps":>6} {"ms":>7}   (half-cycles @ measured Hz)')
    for eid in range(18):
        h.load_state(st)
        h.call(SFX, regs={'A': eid})
        n = m[0x84CF]
        if not n:
            continue
        rows, tot = [], 0
        h.ports.record_writes = True
        for _ in range(n):
            h.ports.writes = []
            _, dt, _ = h.call(STEP)
            e = speaker_edges(h.ports.writes)
            tot += dt
            per = (e[-1][0] - e[0][0]) / (len(e) - 1) if len(e) > 2 else 0
            rows.append((len(e), CPU_HZ / (2 * per) if per else 0))
        h.ports.record_writes = False
        s = ' '.join(f'{c}@{f:.0f}Hz' for c, f in rows)
        print(f'  {eid:3d} {n:6d} {tot/CPU_HZ*1000:7.1f}   {s}')
    print('\n  half-period model, checked against the edge train for all 11'
          '\n  delay values the tables use:  T = 17*delay + 31')
    print('\n  ids with the NOISE RAMP instead ($84D2 threshold, $B8E2 patched'
          '\n  to INC A or DEC A; $B8CC plays it: LD A,R / CP threshold ->'
          '\n  toggle bit 4 of $FE.  127 calls either way):')
    for eid in range(18):
        h.load_state(st)
        h.call(SFX, regs={'A': eid})
        if m[0x84CF] or not m[0x84D2]:
            continue
        d = 'INC A (sparse -> dense)' if m[0xB8E2] == 0x3C else 'DEC A (dense -> sparse)'
        print(f'  {eid:3d}  $84D2 := {m[0x84D2]:3d}, $B8E2 := {d}')
    silent = []
    for eid in range(18):
        h.load_state(st)
        h.call(SFX, regs={'A': eid})
        if not m[0x84CF] and not m[0x84D2]:
            silent.append(eid)
    print(f'\n  SILENT on a 48K (the $B92B dispatcher has no arm for them): {silent}')
    print('\n  the two BLOCKING beeper tunes ($9D0A picks them instead of the'
          '\n  AY pause loop; each LDIRs 318 bytes to $C000 and RUNS them):')
    for addr, tag in ((0xB8B0, 'message  ($6740)'), (0xB8B5, 'level start ($685D)')):
        h.load_state(st)
        h.ports.record_writes = True
        h.ports.writes = []
        _, dt, _ = h.call(addr, limit=8_000_000, interrupts=True)
        e = speaker_edges(h.ports.writes)
        h.ports.record_writes = False
        print(f'    ${addr:04X} {tag}: {dt/FRAME_T:6.1f} video frames '
              f'({dt/CPU_HZ:.2f} s), {len(e)} speaker edges')


def cmd_chan(_=()):
    h = fresh()
    st = h.save_state()
    print('=== ALLOCATION, STEALING AND PRE-EMPTION (all measured)')

    print('\n  the id-0 coin, R enumerated over 0..127:')
    seen = collections.Counter()
    for r in range(128):
        h.load_state(st)
        h.call(0xBA01)
        h.sim.registers[R] = r
        h.call(SFX, regs={'A': 0})
        seen[h.memobj.m[0xBDB6]] += 1
    print(f'    id actually stored: {dict(seen)}  -> 50/50 between 0 and 1')

    def show(tag):
        recs, mask = chan_snapshot(h)
        print(f'    {tag:34s} mask=${mask:02X}  ' + '  '.join(
            f'ch{i}: id{d:2d} step{s:3d} ptr${p:04X}'
            for i, (p, s, d) in enumerate(recs)))

    print('\n  filling, then a 4th effect with all three busy:')
    h.load_state(st)
    h.call(0xBA01)
    show('after $BA01')
    for eid in (4, 7, 17):
        h.call(SFX, regs={'A': eid})
        show(f'after id {eid}')
    for _ in range(2):
        h.call(TICK)
    show('two service frames later')
    h.call(SFX, regs={'A': 11})
    show('id 11 (the 4th) -> steals')

    print('\n  the same id retriggered while it is playing:')
    h.load_state(st)
    h.call(0xBA01)
    h.call(SFX, regs={'A': 15})
    for _ in range(10):
        h.call(TICK)
    show('id 15, 10 frames in')
    h.call(SFX, regs={'A': 15})
    show('id 15 again -> RESTARTS')

    print('\n  which channel is stolen, ages 27/7/2:')
    h.load_state(st)
    h.call(0xBA01)
    h.call(SFX, regs={'A': 15})
    for _ in range(20):
        h.call(TICK)
    h.call(SFX, regs={'A': 11})
    for _ in range(5):
        h.call(TICK)
    h.call(SFX, regs={'A': 6})
    for _ in range(2):
        h.call(TICK)
    show('three busy')
    h.call(SFX, regs={'A': 4})
    show('id 4 -> steals the OLDEST')

    print('\n  MUSIC OUTRANKS EFFECTS ($BA31 tests $BDE9, the tune id):')
    for tune, name in ((0, 'no tune'), (1, 'tune 1 = the level-start fanfare'),
                       (2, 'tune 2 = the treasure room')):
        h.load_state(st)
        h.call(0xBA01)
        h.memobj.m[TUNE] = tune
        for eid in (4, 7, 11):
            h.call(SFX, regs={'A': eid})
        recs, mask = chan_snapshot(h)
        note = ('every effect DROPPED' if mask == 0 else
                'every effect FORCED onto channel 0' if mask == 0x09 else
                'normal three-channel allocation')
        print(f'    $BDE9={tune} {name:34s} mask=${mask:02X}  {note}')


def cmd_gates(_=()):
    print('=== WHAT GATES EACH EFFECT (driven, not read)')
    print('\n  1. EVENT-GATED: plant a cell, walk into it, watch $BA2B.')
    st = pickle.load(open(STATE, 'rb'))
    print(f'  {"cell":>5}  {"sound(s)":40s} {"worst pass":>11}')
    for cell in list(range(0x11, 0x21)) + [0x2F, 0x30, 0x31, 0x32, 0x36]:
        h = Harness()
        h.load_state(st)
        m = h.memobj.m
        m[0x8496] = 0
        row, col = 12, 8
        m[0x8000 + row * 32 + col] = cell
        m[P1], m[P1 + 1] = col * 4, (row - 2) * 4
        m[P1 + 8], m[P1 + 9] = 3, 2
        sel, bit = KM['Q']
        h.ports.press(sel, keymask(bit))
        walk(h, 1, {})
        ev, costs = walk(h, 14, {SFX: 'sfx'})
        s = ', '.join(f'id {a} @${c-3:04X}' for _, _, c, a, _ in ev) or '-'
        print(f'   ${cell:02X}  {s:40s} {max(costs):9.1f}f')

    print('\n  2. COUNTER-GATED: id 3 fires only when (IX+13) has ONE value.')
    h = Harness()
    fired = []
    for v in range(256):
        h.load_state(st)
        h.memobj.m[0x842D] = v
        r = h.sim.registers
        sp = r[SP]
        for s in reversed(h.SENTINELS):
            sp = (sp - 2) & 0xFFFF
            h.memobj.m[sp] = s & 0xFF
            h.memobj.m[sp + 1] = s >> 8
        r[SP] = sp
        r[8], r[9] = 0x84, 0x20
        r[PC] = 0xA4B5
        h.run_until(set(h.SENTINELS) | {SFX}, limit=200_000, interrupts=False)
        if r[PC] == SFX:
            fired.append((v, r[rA]))
    print(f'    (IX+13) values that reach $BA2B: {fired}')
    print('    $96A7 (the placement routine) is the only writer of $FF there,')
    print('    so id 3 is exactly one chime per materialise.')

    print('\n  3. TIME-GATED: the hurry-up pair, ids 14 then 16.')
    print('    $84B8 is incremented at ONE site, $B6E9, behind $B6DA\'s')
    print('    ($8497 & $C0) divider -- once per 64 VIDEO FRAMES, not per pass.')
    print('    Read at ONE site, $9720 ($971B).  $17 -> sweep + id 14 (latch')
    print('    $847D bit 0); $8C -> sweep + id 16 (latch bit 1).  Reset to 0')
    print('    by $8C90 (shot), $9081 (shot hit), $A7D0 (any pickup) and')
    print('    $AEE5 (a monster touch).')
    h = fresh(quiet_actors=True)
    walk(h, 1, {})
    _, _ = walk(h, 120, {})
    print(f'    measured: $84B8 after 120 idle passes = {h.memobj.m[0x84B8]}'
          f'  (~{120*4/64:.0f} expected ticks)')


def cmd_pause(_=()):
    print('=== THE BLOCKING PAUSE.  $BA2B DOES NOT BLOCK.')
    h = fresh()
    st = h.save_state()
    for eid in (0, 4, 16, 17):
        h.load_state(st)
        _, dt, _ = h.call(SFX, regs={'A': eid})
        print(f'  $BA2B(id {eid:2d}) in isolation: {dt} T = '
              f'{dt/FRAME_T:.3f} video frames')
    print('\n  the stall is $9D2D, reached from $8550 CALL $9CD7 -> $9D01 when')
    print('  $847D bit 2 is set.  Each iteration is CALL $9432 / HALT / HALT.')
    for bit5, tag in ((0, 'bit5 clear: message  ($9D22 plays id 16, B=$32=50)'),
                      (1, 'bit5 set:   level start ($9D1E no sfx, B=$82=130)')):
        h.load_state(st)
        walk(h, 1, {})
        m = h.memobj.m
        m[0x847D] = (m[0x847D] | 0x04 | (0x20 if bit5 else 0)) & (0xFF if bit5 else ~0x20 & 0xFF)
        ev, costs = walk(h, 3, {SFX: 'sfx', 0x9D2D: 'iter'})
        it = sum(1 for e in ev if e[1] == 'iter')
        print(f'  {tag}\n      pass costs {["%.1f" % c for c in costs]} video '
              f'frames, {it} iterations')
    print('\n  the AY numbers 103.8 and 263.8 are exactly the "inventory pickup')
    print('  stalls 103.8 frames" and "a level reload costs 271.8" already in')
    print('  the notes -- but the stall is NOT inside $BA2B.  On a 48K the same')
    print('  two events cost 72.1 and 210.0 frames instead (see `beeper`),')
    print('  so PAUSE LENGTH DEPENDS ON THE SOUND MODE.')


CMDS = {'sites': cmd_sites, 'ay': cmd_ay, 'diff': cmd_diff,
        'beeper': cmd_beeper, 'chan': cmd_chan, 'gates': cmd_gates,
        'pause': cmd_pause}


def main():
    args = sys.argv[1:]
    if not args or args[0] == 'all':
        for name in ('sites', 'ay', 'diff', 'beeper', 'chan', 'gates', 'pause'):
            CMDS[name](args[1:])
            print()
        return
    CMDS[args[0]](args[1:])


if __name__ == '__main__':
    main()
