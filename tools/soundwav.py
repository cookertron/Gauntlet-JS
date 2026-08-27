#!/usr/bin/env python3
"""
soundwav.py -- MEASURE THE FREQUENCIES BACK OUT OF THE SHIPPED ENGINE'S AUDIO.

    node   tools/soundwav.js          writes build/sound_effects.wav + .json
    python tools/soundwav.py          measures that WAV and reports

This is manual phase 14's round trip, and its whole point is that it closes
the loop through THE CODE THAT SHIPS rather than through a model agreeing
with itself.  soundwav.js drives web/gauntlet.html with the audio API stubbed
by a recorder and mixes what the page actually SCHEDULED; this reads those
samples back, finds where each effect is by its own envelope, and measures
the pitch out of the waveform with an FFT.

The expected value for a frame is the AY's own formula on the game's own
data -- f = clock / (16 * TP), TP = ((tone byte + 1) & $FF) * 4, clock
1,773,400 Hz -- and NOT anything the engine computed.  Frames whose noise
nibble is non-zero are reported as noise and excluded from the pitch fit,
because their output is a tone ANDed with the LFSR and has no single pitch;
the frames with tone byte $FF (period 0) are the pure-noise rows and are
excluded for the same reason.

WHAT IS NOT MEASURED HERE, declared: loudness.  The AY's 16 volume levels are
rendered through a logarithmic table and the WAV is peak-normalised, so this
tool checks pitch, onset and duration and says nothing about level.
"""
import json
import os
import struct
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, 'build', 'sound_effects')


def read_wav(path):
    with wave.open(path, 'rb') as w:
        n = w.getnframes()
        sr = w.getframerate()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype='<i2').astype(np.float64) / 32768.0
    return a, sr


def segments(a, sr, floor=0.02, gap=0.08):
    """Contiguous audible stretches, found from the signal's own envelope."""
    win = int(sr * 0.005)
    env = np.sqrt(np.convolve(a * a, np.ones(win) / win, mode='same'))
    on = env > floor
    idx = np.flatnonzero(on)
    if not len(idx):
        return []
    runs, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - p > gap * sr:
            runs.append((s, p))
            s = i
        p = i
    runs.append((s, p))
    return [(int(x), int(y)) for x, y in runs if (y - x) > 0.02 * sr]


def envelope(a, sr, floor=0.05, win=0.0015, gap=0.010, minlen=0.0008):
    """segments(), with the window, the gap and the minimum length open.

    The AY's effects are continuous for tens of frames; a BEEPER chirp is
    3.2..4.7 ms long with 76 ms of silence after it, so the 20 ms minimum
    segments() uses would discard every one of them.
    """
    w = max(1, int(sr * win))
    env = np.sqrt(np.convolve(a * a, np.ones(w) / w, mode='same'))
    idx = np.flatnonzero(env > floor)
    if not len(idx):
        return []
    runs, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - p > gap * sr:
            runs.append((s, p))
            s = i
        p = i
    runs.append((s, p))
    return [(int(x), int(y)) for x, y in runs if (y - x) > minlen * sr]


def dominant(x, sr, lo=40.0, hi=8000.0):
    """The strongest partial in [lo, hi], refined parabolically."""
    n = len(x)
    if n < 256:
        return None
    w = np.hanning(n)
    nfft = 1 << int(np.ceil(np.log2(n * 8)))
    sp = np.abs(np.fft.rfft(x * w, nfft))
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    band = (f >= lo) & (f <= hi)
    if not band.any():
        return None
    k = int(np.flatnonzero(band)[np.argmax(sp[band])])
    if 0 < k < len(sp) - 1:
        a0, b0, c0 = sp[k - 1], sp[k], sp[k + 1]
        d = a0 - 2 * b0 + c0
        if d != 0:
            k = k + 0.5 * (a0 - c0) / d
    return k * sr / nfft


def period_ac(x, sr, lo, hi, first_peak=False):
    """Pitch by AUTOCORRELATION, which is what a square wave wants.

    A 20 ms window holds only two periods of a 110 Hz tone, and most of these
    effects change the period every 50 Hz frame, so an FFT bin (50 Hz) is far
    too coarse.  Autocorrelation with a parabolic refinement resolves a square
    wave from three periods, and a square's autocorrelation peak is at the
    true period rather than at a harmonic.
    """
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    if len(x) < 128 or np.all(x == 0):
        return None
    lag_lo = max(2, int(sr / hi))
    lag_hi = min(len(x) - 2, int(sr / lo))
    if lag_hi <= lag_lo + 2:
        return None
    ac = np.correlate(x, x, mode='full')[len(x) - 1:]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    seg = ac[lag_lo:lag_hi + 1]
    k = int(np.argmax(seg)) + lag_lo
    if ac[k] < 0.25:                      # nothing periodic enough to call
        return None
    if first_peak:
        # THE OCTAVE TRAP, and it is not theoretical: measured on the
        # beeper's own chirps, the 5,193 Hz step came back as 2,597 and the
        # 13,158 Hz step as 4,392 -- a half and a third, because the
        # autocorrelation at 2T and 3T was fractionally higher than at T.
        # A periodic signal peaks at EVERY multiple of its period, so the
        # fundamental is the SMALLEST lag that peaks, not the largest.
        thr = 0.85 * ac[k]
        for kk in range(lag_lo + 1, k):
            if ac[kk] >= thr and ac[kk] >= ac[kk - 1] and ac[kk] >= ac[kk + 1]:
                k = kk
                break
    if 0 < k < len(ac) - 1:
        a0, b0, c0 = ac[k - 1], ac[k], ac[k + 1]
        d = a0 - 2 * b0 + c0
        # THE REFINEMENT MUST BE CLAMPED.  A parabola through three nearly
        # equal samples has d ~ 0 and the correction runs away: measured, a
        # 1,588 Hz beeper chirp (five cycles, 139 samples) came back as
        # 3.9 Hz because the shift was thousands of samples.  A true peak's
        # sub-sample correction is by construction inside +-0.5.
        if d != 0:
            shift = 0.5 * (a0 - c0) / d
            if -0.5 <= shift <= 0.5:
                k = k + shift
    return sr / k


def beeper(base):
    """THE SHIPPED BRANCH'S ROUND TRIP.

    A beeper effect is a train of n CHIRPS, one per MAIN-LOOP PASS, so the
    segment finder cannot be used as it is for the AY: an effect is not one
    audible stretch but n stretches 3..5 ms long separated by ~76 ms of
    silence.  Each chirp is therefore found on its own and measured against
    the pitch its own (count, delay) row predicts,

        f = clock / (2 * (17*E + 31))      clock = 3,500,000 Hz

    which is the model tools/beepgate.py fitted to the REAL Z80's recorded
    port-$FE edges, not anything this engine computed.  The two ULTRASONIC
    steps in the game's data (15,086 and 21,341 Hz) are reported and
    excluded from the fit: they are above the band a 44.1 kHz zero-crossing
    or FFT measurement can resolve, and on real hardware they are clicks.
    The two NOISE ids are reported by DURATION only -- their content is
    `LD A,R` and the port plays a declared substitute.
    """
    a, sr = read_wav(base + '.wav')
    j = json.load(open(base + '.json'))
    cpu, fhz = j['cpu_hz'], j['frame_hz']
    marks = j['marks']
    t0, fbase = j['audio_t0'], j['frame_base']
    # THE BRIDGE'S MAP IS PIECEWISE.  SoundOut resyncs whenever the engine
    # drains more simulated frames at once than the audio clock has room for,
    # which a 72- or 210-frame blocking tune guarantees, and each resync
    # starts a new (base, t0).  Using only the LAST pair placed the level
    # tune's rows two rows early and read every pitch back as its
    # neighbour's -- a uniform-looking 29.8% "error" that was entirely this
    # lookup.  soundwav.js now records every piece, so pick the one in force
    # when that frame was rendered.
    pieces = j.get('frame_map') or [{'base': fbase, 't0': t0, 'at': -1e18}]

    def at(frame):
        p = pieces[0]
        for q in pieces:
            if q['at'] <= frame:
                p = q
            else:
                break
        return int(round((p['t0'] + (frame - p['base']) / fhz) * sr))
    burst = envelope(a, sr, floor=0.05, win=0.0015, gap=0.010, minlen=0.0008)
    print('%s: %.2f s, %d Hz, %d scheduled buffers, %d audible bursts'
          % (os.path.basename(base + '.wav'), len(a) / sr, sr, j['buffers'],
             len(burst)))
    print('  (each chirp is located from the BRIDGE\'s own frame->time map,')
    print('   then its pitch is measured out of the samples by '
          'autocorrelation)')
    print()
    print(' id  steps  model pitches (Hz)                       measured '
          '(Hz)                        worst')
    bad = tot = 0
    for mk in marks:
        rows = mk.get('rows') or []
        if mk.get('silent'):
            # A NEGATIVE, MEASURED: nothing audible in the window this id
            # would have occupied.
            s = at(mk['frame0'])
            e = at(mk['frame0'] + 8)
            pk = float(np.max(np.abs(a[max(0, s):max(1, e)]))) if e > s else 0
            print(' $%02X    --   SILENT ($B98A, a bare RET); peak in its own '
                  'window %.4f' % (mk['id'], pk))
            continue
        if mk.get('noise'):
            # THE NOISE IS A SPARSE RANDOM TELEGRAPH, so the envelope
            # detector that resolves a chirp cannot resolve this: it merges
            # across 10 ms gaps and smooths over 1.5 ms, while the burst's
            # own toggle gaps run from 223 T to ~1,900 T with dropouts.  The
            # PRECISE span is the driver's own edge log, which
            # tools/beepgate.py gates against the real Z80's $B8CC ramp
            # (`python tools/beepgate.py ticks`, 16 bursts, 0 outside
            # 0.126 video frames).  The WAV envelope is printed next to it
            # as the audible confirmation, not as the measurement.
            s, e = at(mk['frame0']), at(mk['frame0'] + 8)
            win = [b for b in burst if s <= b[0] <= e]
            ms = [1000.0 * (y - x) / sr for x, y in win]
            span = 1000.0 * mk.get("logSpanFrames", 0) / fhz
            print(' $%02X    --   NOISE ramp %-4s armed at pass phase %.2f: '
                  '%d edges, log span %5.1f ms; WAV envelope %s ms'
                  % (mk['id'], 'UP' if mk['id'] == 0 else 'DOWN',
                     mk.get('armedAt') or 0.0, mk.get('edges', 0), span,
                     ' '.join('%.1f' % g for g in ms) or 'none'))
            continue
        if mk.get('tune'):
            # THE TWO BLOCKING TUNES.  The raw edge train is a 48/144 T
            # interleave CARRIER at ~18 kHz with the two voices modulating
            # it, so no pitch can be read off the edges (manual 11.3(c)).
            # It CAN be read off the SAMPLES, because the box integral has
            # already averaged the carrier away: what is left in the audio
            # band is the two square waves themselves.  The expected value
            # is clock/(2*96*reload) with the reload MEASURED at $C081 on
            # the running original by tools/beepdata.py.
            print(' --   the %s tune, %d rows, %.2f video frames'
                  % (mk['tune'], len(rows), mk['frames']))
            print('      row   start(f)  model v1 / v2 (Hz)   measured (Hz)'
                  '     err')
            for k, r in enumerate(rows):
                lo = at(mk['frame0'] + r['t'] / 69888.0 * 0 + r['t']
                        / (3500000.0 / fhz))
                hi = at(mk['frame0'] + (r['t'] + r['len_t'])
                        / (3500000.0 / fhz))
                lo, hi = max(0, lo), min(len(a), hi)
                v1, v2 = r['hz1'], r['hz2']
                aud = [v for v in (v1, v2) if v < 2000.0]
                got = []
                for w in aud:
                    f = dominant(a[lo:hi], sr, w * 0.7, w * 1.4)
                    got.append(f)
                errs = [abs(g - w) / w for g, w in zip(got, aud)
                        if g is not None]
                print('      %3d   %7.2f   %8.1f / %8.1f   %-16s %s'
                      % (k, r['t'] / (3500000.0 / fhz), v1, v2,
                         ' '.join('-' if g is None else '%.1f' % g
                                  for g in got) or 'both REST',
                         ('%.1f%%' % (100 * max(errs))) if errs else 'n/a'))
                if errs:
                    tot += 1
                    if max(errs) >= 0.05:
                        bad += 1
            continue
        if not rows:
            print(' --   a driven session: %.2f s, %d audible bursts'
                  % (len(a) / sr, len(burst)))
            continue
        want = [cpu / (2 * (17 * (e or 256) + 31)) for _c, e in rows]
        got = []
        for k, (_c, ee) in enumerate(rows):
            # chirp k is on pass k of the effect, at BEEP_TONE_AT frames into
            # that pass; a generous window either side and then the chirp's
            # own envelope inside it
            lo = at(mk['frame0'] + 4 * k + 1.2)
            hi = at(mk['frame0'] + 4 * k + 3.2)
            lo, hi = max(0, lo), min(len(a), hi)
            if hi - lo < 32:
                got.append(None)
                continue
            # the chirp is 3.2..4.7 ms of full-amplitude square with 76 ms
            # of silence after it, so the envelope is taken TIGHT: a 0.5 ms
            # window and a floor at 0.2 keep the chirp and drop the DC
            # blocker's decay tail, which would otherwise be autocorrelated
            # along with it.
            seg = envelope(a[lo:hi], sr, floor=0.20, win=0.0005, gap=0.0005,
                           minlen=0.0005)
            if not seg:
                got.append(None)
                continue
            s, e = max(seg, key=lambda p: p[1] - p[0])
            x = a[lo + s: lo + e + 1]
            f = period_ac(x, sr, 800.0, 22050.0, first_peak=True)
            got.append(f)
        errs = []
        for w, g in zip(want, got):
            if g is None or w > 12000.0:
                continue
            errs.append(abs(g - w) / w)
        worst = max(errs) if errs else None
        ok = worst is not None and worst < 0.05
        print(' $%02X   %3d   %-38s %-38s %s'
              % (mk['id'], len(rows),
                 ' '.join('%.0f' % w for w in want),
                 ' '.join('-' if g is None else '%.0f' % g for g in got),
                 ('%.1f%%' % (100 * worst)) if worst is not None else 'n/a'))
        if errs:
            tot += 1
            if not ok:
                bad += 1
                print('       <-- OFF')
    print()
    print('ROUND TRIP (beeper): %d effects with a measurable pitch, %d outside'
          ' 5%%.  Steps above 12 kHz are excluded and named: they are the two'
          % (tot, bad))
    print('ultrasonic values in the game\'s own data ($03 -> 21,341 Hz and $05'
          ' -> 15,086 Hz), which ids 2 and $0A end on.')
    return bad


def main():
    if len(sys.argv) > 1 and sys.argv[1].startswith('beep'):
        name = {'beep': 'beeper_effects', 'beepplay': 'beeper_play',
                'beeptune': 'beeper_tune'}[sys.argv[1]]
        base = os.path.join(ROOT, 'build', name)
        if not os.path.exists(base + '.wav'):
            sys.exit('run:  node tools/soundwav.js ' + sys.argv[1])
        return beeper(base)
    wav, meta = BASE + '.wav', BASE + '.json'
    if not (os.path.exists(wav) and os.path.exists(meta)):
        sys.exit('run:  node tools/soundwav.js')
    a, sr = read_wav(wav)
    j = json.load(open(meta))
    clock, fhz = j['ay_clock'], j['frame_hz']
    marks = j['marks']
    segs = segments(a, sr)
    print('%s: %.2f s, %d Hz, %d scheduled buffers, %d audible segments '
          '(expected %d effects)'
          % (os.path.basename(wav), len(a) / sr, sr, j['buffers'], len(segs),
             len(marks)))
    if len(segs) != len(marks):
        print('  NOTE: segment count differs from effect count; the silent '
              'gaps between effects are the separator and a very quiet effect '
              'can be split or merged.  Rows are matched IN ORDER.')
    print()
    print(' id  model                     measured')
    print('     frames  ms   tone rows    ms      pitch: n  worst  mean err')
    bad = tot = 0
    for k, mk in enumerate(marks):
        if k >= len(segs):
            print(' $%02X  -- no audible segment --' % mk['id'])
            bad += 1
            continue
        s, e = segs[k]
        rows = mk['rows']
        model_ms = 1000.0 * len(rows) / fhz
        meas_ms = 1000.0 * (e - s) / sr
        # per-frame model, and the runs of identical tone the pitch is fit on
        pf = []
        for b0, b1 in rows:
            npr = (b0 & 15) * 2
            tp = ((b1 + 1) & 0xFF) * 4
            vol = b0 >> 4
            pf.append((vol, npr, tp))
        # RUNS: consecutive frames whose tone period is within 2% of the
        # first, no noise and non-zero volume.  Most of these effects sweep
        # the pitch every frame, so a run is usually one or two frames and
        # the measurement window is one or two 50 Hz frames long -- which is
        # why the pitch is taken by autocorrelation and not by an FFT bin.
        runs, i = [], 0
        while i < len(pf):
            j2 = i
            while (j2 + 1 < len(pf) and pf[i][2] and
                   abs(pf[j2 + 1][2] - pf[i][2]) <= 0.02 * pf[i][2]
                   and pf[j2 + 1][1] == pf[i][1] and pf[j2 + 1][0] > 0):
                j2 += 1
            runs.append((i, j2, pf[i]))
            i = j2 + 1
        errs = []
        for (i0, i1, (vol, npr, tp)) in runs:
            if npr or tp == 0 or vol == 0:
                continue                       # noise, or silent
            f_model = clock / (16.0 * tp)
            if not (45.0 <= f_model <= 6000.0):
                continue
            x0 = s + int(round(i0 * sr / fhz))
            x1 = s + int(round((i1 + 1) * sr / fhz))
            x1 = min(x1, e)
            # at least three periods of the model tone, or the lag is not
            # resolvable at all
            cycles = (x1 - x0) * f_model / sr
            if cycles < 3.0:
                continue
            if cycles >= 8.0:
                # enough cycles for the FFT to resolve: a square wave's
                # fundamental is its strongest partial, so peak-picking in a
                # band of +/- one and a third octaves finds it -- and leaves
                # an OCTAVE ERROR visible as 50% or 100% rather than hiding it
                f = dominant(a[x0:x1], sr, f_model / 2.5, f_model * 2.5)
            else:
                # too few cycles for a 50 Hz bin: autocorrelate instead
                f = period_ac(a[x0:x1], sr, max(45.0, f_model / 2.5),
                              min(9000.0, f_model * 2.5))
            if f is None:
                continue
            errs.append(abs(f - f_model) / f_model)
        n_tone = sum(1 for v, npr, tp in pf if not npr and tp and v)
        dur_err = abs(meas_ms - model_ms) / model_ms if model_ms else 0
        if errs:
            worst, mean = max(errs), sum(errs) / len(errs)
            ok = worst < 0.05
            print(' $%02X  %4d %6.0f   %4d       %6.0f    %2d  %5.2f%%  %5.2f%%%s'
                  % (mk['id'], len(rows), model_ms, n_tone, meas_ms, len(errs),
                     100 * worst, 100 * mean, '' if ok else '   <-- OFF'))
            tot += 1
            if not ok:
                bad += 1
        else:
            print(' $%02X  %4d %6.0f   %4d       %6.0f     -- noise only --'
                  % (mk['id'], len(rows), model_ms, n_tone, meas_ms))
        if dur_err > 0.20:
            print('      DURATION off by %.0f%% (model %.0f ms, measured %.0f)'
                  % (100 * dur_err, model_ms, meas_ms))
    print()
    print('ROUND TRIP: %d effects with a measurable pitch, %d outside 5%%'
          % (tot, bad))
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
