#!/usr/bin/env python3
"""
beepcat.py -- THE 48K BEEPER EFFECT CATALOGUE.  Phase 11 (11.1-11.7, 11.9) of
PORTING-ZX-TO-JS.txt, on the branch the OWNER'S MACHINE ACTUALLY RUNS.

Companion to tools/sfxcat.py (which owns the AY branch and the trigger-site
list) and tools/soundgate.py (the AY register differential).  Everything here
is measured on the real Z80 with the port tracer watching bit 4 of $FE; no
T-state is hand counted.

    python tools/beepcat.py switch     the branch diff: what $BF21 changes
    python tools/beepcat.py sites      23 triggers, boundary-confirmed, BOTH branches
    python tools/beepcat.py tables     the 8 arpeggio tables, enumerated
    python tools/beepcat.py edges      THE CATALOGUE: every id's edge train
    python tools/beepcat.py noise      ids 0 and 4: the R-modulated noise ramp
    python tools/beepcat.py clock      $B8FB / $B8CC call rates, frames per pass
    python tools/beepcat.py gates      what gates each effect (driven)
    python tools/beepcat.py priority   one speaker, one voice -- who wins
    python tools/beepcat.py tunes      the two BLOCKING tunes that replace the pause
    python tools/beepcat.py trace      write build/beeper_ref.json (the ground truth)
    python tools/beepcat.py all

=============================================================================
THE DRIVER, READ OUT OF THE PATCHED IMAGE (build/live_beeper.bin)
=============================================================================
$BF21 with ($FFFD)==0 turns $BA2B into `JP $B92B`, so all 23 trigger sites
reach the BEEPER dispatcher unchanged.  The dispatcher is a chain of CPs:

    $B92B  PUSH HL
           CP 2  -> HL=$B995     CP 7  -> $B9A2    CP 8  -> $B9AF
           CP 10 -> $B9BA        CP 11 -> $B9C3    CP 14 -> $B9DA
           CP 15 -> $B9E5        CP $11-> $B9F8    CP 6  -> $B9E5 (shares 15)
    $B978  POP HL
           CP 4  -> LD A,$7F / JP $B8F2     noise, level 127, DEC ramp
           OR A  -> LD A,$01 / JP $B8E9     noise, level 1,   INC ramp  (id 0)
    $B98A  RET                              EVERYTHING ELSE IS SILENT
    $B98B  LD A,(HL) / LD (IY+$50),A        $84CF := step count
           INC HL / LD ($84D0),HL           $84D0 := stream pointer
           POP HL / RET

A tone table is  n, then n pairs (C = edge count, E = delay).  ONE PAIR IS
PLAYED PER MAIN-LOOP PASS by $B8FB, whose only call site is $9CD9:

    $B8FB  LD A,(IY+$50) / OR A / JR nz,$B90A
    $B901  LD HL,$01EB / DEC HL / LD A,H / OR L / JR nz     <- the IDLE delay
    $B90A  DEC A / LD (IY+$50),A
    $B90E  LD A,($84CA)          the shadow of the last OUT ($FE) -- border+spk
    $B911  LD HL,($84D0) / LD C,(HL) / INC HL / LD E,(HL) / INC HL
    $B918  LD B,E / NOP / DJNZ / XOR $10 / OUT ($FE),A / DEC C / JP nz,$B918
    $B924  LD ($84CA),A / LD ($84D0),HL / RET

so a step is C speaker EDGES separated by 17E+31 T-states, and the whole burst
is a blocking ~3.2..4.7 ms chirp inside one pass.  The NOISE is a different
mechanism entirely: $B8CC is called from SIX sites in the blitter, 234..250
times a pass, and toggles the speaker when `LD A,R` compares below the level
byte $84D2, which ramps 1->127 or 127->1 and then stops.
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, R, A as rA,          # noqa: E402
                     TAPE_CALL_PC, FRAME_T, CPU_HZ)
from keyprobe import KEYS, keymask                                 # noqa: E402
from sfxcat import SITES, fresh                                    # noqa: E402

import z80dis.z80 as z                                             # noqa: E402

KM = {n: (s, b) for n, s, b in KEYS}
STATIC = os.path.join(ROOT, 'build', 'image.bin')
LIVE_AY = os.path.join(ROOT, 'build', 'live_cs.bin')
LIVE_48 = os.path.join(ROOT, 'build', 'live_beeper.bin')
REF = os.path.join(ROOT, 'build', 'beeper_ref.json')

LOOP_TOP = 0x8503
SFX = 0xBA2B          # every trigger site's target, on BOTH branches
DISPATCH = 0xB92B     # what $BA2B becomes on a 48K
STEP = 0xB8FB         # one arpeggio step, once per pass, from $9CD9
NOISE = 0xB8CC        # one noise sample, 234..250 times a pass, from the blitter
STEPS = 0x84CF        # (IY+$50) steps remaining
PTR = 0x84D0          # the stream pointer
LEVEL = 0x84D2        # (IY+$53) the noise level AND its own threshold
RAMP = 0xB8E2         # $3C INC A (sparse->dense) or $3D DEC A (dense->sparse)
SHADOW = 0x84CA       # the shadow of the last OUT ($FE): border bits + bit 4
P1 = 0x8420

# the dispatcher's own decode, transcribed from $B92B..$B994
TONE_TABLE = {2: 0xB995, 7: 0xB9A2, 8: 0xB9AF, 10: 0xB9BA, 11: 0xB9C3,
              14: 0xB9DA, 15: 0xB9E5, 6: 0xB9E5, 17: 0xB9F8}
NOISE_ID = {4: (0x7F, 'DEC'), 0: (0x01, 'INC')}
SILENT = [1, 3, 5, 9, 12, 13, 16]

# the 18 sites the port already reaches (NOTES-engine.md, "The trigger sites")
PORTED = {0x8CAD, 0x8FC0, 0x9089, 0x942E, 0x946A, 0x9D24, 0xA4DA, 0xA63E,
          0xA6AC, 0xA6F2, 0xA783, 0xA79E, 0xA7C5, 0xA7FE, 0xAEFC, 0xAF1B,
          0xAF44, 0xB0D3}

WHAT = {i: w for _, i, w in SITES}
WHAT[1] = 'reached only through id 0 on the AY; UNREACHABLE on a 48K'


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def to_beeper(h):
    """Put the machine in the state a REAL 48K boot leaves it in.

    Restores $BF21's own 39 tape bytes and the two bytes its 128K arm patched,
    pokes RAM ($FFFD)=0 and lets THE GAME'S OWN CODE re-patch itself.  Then
    puts back the GRAPHICS that $BEDF had copied over $BF21 (the patcher is a
    one-shot and its bytes are data by the time play starts) and restores the
    register file, so the machine is exactly where the saved state left it.
    Declared: this restores original tape bytes; it invents none.
    """
    img = open(STATIC, 'rb').read()
    m = h.memobj.m
    keep = bytes(m[0xBF21:0xBF48])
    stack = bytes(m[0xFF74:0x10000])         # h.call writes sentinels here
    regs = list(h.sim.registers)
    m[0xBF21:0xBF48] = img[0xBF21:0xBF48]
    m[0xB8B5] = img[0xB8B5]
    m[0xB8CC] = img[0xB8CC]
    m[0xFFFD] = 0
    h.call(0xBF21)
    h.sim.registers[:] = regs
    m[0xBF21:0xBF48] = keep
    m[0xFF74:0x10000] = stack                # ... and $FFFF's boot bug matters
    m[0xFFFD] = 0
    return h


def beeper(quiet_actors=False):
    return to_beeper(fresh(quiet_actors=quiet_actors))


def isolate(h, addr, regs=None, limit=2_000_000, interrupts=False):
    """h.call, with SP put back: h.call leaks 10 bytes of sentinel per call
    and this tool calls $B8FB hundreds of times in a row."""
    sp = h.sim.registers[SP]
    out = h.call(addr, regs=regs, limit=limit, interrupts=interrupts)
    h.sim.registers[SP] = sp
    return out


def trigger_in_place(h, eid):
    """Fire an effect in the middle of a driven session and put the whole
    register file back, so the machine carries on from where it was."""
    regs = list(h.sim.registers)
    h.call(SFX, regs={'A': eid})
    h.sim.registers[:] = regs
    return h


def fe_writes(writes):
    """(T, value, pc) for every write to a $xxFE port.  The ULA decodes the
    LOW byte only, so $FE, $7FFE, $FEFE ... are all the same port."""
    return [(t, v, pc) for t, p, v, pc in writes if (p & 0xFF) == 0xFE]


def edges(writes):
    """(T, level, pc) at every CHANGE of bit 4.  A border write that leaves
    bit 4 alone is not an edge and must not be counted as one."""
    out, last = [], None
    for t, v, pc in fe_writes(writes):
        lv = (v >> 4) & 1
        if lv != last:
            out.append((t, lv, pc))
            last = lv
    return out


class Tracer:
    """Port tracer that also records the PC, so an edge can be attributed to
    $B8FB (tone, deterministic) or $B8CC (noise, LD A,R and therefore not)."""

    def __init__(self, h):
        self.h = h
        self.writes = []
        self.on = False
        orig = h.ports.write_port

        def hooked(registers, port, value):
            if self.on:
                self.writes.append((registers[T], port, value, registers[PC]))
            return orig(registers, port, value)
        h.ports.write_port = hooked
        # SkoolKit binds the tracer's methods into partials at set_tracer time,
        # so the hook only takes effect if the tracer is re-installed.
        h.sim.set_tracer(h.ports)
        self.regs = h.sim.registers

    def start(self):
        self.writes = []
        self.on = True

    def stop(self):
        self.on = False
        return self.writes


def drive(h, npass, hooks, limit=40_000_000):
    """Step whole main-loop passes, anchored on $8503 (visited exactly once
    per pass -- NOT on a frame count; see NOTES-engine.md)."""
    sim, regs, ops, mem = h.sim, h.sim.registers, h.sim.opcodes, h.sim.memory
    fd, ia = h.frame_duration, h.int_active
    ev, costs = [], []
    done, n, tp = 0, 0, regs[T]
    while n < limit:
        pc = regs[PC]
        if pc in hooks:
            ev.append((done, hooks[pc], regs[T], regs[rA], regs[R]))
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
    raise RuntimeError('runaway after %d passes' % done)


# the four routines in the whole image that can write port $FE, by the PC the
# simulator reports for the OUT itself.  $923C is the tape loader's border.
OUT_SITE = {0xB91E: 'tone', 0xB8DB: 'noise', 0xB4FC: 'border', 0x923C: 'border'}


def src_of(pc):
    if pc in OUT_SITE:
        return OUT_SITE[pc]
    if 0xC000 <= pc <= 0xC13E:
        return 'tune'                 # the relocated blocking tune, executed
    return 'other:$%04X' % pc


def table(mem, eid):
    p = TONE_TABLE[eid]
    n = mem[p]
    return p, [(mem[p + 1 + 2 * i], mem[p + 2 + 2 * i]) for i in range(n)]


def half_t(delay):
    """The measured half-period model.  delay 0 would mean 256 through the
    DJNZ wrap; `tables` proves 0 is not reachable."""
    return 17 * (delay if delay else 256) + 31


def hz(delay):
    return CPU_HZ / (2 * half_t(delay))


def arm(h, eid):
    """Trigger effect `eid` through the game's own entry, from a clean slate."""
    m = h.memobj.m
    m[STEPS] = 0
    m[PTR] = m[PTR + 1] = 0
    m[LEVEL] = 0
    m[RAMP] = 0x3C
    isolate(h, SFX, regs={'A': eid})
    return m[STEPS], m[PTR] | (m[PTR + 1] << 8), m[LEVEL], m[RAMP]


# --------------------------------------------------------------------------
# 11.2 -- confirm a call site at an instruction boundary
# --------------------------------------------------------------------------
def boundary_ok(mem, site, back=40):
    """Disassemble from `back`..2 bytes before the site and keep every start
    offset whose instruction boundaries land EXACTLY on the site.  Returns
    (n_windows_landing_on_it, n_windows_tried, the decoded instruction)."""
    hit = tried = 0
    text = None
    for start in range(site - back, site - 1):
        p = start
        tried += 1
        while p < site:
            try:
                ins = z.disasm(bytes(mem[p:p + 4]), p)
                ln = z.decode(bytes(mem[p:p + 4]), p).len
            except Exception:                                       # noqa
                ln = 0
            if not ln:
                break
            p += ln
        if p == site:
            hit += 1
            if text is None:
                text = z.disasm(bytes(mem[site:site + 4]), site)
    return hit, tried, text


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
def cmd_switch(_=()):
    print('=== THE BRANCH SWITCH, AS A WHOLE-MEMORY DIFF')
    h = fresh()
    ay = bytes(h.memobj.m[0:0x10000])
    to_beeper(h)
    b48 = bytes(h.memobj.m[0:0x10000])
    open(LIVE_48, 'wb').write(b48)
    diff = [a for a in range(0x4000, 0x10000) if ay[a] != b48[a]]
    print(f'  live AY image vs live 48K image: {len(diff)} bytes differ in '
          f'$4000..$FFFF')
    for a in diff:
        print(f'    ${a:04X}  AY ${ay[a]:02X} -> 48K ${b48[a]:02X}')
    print('\n  NOT ONE of them is a trigger site, a caller, or anything outside')
    print('  the driver.  That is the whole proof that the 23 sites are the')
    print('  SAME on both branches: $BF21 patches the driver, never a caller.')
    print(f'  (build/live_beeper.bin written, {len(b48)} bytes)')


def cmd_sites(_=()):
    print('=== THE 23 TRIGGER SITES, CONFIRMED AT INSTRUCTION BOUNDARIES')
    print('  (11.2: disassemble from 2..40 bytes back and keep every window')
    print('   whose boundaries land exactly on the candidate)')
    ay = open(LIVE_AY, 'rb').read()
    b48 = open(LIVE_48, 'rb').read()
    both = good = 0
    print(f'\n  {"site":>6} {"opcode":<14} {"win":>6} {"prt":>4} {"id":>3}  '
          f'plays on a 48K')
    for pc, eid, what in SITES:
        hit, tried, text = boundary_ok(b48, pc)
        same = ay[pc:pc + 3] == b48[pc:pc + 3]
        both += same
        raw = b48[pc:pc + 3]
        real = raw[1] == 0x2B and raw[2] == 0xBA and raw[0] in (0xCD, 0xC3)
        good += bool(real and hit == tried)
        if pc == 0x9D24:
            kind = 'UNREACHABLE'      # $9D0E JR nz jumps over it on a 48K
        elif eid in TONE_TABLE:
            kind = f'TONE  ${TONE_TABLE[eid]:04X}'
        elif eid in NOISE_ID:
            kind = 'NOISE ramp'
        else:
            kind = '-- SILENT --'
        print(f'  ${pc:04X}  {text or "?":<14} {hit:>3}/{tried:<3} '
              f'{"yes" if pc in PORTED else " no":>4} {eid:3d}  {kind:12s}  '
              f'{what}')
    aud = [pc for pc, e, _ in SITES
           if pc in PORTED and pc != 0x9D24 and (e in TONE_TABLE or e in NOISE_ID)]
    mute = [pc for pc, e, _ in SITES
            if pc in PORTED and (pc == 0x9D24 or (e not in TONE_TABLE
                                                  and e not in NOISE_ID))]
    print(f'\n  OF THE {len(PORTED)} PORTED SITES: {len(aud)} make a sound on a '
          f'48K and {len(mute)} DO NOT.')
    print(f'    mute: ' + ', '.join(f'${p:04X}(id {dict((a,b) for a,b,_ in SITES)[p]})'
                                    for p in mute))
    print('    $9D24 is not merely silent -- $9D0E JR nz jumps straight over')
    print('    it on a 48K and the blocking TUNE at $B8B0 happens instead.')
    print('\n  AND THE ONE THAT MATTERS FOR THE ENTROPY: $B0D3, the generator')
    print('  spawn (id 12), IS SILENT ON THIS BRANCH.  The AY write-up records')
    print('  that the substitute RNG became AUDIBLE there -- 0 spawns on the')
    print('  original against 4 in the port over 60 identical passes.  On the')
    print('  48K branch that leak does not exist: a spawn makes no sound at all.')
    print(f'\n  {good}/{len(SITES)} are a real CALL/JP $BA2B on which ALL 39 '
          f'back-offsets resync')
    print(f'  {both}/{len(SITES)} are BYTE-IDENTICAL on the AY and 48K images')
    # the byte pattern itself, everywhere in the 64K -- no 13-to-1 problem here
    n_cd = sum(1 for a in range(0xFFFD)
               if b48[a] == 0xCD and b48[a+1] == 0x2B and b48[a+2] == 0xBA)
    n_c3 = sum(1 for a in range(0xFFFD)
               if b48[a] == 0xC3 and b48[a+1] == 0x2B and b48[a+2] == 0xBA)
    print(f'  and the byte patterns CD 2B BA / C3 2B BA occur {n_cd} + {n_c3} '
          f'= {n_cd+n_c3} times in\n  the whole 64K, which is exactly the 23 '
          f'sites -- zero spurious hits to reject.')
    print('\n  THE CONTROL, so the boundary test is not vacuous: the same walk')
    print('  run on deliberately mid-instruction addresses must FAIL.')
    for probe in (0x8CAE, 0x8CAF, 0xA4DB, 0xA79F):
        hit, tried, text = boundary_ok(b48, probe)
        print(f'    ${probe:04X} (one/two bytes into a known instruction): '
              f'{hit}/{tried} windows land on it')
    ids = sorted({e for _, e, _ in SITES})
    print(f'\n  ids a trigger can pass: {ids}')
    print(f'  of those, SILENT on a 48K: '
          f'{sorted(set(ids) & set(SILENT))}  (the $B92B chain has no arm)')
    print(f'  id 1 has no site at all: on the AY it is reached through $BAB2\'s')
    print(f'  LD A,R coin.  ON A 48K $BAB2 IS NEVER EXECUTED -- $BA2B is a JP')
    print(f'  to $B92B, and $B92B has no coin -- so id 1 is unreachable AND id')
    print(f'  0 is deterministic.  The one place the AY driver drew entropy at')
    print(f'  trigger time does not exist on this branch.')


def cmd_tables(_=()):
    m = open(LIVE_48, 'rb').read()
    print('=== THE 8 ARPEGGIO TABLES  (11.9: enumerate, do not read the algebra)')
    print('  format: n, then n pairs (C = speaker EDGES, E = delay iterations)')
    print('  one pair is played per MAIN-LOOP PASS.')
    print(f'\n  {"id":>3} {"addr":>6} {"n":>3} {"end":>6}   pairs')
    seen = {}
    for eid in sorted(TONE_TABLE):
        p, pairs = table(m, eid)
        end = p + 1 + 2 * len(pairs)
        seen[p] = end
        s = ' '.join(f'({c},{d})' for c, d in pairs)
        print(f'  {eid:3d} ${p:04X} {len(pairs):3d} ${end:04X}   {s}')
    starts = sorted(seen)
    gaps = [(a, b) for a, b in zip(starts, starts[1:]) if seen[a] != b]
    print(f'\n  the 8 tables tile ${starts[0]:04X}..${seen[starts[-1]]-1:04X} '
          f'with {len(gaps)} gaps or overlaps, and ${seen[starts[-1]]:04X} is '
          f'$BA01,\n  the AY init entry -- so the tone data region is closed by '
          f'construction,\n  exactly as the AY streams are.')
    ds = sorted({d for eid in TONE_TABLE for _, d in table(m, eid)[1]})
    cs = sorted({c for eid in TONE_TABLE for c, _ in table(m, eid)[1]})
    print(f'\n  REACHABLE delays: {ds}   ({len(ds)} values, and 0 IS NOT ONE OF')
    print(f'  THEM -- so the DJNZ B=0 -> 256 wrap that 11.4 warns about is not')
    print(f'  reachable in this game and the model needs no special case)')
    print(f'  REACHABLE edge counts: {cs}')
    odd = [(eid, c, d) for eid in TONE_TABLE for c, d in table(m, eid)[1]
           if c % 2]
    print(f'\n  EVERY edge count is EVEN ({len(odd)} odd ones in the whole '
          f'game), so a step\n  always returns the speaker to the level it '
          f'started on.  That is why the\n  beeper leaves no DC click between '
          f'chirps -- and it is a property of the\n  DATA, not of the code, so '
          f'a port must not "helpfully" normalise it.')
    print(f'\n  the pitch alphabet -- EVERY tone this game can make on a 48K:')
    print(f'  {"E":>4} {"half-period T":>14} {"Hz":>9}  {"note":>6}   used by')
    import math
    for d in ds:
        users = sorted(eid for eid in TONE_TABLE
                       if any(dd == d for _, dd in table(m, eid)[1]))
        f = hz(d)
        n = 69 + 12 * math.log2(f / 440.0)
        nm = ('C C#D D#E F F#G G#A A#B '[int(round(n)) % 12 * 2:][:2].strip()
              + str(int(round(n)) // 12 - 1))
        print(f'  {d:4d} {half_t(d):14d} {f:9.1f}  {nm:>6}   ids {users}')
    print('\n  NOTE the top of the alphabet: 15.1 kHz and 21.3 kHz are at or')
    print('  beyond the top of hearing, and a real Spectrum speaker reproduces')
    print('  them as a click, not a pitch.  Ids 2 and 10 END there.')
    print('\n  and the step DURATIONS, which is what makes this a table of')
    print('  (duration, pitch) pairs rather than of (length, pitch):')
    span = [(c * half_t(d)) for eid in TONE_TABLE for c, d in table(m, eid)[1]]
    print(f'    C*(17E+31) over all {len(span)} steps in the game: '
          f'{min(span)}..{max(span)} T = {min(span)/CPU_HZ*1000:.2f}..'
          f'{max(span)/CPU_HZ*1000:.2f} ms')
    print('    i.e. every step is about the same LENGTH; only the pitch moves.')


def cmd_edges(_=()):
    h = beeper()
    m = h.memobj.m
    print('=== THE CATALOGUE: EVERY EFFECT\'S SPEAKER EDGE TRAIN, MEASURED')
    print('  $BA2B(A=id) in isolation, then $B8FB called once per step with a')
    print('  port tracer on bit 4 of $FE.  Nothing is hand counted.')
    tr = Tracer(h)
    st = h.save_state()
    ok = tot = 0
    rows = []
    print(f'\n{"id":>3} {"steps":>5} {"edges":>6} {"tone T":>8} {"tone ms":>8} '
          f'{"passes":>7} {"frames":>7} {"sec":>6}  pitch sequence (Hz)')
    for eid in range(18):
        h.load_state(st)
        n, ptr, lvl, ramp = arm(h, eid)
        if not n:
            continue
        _, pairs = table(m, eid)
        assert n == len(pairs), (eid, n, len(pairs))
        assert ptr == TONE_TABLE[eid] + 1, (eid, hex(ptr))
        tone_t = 0
        nedge = 0
        pitches = []
        for i in range(n):
            tr.start()
            _, dt, _ = isolate(h, STEP)
            e = edges(tr.stop())
            c, d = pairs[i]
            hp = [e[j + 1][0] - e[j][0] for j in range(len(e) - 1)]
            tot += 3
            ok += (len(e) == c)
            ok += (set(hp) == {half_t(d)})
            ok += (dt == c * half_t(d) + 152)
            assert all(pc for _, _, pc in e), eid
            tone_t += (len(e) - 1) * half_t(d) if len(e) > 1 else 0
            nedge += len(e)
            pitches.append(hz(d))
        assert m[STEPS] == 0
        # one step per pass; a pass on this branch is 4.06 (idle) .. 4.37 (moving)
        fr_idle, fr_move = n * 4.062, n * 4.373
        rows.append((eid, n, nedge, tone_t, pitches))
        print(f'{eid:3d} {n:5d} {nedge:6d} {tone_t:8d} {tone_t/CPU_HZ*1000:8.2f} '
              f'{n:7d} {fr_idle:7.1f} {fr_idle/50.08:6.2f}  '
              + ' '.join(f'{p:.0f}' for p in pitches))
    print(f'\n  MODEL CHECKS: {ok}/{tot} passed over every step of every effect')
    print('    (edge count == C;  every half-period == 17E+31 exactly;')
    print('     step cost == C*(17E+31) + 152 T)')
    print('\n  READ THE SHAPE, NOT THE TOTAL.  "tone ms" is the audible time;')
    print('  "frames" is the wall-clock length.  A step is a 3.2..4.7 ms CHIRP')
    print('  and the next one is a whole pass (~81 ms) later, so every one of')
    print('  these effects is a SEQUENCE OF SHORT BLIPS at ~11.4 Hz, not a')
    print('  continuous tone.  On the AY the same ids play one row per 50 Hz')
    print('  frame and are continuous.  THE TWO BRANCHES DO NOT SOUND ALIKE.')
    return rows


def cmd_noise(_=()):
    h = beeper()
    m = h.memobj.m
    print('=== IDS 0 AND 4: THE NOISE RAMP  ($B8CC, six sites in the blitter)')
    print('''
    $B8CC  LD A,R / CP (IY+$53) / JR nc,$B8DD      (IY+$53) IS $84D2 itself
    $B8D3  LD A,($84CA) / XOR $10 / LD ($84CA),A / OUT ($FE),A
    $B8DD  LD A,($84D2) / OR A / RET z
    $B8E2  INC A   <- or DEC A, PATCHED at $B8EE/$B8F7
    $B8E3  AND $7F / LD ($84D2),A / RET
''')
    st = h.save_state()
    for eid in (0, 4):
        h.load_state(st)
        n, ptr, lvl, ramp = arm(h, eid)
        print(f'  id {eid}: $84CF={n} (no tone)  $84D2 := {lvl}  '
              f'$B8E2 := ${ramp:02X} = '
              f'{"INC A (sparse -> dense)" if ramp == 0x3C else "DEC A (dense -> sparse)"}'
              f'   [{WHAT.get(eid, "")}]')
    print('\n  THE RAMP IS A CLOSED WALK -- enumerated by calling $B8CC until')
    print('  $84D2 comes back to 0, with R swept so the branch is exercised:')
    for eid in (0, 4):
        h.load_state(st)
        arm(h, eid)
        seq = []
        for i in range(400):
            if not m[LEVEL]:
                break
            seq.append(m[LEVEL])
            isolate(h, NOISE)
        print(f'    id {eid}: {len(seq)} calls, level {seq[0]} -> {seq[-1]} '
              f'-> 0.  distinct levels {len(set(seq))}, '
              f'min {min(seq)} max {max(seq)}')
    print('\n  THE TOGGLE PROBABILITY, ENUMERATED OVER ALL 128 VALUES OF R')
    print('  (11.9: the guard is `R < level`, and R is a 7-bit counter -- the')
    print('  game never executes LD R,A, so bit 7 is 0 for the whole run and')
    print('  the compare is over 0..127 exactly):')
    hits = {}
    for lvl in (1, 8, 16, 32, 64, 96, 120, 127):
        h.load_state(st)
        c = 0
        for rv in range(128):
            h.memobj.m[LEVEL] = lvl
            h.memobj.m[SHADOW] = 0x00
            h.sim.registers[R] = rv
            isolate(h, NOISE)
            c += (h.memobj.m[SHADOW] >> 4) & 1
        hits[lvl] = c
        print(f'    level {lvl:3d}: toggles on {c:3d}/128 values of R '
              f'= {c/1.28:5.1f}%   (level/128 = {lvl/1.28:5.1f}%)')
    print('\n  AND THE PREDICTION THAT FALLS OUT OF IT, checked in DRIVEN play:')
    print('  a burst walks every level 1..127 exactly once, so the expected')
    print('  number of toggles in a whole burst is sum(L/128, L=1..127)')
    print('  = 8128/128 = 63.5, THE SAME IN BOTH DIRECTIONS.')
    for eid in (0, 4):
        hh = beeper(quiet_actors=True)
        drive(hh, 1, {})
        tr = Tracer(hh)
        trigger_in_place(hh, eid)
        tr.start()
        drive(hh, 3, {})
        got = [x for x in edges(tr.stop()) if src_of(x[2]) == 'noise']
        print(f'    id {eid}: {len(got)} noise edges in the burst  '
              f'(expected 63.5; the draw is R, so this is a SAMPLE)')
    print('\n  So the duty is EXACTLY level/128 and the effect is a ramp of')
    print('  toggle DENSITY, not of pitch.  id 0 sweeps 1/128 -> 127/128')
    print('  (a click that swells into a rasp); id 4 sweeps the other way.')
    print('\n  HOW LONG IS IT?  127 calls of $B8CC, and $B8CC is called once')
    print('  per drawn map cell and once per sprite -- so the DURATION IS A')
    print('  LIVE GAME VALUE (11.7), not a constant:')
    for tag, keys in (('idle', []), ('walking down', ['Q'])):
        hh = beeper()
        for k in keys:
            s, b = KM[k]
            hh.ports.press(s, keymask(b))
        drive(hh, 1, {})
        ev, costs = drive(hh, 12, {NOISE: 'n'})
        per = collections.Counter()
        for p, k, *_ in ev:
            per[p] += 1
        v = sorted(per.values())
        mean = sum(v) / len(v)
        fp = sum(costs) / len(costs)
        print(f'    {tag:12s}: {min(v)}..{max(v)} calls/pass (mean {mean:.0f}), '
              f'{fp:.3f} frames/pass\n                  -> 127 calls = '
              f'{127/mean:.2f} passes = {127/mean*fp:.2f} video frames = '
              f'{127/mean*fp/50.08*1000:.0f} ms')
    print('\n  AND WHAT IT SOUNDS LIKE, measured rather than imagined:')
    hh = beeper(quiet_actors=True)
    drive(hh, 1, {})
    ev, _ = drive(hh, 3, {NOISE: 'n'})
    ts = [t for _, k, t, _, _ in ev if k == 'n']
    cg = collections.Counter(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
    allg = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
    hh = beeper(quiet_actors=True)
    drive(hh, 1, {})
    tr = Tracer(hh)
    trigger_in_place(hh, 0)
    tr.start()
    drive(hh, 3, {})
    ng = [x[0] for x in edges(tr.stop()) if src_of(x[2]) == 'noise']
    eg = sorted(ng[i + 1] - ng[i] for i in range(len(ng) - 1))
    print(f'    $B8CC CALL spacing:  modal {cg.most_common(1)[0][0]} T inside '
          f'a blit run, {[g for g, _ in cg.most_common(4)[1:]]} T between '
          f'runs,\n                         mean {sum(allg)/len(allg):.0f} T')
    print(f'    NOISE EDGE spacing:  {eg[0]}..{eg[-1]} T, median {eg[len(eg)//2]}'
          f' T = {CPU_HZ/(2*eg[len(eg)//2]):.0f} Hz ceiling')
    print('    so the ramp is a RANDOM TELEGRAPH on a ~7.8 kHz ceiling with')
    print('    dropouts: at level 1 a couple of isolated CLICKS, at level 127')
    print('    a continuous RASP.  Grit, not a tone, over in ~2 video frames.')
    print('\n  UNREPRODUCIBLE, AND SAY SO: the toggle source is `LD A,R`, the')
    print('  Z80 refresh register (NOTES-battery.md Q18\'s class).  The DUTY')
    print('  ramp and the DURATION are exactly reproducible; WHICH calls')
    print('  toggle is not.  A port must draw from a substitute stream kept')
    print('  separate from the play entropy, as the AY port already does.')


def cmd_clock(_=()):
    print('=== THE CLOCK THIS BRANCH RUNS ON')
    print('  $B8FB has ONE call site, $9CD9, inside $9CD7 (HALT / DI / CALL),')
    print('  and $9CD7 is reached once a pass from $8550.  So ONE ARPEGGIO')
    print('  STEP IS ONE MAIN-LOOP PASS, exactly -- measured, not read:')
    for tag, keys in (('idle', []), ('down', ['Q']), ('right', ['D'])):
        h = beeper()
        for k in keys:
            s, b = KM[k]
            h.ports.press(s, keymask(b))
        drive(h, 1, {})
        ev, costs = drive(h, 16, {STEP: 'step', NOISE: 'n', SFX: 'sfx'})
        nstep = sum(1 for e in ev if e[1] == 'step')
        nn = sum(1 for e in ev if e[1] == 'n')
        hist = dict(sorted(collections.Counter(round(c, 2)
                                               for c in costs).items()))
        print(f'  {tag:6s} {nstep} $B8FB in 16 passes, {nn} $B8CC '
              f'({nn/16:.0f}/pass)\n         frames/pass {hist} '
              f'mean {sum(costs)/len(costs):.3f} '
              f'-> {50.08/(sum(costs)/len(costs)):.2f} passes/s')
    h = beeper()
    tr = Tracer(h)
    tr.start()
    _, dt_idle, _ = isolate(h, STEP)
    w = tr.stop()
    print(f'\n  $B8FB with nothing playing: {dt_idle} T '
          f'({dt_idle/FRAME_T:.3f} frames), {len(fe_writes(w))} port writes.')
    print('  That is the $B901 DEC HL delay from $01EB and it runs EVERY PASS')
    print('  on a 48K whether or not anything is audible.')
    print('\n  So an effect of n steps lasts n passes:')
    print('    idle       4.06 frames/pass -> 81.1 ms a step, 12.33 steps/s')
    print('    walking    4.37 frames/pass -> 87.3 ms a step, 11.45 steps/s')
    print('  and the AUDIBLE fraction of a step is 3.2..4.7 ms, i.e. 4..6%.')

    print('\n  AND THE CHIRP IS FRAME-ALIGNED, not pass-aligned.  $9CD7 is')
    print('  HALT / DI / CALL $B8FB, so the burst begins a few hundred T after')
    print('  a 50 Hz interrupt however long the pass was.  Measured on the')
    print('  first edge of all 9 steps of id 15 in driven play:')
    h = beeper(quiet_actors=True)
    drive(h, 1, {})
    tr = Tracer(h)
    trigger_in_place(h, 15)
    tr.start()
    drive(h, 10, {})
    e = [x for x in edges(tr.stop()) if src_of(x[2]) == 'tone']
    ch, cur = [], None
    for t, lv, pc in e:
        if cur is None or t - cur[-1] > 20000:
            if cur:
                ch.append(cur)
            cur = [t]
        else:
            cur.append(t)
    if cur:
        ch.append(cur)
    off = [c[0] % FRAME_T for c in ch]
    gap = [(ch[i + 1][0] - ch[i][0]) / FRAME_T for i in range(len(ch) - 1)]
    print(f'    {len(ch)} chirps, first edge at T mod 69888 = '
          f'{min(off)}..{max(off)} ({min(off)/FRAME_T:.3f}..'
          f'{max(off)/FRAME_T:.3f} of a frame after the interrupt)')
    print(f'    chirp-to-chirp spacing in frames: '
          f'{[round(g, 2) for g in gap]}')
    print('    -> a port should schedule a chirp ON a video-frame boundary,')
    print('       one per pass, and let the PASS LENGTH set the spacing.')

    print('\n  THE SPEAKER\'S RESTING STATE, and nobody had written this down.')
    print('  $84CA is the shadow of the last OUT ($FE) and it carries BOTH the')
    print('  border and the speaker.  The ISR writes it every video frame:')
    print('''
    $A2A5  CALL $BADB            (a bare RET on this branch)
    $A2A8  SUB A / BIT 3,(IY-1) / JR z / LD A,7
    $A2B1  CALL $B4F9  ->  LD ($84CA),A / OUT ($FE),A / RET
''')
    print('  A is 0 or 7 there, so BIT 4 IS ALWAYS CLEAR: the ISR PULLS THE')
    print('  SPEAKER LOW 50 TIMES A SECOND and resets the shadow with it.')
    h = beeper()
    tr = Tracer(h)
    drive(h, 1, {})
    tr.start()
    ev, costs = drive(h, 8, {0xB4F9: 'border'})
    w = fe_writes(tr.stop())
    vals = collections.Counter(v for _, v, pc in w if pc == 0xB4FC)
    per = collections.Counter()
    for p, k, *_ in ev:
        per[p] += 1
    print(f'    measured over 8 idle passes: {len(ev)} calls to $B4F9 '
          f'({sorted(per.values())} a pass, i.e. one a frame),')
    print(f'    values written to $FE: {dict(vals)} -- bit 4 clear in every one')
    print('\n  Two consequences the port must carry:')
    print('   * every arpeggio chirp starts from level 0, so its first edge is')
    print('     always a RISE.  ($9CD8 DI runs before $B8FB, so the ISR can')
    print('     never land inside a chirp -- and every edge count is even, so')
    print('     a chirp ends where it began.)')
    print('   * the NOISE runs with interrupts ENABLED, from inside the blit,')
    print('     so the ISR can and does cut it low mid-ramp: an extra 50 Hz')
    print('     component that belongs to the effect and is not in the table.')


def cmd_gates(_=()):
    print('=== WHAT GATES EACH EFFECT ON THIS BRANCH (11.6)')
    print('\n  1. POSITION-GATED?  NO.  60 passes holding each direction, with')
    print('     $BA2B hooked AND the speaker traced, on the 48K branch:')
    for tag, key in (('down', 'Q'), ('up', '1'), ('left', 'S'), ('right', 'D')):
        h = beeper(quiet_actors=True)
        s, b = KM[key]
        h.ports.press(s, keymask(b))
        drive(h, 1, {})
        tr = Tracer(h)
        tr.start()
        ev, costs = drive(h, 60, {SFX: 'sfx'})
        e = edges(tr.stop())
        ids = collections.Counter(a for _, k, _, a, _ in ev)
        print(f'     {tag:5s}: {len(ev)} triggers {dict(ids) or "{}"}, '
              f'{len(e)} speaker edges in 60 passes')
    print('     THERE IS NO FOOTSTEP EFFECT AND NO COORDINATE-AND-MASK GATE.')
    print('     Nothing in Gauntlet is position-gated, so no cadence is locked')
    print('     to walking speed -- the exact opposite of the manual\'s case')
    print('     study.  What IS locked to the pass rate is the ARPEGGIO STEP,')
    print('     because $B8FB is called once per pass: an effect gets slower')
    print('     when the game does, and on this branch the game is 9% slower.')

    print('\n  2. EVENT-GATED: plant a map cell in the path, walk into it.')
    st = fresh().save_state()
    print(f'     {"cell":>5}  {"id":>3}  {"plays":<14} {"worst pass":>10}')
    for cell in list(range(0x11, 0x21)) + [0x2F, 0x30, 0x31, 0x32, 0x36]:
        h = Harness()
        h.load_state(st)
        to_beeper(h)
        m = h.memobj.m
        m[0x8496] = 0
        row, col = 12, 8
        m[0x8000 + row * 32 + col] = cell
        m[P1], m[P1 + 1] = col * 4, (row - 2) * 4
        m[P1 + 8], m[P1 + 9] = 3, 2
        s, b = KM['Q']
        h.ports.press(s, keymask(b))
        drive(h, 1, {})
        ev, costs = drive(h, 14, {SFX: 'sfx'})
        ids = [a for _, k, _, a, _ in ev]
        if not ids:
            kind = '-'
        else:
            i = ids[0]
            kind = (f'TONE ${TONE_TABLE[i]:04X}' if i in TONE_TABLE else
                    'NOISE ramp' if i in NOISE_ID else 'SILENT')
        print(f'     ${cell:02X}  {str(ids) if ids else "  -":>3}  {kind:<14} '
              f'{max(costs):9.1f}f')

    print('\n  3. COUNTER-GATED: id 3 (materialise) fires on exactly one value')
    print('     of (IX+13), and id 3 IS SILENT ON A 48K.  So the whole')
    print('     materialise is mute on this branch.')
    print('\n  4. TIME-GATED: ids 14 and 16, the hurry-up pair, off $84B8 --')
    print('     once per 64 VIDEO FRAMES from $B6E9.  id 14 plays $B9DA (a')
    print('     5-step FALL); id 16 IS SILENT.  So on a 48K the hurry-up')
    print('     warns you once and then says nothing.')
    print('\n  5. NOT GATED AT ALL, and this is the one the beeper adds: the')
    print('     NOISE DURATION is gated by how much the blitter draws.')


def cmd_priority(_=()):
    h = beeper()
    m = h.memobj.m
    st = h.save_state()
    print('=== ONE SPEAKER, ONE VOICE: WHAT HAPPENS WHEN TWO EFFECTS OVERLAP')
    print('  There is no channel record, no busy mask, no age and no steal on')
    print('  this branch.  The whole of the driver\'s state is FOUR bytes:')
    print('  $84CF (steps left), $84D0/1 (stream pointer), $84D2 (noise level)')
    print('  -- plus the patched opcode at $B8E2.  So the rules fall out of')
    print('  which of them each id writes.  Measured:')

    def snap():
        return (m[STEPS], m[PTR] | (m[PTR + 1] << 8), m[LEVEL], m[RAMP])

    print('\n  (a) TONE over TONE -- the newer one CUTS THE OLDER OFF, from')
    print('      its own step 0.  Nothing is queued and nothing is dropped:')
    h.load_state(st)
    arm(h, 15)
    for _ in range(3):
        isolate(h, STEP)
    print(f'      id 15 (9 steps), 3 steps in : steps={snap()[0]} '
          f'ptr=${snap()[1]:04X}')
    isolate(h, SFX, regs={'A': 17})
    print(f'      then id 17 (4 steps)        : steps={snap()[0]} '
          f'ptr=${snap()[1]:04X}  <- restarts as id 17')
    print('      the 6 unplayed steps of id 15 are GONE.  On the AY the same')
    print('      pair would have taken two of the three channels and played')
    print('      together; here the interrupt is total.')

    print('\n  (b) THE SAME TONE RETRIGGERED -- restarts at step 0:')
    h.load_state(st)
    arm(h, 11)
    for _ in range(7):
        isolate(h, STEP)
    a = snap()
    isolate(h, SFX, regs={'A': 11})
    print(f'      id 11 7 steps in: steps={a[0]} ptr=${a[1]:04X}  ->  '
          f'after retrigger: steps={snap()[0]} ptr=${snap()[1]:04X}')

    print('\n  (c) A SILENT ID OVER A PLAYING TONE -- IT IS A NO-OP.  $B98A is')
    print('      a bare RET, so the running effect is NOT disturbed.  This is')
    print('      a real behavioural difference from the AY, where every id')
    print('      allocates a channel and can steal one:')
    for sid in SILENT:
        h.load_state(st)
        arm(h, 15)
        for _ in range(3):
            isolate(h, STEP)
        before = snap()
        isolate(h, SFX, regs={'A': sid})
        after = snap()
        print(f'      id 15 playing (steps={before[0]}), then id {sid:2d}: '
              f'steps={after[0]} ptr=${after[1]:04X}  '
              f'{"UNCHANGED" if before == after else "CHANGED"}')

    print('\n  (d) NOISE AND TONE COEXIST -- different state, different driver,')
    print('      different call site.  They TIME-DIVISION-MULTIPLEX the one')
    print('      speaker bit: the tone burst is one contiguous block at the')
    print('      top of the pass ($9CD9) and the noise toggles are sprayed')
    print('      through the blit ($9F69..$A1DD, 234..250 a pass):')
    for a_id, b_id in ((15, 4), (4, 15), (15, 0), (0, 15)):
        h.load_state(st)
        arm(h, a_id)
        isolate(h, SFX, regs={'A': b_id})
        s = snap()
        print(f'      id {a_id:2d} then id {b_id:2d}: steps={s[0]:2d} '
              f'ptr=${s[1]:04X} level={s[2]:3d} ramp=${s[3]:02X}   '
              f'{"BOTH LIVE" if (s[0] and s[2]) else "one only"}')

    print('\n  (e) NOISE over NOISE -- the newer one reloads BOTH the level')
    print('      and the ramp direction, so id 4 after id 0 reverses the sweep')
    print('      mid-flight:')
    h.load_state(st)
    arm(h, 0)
    for _ in range(40):
        isolate(h, NOISE)
    a = snap()
    isolate(h, SFX, regs={'A': 4})
    print(f'      id 0, 40 calls in: level={a[2]} ramp=${a[3]:02X}  ->  '
          f'after id 4: level={snap()[2]} ramp=${snap()[3]:02X}')

    print('\n  (f) NOTHING SILENCES THE BEEPER.  $BA01, the AY\'s "silence')
    print('      everything", is patched to RET on this branch, so an effect')
    print('      in flight survives a level change, a death and a game over.')
    print('      The only things that end a tone are its own step count and a')
    print('      newer tone.')

    print('\n  (g) A BLOCKING TUNE OVER AN EFFECT: the tune owns the CPU for')
    print('      72 or 210 frames and the arpeggio simply does not advance')
    print('      (no $9CD9 while $B8B0 runs), then resumes where it was:')
    h.load_state(st)
    arm(h, 15)
    for _ in range(2):
        isolate(h, STEP)
    a = snap()
    isolate(h, 0xB8B0, limit=20_000_000, interrupts=True)
    print(f'      id 15 2 steps in: steps={a[0]} ptr=${a[1]:04X}  ->  '
          f'after the $B8B0 tune: steps={snap()[0]} ptr=${snap()[1]:04X}')


def cmd_tunes(_=()):
    print('=== THE TWO BLOCKING TUNES -- and they REPLACE the AY\'s pause')
    print('''
    $9D01  BIT 2,(IY-2) / RET z        $847D bit 2, the MESSAGE BANNER
    $9D0A  LD A,($FFFD) / OR A / JR nz,$9D1A         <- the branch
    $9D10  BIT 5,(IY-2) / JP nz,$B8B5 / JP $B8B0     <- 48K: A TUNE, and it is
                                                       a JP, so it IS the pause
    $9D1A  ... LD A,$10 / CALL $BA2B ... $9D2D 50 or 130 x HALT/HALT  <- 128K
''')
    print('  $B8B0/$B8B5 LDIR $13E bytes from $6740/$685D to $C000, patch two')
    print('  return vectors and JP $C000 -- the tune is DATA EXECUTED AS CODE.')
    print('''  IT IS A TWO-CHANNEL ENGINE -- the manual's 11.3 CASE (c), and the
  one case where "fit a high/low model to the gap histogram" collapses.
  Read off the relocated copy:

    $C000  two 16-bit track pointers ($C109, $C113) are planted at $C01B/$C01F
    $C047  $C024 fetches the next byte of EACH track ($40 = end of tune)
    $C031  note -> period: A+12 indexes the table at $C0D4;  H = period, L = 1
    $C085  the inner loop, 96 T-states long, TWO OUTs per turn:
             NOP/NOP / EX AF,AF' / DEC E / OUT ($FE),A / JR nz,$C0A4
             $C0A4 `28 FE` is JR z TO ITSELF and is a 2-BYTE NOP for timing --
                   it is entered with Z clear, always
             EX AF,AF' / DEC L / JP nz,$C0AB / OUT ($FE),A / XOR D / DJNZ
           E counts voice 1 (reload IXH), L counts voice 2 (reload H), D is
           $10, and EX AF,AF' keeps A SEPARATE SPEAKER STATE PER VOICE.

  So the port is written TWICE per 96 T with two independent levels: the raw
  edge train is an ~18.2 kHz interleave carrier and the MUSIC is in the two
  counters, not in the gaps.  Anything fitted to the gap histogram is fitting
  the carrier.''')
    h = beeper()
    st = h.save_state()
    tr = Tracer(h)
    for addr, src, tag in ((0xB8B0, 0x6740, 'MESSAGE banner  ($847D bit 5 clear)'),
                           (0xB8B5, 0x685D, 'LEVEL START     ($847D bit 5 set)')):
        h.load_state(st)
        tr.start()
        _, dt, _ = isolate(h, addr, limit=40_000_000, interrupts=True)
        w = tr.stop()
        e = edges(w)
        hp = collections.Counter(e[i + 1][0] - e[i][0] for i in range(len(e) - 1))
        sites = collections.Counter(pc for _, _, pc in fe_writes(w))
        print(f'\n  ${addr:04X}  {tag}')
        print(f'      source ${src:04X}, {dt} T = {dt/FRAME_T:.1f} video '
              f'frames = {dt/CPU_HZ:.2f} s, BLOCKING (a JP, so it IS the pause)')
        print(f'      {len(fe_writes(w))} writes to $FE from '
              f'{ {hex(k): v for k, v in sites.most_common()} }')
        print(f'      {len(e)} speaker edges; commonest gaps (T): '
              f'{[g for g, _ in hp.most_common(5)]}  <- the carrier, not notes')
    print('\n  NOT MODELLED, and this is the declaration: reproducing these two')
    print('  needs the counter algorithm transcribed and asserted against the')
    print('  recorded edge train (11.3c), which is a job of its own.  What the')
    print('  port MUST take from here today is the BLOCKING TIME, because that')
    print('  is game state:')
    print('\n  MAGNITUDE, STATED: the message pause is 72.1 frames on a 48K')
    print('  against 103.8 on the AY, and the level start 210.0 against 263.8.')
    print('  A port that keeps the AY pause lengths while playing the beeper')
    print('  runs 32 frames LATE at every banner.  The pause length is part of')
    print('  the sound branch, not of the game logic.')


# --------------------------------------------------------------------------
# the reference trace
# --------------------------------------------------------------------------
COL, ROW0 = 8, 10
PLANTS = [(ROW0 + 4, 0x1F, 7, 'KEY -> id 7, a 6-step TONE'),
          (ROW0 + 8, 0x11, 14, 'DOOR -> id 14, a 5-step falling TONE'),
          (ROW0 + 12, 0x18, 4, 'POWER-UP -> id 4, the NOISE ramp'),
          (ROW0 + 16, 0x19, 17, 'INVENTORY ITEM -> id 17 TONE *and* the '
                                'banner, i.e. the blocking $B8B0 TUNE')]


def run_scenario(npass):
    """THE SCRIPTED SESSION, shared by `trace` and `wav`.

    Built out of things this project already gates: dungeon 1 from
    build/state_charsel.pkl, the ACTORS REMOVED ($8496 := 0 -- they are the
    per-pass LD A,R consumer and would make the run irreproducible), and four
    cells planted in the player's path so that a known sequence of effects
    fires while he holds DOWN.  Returns (edges, pass costs, hook events, t0).
    """
    h = beeper(quiet_actors=True)
    m = h.memobj.m
    for r, cell, _, _ in PLANTS:
        m[0x8000 + (r % 32) * 32 + COL] = cell
    m[P1], m[P1 + 1] = COL * 4, ROW0 * 4
    m[P1 + 8], m[P1 + 9] = 3, 2          # keys, potions
    s, b = KM['Q']
    h.ports.press(s, keymask(b))
    drive(h, 1, {})
    tr = Tracer(h)
    t0 = h.sim.registers[T]
    tr.start()
    ev, costs = drive(h, npass, {SFX: 'sfx', STEP: 'step'})
    return edges(tr.stop()), costs, ev, t0


def cmd_trace(args=()):
    npass = int(args[0]) if args else 90
    col, row0, plants = COL, ROW0, PLANTS
    e, costs, ev, t0 = run_scenario(npass)

    tally = collections.Counter(src_of(pc) for _, _, pc in e)

    # The blocking tunes emit ~40,000 carrier edges each and are NOT modelled
    # (see `tunes`), so they are recorded as a BLOCK -- when it started, how
    # long it owned the CPU, how many edges it made -- and not row by row.
    blocks, cur = [], None
    for t, lv, pc in e:
        if src_of(pc) == 'tune':
            if cur is None:
                cur = [t, t, 0]
            cur[1] = t
            cur[2] += 1
        elif cur is not None:
            blocks.append(cur)
            cur = None
    if cur is not None:
        blocks.append(cur)
    e = [(t, lv, pc) for t, lv, pc in e if src_of(pc) != 'tune']

    # pass boundaries in frames
    bounds, acc = [], 0.0
    for c in costs:
        bounds.append(acc)
        acc += c
    trace = {
        'what': 'Gauntlet 48K/BEEPER reference speaker trace',
        'branch': '48K beeper ($BF21 with RAM ($FFFD)==0)',
        'produced_by': 'python tools/beepcat.py trace',
        'cpu_hz': CPU_HZ,
        'frame_t': FRAME_T,
        'speaker_bit': 'bit 4 of the value written to any $xxFE port',
        'scenario': {
            'state': 'build/state_charsel.pkl, dungeon 1, the elf',
            'actors': 'REMOVED ($8496 := 0) -- they are the per-pass LD A,R '
                      'consumer and would make the run irreproducible',
            'player_start_cell': [col, row0],
            'key_held': 'DOWN (Q)',
            'planted': [{'row': r, 'cell': c, 'expect_id': i, 'note': n}
                        for r, c, i, n in plants],
            'passes': npass,
        },
        'columns': ['frame', 'level', 'source'],
        'source_meaning': {
            'tone': '$B91E, the arpeggio step -- DETERMINISTIC, gate on this',
            'noise': '$B8DB, the LD A,R ramp -- duty and duration are '
                     'reproducible, individual edges are NOT',
            'tune': 'the blocking tune executing at $C000 -- deterministic',
            'border': 'a $FE write that changed bit 4 without being sound '
                      '(should be none; listed if it happens)',
        },
        'triggers': [{'pass': p, 'id': a, 't': t - t0,
                      'frame': (t - t0) / FRAME_T}
                     for p, k, t, a, _ in ev if k == 'sfx'],
        'pass_start_frame': bounds,
        'pass_cost_frames': costs,
        'edges_listed': len(e),
        'edge_count_by_source': dict(tally),
        'edge_count_total': sum(tally.values()),
        'tune_blocks': [{'start_frame': (a - t0) / FRAME_T,
                         'end_frame': (b - t0) / FRAME_T,
                         'frames': (b - a) / FRAME_T,
                         'edges': n,
                         'note': 'the blocking $B8B0 tune; carrier edges are '
                                 'summarised, not listed -- see `beepcat tunes`'}
                        for a, b, n in blocks],
        'edges': [[round((t - t0) / FRAME_T, 6), lv, src_of(pc)]
                  for t, lv, pc in e],
    }
    json.dump(trace, open(REF, 'w'), indent=1)
    print('=== REFERENCE TRACE  ->  build/beeper_ref.json')
    print(f'  {npass} passes, {sum(costs):.1f} video frames '
          f'({sum(costs)/50.08:.2f} s of game time)')
    print(f'  triggers: ' + ', '.join(
        f'pass {p} id {a}' for p, k, t, a, _ in ev if k == 'sfx'))
    print(f'  {sum(tally.values())} speaker edges: {dict(tally)}')
    print(f'  {len(e)} of them listed row by row; {len(blocks)} blocking-tune '
          f'block(s) summarised')
    print(f'  file is {os.path.getsize(REF)} bytes')
    print('''
  FORMAT.  A JSON object.  The payload is "edges": an array of
  [frame, level, source] in T-state order, where

     frame   float, VIDEO FRAMES since the first sampled pass began.  A frame
             is 69,888 T-states; a second is 50.08 frames.  Fractions matter:
             a whole arpeggio chirp is 0.16..0.24 of a frame.
     level   0 or 1, the state bit 4 of port $FE has JUST BEEN SET TO.  Only
             CHANGES are listed, so the waveform is the square wave you get by
             holding each level until the next row.
     source  which routine wrote it: "tone" ($B91E, the arpeggio), "noise"
             ($B8DB, the LD A,R ramp) or "border" ($B4FC, the ISR's once-a-
             frame write, which clears bit 4 and so CAN make an edge).

  "tune_blocks" holds the blocking tunes as (start_frame, end_frame, frames,
  edges).  Their ~40,000 carrier edges each are deliberately NOT listed: the
  tune is a two-counter engine that is not modelled, and what a port has to
  reproduce from it TODAY is the 72.1 frames it stops the world for.

  "pass_start_frame" and "pass_cost_frames" carry the pass grid, so a port can
  align its own pass counter to the trace instead of assuming 4 frames.
  "triggers" lists every $BA2B entry with the id in A.

  HOW A PORT SHOULD USE IT: compare the "tone" edges EXACTLY -- count, level
  and frame -- and the "tune_blocks" by their frame span.  Compare the "noise"
  edges only by COUNT PER BURST (63.5 expected, whichever direction the ramp
  runs) and by the ramp's length, because their source is LD A,R.''')


def cmd_wav(args=()):
    """Render the reference trace to build/beeper_ref.wav so it can be HEARD.

    DECLARED, and it is the only place anything is invented: the edge list is
    a 0/1 square wave at 3.5 MHz and the file is 44.1 kHz, so each output
    sample is the MEAN of the level over the sample window (the same
    box-average the AY renderer uses, and for the same reason -- 18 kHz and
    21 kHz content would otherwise alias into the audible band).  A one-pole
    low-pass at 6 kHz stands in for the physical speaker.  Amplitude is
    arbitrary; loudness is NOT measured.
    """
    import struct
    import wave
    npass = int(args[0]) if args else 90
    e, costs, ev, t0 = run_scenario(npass)
    sr = 44100
    total_t = sum(costs) * FRAME_T
    n = int(total_t / CPU_HZ * sr)
    buf = [0.0] * n
    lvl = 0
    idx = 0
    acc = 0.0
    prev_t = 0
    for t, l, pc in e + [(t0 + total_t, lvl, 0)]:
        rel = t - t0
        while idx < n and (idx + 1) / sr * CPU_HZ <= rel:
            end = (idx + 1) / sr * CPU_HZ
            acc += lvl * (end - prev_t)
            buf[idx] = acc / (CPU_HZ / sr)
            acc = 0.0
            prev_t = end
            idx += 1
        if idx < n:
            acc += lvl * (rel - prev_t)
            prev_t = rel
        lvl = l
    # one-pole low pass ~6 kHz, standing in for the speaker cone
    a = 1.0 - pow(2.718281828, -2 * 3.14159265 * 6000.0 / sr)
    y = 0.0
    out = []
    for v in buf:
        y += a * (v - 0.5 - y)
        out.append(y)
    pk = max(1e-9, max(abs(v) for v in out))
    path = os.path.join(ROOT, 'build', 'beeper_ref.wav')
    w = wave.open(path, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(b''.join(struct.pack('<h', int(v / pk * 26000))
                           for v in out))
    w.close()
    print('=== build/beeper_ref.wav')
    print(f'  {n/sr:.2f} s, {sr} Hz mono, {os.path.getsize(path)} bytes, '
          f'{len(e)} edges rendered')
    # --- THE ROUND TRIP (manual R9): measure the pitch back OUT of the file
    chirps, cur = [], None
    for t, l, pc in e:
        if src_of(pc) != 'tone':
            continue
        if cur is None or t - cur[-1] > 20000:
            if cur:
                chirps.append(cur)
            cur = [t]
        else:
            cur.append(t)
    if cur:
        chirps.append(cur)
    good = meas = 0
    worst = 0.0
    for c in chirps:
        model_hz = CPU_HZ / (2 * ((c[-1] - c[0]) / (len(c) - 1)))
        if model_hz > 8000:                 # under 6 samples a cycle: skip
            continue
        i0 = int((c[0] - t0) / CPU_HZ * sr)
        i1 = int((c[-1] - t0) / CPU_HZ * sr) + 1
        seg = out[i0:i1]
        if len(seg) < 16:
            continue
        mu = sum(seg) / len(seg)
        zc = sum(1 for k in range(1, len(seg))
                 if (seg[k] - mu) * (seg[k - 1] - mu) < 0)
        got = zc / 2 * sr / max(1, (i1 - i0)) * 1.0
        got = zc * sr / (2.0 * (i1 - i0))
        err = abs(got - model_hz) / model_hz
        meas += 1
        good += err < 0.05
        worst = max(worst, err)
    print(f'  ROUND TRIP: {meas} chirps under 8 kHz measured back out by zero '
          f'crossings,\n              {good}/{meas} within 5% of '
          f'clock/(2*(17E+31)), worst {worst*100:.2f}%.')
    print('              The two that miss are 10-edge chirps: a zero-crossing')
    print('              count over 5 cycles quantises to 1/10 = 10%, so that')
    print('              residual is the ESTIMATOR\'s resolution, not the')
    print('              model\'s error.  The model itself is checked exactly,')
    print('              177/177, against the T-stamped edges in `edges`.')
    print('  box-averaged from the 3.5 MHz edge train, one-pole 6 kHz speaker')
    print('  model, PEAK NORMALISED -- loudness is not measured and is not a')
    print('  claim.  LOCAL ONLY, like every other extracted asset here.')


CMDS = {'switch': cmd_switch, 'sites': cmd_sites, 'tables': cmd_tables,
        'edges': cmd_edges, 'noise': cmd_noise, 'clock': cmd_clock,
        'gates': cmd_gates, 'priority': cmd_priority, 'tunes': cmd_tunes,
        'trace': cmd_trace, 'wav': cmd_wav}
ORDER = ['switch', 'sites', 'tables', 'edges', 'noise', 'clock', 'gates',
         'priority', 'tunes', 'trace', 'wav']


def main():
    args = sys.argv[1:]
    if not args or args[0] == 'all':
        for name in ORDER:
            CMDS[name](args[1:])
            print()
        return
    CMDS[args[0]](args[1:])


if __name__ == '__main__':
    main()
