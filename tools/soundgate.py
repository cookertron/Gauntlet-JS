#!/usr/bin/env python3
"""
soundgate.py -- THE PHASE-11 GATE: a REGISTER-LEVEL SOUND DIFFERENTIAL.

    python tools/soundgate.py effects     the 18 effects, one at a time
    python tools/soundgate.py play        five driven-play scenarios
    python tools/soundgate.py cells       one planted interactive cell each
    python tools/soundgate.py all         all three, with a total
    python tools/soundgate.py rows        the row counts, from the streams
    python tools/soundgate.py table down 60      print one side's table

The manual's AY branch (phase 11) says the verification is not a frequency
round-trip but this:

    "does your player write the same 14 registers with the same values on
     the same frames as the original?"

So both sides print ONE ROW PER AY TICK -- per 50 Hz video frame, because
$BADB's only call site is $A2A5 inside the IM2 handler at $A29F -- listing
the (register, value) writes that tick made, IN THE ORDER THEY WERE MADE.
The real Z80's side is read off its own OUTs to $FFFD (register select) and
$BFFD (data), the only two AY writes in the game ($BB9D and $BBA2, both
inside the single writer $BB96).  Nothing is compared against a model: the
left column is the machine.

WHERE THE EXPECTED VALUES COME FROM (manual: never from the code under test)
  * every row of every table below is the ORIGINAL's own port traffic,
    captured with tools/harness.py's port tracer over driven play;
  * the row COUNTS printed by `rows` come from walking the game's own $73DA
    streams in tools/sfxdata.py, which independently asserts that the 18
    streams tile $73FE..$77AF with zero gaps and zero overlaps;
  * the tick counts fed to the engine are measured by hooking $BADB.

THE ONE SUBSTITUTION, DECLARED.  The engine charges a flat four video frames
per main-loop pass; the original's cost 3.92..5.03 (measured here: over 60
passes holding DOWN the per-pass tick histogram is {3: 4, 4: 55, 5: 1}, 237
ticks in 240.6 frames).  Left alone, the two clocks walk apart and every row
after the first divergence shifts by one tick.  `play` and `cells` therefore
hand the engine the ORIGINAL'S OWN per-pass tick count through
--ticks, exactly as tools/p2gate.py hands it the original's $8497.  What is
under test is the DRIVER -- allocation, the row decode, the mixer, the write
order -- not this engine's approximation of the video clock.  The unhelped
number is printed too, as "flat clock", so the size of the approximation is
visible rather than hidden.
"""
import collections
import os
import pickle
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, SP, A as rA, FRAME_T,          # noqa
                     TAPE_CALL_PC)
from keyprobe import KEYS, keymask                                       # noqa

KM = {n: (s, b) for n, s, b in KEYS}
DIRKEY = {'up': '1', 'down': 'Q', 'left': 'S', 'right': 'D'}
# THE AY BRANCH, DELIBERATELY.  This differential is a REGISTER dump and the
# registers only exist on the 128K arm; the shipped baseline is
# build/state_48k.pkl (the beeper), gated by tools/beepgate.py.  This state is
# its CONTROLLED TWIN: tools/boot48.py runs the identical boot script with the
# $7FFD probe SKIPPED, so ($FFFD) keeps the loader stub's $2A, $BF21 takes its
# 128K arm and the two states differ only in the ten patched bytes and in what
# the refresh register did.  The harness cannot make the probe answer 128K --
# its OUT ($7FFD) goes nowhere, which IS a 48K -- so this is the one place a
# gate cannot reach its branch through the game's own probe, and it is
# declared rather than hidden.  headless.js sets SOUND_MODE to $01 to match.
STATE = os.path.join(ROOT, 'build', 'state_48k_ay.pkl')
LOOP_TOP = 0x8503
TICK = 0xBADB                       # the per-frame driver entry
DISPATCH = 0xBA2B                   # the sfx entry point
SILENCE = 0xBA01
SENT = 0xFE00
ROW = re.compile(r'^\s*(\d+)\s+(\d+)\s{2}(.*)$')      # pass tick writes


# --------------------------------------------------------------- the machine
def fresh():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    return h


def _step(h, r, mem):
    pc = r[PC]
    if h.deck is not None and pc == TAPE_CALL_PC:
        h._tape(); return
    if mem[pc] == 0x76 and r[IFF]:
        h._fast_halt(); return
    h.sim.opcodes[mem[pc]]()
    if r[IFF] and r[T] % h.frame_duration < h.int_active:
        h.sim.accept_interrupt(r, mem, pc)


def icall(h, addr, a=0, limit=4_000_000):
    """Call a routine in isolation and RESTORE SP AND PC afterwards.

    harness.call leaves PC on a sentinel and SP ten bytes low, which is
    invisible if you stop and read memory and fatal if the machine then keeps
    running -- it is the rig bug that once produced a phantom write to AY
    register $A1.  This is the same trick with the damage undone.
    """
    r = h.sim.registers
    sp0, pc0 = r[SP], r[PC]
    sp = (sp0 - 2) & 0xFFFF
    h.memobj.m[sp] = SENT & 0xFF
    h.memobj.m[sp + 1] = SENT >> 8
    r[SP] = sp
    r[rA] = a
    r[PC] = addr
    t0 = r[T]
    n = 0
    while r[PC] != SENT:
        h.sim.opcodes[h.sim.memory[r[PC]]]()
        n += 1
        if n > limit:
            raise RuntimeError('runaway inside $%04X' % addr)
    dt = r[T] - t0
    r[SP], r[PC] = sp0, pc0
    return dt


def decode(writes):
    """(T, port, value) -> the ordered [(register, value)] the game wrote.

    One state machine: $FFFD selects, $BFFD writes.  $00FE (the border, one
    per frame) and the keyboard reads are not AY traffic and are dropped.
    """
    out, sel = [], None
    for _t, p, v in writes:
        if p == 0xFFFD:
            sel = v
        elif p == 0xBFFD:
            out.append((sel, v))
    return out


def fmt_writes(pairs):
    return ' '.join('%d=%02X' % (r, v) for r, v in pairs)


# ------------------------------------------------------- ORIGINAL: solo effect
def orig_solo(h, idv, nticks):
    """Silence, arm one effect, then step the driver `nticks` times."""
    icall(h, SILENCE)
    icall(h, DISPATCH, a=idv)
    chosen = None
    for c in range(3):
        if h.memobj.m[0xBDC0] & (1 << c):
            chosen = h.memobj.m[0xBDB6 + 4 * c]
            break
    rows = []
    h.ports.record_writes = True
    for _ in range(nticks):
        h.ports.writes = []
        icall(h, TICK)
        rows.append(decode(h.ports.writes))
    return chosen, rows


# -------------------------------------------------------- ORIGINAL: play table
def orig_play(direction, passes, plant=None, noactors=False,
              nogen=False):
    """Drive the real Z80 and bucket every AY write by (pass, tick).

    A tick is one entry to $BADB.  Every AY write in the game happens inside
    it ($BB96 has no other caller reachable here), so the bucket is exact
    rather than a time window.
    """
    h = fresh()
    m = h.memobj.m
    if noactors:
        # the same seeding tools/p2gate.py uses: $8496 the live count and
        # $8494 the tail pointer.  With no actors there is no LD A,R in the
        # pass at all, which is what makes the comparison strict.
        m[0x8496] = 0
        m[0x8494], m[0x8495] = 0x00, 0x5C
    if nogen:
        # erase the generators: their spawn roll is $AA1D CALL $B575, the
        # refresh register, so it is the one input no port can reproduce
        for a in range(0x8000, 0x8400):
            if 0x20 <= m[a] <= 0x2E:
                m[a] = 0
    if plant is not None:
        col, row, val = plant
        m[0x8000 + (row & 31) * 32 + (col & 31)] = val
    if direction != 'idle':
        sel, bit = KM[DIRKEY[direction]]
        h.ports.press(sel, keymask(bit))
    r, mem = h.sim.registers, h.sim.memory
    while r[PC] != LOOP_TOP:
        _step(h, r, mem)
    # the live driver state at the first loop top, so the engine can start
    # from the same channel records (the capture is mid-level)
    seed = [m[0xBDC0]]
    for c in range(3):
        ptr = m[0xBDB3 + 4 * c] | (m[0xBDB4 + 4 * c] << 8)
        idv = m[0xBDB6 + 4 * c]
        base = stream_addr(m, idv)
        seed += [idv, max(0, (ptr - base) // 2)]
    h.ports.record_writes = True
    table, ticks, events, coins, pauses = [], [], [], [], []
    for i in range(passes):
        h.ports.writes = []
        marks = []            # (write index at which each tick started)
        first = True
        n = 0
        want = None
        # THE PAUSE.  $9D29 is the instruction after both arms of $9D1A have
        # chosen B, and $9D36 is the DEC that ends the DJNZ loop, so the ticks
        # between them are the HALTs the game spends stopped.  Counting them
        # separately is what lets the engine's own modelled 100 be CHECKED
        # rather than assumed, and keeps the pass's ordinary ticks comparable.
        in_pause = False
        npause = 0
        while True:
            pc = r[PC]
            if pc == LOOP_TOP and not first:
                break
            first = False
            if pc == 0x9D29:
                in_pause = True
            if pc == 0x9D36:
                in_pause = False
            if pc == TICK:
                marks.append(len(h.ports.writes))
                n += 1
                if in_pause:
                    npause += 1
            if pc == DISPATCH:
                want = r[rA]
                events.append((i + 1, want))
            # $BAB7 LD (IX+3),A -- A is the RESOLVED id, so a request for 0
            # shows here as the 0-or-1 the refresh register produced
            if pc == 0xBAB7 and want == 0:
                coins.append(r[rA])
                want = None
            _step(h, r, mem)
        w = h.ports.writes
        marks.append(len(w))
        for t in range(n):
            table.append((i + 1, t, decode(w[marks[t]:marks[t + 1]])))
        ticks.append(n)
        pauses.append(npause)
    return table, ticks, seed, events, coins, pauses


def stream_addr(m, idv):
    off = m[0x73DA + 2 * idv] | (m[0x73DB + 2 * idv] << 8)
    return (0x73DA + off) & 0xFFFF


# ------------------------------------------------------------------- ENGINE
def node(args):
    out = subprocess.run(['node', os.path.join('tools', 'headless.js')] + args,
                         capture_output=True, text=True, cwd=ROOT)
    if out.returncode not in (0, 1):
        sys.exit('node failed: ' + out.stderr[-800:])
    return out.stdout


def eng_play(direction, passes, ticks, seed, plant=None, coins=(),
             flat=False, noactors=False, nogen=False, pauses=None):
    args = ['--soundtable', direction, str(passes),
            '--sfxseed', ','.join(str(v) for v in seed)]
    if noactors:
        args.append('--noactors')
    if nogen:
        args.append('--nogen')
    if not flat:
        # the ORIGINAL's own tick count for each pass, MINUS the ticks it
        # spent inside the $9D2D pause -- the engine models that separately
        # and soundgate asserts its length, so feeding it here would count
        # those frames twice.
        pre = [t - (pauses[i] if pauses else 0) for i, t in enumerate(ticks)]
        args += ['--ticks', ','.join(str(t) for t in pre)]
        args += ['--coin', ','.join(str(c) for c in coins)]
    if plant:
        args += ['--plant', '%d,%d,%d' % (plant[0], plant[1], plant[2])]
    rows = []
    for line in node(args).splitlines():
        mm = ROW.match(line)
        if mm:
            rows.append((int(mm.group(1)), int(mm.group(2)),
                         mm.group(3).rstrip()))
    return rows


def eng_solo(idv, nticks, forced=None):
    args = ['--sfxsolo', str(idv), str(nticks)]
    if forced is not None:
        args.append(str(forced))
    rows = []
    for line in node(args).splitlines():
        mm = re.match(r'^\s*(\d+)\s{2}(.*)$', line)
        if mm and not line.startswith('  ok') and not line.startswith('  FAIL'):
            rows.append((int(mm.group(1)), mm.group(2).rstrip()))
    return rows


# -------------------------------------------------------------- the commands
def cmd_rows(*_a):
    """The 18 row counts, walked out of the game's own streams."""
    h = fresh()
    m = h.memobj.m
    total = 0
    print('id   addr   rows   ms')
    for i in range(18):
        a = stream_addr(m, i)
        n = 0
        while m[a + 2 * n] != 0:
            n += 1
        total += n
        print('$%02X  $%04X  %4d  %5.0f' % (i, a, n, 1000 * n / 50.08))
    print('total %d rows' % total)


def cmd_effects(*_a):
    """Each of the 18 effects, from silence, register for register."""
    h = fresh()
    bad = tot = 0
    print('  id   ticks  mismatching')
    for idv in range(18):
        m = h.memobj.m
        a = stream_addr(m, idv)
        n = 0
        while m[a + 2 * n] != 0:
            n += 1
        nticks = n + 2                        # + the terminator + one silent
        chosen, orows = orig_solo(h, idv, nticks)
        erows = eng_solo(idv, nticks, forced=(chosen if idv == 0 else None))
        miss = 0
        for t in range(nticks):
            o = fmt_writes(orows[t])
            e = erows[t][1] if t < len(erows) else '<missing>'
            tot += 1
            if o != e:
                miss += 1
                if bad + miss <= 6:
                    print('    id $%02X tick %d\n      orig   %s\n      engine %s'
                          % (idv, t, o, e))
        bad += miss
        print('  $%02X   %4d   %d%s' % (idv, nticks, miss,
                                        '   (coin -> id %d)' % chosen
                                        if idv == 0 else ''))
    print('EFFECTS: %d ticks, %d mismatching' % (tot, bad))
    return bad, tot


# STRICT scenarios: no actors, so the pass contains no `LD A,R` at all and
# every difference would be a real one.  `right` keeps its actors because the
# pickup it walks into is the only trigger in it and the 60 passes agree.
# STRICT scenarios: the two unreproducible inputs are removed rather than
# hidden.  `noact` kills the actor list ($8496 = 0, as tools/p2gate.py does),
# `nogen` erases the generator cells; between them the pass contains no
# `LD A,R` at all, so every difference left would be a real one.
PLAY = [('idle', 60, True, True), ('down', 60, True, True),
        ('right', 60, True, True), ('left', 40, True, True),
        ('up', 40, True, True)]
# and the LIVE ones, actors and generators and all, whose divergence is
# ATTRIBUTED rather than counted -- see cmd_play.
LIVE = [('down', 60), ('right', 60)]


def cmd_play(*_a):
    bad = tot = 0
    flatbad = flattot = 0
    for direction, n, noact, nogen in PLAY:
        otab, ticks, seed, events, coins, pauses = orig_play(direction, n,
                                                     noactors=noact,
                                                     nogen=nogen)
        etab = eng_play(direction, n, ticks, seed, coins=coins,
                        noactors=noact, nogen=nogen, pauses=pauses)
        tag = '%s %d%s' % (direction, n,
                           ' (no actors/gens)' if noact and nogen else '')
        b, t = compare(otab, etab, tag, events)
        bad += b
        tot += t
        # and the same run with NEITHER substitution, to size them
        ftab = eng_play(direction, n, ticks, seed, flat=True, noactors=noact,
                        nogen=nogen, pauses=pauses)
        fb, ft = quiet(otab, ftab)
        flatbad += fb
        flattot += ft
    print('PLAY: %d ticks, %d mismatching' % (tot, bad))
    print('  -- and the same scenes with the actors and generators LIVE.')
    print('     These are NOT counted: the actor coins ($AC25/$AC4C) and the')
    print('     generator roll ($AA1D) are all LD A,R, so the port draws from')
    print('     a substitute and monsters spawn and touch at different')
    print('     moments.  Sound makes that visible for the first time,')
    print('     because every spawn plays effect $0C ($B0D3).')
    for direction, n in LIVE:
        otab, ticks, seed, events, coins, pauses = orig_play(direction, n)
        etab = eng_play(direction, n, ticks, seed, coins=coins,
                        pauses=pauses)
        b, t = quiet(otab, etab)
        print('     %-10s %4d ticks -> %d differing   [orig %s]'
              % ('%s %d' % (direction, n), t, b,
                 ' '.join('p%d:$%02X' % e for e in events) or 'no triggers'))
    print('      (flat 4-frame clock and the port\'s own coin: %d/%d '
          'mismatching -- the size of the two declared substitutions)'
          % (flatbad, flattot))
    return bad, tot


# One planted cell per interactive value, walked into by holding DOWN.  The
# ids these fire were established by driving the original, not by reading:
#   $11/$12 -> 14   $13..$17 -> 17   $18 -> 4   $19..$1E -> 17 then 16 with a
#   104-frame pause   $1F -> 7   $2F -> 9   $31 -> 17 + 16 + pause   $32 -> 17
#   $36 -> 6
CELLS = [0x12, 0x13, 0x18, 0x19, 0x1F, 0x2F, 0x31, 0x32, 0x36]


def cmd_cells(*_a):
    h = fresh()
    m = h.memobj.m
    col, row = m[0x8420] >> 2, (m[0x8421] >> 2)
    bad = tot = 0
    for v in CELLS:
        plant = (col, (row + 3) & 31, v)
        otab, ticks, seed, events, coins, pauses = orig_play('down', 12, plant=plant,
                                                     noactors=True, nogen=True)
        etab = eng_play('down', 12, ticks, seed, plant=plant, coins=coins,
                        noactors=True, nogen=True, pauses=pauses)
        b, t = compare(otab, etab, 'cell $%02X' % v, events,
                       note=('pause %d frames' % max(pauses)) if max(pauses)
                            else '')
        bad += b
        tot += t
        # the modelled pause length, CHECKED against the machine rather than
        # asserted: $9D27 LD B,$32 = 50 iterations of two HALTs = 100 frames
        if max(pauses) and max(pauses) != 100:
            print('      PAUSE LENGTH: original %d ticks, engine models 100'
                  % max(pauses))
            bad += 1
    print('CELLS: %d ticks, %d mismatching' % (tot, bad))
    return bad, tot


def compare(otab, etab, label, events=(), note=''):
    o = [(p, t, fmt_writes(w)) for p, t, w in otab]
    e = [(p, t, w) for p, t, w in etab]
    n = min(len(o), len(e))
    miss = sum(1 for i in range(n) if o[i] != e[i]) + abs(len(o) - len(e))
    ev = ' '.join('p%d:$%02X' % (p, i) for p, i in events) or 'no triggers'
    print('  %-12s %4d ticks -> %d mismatching   [%s]%s'
          % (label, len(o), miss, ev, '  ' + note if note else ''))
    if miss:
        shown = 0
        for i in range(n):
            if o[i] != e[i] and shown < 5:
                print('      pass %d tick %d\n        orig   %s\n        engine %s'
                      % (o[i][0], o[i][1], o[i][2], e[i][2]))
                shown += 1
        if len(o) != len(e):
            print('      ROW COUNT orig %d engine %d' % (len(o), len(e)))
    return miss, len(o)


def quiet(otab, etab):
    o = [(p, t, fmt_writes(w)) for p, t, w in otab]
    e = [(p, t, w) for p, t, w in etab]
    n = min(len(o), len(e))
    return (sum(1 for i in range(n) if o[i] != e[i]) + abs(len(o) - len(e)),
            len(o))


def cmd_table(direction='down', n='20'):
    otab, ticks, seed, events, coins, pauses = orig_play(direction, int(n))
    print('# original, %s %s   ticks/pass %s' % (direction, n,
                                                 dict(collections.Counter(ticks))))
    print('# sfxseed %s   triggers %s   coins %s' % (seed, events, coins))
    print('pass tick  writes')
    for p, t, w in otab:
        print('%4d %4d  %s' % (p, t, fmt_writes(w)))


def cmd_all(*_a):
    b1, t1 = cmd_effects()
    print()
    b2, t2 = cmd_play()
    print()
    b3, t3 = cmd_cells()
    print()
    print('TOTAL MISMATCHING SOUND TICKS: %d   (%d ticks compared)'
          % (b1 + b2 + b3, t1 + t2 + t3))
    return b1 + b2 + b3


CMD = {'rows': cmd_rows, 'effects': cmd_effects, 'play': cmd_play,
       'cells': cmd_cells, 'table': cmd_table, 'all': cmd_all}


def main():
    args = sys.argv[1:] or ['all']
    fn = CMD.get(args[0])
    if not fn:
        sys.exit(__doc__)
    r = fn(*args[1:])
    if args[0] == 'all':
        sys.exit(1 if r else 0)


if __name__ == '__main__':
    main()
