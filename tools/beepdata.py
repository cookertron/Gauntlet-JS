#!/usr/bin/env python3
"""
beepdata.py -- extract the 48K BEEPER driver's data into build/beeper_data.json.

    python tools/beepdata.py            write build/beeper_data.json
    python tools/beepdata.py --print    print the tables, the ids and the tunes

WHAT IS BEING EXTRACTED, and how each piece was established (all first hand on
the real Z80 through tools/harness.py; the reproduction is tools/beepgate.py):

THE BRANCH.  build/state_48k.pkl is the probe-driven boot: block A's own
$7FFD paging probe ran, wrote 0 to RAM $FFFD, and $BEB9 CALL $BF21 then
patched the ten bytes that make the 48K arm.  Nothing here pokes a branch
byte; the image this file reads is the one the GAME chose.  (Asserted below:
($FFFD) == 0, $BA2B..$BA2D == C3 2B B9, $B8CC == ED.)

THE DISPATCHER, $B92B -- what $BA2B becomes on a 48K.  It is a bare CP chain
and it is PARSED HERE FROM THE BYTES rather than transcribed, so a wrong id
map cannot survive:

    $B92B  PUSH HL
    then, repeated:   CP n / JR nz,+5 / LD HL,tbl / JR $B98B
    then              CP 6 / JR z,<the $B9E5 arm>     ids 6 and 15 SHARE a table
    $B978  POP HL / CP 4  / JR nz / LD A,$7F / JP $B8F2     noise, ramp DOWN
           OR A          / JR nz / LD A,$01 / JP $B8E9     noise, ramp UP (id 0)
    $B98A  RET                                              EVERY OTHER ID IS SILENT
    $B98B  LD A,(HL) / LD (IY+$50),A / INC HL / LD ($84D0),HL / POP HL / RET

So ELEVEN of the eighteen ids do something (0, 2, 4, 6, 7, 8, $0A, $0B, $0E,
$0F, $11) and SEVEN are a bare RET (1, 3, 5, 9, $0C, $0D, $10).  Note id 8:
$B93E CP 8 / LD HL,$B9AF is in the chain and an earlier write-up dropped it.

A TONE STREAM is `n` followed by n rows of (C, E):
    C  the number of speaker TOGGLES in the step      -- always EVEN in the data
    E  the delay-loop count, NOT the half-period      -- half = 17*E + 31 T
One row is one MAIN-LOOP PASS ($B8FB has exactly one caller, $9CD9, inside
$9CD7 HALT / $9CD8 DI), where an AY row is one video frame.  That is a
different clock and the port must not copy the AY row timing.

THE FIVE INVARIANTS ASSERTED HERE, none of which comes from the engine:

  1. the eight streams TILE $B995..$BA00 with zero gaps and zero overlaps and
     end exactly at $BA01, the AY init entry that this arm has patched to a
     RET -- the region is closed by construction, as the AY streams are;
  2. 50 step records in 8 streams, 59 played steps across 9 ids (6 aliases 15);
  3. every C is EVEN, so a step returns the speaker to the level it started
     on and there is no DC walk between steps;
  4. no C and no E is ZERO, so neither of the two wrap-to-256 traps (DJNZ with
     B=0, DEC C with C=0) is reachable from the shipped data -- ENUMERATED,
     not argued;
  5. the eleven distinct E values give eleven half-periods 17E+31, and the
     tone model was MEASURED against the recorded port-$FE edges over all 50
     rows and over a full 0..255 sweep of each field (tools/beepgate.py fit).

THE TWO BLOCKING TUNES are DECODED BY MEASUREMENT, which is new: both earlier
write-ups declared them un-modelled.  $B8B0/$B8B5 LDIR a $13E-byte two-voice
player to $C000 and JP into it.  $C047 is one ROW: it fetches one note byte
per voice, turns each into a counter RELOAD through the 53-entry table at
$C0D4 (index = note + 12, $C031 ADD A,12), and runs a 96 T interleave loop.
This file hooks the two instructions where a row's state is complete --

    $C081  LD IXH,D     D is voice 1's reload and H is voice 2's, and this is
                        the first instruction after both have been decoded
    $C0B6  the BOTH-VOICES-REST arm, which emits nothing at all

-- and measures each row's WALL LENGTH as the T between successive hooks.  So
what ships is (reload1, reload2, length in T) per row, read off the running
original, and the pitch is clock/(2*96*reload) because the loop is 96 T and
one toggle happens per underflow.  Note reload 1 (note byte $29, one PAST the
table, table[53] = 1) is the game's REST: 18,229 Hz, at or beyond the top of
hearing, and the code only takes the silent arm when BOTH voices rest.

Totals asserted: $B8B0 = 8 rows / 72.07 video frames, $B8B5 = 23 rows /
209.97 frames, both matching the routines' measured wall cost to the T.
"""
import json
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, D, H, SP, PC, T, IFF, FRAME_T, CPU_HZ,  # noqa: E402
                     TAPE_CALL_PC)

STATE = os.path.join(ROOT, 'build', 'state_48k.pkl')
OUT = os.path.join(ROOT, 'build', 'beeper_data.json')

DISPATCH = 0x92B + 0xB000          # $B92B
STREAM_LO, STREAM_HI = 0xB995, 0xBA01
TUNE_LOOP_HOOK = 0xC081            # LD IXH,D -- both reloads decoded
TUNE_MUTE_HOOK = 0xC0B6            # the both-rest arm
TUNE_TURN_HOOK = 0xC087            # the top of the interleave turn
TUNE_T = 96                        # the interleave loop, one turn
TUNE_OUT2 = 48                     # voice 2's OUT, half a turn later


def load():
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    m = h.memobj.m
    # the branch the GAME chose, not one this file poked
    assert m[0xFFFD] == 0x00, 'build/state_48k.pkl is not on the beeper branch'
    assert [m[0xBA2B], m[0xBA2C], m[0xBA2D]] == [0xC3, 0x2B, 0xB9], \
        '$BA2B is not JP $B92B -- $BF21 did not run its 48K arm'
    assert m[0xB8CC] == 0xED and m[0xB8B5] == 0x21, 'the beeper is patched out'
    assert m[0xBA01] == 0xC9 and m[0xBADB] == 0xC9, 'the AY is still live'
    return h


def tick_curve(keys=('down', 'left', 'right', 'up'), npass=120, steps=20,
               idle_keys=('idle',)):
    """THE DRAWN-OBJECT TICK CURVE -- when in a pass the blitter makes its
    $B8CC calls, MEASURED rather than assumed.

    A noise burst's DURATION is not a property of the driver at all: the 127
    ramp calls come from the six $B8CC sites INSIDE THE BLITTER, so a burst
    lasts exactly as long as it takes the blit to draw 127 more objects.  An
    engine that spreads those calls UNIFORMLY over the blit window gets the
    duration wrong, because the real distribution is strongly FRONT-LOADED:
    the median call is at 0.50 video frames from the loop top, not at the
    window's midpoint of 0.75.

    What this returns is the normalised curve -- the offset, in video frames
    from $8503, of the call at fraction q of the pass's own call count -- at
    `steps`+1 evenly spaced q.

    TWO CURVES, NOT ONE, AND THE SPLIT IS MEASURED RATHER THAN CHOSEN.  The
    four WALKING scenes agree with each other to 0.004 video frames at the
    first call and 0.024 by the median; IDLE does not, and its first call is
    at 0.067 against their 0.126 -- 0.06 of a frame earlier, because a
    stationary player gives the blit less to do before the map tiles start.
    Pooling idle in with the other four therefore biases the curve low, and
    the burst-span differential showed it: every burst in a walking scene
    came out 0.10..0.14 frames short.  Splitting on "did the player move
    this pass", which is a quantity the engine has, removes that.

    Camera scrolling was tested as the discriminator FIRST and rejected:
    only `down` and `right` ever move the camera in these scenes, and
    splitting on it leaves a 0.19-frame spread among the still passes while
    the walk/idle split leaves 0.024 in the region a burst actually reaches.

    Measured here, and re-measured independently by `python
    tools/beepgate.py ticks` in a scene this file does not pool over:
        idle 250 calls/pass exactly;  down 233..255;  left/right/up 240..250
    """
    import beepgate as BG
    curves = {}
    for key in tuple(keys) + tuple(idle_keys):
        h = BG.fresh()
        BG.align(h, key)
        sim = h.sim
        regs, ops, mem = sim.registers, sim.opcodes, sim.memory
        fd, ia = h.frame_duration, h.int_active
        npassed = nsteps = 0
        t_top = None
        cur, per = [], []
        while npassed < npass and nsteps < 200_000_000:
            pc = regs[PC]
            if pc == 0x8503:
                if t_top is not None and cur:
                    per.append(cur)
                cur = []
                t_top = regs[T]
                npassed += 1
            if pc == 0xB8CC and t_top is not None:
                cur.append((regs[T] - t_top) / FRAME_T)
            if h.deck is not None and pc == TAPE_CALL_PC:
                h._tape(); nsteps += 1; continue
            if mem[pc] == 0x76 and regs[IFF]:
                h._fast_halt(); nsteps += 1; continue
            ops[mem[pc]]()
            if regs[IFF] and regs[T] % fd < ia:
                sim.accept_interrupt(regs, mem, pc)
            nsteps += 1
        qs = [i / steps for i in range(steps + 1)]
        rows = [[p[min(len(p) - 1, int(round(q * (len(p) - 1))))] for q in qs]
                for p in per]
        curves[key] = ([sum(r[i] for r in rows) / len(rows)
                        for i in range(len(qs))],
                       [len(p) for p in per])
    qs = [i / steps for i in range(steps + 1)]

    def pool(group):
        c = [round(sum(curves[k][0][i] for k in group) / len(group), 4)
             for i in range(len(qs))]
        sp = max(max(curves[k][0][i] for k in group)
                 - min(curves[k][0][i] for k in group)
                 for i in range(len(qs))) if len(group) > 1 else 0.0
        # MONOTONIC (it is a list of times in issue order) and inside the
        # blit window the `where` measurement found, both asserted not fitted
        assert all(c[i] <= c[i + 1] for i in range(len(c) - 1)), \
            f'the {group} tick curve is not monotonic'
        assert 0.02 < c[0] < 0.20 and 1.20 < c[-1] < 1.50, \
            f'the {group} tick curve leaves the blit window: {c[0]}..{c[-1]}'
        return c, round(sp, 4)

    walk, walk_sp = pool(list(keys))
    idle, idle_sp = pool(list(idle_keys))
    counts = [n for k in curves for n in curves[k][1]]
    # THE SPLIT HAS TO EARN ITSELF, PER POINT.  At each q the walk/idle gap
    # is compared with the SPREAD INSIDE the walking group at that same q --
    # signal against noise.  It wins over the first half of the curve, which
    # is the half a 127-call burst out of ~249 objects actually reaches, and
    # LOSES in the tail, where the scroll state moves the last object by up
    # to 0.19 frames.  Both facts are recorded rather than averaged away.
    persp = [max(curves[k][0][i] for k in keys)
             - min(curves[k][0][i] for k in keys) for i in range(len(qs))]
    gaps = [abs(walk[i] - idle[i]) for i in range(len(qs))]
    half = steps // 2
    beaten = [i for i in range(half + 1) if gaps[i] <= persp[i]]
    assert not beaten, \
        ('the walk/idle split is inside the walking scenes\' own spread at '
         f'q={[round(i / steps, 2) for i in beaten]}')
    return {'q_steps': steps, 'frames': walk, 'frames_idle': idle,
            'spread_frames': walk_sp, 'spread_frames_idle': idle_sp,
            'spread_by_q': [round(x, 4) for x in persp],
            'walk_idle_gap_by_q': [round(x, 4) for x in gaps],
            'walk_idle_gap': round(max(gaps), 4),
            'calls_per_pass': [min(counts), max(counts)],
            'calls_mean': round(sum(counts) / len(counts), 1),
            'scenes': list(keys), 'idle_scenes': list(idle_keys),
            'passes_per_scene': npass,
            'per_scene': {k: [round(x, 4) for x in curves[k][0]]
                          for k in curves}}


def parse_dispatch(m):
    """Walk $B92B's CP chain out of the BYTES and return {id: stream}."""
    a = DISPATCH
    assert m[a] == 0xE5, 'no PUSH HL at $B92B'
    a += 1
    ids = {}
    while m[a] == 0xFE and m[a + 2] == 0x20 and m[a + 3] == 0x05 \
            and m[a + 4] == 0x21 and m[a + 7] == 0x18:
        ident = m[a + 1]
        tbl = m[a + 5] | (m[a + 6] << 8)
        # the JR at +7 must land on the common tail $B98B
        assert a + 9 + ((m[a + 8] ^ 0x80) - 0x80) == 0xB98B, 'chain tail moved'
        ids[ident] = tbl
        a += 9
    # the ALIAS: CP 6 / JR z,<back into an earlier arm's LD HL>
    assert m[a] == 0xFE and m[a + 2] == 0x28, 'no aliased id at the chain tail'
    alias_id = m[a + 1]
    target = a + 4 + ((m[a + 3] ^ 0x80) - 0x80)
    assert m[target] == 0x21, 'the alias does not land on a LD HL,nn'
    ids[alias_id] = m[target + 1] | (m[target + 2] << 8)
    a += 4
    # POP HL / CP 4 -> noise DOWN from $7F ; OR A -> noise UP from $01 ; RET
    assert m[a] == 0xE1, 'no POP HL before the noise arms'
    assert m[a + 1] == 0xFE and m[a + 3] == 0x20, 'no CP for the noise arm'
    noise_down_id = m[a + 2]
    assert m[a + 5] == 0x3E and m[a + 7] == 0xC3, 'noise DOWN arm moved'
    noise_down = (m[a + 6], m[a + 8] | (m[a + 9] << 8))
    assert m[a + 10] == 0xB7, 'no OR A for the id-0 arm'
    assert m[a + 13] == 0x3E and m[a + 15] == 0xC3, 'noise UP arm moved'
    noise_up = (m[a + 14], m[a + 16] | (m[a + 17] << 8))
    assert m[a + 12] == 0x05 and m[a + 18] == 0xC9, 'the silent RET moved'
    return ids, noise_down_id, noise_down, noise_up


def streams(m, ids):
    out = {}
    covered = set()
    for ident, addr in sorted(ids.items()):
        n = m[addr]
        rows = [[m[addr + 1 + 2 * i], m[addr + 2 + 2 * i]] for i in range(n)]
        out[ident] = {'addr': addr, 'rows': rows}
        for k in range(addr, addr + 1 + 2 * n):
            covered.add(k)
    # invariant 1: the streams tile $B995..$BA00 exactly
    assert min(covered) == STREAM_LO and max(covered) == STREAM_HI - 1, \
        'the streams do not start at $B995 and end at $BA00'
    assert len(covered) == STREAM_HI - STREAM_LO, \
        'the streams leave a gap or overlap in $B995..$BA00'
    distinct = {v['addr'] for v in out.values()}
    assert len(distinct) == 8, 'expected 8 distinct streams'
    recs = sum(m[a] for a in distinct)
    assert recs == 50, f'expected 50 step records, got {recs}'
    played = sum(len(v['rows']) for v in out.values())
    assert played == 59, f'expected 59 played steps across 9 ids, got {played}'
    # invariants 3 and 4
    for v in out.values():
        for c, e in v['rows']:
            assert c % 2 == 0, 'an ODD toggle count would walk the DC level'
            assert c != 0 and e != 0, 'a zero field would reach the 256 wrap'
    return out


def half_period(e):
    """MEASURED, not derived: tools/beepgate.py fit sweeps E over 0..255 and
    compares the recorded inter-edge T gaps.  0 means 256 (the DJNZ wrap) and
    is not reachable from the shipped data."""
    return 17 * (e or 256) + 31


def tune(h, entry):
    """Drive the real tune player and record one row per hook."""
    sim = h.sim
    regs = sim.registers
    ops = sim.opcodes
    mem = sim.memory
    sp = regs[SP]
    for s in reversed(Harness.SENTINELS):
        sp = (sp - 2) & 0xFFFF
        mem[sp] = s & 0xFF
        mem[sp + 1] = s >> 8
    regs[SP] = sp
    regs[PC] = entry
    t0 = regs[T]
    rows = []
    n = 0
    while n < 80_000_000:
        pc = regs[PC]
        if pc in Harness.SENTINELS:
            break
        if pc == TUNE_LOOP_HOOK:
            rows.append([regs[T] - t0, regs[D], regs[H], 0])
        elif pc == TUNE_MUTE_HOOK:
            rows.append([regs[T] - t0, 1, 1, 0])
        elif pc == TUNE_TURN_HOOK and rows:
            # $C087 is the top of the 96 T interleave turn; each turn does
            # exactly TWO OUTs, voice 1's state then (48 T later) voice 2's
            rows[-1][3] += 1
        ops[mem[pc]]()
        n += 1
    total = regs[T] - t0
    out = []
    for i, (t, p1, p2, turns) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else total
        out.append({'t': t, 'len_t': nxt - t, 'reload1': p1, 'reload2': p2,
                    'turns': turns,
                    'hz1': CPU_HZ / (2 * TUNE_T * p1),
                    'hz2': CPU_HZ / (2 * TUNE_T * p2)})
    return {'entry': entry, 'total_t': total, 'rows': out,
            'outs': 2 * sum(r['turns'] for r in out)}


def main():
    h = load()
    m = h.memobj.m
    ids, ndown_id, ndown, nup = parse_dispatch(m)
    st = streams(m, ids)
    tunes = {}
    for label, entry in (('banner', 0xB8B0), ('level', 0xB8B5)):
        # a fresh machine per tune: the player is LDIR'd over $C000 each time
        hh = load()
        tunes[label] = tune(hh, entry)
    # the two totals, asserted against the wall cost measured in isolation
    assert abs(tunes['banner']['total_t'] / FRAME_T - 72.07) < 0.05, \
        'the banner tune is not 72.07 video frames'
    assert abs(tunes['level']['total_t'] / FRAME_T - 209.97) < 0.05, \
        'the level-intro tune is not 209.97 video frames'
    assert len(tunes['banner']['rows']) == 8
    assert len(tunes['level']['rows']) == 23

    data = {
        'cpu_hz': CPU_HZ, 'frame_t': FRAME_T, 'speaker_bit': 0x10,
        'dispatch': {str(k): v['addr'] for k, v in sorted(st.items())},
        'streams': {str(k): v['rows'] for k, v in sorted(st.items())},
        'silent': [i for i in range(18) if i not in st and i != 4 and i != 0],
        'noise': {'down_id': ndown_id, 'down_level': ndown[0],
                  'up_level': nup[0], 'ramp_calls': 127,
                  'arm_down': ndown[1], 'arm_up': nup[1],
                  'tick_curve': tick_curve()},
        'half_period': {str(e): half_period(e)
                        for e in sorted({r[1] for v in st.values()
                                         for r in v['rows']})},
        'tunes': tunes,
        'tune_loop_t': TUNE_T, 'tune_out2_t': TUNE_OUT2,
    }
    # cross-check: the OUT count synthesised from the measured turn counts
    # against the port writes the routine actually makes.  This is an
    # independent quantity -- turns are counted at $C087, writes at the port.
    for label, entry in (('banner', 0xB8B0), ('level', 0xB8B5)):
        hh = load()
        hh.ports.record_writes = True
        hh.ports.writes = []
        hh.call(entry, limit=40_000_000, interrupts=True)
        hh.ports.record_writes = False
        got = len([1 for t, p, v in hh.ports.writes if (p & 0xFF) == 0xFE])
        want = tunes[label]['outs']
        assert abs(got - want) <= 4, \
            f'{label}: {want} OUTs synthesised from turns, {got} measured'
        tunes[label]['outs_measured'] = got
    silent = data['silent']
    assert sorted(silent) == [1, 3, 5, 9, 12, 13, 16], \
        f'the seven silent ids moved: {silent}'

    if '--print' in sys.argv:
        print('THE 48K BEEPER, from build/state_48k.pkl (the game\'s own probe)')
        print('dispatcher $B92B -> %d ids, %d distinct streams, %d records'
              % (len(st), len({v['addr'] for v in st.values()}),
                 sum(m[a] for a in {v['addr'] for v in st.values()})))
        for k, v in sorted(st.items()):
            hz = ' '.join('%.0f' % (CPU_HZ / (2 * half_period(e)))
                          for c, e in v['rows'])
            print('  id %2d  $%04X  %2d steps  %s Hz' % (k, v['addr'],
                                                         len(v['rows']), hz))
        print('  silent (a bare RET at $B98A): %s' % silent)
        print('  noise: id 0 arms UP from %d, id %d arms DOWN from %d, '
              '127 ramp calls' % (nup[0], ndown_id, ndown[0]))
        for label, tn in tunes.items():
            print('  tune %-6s $%04X  %6.2f video frames, %d rows'
                  % (label, tn['entry'], tn['total_t'] / FRAME_T,
                     len(tn['rows'])))
            for r in tn['rows']:
                print('      %6.2f f  len %5.2f f  reload %3d/%3d  '
                      '%8.1f / %8.1f Hz'
                      % (r['t'] / FRAME_T, r['len_t'] / FRAME_T,
                         r['reload1'], r['reload2'], r['hz1'], r['hz2']))
        return

    json.dump(data, open(OUT, 'w'), separators=(',', ':'))
    print('wrote %s: %d ids, %d streams, %d records, %d + %d tune rows'
          % (OUT, len(st), len({v['addr'] for v in st.values()}),
             sum(m[a] for a in {v['addr'] for v in st.values()}),
             len(tunes['banner']['rows']), len(tunes['level']['rows'])))


if __name__ == '__main__':
    main()
