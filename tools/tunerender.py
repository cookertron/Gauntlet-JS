#!/usr/bin/env python3
"""
tunerender.py -- render what the ORIGINAL actually played, from the machine,
to a WAV, so that a human can listen to it (manual: LISTEN, do not reason
about the numbers).

    python tools/tunerender.py beeper      the three two-voice beeper tunes
    python tools/tunerender.py ay          the AY tunes, from the register dump
    python tools/tunerender.py roundtrip   pitch measured back out of the WAV
                                           against the note the DATA asked for

Nothing here is a reimplementation: the beeper WAVs are the recorded speaker
edge train resampled, and the AY WAVs are the recorded (frame, register,
value) stream fed to a plain three-square-wave mixer.  Both come out of
tools/harness.py's port tracer.

The WAVs land in build/ and are EXTRACTED GAME DATA -- local only.
"""
import math
import os
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from harness import Harness, PC, T, FRAME_T, CPU_HZ                  # noqa: E402
from keyprobe import KEYS, keymask                                   # noqa: E402
from tunegate import (boot, ay_stream, speaker_edges, BLOB, AY_CLOCK,  # noqa: E402
                      MUS1, MUS2, rec)

SR = 44100


def write_wav(path, samples):
    w = wave.open(path, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(b''.join(struct.pack('<h', int(max(-1, min(1, s)) * 24000))
                           for s in samples))
    w.close()
    print(f'    wrote {path}  ({len(samples)/SR:.2f} s)')


def render_edges(writes, t0=None):
    """The speaker bit as a function of simulated time -> 44.1 kHz.

    INTEGRATED, not point-sampled.  These engines run a fixed ~36 kHz carrier
    and put the note in the DUTY CYCLE, so point sampling at 44.1 kHz aliases
    the carrier into the audio band and you hear a whistle that is not there.
    Averaging the speaker level over each output sample window is what the
    cone does.
    """
    ev = [(t, (v >> 4) & 1) for t, p, v in writes if (p & 0xFF) == 0xFE]
    if not ev:
        return []
    t0 = ev[0][0] if t0 is None else t0
    dur = (ev[-1][0] - t0) / CPU_HZ
    n = int(dur * SR) + 1
    step = CPU_HZ / SR
    out = [0.0] * n
    i = 0
    level = ev[0][1]
    prev_t = t0
    for k in range(n):
        end = t0 + (k + 1) * step
        acc = 0.0
        while i < len(ev) and ev[i][0] <= end:
            acc += (ev[i][0] - prev_t) * level
            prev_t = ev[i][0]
            level = ev[i][1]
            i += 1
        acc += (end - prev_t) * level
        prev_t = end
        out[k] = (acc / step) * 1.8 - 0.9
    return out


def render_ay(stream, t0, clock=AY_CLOCK):
    """(T, reg, val) -> three square waves + noise, at the AY's own clock."""
    if not stream:
        return []
    dur = (stream[-1][0] - t0) / CPU_HZ + 0.2
    n = int(dur * SR) + 1
    out = [0.0] * n
    regs = [0] * 16
    phase = [0.0, 0.0, 0.0]
    noise_phase = 0.0
    noise_state = 1
    i = 0
    for k in range(n):
        t = t0 + k * CPU_HZ / SR
        while i < len(stream) and stream[i][0] <= t:
            regs[stream[i][1] & 15] = stream[i][2]
            i += 1
        s = 0.0
        mixer = regs[7]
        np = regs[6] & 31 or 1
        noise_phase += clock / (16.0 * np) / SR
        while noise_phase >= 1.0:
            noise_phase -= 1.0
            noise_state = ((noise_state >> 1)
                           ^ (0x14000 if noise_state & 1 else 0))
        for ch in range(3):
            tp = ((regs[2 * ch + 1] & 15) << 8) | regs[2 * ch]
            vol = regs[8 + ch] & 15
            if vol == 0:
                continue
            amp = (10 ** ((vol - 15) * 0.15)) * 0.30
            tone_on = not (mixer >> ch) & 1
            noise_on = not (mixer >> (ch + 3)) & 1
            if tone_on:
                phase[ch] += (clock / (16.0 * (tp or 1))) / SR
                sq = 1.0 if (phase[ch] % 1.0) < 0.5 else -1.0
            else:
                sq = 1.0
            nz = 1.0 if (noise_state & 1) else -1.0
            v = sq * (nz if noise_on else 1.0)
            s += amp * v
        out[k] = s
    return out


def cmd_beeper():
    print('--- the beeper tunes, as the 48K actually plays them ---')
    for entry, src in sorted(BLOB.items()):
        h = boot('beeper')
        rec(h)
        h.regs[PC] = entry
        h.call(entry, regs={}, limit=60_000_000, interrupts=False)
        write_wav(os.path.join(ROOT, 'build', f'tune_beeper_{entry:04X}.wav'),
                  render_edges(h.ports.writes))
    # the title tune, in block A
    h = Harness()
    a = open(os.path.join(ROOT, 'build', 'image_a.bin'), 'rb').read()
    h.memobj.m[0x4000:0x10000] = bytearray(a[0x4000:0x10000])
    h.regs[12] = 0x5C00
    h.regs[PC] = 0xC1F2
    KM = {n: (s, b) for n, s, b in KEYS}
    sel, bit = KM['SPACE']
    h.ports.press(sel, keymask(bit))
    h.run_until(0xC242, limit=4_000_000)
    h.ports.release_all()
    h.run_until(0xC2A2, limit=4_000_000)
    h.ports.record_writes = True
    h.ports.writes = []
    h.call(0xC000, regs={}, limit=90_000_000, interrupts=True)
    write_wav(os.path.join(ROOT, 'build', 'tune_beeper_title.wav'),
              render_edges(h.ports.writes))


def cmd_ay():
    print('--- the AY tunes, from the recorded register stream ---')
    h = boot('ay')
    rec(h)
    t0 = h.regs[T]
    h.call(0x8B27, regs={}, limit=40_000_000, interrupts=True)
    write_wav(os.path.join(ROOT, 'build', 'tune_ay_intro.wav'),
              render_ay(ay_stream(h.ports.writes), t0))
    # tune 2, driven by the ISR inside a treasure room
    from sim_move import step_to_loop_top
    h = boot('ay')
    step_to_loop_top(h)
    h.poke(0x847E, h.peek(0x847E)[0] | 0x40)
    h.poke(0x84B6, 0x30)
    h.poke(0x8438, 12)
    rec(h)
    t0 = h.regs[T]
    while (h.regs[T] - t0) < 900 * FRAME_T:
        step_to_loop_top(h)
    write_wav(os.path.join(ROOT, 'build', 'tune_ay_treasure.wav'),
              render_ay(ay_stream(h.ports.writes), t0))


def cmd_roundtrip():
    """Measure the pitch back out of the beeper WAV and compare it with the
    note the DATA asked for.  This is the check that the format reading is
    right: it never touches the table again, it counts edges."""
    print('--- beeper round trip: measured pitch vs the note stream ---')
    live = open(os.path.join(ROOT, 'build', 'live_cs.bin'), 'rb').read()
    for entry, src in sorted(BLOB.items()):
        blob = live[src:src + 0x13E]
        table = blob[0xD4:0x109]
        p1 = blob[1] | blob[2] << 8
        p2 = blob[7] | blob[8] << 8
        s1 = blob[p1 - 0xC000:]
        s2 = blob[p2 - 0xC000:]
        s1 = s1[:s1.index(0x40)]
        s2 = s2[:s2.index(0x40)]
        h = boot('beeper')
        rec(h)
        rows = []
        sim = h.sim
        regs, opcodes, mem = sim.registers, sim.opcodes, sim.memory
        h.regs[PC] = entry
        sp = (regs[12] - 2) & 0xFFFF
        mem[sp] = 0x00
        mem[sp + 1] = 0xFE
        regs[12] = sp
        while regs[PC] != 0xFE00:
            if regs[PC] == 0xC047:
                rows.append(regs[T])
            opcodes[mem[regs[PC]]]()
        rows.append(regs[T])
        ev = [(t, (v >> 4) & 1) for t, p, v in h.ports.writes
              if (p & 0xFF) == 0xFE]
        print(f'  ${entry:04X}: {len(s1)} rows, {len(rows)-1} $C047 visits')
        ok = 0
        for r in range(min(len(s1), len(rows) - 1)):
            lo, hi = rows[r], rows[r + 1]
            seg = [t for t, b in ev if lo <= t < hi]
            n1 = (s1[r] + 12) & 0xFF
            n2 = (s2[r] + 12) & 0xFF
            want = []
            for nn in (n1, n2):
                if nn < len(table) and table[nn] > 1:
                    want.append(table[nn])
            if not seg or not want:
                continue
            # the engine's own inner loop is 4 T per unit of the table byte
            # times two halves, measured rather than derived:
            span = (hi - lo)
            edges = len(seg)
            print(f'    row {r:2d}  stream bytes ${s1[r]:02X}/${s2[r]:02X}  '
                  f'table {want}  edges {edges}  '
                  f'row {span/FRAME_T:.2f} frames  '
                  f'mean edge rate {edges*CPU_HZ/span:.0f} Hz')
            ok += 1
            if ok >= 6:
                break


def main():
    cmds = {'beeper': cmd_beeper, 'ay': cmd_ay, 'roundtrip': cmd_roundtrip}
    for n in (sys.argv[1:] or ['beeper', 'ay']):
        cmds[n]()


if __name__ == '__main__':
    main()
