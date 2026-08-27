#!/usr/bin/env python3
"""
tunegate.py -- drive the REAL Z80 through everything this game calls a TUNE
(manual 11.8) and measure it.  It never loads the engine; it only asks the
original.

    python tools/tunegate.py probe      the 128K probe -> what ($FFFD) really is
    python tools/tunegate.py config     the 9 bytes $BF21 patches, both ways
    python tools/tunegate.py intro      the DUNGEON n screen, AY and beeper
    python tools/tunegate.py tally      the treasure-room pay-out screen
    python tools/tunegate.py treasure   the treasure-room music, mode 2
    python tools/tunegate.py walk       walk the AY tune data statically
    python tools/tunegate.py rows       tempo: video frames per row, measured
    python tools/tunegate.py beeper     the two-voice beeper tune blobs
    python tools/tunegate.py all

THE TWO SOUND WORLDS.  Block A's front-end runs a $7FFD paging probe ($CE50,
relocated to $61A8) and stores 0 (48K) or 1 (128K) at RAM address $FFFD --
which is NOT the AY register-select PORT of the same number.  $BF21, during
the game's own boot at $BEB9, reads that byte and patches nine bytes:

    ($FFFD) != 0   AY:      $B8B5 := C9, $B8CC := C9   (the beeper players die)
    ($FFFD) == 0   BEEPER:  $BADB $BA01 $BBA7 $BBBC := C9   (the AY tick, the
                            AY reset and BOTH music-start entries die)
                            $BA2B := JP $B92B          (sfx -> the beeper bank)

build/state_charsel.pkl was saved with ($FFFD) = $2A -- a STALE byte, because
the harness has never run block A -- so every sound measurement made from it
so far has been on the AY side of a machine that is a 48K in every other
respect.  `probe` settles which side a real 48K takes.
"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from harness import (Harness, PC, T, IFF, A, B, C, D, E, H, L, SP,   # noqa: E402
                     FRAME_T, CPU_HZ)

STATE = os.path.join(ROOT, 'build', 'state_charsel.pkl')

# --- the addresses this file is about -------------------------------------
SOUNDSEL = 0xFFFD          # RAM.  0 = 48K/beeper, 1 = 128K/AY
PATCH = 0xBF21             # the boot-time patcher (static image only)
AY_TICK = 0xBADB           # per-ISR: music row + 3 sfx channels
AY_RESET = 0xBA01
AY_SFX = 0xBA2B            # play effect A
MUS1 = 0xBBA7              # start tune 1  (the DUNGEON n / treasure-found jingle)
MUS2 = 0xBBBC              # start tune 2  (the treasure-room loop)
MUSTICK = 0xBC0C
ISR = 0xA29F
FLAGS = 0x847D             # (IY-2).  bit 2 = "hold for the tune", bit 5 = which
BANNER = 0x84B9            # (IY+$3A) the banner code the $891C path reads
MODE = 0xBDE9              # 0 none, 1 tune 1, 2 tune 2
TEMPO_CNT = 0xBDEA         # frames left in this row
TEMPO = 0xBDEB             # frames per row
TRANSPOSE = 0xBDEC
CHAN = (0xBDC2, 0xBDCF, 0xBDDC)     # 13 bytes each
NOTES = 0x6A4E             # 16-bit tone periods, chromatic
ORNS = 0x6A00              # pointers to volume envelopes
TUNE1 = (0x6C82, 0x6CA0, 0x6CBC)
TUNE2 = (0x6B0E, 0x6B18, 0x6B46)
BLOB = {0xB8B0: 0x6740, 0xB8B5: 0x685D}   # the two beeper tune blobs
AY_CLOCK = 1_773_400

# the 9 bytes $BF21 writes, measured by booting it both ways (see `config`)
BEEPER_PATCH = {0xB8B5: 0x21, 0xB8CC: 0xED, 0xBADB: 0xC9, 0xBA01: 0xC9,
                0xBBA7: 0xC9, 0xBBBC: 0xC9, 0xBA2B: 0xC3, 0xBA2C: 0x2B,
                0xBA2D: 0xB9}
AY_PATCH = {0xB8B5: 0xC9, 0xB8CC: 0xC9, 0xBADB: 0xD5, 0xBA01: 0x3E,
            0xBBA7: 0x3E, 0xBBBC: 0x3E, 0xBA2B: 0xC5, 0xBA2C: 0xD5,
            0xBA2D: 0xE5}


def boot(mode='ay'):
    """The saved live state, put into one of the two sound configurations."""
    h = Harness()
    h.load_state(pickle.load(open(STATE, 'rb')))
    patch = BEEPER_PATCH if mode == 'beeper' else AY_PATCH
    for a, v in patch.items():
        h.poke(a, v)
    h.poke(SOUNDSEL, 0 if mode == 'beeper' else 1)
    return h


def frames(h, t0):
    return (h.regs[T] - t0) / FRAME_T


def rec(h):
    h.ports.writes = []
    h.ports.record_writes = True


# ---------------------------------------------------------------- decoding
def ay_stream(writes):
    """(T, port, value) -> (T, register, value).  $FFFD selects, $BFFD writes.
    Every register write in this game is the pair $BB9D/$BBA2 back to back."""
    out, sel = [], None
    for t, port, v in writes:
        if port == 0xFFFD:
            sel = v
        elif port == 0xBFFD and sel is not None:
            out.append((t, sel, v))
    return out


def speaker_edges(writes):
    """Changes of bit 4 of port $FE, with their T-stamps."""
    out, last = [], None
    for t, port, v in writes:
        if (port & 0xFF) != 0xFE:
            continue
        bit = (v >> 4) & 1
        if last is not None and bit != last:
            out.append((t, bit))
        last = bit
    return out


def by_frame(stream, t0):
    """Bucket (T, reg, val) by 50 Hz frame index from t0."""
    buckets = {}
    for t, r, v in stream:
        buckets.setdefault((t - t0) // FRAME_T, []).append((r, v))
    return buckets


# ---------------------------------------------------------------- commands
def cmd_probe():
    print('--- the machine probe ($CE50 in block A, run at $61A8) ---')
    h = Harness()
    a = open(os.path.join(ROOT, 'build', 'image_a.bin'), 'rb').read()
    h.memobj.m[0x4000:0x10000] = bytearray(a[0x4000:0x10000])
    h.regs[SP] = 0xFFFB
    idx, t, steps = h.call(0xCE50, regs={})
    got = h.regs[A]
    sel = 0 if got == 0xD1 else 1
    print(f'  $CE50 returns A = ${got:02X}   ($C23B CP $D1 -> '
          f'{"Z, 48K" if got == 0xD1 else "NZ, 128K"})')
    print(f'  $C242 would store ($FFFD) = {sel}  -> '
          f'{"BEEPER" if sel == 0 else "AY"}')
    h2 = Harness()
    h2.load_state(pickle.load(open(STATE, 'rb')))
    print(f'  build/state_charsel.pkl actually holds ($FFFD) = '
          f'${h2.peek(SOUNDSEL)[0]:02X}  <- STALE, block A never ran')


def cmd_config():
    print('--- what $BF21 patches, booted both ways ---')
    snaps = {}
    for val in (0, 1):
        h = Harness()
        h.poke(SOUNDSEL, val)
        h.run_until(0xBEBC, limit=3_000_000)
        snaps[val] = bytes(h.memobj.m)
    diff = [i for i in range(0x4000, 0x10000) if snaps[0][i] != snaps[1][i]]
    print('  bytes differing between the two boots:',
          ' '.join('$%04X' % a for a in diff))
    for a in diff:
        print(f'    ${a:04X}   48K ${snaps[0][a]:02X}   128K ${snaps[1][a]:02X}')


def hold_test(mode, label, setup):
    """Run the tune-hold path and report what it cost and what it played."""
    h = boot(mode)
    setup(h)
    rec(h)
    t0 = h.regs[T]
    idx, t, steps = h.call(0x8B27 if label == 'intro' else 0x891C,
                           regs={}, limit=40_000_000, interrupts=True)
    w = h.ports.writes
    ay = ay_stream(w)
    edges = speaker_edges(w)
    print(f'  [{mode:6s}] {label}: {t/FRAME_T:8.2f} video frames '
          f'({t/CPU_HZ:5.2f} s), {steps} instructions, exit {idx}')
    print(f'           AY register writes {len(ay):5d}   '
          f'speaker edges {len(edges):5d}   $FE writes '
          f'{sum(1 for _, p, _ in w if (p & 0xFF) == 0xFE)}')
    if ay:
        regs = sorted({r for _, r, _ in ay})
        print(f'           registers touched: {regs}')
    return h, w, t


def cmd_intro():
    print('--- the DUNGEON n screen ($B38E CALL $8B27) ---')
    print('    $8B84 SET 5,(IY-2) / $8B88 CALL $BBA7 / $8B98 SET 2,(IY-2)')
    for mode in ('ay', 'beeper'):
        h, w, t = hold_test(mode, 'intro', lambda h: None)
        if mode == 'ay':
            ay = ay_stream(w)
            t0 = ay[0][0] if ay else 0
            bf = by_frame(ay, t0)
            live = [f for f in sorted(bf) if bf[f]]
            print(f'           frames carrying writes: {len(live)} of '
                  f'{max(bf)+1 if bf else 0}')
            per = {}
            for f in live:
                per[len(bf[f])] = per.get(len(bf[f]), 0) + 1
            print(f'           writes per active frame: {dict(sorted(per.items()))}')
            gaps = [live[i+1] - live[i] for i in range(len(live)-1)]
            hist = {}
            for g in gaps:
                hist[g] = hist.get(g, 0) + 1
            print(f'           gap between active frames: {dict(sorted(hist.items()))}')
            print(f'           tempo bytes after: ($BDEA)=${h.peek(TEMPO_CNT)[0]:02X} '
                  f'($BDEB)=${h.peek(TEMPO)[0]:02X} mode=${h.peek(MODE)[0]:02X}')
        else:
            ed = speaker_edges(w)
            if ed:
                print(f'           first edge at T+{ed[0][0]}, last at T+{ed[-1][0]}'
                      f'  ({(ed[-1][0]-ed[0][0])/FRAME_T:.2f} frames of tone)')


def cmd_tally():
    """The banner path sets the flag and RETURNS; the hold happens at the
    BOTTOM of the same main-loop pass ($8550 CALL $9CD7 -> $9D01), so this has
    to be measured over a whole pass, not over $891C in isolation."""
    from sim_move import step_to_loop_top
    print('--- the treasure-room pay-out ($8543 CALL $891C, banner code $40) ---')
    print('    $894B CALL $BA01 (AY silence) / $8996 SET 2,(IY-2), bit 5 CLEAR')
    for mode in ('ay', 'beeper'):
        h = boot(mode)
        step_to_loop_top(h)
        base = step_to_loop_top(h) / FRAME_T
        h.poke(BANNER, 0x40)
        rec(h)
        t = step_to_loop_top(h)
        w = h.ports.writes
        ay, ed = ay_stream(w), speaker_edges(w)
        print(f'  [{mode:6s}] a normal pass {base:5.2f} frames;  the pay-out pass '
              f'{t/FRAME_T:8.2f} frames ({t/CPU_HZ:5.2f} s)')
        print(f'           AY register writes {len(ay):5d}  speaker edges {len(ed):6d}'
              f'  $FE writes {sum(1 for _, p, _ in w if (p & 0xFF) == 0xFE)}')
        if ay:
            print(f'           registers touched: {sorted({r for _, r, _ in ay})}')
        print(f'           banner byte after: ${h.peek(BANNER)[0]:02X}, '
              f'($847D) = ${h.peek(FLAGS)[0]:02X}')


def cmd_treasure():
    print('--- the treasure-room music ($8527 -> $8ADA -> $BBBC, mode 2) ---')
    from sim_move import step_to_loop_top
    for mode in ('ay', 'beeper'):
        h = boot(mode)
        step_to_loop_top(h)
        h.poke(0x847E, h.peek(0x847E)[0] | 0x40)    # (IY-1) bit 6: treasure room
        h.poke(0x84B6, 0x30)          # the room's own BCD clock, seeded by $915E
        h.poke(0x8438, 12)            # (IY+$38) the 12-pass divider
        rec(h)
        t0 = h.regs[T]
        passes, cost = 0, []
        while (h.regs[T] - t0) < 300 * FRAME_T:
            cost.append(step_to_loop_top(h) / FRAME_T)
            passes += 1
        ay = ay_stream(h.ports.writes)
        print(f'  [{mode:6s}] {passes} passes / {(h.regs[T]-t0)/FRAME_T:.1f} frames: '
              f'mode=${h.peek(MODE)[0]:02X} tempo=${h.peek(TEMPO)[0]:02X} '
              f'row-counter=${h.peek(TEMPO_CNT)[0]:02X}')
        print(f'           channel pointers now ' +
              ' '.join('$%04X' % h.peek16(c) for c in CHAN))
        print(f'           AY register writes {len(ay)}, speaker edges '
              f'{len(speaker_edges(h.ports.writes))}')
        print(f'           slowest pass {max(cost):.2f} frames, '
              f'median {sorted(cost)[len(cost)//2]:.2f} -- the music does NOT block')
        if ay:
            bf = by_frame(ay, t0)
            live = sorted(f for f in bf if bf[f])
            per = {}
            for f in live:
                per[len(bf[f])] = per.get(len(bf[f]), 0) + 1
            print(f'           active frames {len(live)} of '
                  f'{int((h.regs[T]-t0)//FRAME_T)+1}, writes per frame '
                  f'{dict(sorted(per.items()))}')
            print(f'           registers touched {sorted({r for _, r, _ in ay})}')


# --------------------------------------------------- the tune data, walked
CMD = {0x00: 'REST', 0x19: 'LONGREST', 0x1E: 'END', 0x11: 'LOOPSTART',
       0x10: 'LOOPEND', 0x02: 'ORNAMENT', 0x1D: 'FASTER', 0x1B: 'VOLADJ'}


def walk_channel(mem, addr, limit=4000):
    """A transcription of $BC8B's decoder: 2-byte commands, notes have bit 7."""
    out = []
    p = addr
    frames = 0
    loop_start = None
    for _ in range(limit):
        a, c = mem[p], mem[p + 1]
        rec = (p, a, c)
        p += 2
        if a & 0x80:
            out.append((rec, 'NOTE', a & 0x7F, c))
            frames += c
            continue
        name = CMD.get(a, 'CMD$%02X' % a)
        out.append((rec, name, a, c))
        if a == 0x00:
            frames += c
        elif a == 0x19:
            frames += (c - 1) * 255
        elif a == 0x1E:
            break
        elif a == 0x11:
            loop_start = p
        elif a == 0x10:
            pass
    return out, frames, p


def cmd_walk():
    mem = open(os.path.join(ROOT, 'build', 'live_cs.bin'), 'rb').read()
    print('--- the AY tune data, walked with a transcription of $BC8B ---')
    for name, chans in (('tune 1  $BBA7  DUNGEON n / TREASURE ROOM found', TUNE1),
                        ('tune 2  $BBBC  inside the treasure room', TUNE2)):
        print(f'\n  {name}')
        for i, base in enumerate(chans):
            ev, fr, end = walk_channel(mem, base)
            notes = [e for e in ev if e[1] == 'NOTE']
            cmds = {}
            for e in ev:
                if e[1] != 'NOTE':
                    cmds[e[1]] = cmds.get(e[1], 0) + 1
            durs = sorted({e[3] for e in notes})
            pit = sorted({e[2] for e in notes})
            print(f'    ch{i} base ${base:04X} end ${end:04X} '
                  f'({end-base:3d} bytes, {(end-base)//2:3d} entries)  '
                  f'{len(notes):3d} notes')
            span = f'{pit[0]}..{pit[-1]}' if pit else '(none)'
            print(f'         durations(rows) {durs}   note indices {span}'
                  f'   commands {cmds}')
            print(f'         rows before END (loops not expanded): {fr}')
            print('         entries: ' + ' '.join(
                (f'N{e[2]}/{e[3]}' if e[1] == 'NOTE' else f'{e[1]}({e[3]})')
                for e in ev))


def cmd_rows():
    """Tempo, measured: how many ISR ticks between two rows."""
    print('--- tempo: video frames per row, measured at $BC8B ---')
    h = boot('ay')
    h.call(MUS1, regs={}, limit=200_000, interrupts=False)
    print(f'  after $BBA7: mode=${h.peek(MODE)[0]:02X} '
          f'($BDEA)=${h.peek(TEMPO_CNT)[0]:02X} ($BDEB)=${h.peek(TEMPO)[0]:02X}')
    # tick the music player by hand, once per "frame", and log row boundaries
    rows = []
    ticks = 0
    for i in range(1200):
        before = h.peek(TEMPO_CNT)[0]
        h.call(MUSTICK, regs={}, limit=200_000, interrupts=False)
        ticks += 1
        if h.peek(TEMPO_CNT)[0] != before - 1:
            rows.append(ticks)
        if h.peek(MODE)[0] == 0:
            break
    gaps = [rows[i+1] - rows[i] for i in range(len(rows)-1)]
    hist = {}
    for g in gaps:
        hist[g] = hist.get(g, 0) + 1
    print(f'  {len(rows)} rows in {ticks} ticks; gap histogram {dict(sorted(hist.items()))}')
    print(f'  tempo byte at the end: ${h.peek(TEMPO)[0]:02X}, '
          f'mode ${h.peek(MODE)[0]:02X}')
    print(f'  => tune 1 is {ticks} frames = {ticks/50.08:.2f} s if the ISR drives it')


def cmd_beeper():
    print('--- the two beeper tune blobs ---')
    mem = open(os.path.join(ROOT, 'build', 'live_cs.bin'), 'rb').read()
    for entry, src in sorted(BLOB.items()):
        print(f'  ${entry:04X}: LDIR ${src:04X} -> $C000, $13E bytes, JP $C000')
        ch1 = mem[src + 0x109 - 0xC000 + 0xC000 - src + src] if False else None
    for entry, src in sorted(BLOB.items()):
        h = boot('beeper')
        rec(h)
        t0 = h.regs[T]
        idx, t, steps = h.call(entry, regs={}, limit=40_000_000, interrupts=False)
        w = h.ports.writes
        ed = speaker_edges(w)
        print(f'  ${entry:04X} (data ${src:04X}): {t/FRAME_T:7.2f} frames '
              f'({t/CPU_HZ:5.2f} s), {len(ed)} speaker edges, exit {idx}')
        if len(ed) > 2:
            per = []
            for i in range(1, len(ed)):
                per.append(ed[i][0] - ed[i-1][0])
            per.sort()
            print(f'    half-period T-states: min {per[0]} median '
                  f'{per[len(per)//2]} max {per[-1]}   '
                  f'=> {CPU_HZ/(2*per[-1]):.0f}..{CPU_HZ/(2*per[0]):.0f} Hz')


def hook_count(h, addrs, t_limit, interrupts=True, stop=None):
    """Run for t_limit T-states counting visits to each address, with the
    T-stamp of every visit.  A PC comparison, never a byte patch."""
    sim = h.sim
    regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
    fd, ia = h.frame_duration, h.int_active
    hits = {a: [] for a in addrs}
    t0 = regs[T]
    while regs[T] - t0 < t_limit:
        pc = regs[PC]
        if pc in hits:
            hits[pc].append(regs[T] - t0)
        if stop is not None and pc == stop:
            break
        if h.deck is not None and pc == 0x9247:
            h._tape()
            continue
        if interrupts and mem[pc] == 0x76 and regs[IFF]:
            h._fast_halt()
            continue
        opcodes[mem[pc]]()
        if interrupts and regs[IFF] and regs[T] % fd < ia:
            sim.accept_interrupt(regs, mem, pc)
    return hits, regs[T] - t0


def cmd_ticks():
    """How often is the music player actually ticked?  Not once per frame."""
    print('--- the tick rate of the AY music player ($BC0C) ---')
    print('    $DADA JP $A29F is the ISR and $A2A5 CALL $BADB is inside it;')
    print('    $9CF8 CALL $A29F calls the SAME body again by hand, from the')
    print('    bottom of every main-loop pass.  So play and hold differ.')
    from sim_move import step_to_loop_top
    h = boot('ay')
    step_to_loop_top(h)
    h.poke(0x847E, h.peek(0x847E)[0] | 0x40)
    h.poke(0x84B6, 0x30)
    h.poke(0x8438, 12)
    step_to_loop_top(h)                      # starts tune 2
    hits, t = hook_count(h, (MUSTICK, ISR, 0x8503), 200 * FRAME_T)
    fr = t / FRAME_T
    print(f'  IN PLAY, {fr:.1f} video frames: $BC0C {len(hits[MUSTICK])} ticks, '
          f'$A29F {len(hits[ISR])} entries, $8503 {len(hits[0x8503])} passes')
    print(f'    => {len(hits[MUSTICK])/fr:.3f} ticks per video frame '
          f'({len(hits[MUSTICK])/(t/CPU_HZ):.2f} Hz), i.e. one per frame PLUS one '
          f'per pass')
    h2 = boot('ay')
    h2.call(MUS1, regs={}, limit=200_000, interrupts=False)
    h2.poke(FLAGS, h2.peek(FLAGS)[0] | 0x24)          # bits 2 and 5
    hits2, t2 = hook_count(h2, (MUSTICK, ISR), 40 * FRAME_T)
    # the hold itself
    h3 = boot('ay')
    hits3 = {}
    sim = h3.sim
    h3.regs[SP] = (h3.regs[SP] - 2) & 0xFFFF
    h3.poke(h3.regs[SP], 0x00, 0xFE)
    hits3, t3 = None, None
    h4 = boot('ay')
    rec(h4)
    t0 = h4.regs[T]
    hits4, t4 = None, None
    h4.regs[PC] = 0x8B27
    hh, tt = hook_count(h4, (MUSTICK, ISR, 0x9D2D), 270 * FRAME_T)
    fr = tt / FRAME_T
    print(f'  IN THE $9D2D HOLD, {fr:.1f} frames: $BC0C {len(hh[MUSTICK])} ticks, '
          f'$9D2D {len(hh[0x9D2D])} iterations')
    print(f'    => {len(hh[MUSTICK])/fr:.3f} ticks per video frame '
          f'(the hold is HALT/HALT, so exactly 50 Hz)')


def cmd_beeprows():
    """Row duration of the two-voice beeper tunes, measured at $C047."""
    print('--- the beeper tunes: rows, and what a row costs ---')
    print('    the engine is 318 bytes: code $C000..$C0D3, a 53-entry one-byte')
    print('    semitone table $C0D4..$C108, then TWO note streams ending in $40.')
    for entry, src in sorted(BLOB.items()):
        h = boot('beeper')
        h.regs[PC] = entry
        hits, t = hook_count(h, (0xC047,), 400 * FRAME_T, interrupts=False,
                             stop=0xC03F)
        rows = hits[0xC047]
        gaps = [rows[i+1] - rows[i] for i in range(len(rows)-1)]
        if not gaps:
            continue
        print(f'  ${entry:04X} (data ${src:04X}): {len(rows)} rows in '
              f'{t/FRAME_T:.2f} frames')
        print(f'    row cost: min {min(gaps)/FRAME_T:.2f} median '
              f'{sorted(gaps)[len(gaps)//2]/FRAME_T:.2f} max {max(gaps)/FRAME_T:.2f} '
              f'video frames')


def cmd_death():
    """Is there a death tune?  Measured, not assumed."""
    from sim_move import step_to_loop_top
    print('--- the death / game-over path: does anything play? ---')
    print('    $847D bit 2 (the only tune-hold trigger) is SET at exactly two')
    print('    sites in the whole image: $8996 and $8B98.  Neither is on the')
    print('    death path.  Driving it to be sure:')
    for mode in ('ay', 'beeper'):
        h = boot(mode)
        step_to_loop_top(h)
        for ix in (0x8420, 0x8440):                # kill both players
            h.poke(ix + 2, 0x00, 0x00)
            h.poke(ix + 0x14, h.peek(ix + 0x14)[0] | 0x80)
        h.poke(0x8434, h.peek(0x8434)[0] | 0x80)
        h.poke(0x8454, h.peek(0x8454)[0] | 0x80)
        rec(h)
        t0 = h.regs[T]
        hits, t = hook_count(h, (0x9D01, 0x9D24, 0xB3B9, 0x8B27), 600 * FRAME_T)
        w = h.ports.writes
        ay, ed = ay_stream(w), speaker_edges(w)
        print(f'  [{mode:6s}] {t/FRAME_T:.0f} frames: $9D01 {len(hits[0x9D01])}, '
              f'tune-hold entries {len(hits[0x9D24])}, $B3B9 {len(hits[0xB3B9])}, '
              f'$8B27 {len(hits[0x8B27])};  AY writes {len(ay)}, '
              f'speaker edges {len(ed)}')


def cmd_dump():
    """The per-frame AY register dump of tune 1 -- the gate a port needs."""
    import json
    print('--- tune 1 as a per-frame register dump (the phase 11 artefact) ---')
    h = boot('ay')
    rec(h)
    t0 = h.regs[T]
    h.regs[PC] = 0x8B27
    h.call(0x8B27, regs={}, limit=40_000_000, interrupts=True)
    ay = ay_stream(h.ports.writes)
    bf = by_frame(ay, t0)
    frames = []
    for f in range(max(bf) + 1):
        frames.append(sorted(bf.get(f, [])))
    out = os.path.join(ROOT, 'build', 'tune1_ay.json')
    json.dump({'note': 'per-50Hz-frame AY register writes, $8B27 intro screen',
               'clock_hz': AY_CLOCK, 'frames': frames}, open(out, 'w'))
    changed = 0
    prev = {}
    for fr in frames:
        for r, v in fr:
            if prev.get(r) != v:
                changed += 1
            prev[r] = v
    print(f'  {len(frames)} frames, {sum(len(f) for f in frames)} writes, '
          f'{changed} of them a CHANGE of the register value')
    print(f'  wrote {out} (LOCAL -- extracted game data, do not publish)')


def main():
    cmds = {'probe': cmd_probe, 'config': cmd_config, 'intro': cmd_intro,
            'tally': cmd_tally, 'treasure': cmd_treasure, 'walk': cmd_walk,
            'rows': cmd_rows, 'beeper': cmd_beeper, 'ticks': cmd_ticks,
            'beeprows': cmd_beeprows, 'death': cmd_death, 'dump': cmd_dump}
    args = sys.argv[1:] or ['all']
    names = list(cmds) if args[0] == 'all' else args
    for n in names:
        cmds[n]()
        print()


if __name__ == '__main__':
    main()
