# Sound

Everything Chuckie Egg plays during a game goes through **one routine**, and
there are only two `OUT ($FE),A` instructions in the whole binary — both inside
it. (A third `D3 FE` at `$A8D0` is a false positive: it is the operand of the
`JR` at `$A8CF` plus the following opcode.)

```
$9CA4 SoundSquareWave:
        LD A,$10        ; bit 4 = speaker
        OUT ($FE),A
        LD B,H
        DJNZ $           ; delay
        XOR A
        OUT ($FE),A
        LD B,H
        DJNZ $
        DEC L
        JR nz,SoundSquareWave
        RET
```

**H is the half-period delay count, L the number of square wave cycles.**

## The waveform

The two halves are *not* equal — the loop's own overhead (`DEC L`, `JR`, `LD A`)
all falls on the low half:

| | T-states |
|---|---|
| speaker high | `13H + 14` |
| speaker low | `13H + 33` |
| period | `26H + 47` |
| frequency | `3500000 / (26H + 47)` |
| length | `L` periods |

`H = 0` means 256, because `DJNZ` with `B = 0` wraps.

These figures are **not hand-counted**. `tools/extract_sound.py` runs the real
routine in the simulator with `HL` set, records every speaker edge with its
T-state, and checks the model against them for H in {0,1,2,4,6,15,30,37,40,63,
100,200,255}. Hand-counting is exactly where a `DJNZ` wrap or a `JR`/`RET` split
goes wrong.

I/O contention is not modelled. `OUT ($FE),A` is a ULA port, so a real 48K
machine adds a few T-states per access; against a 151–5000 T-state period that
is well under 1% and inaudible.

## The effects

| Sound | Where | H | L | Hz | Length |
|---|---|---|---|---|---|
| Footstep, walking left | `$9D67` | 40 | 5 | 3220 | 1.55ms |
| Footstep, walking right | `$9DA3` | 40 | 6 | 3220 | 1.86ms |
| Ladder rung, up | `$9EE5` | 30 | 20 | 4232 | 4.73ms |
| Ladder rung, down | `$9F1F` | 30 | 21 | 4232 | 4.96ms |
| Bonus tick | `$992F` | 4 | 2 | 23179 | 0.09ms |
| Bonus tallied at level end | `$A685` | 30 | 4 | 4232 | 0.95ms |

The left/right and up/down pairs differ by a single cycle of length. It is not
obvious why — the two code paths are otherwise mirror images — but it is what
the bytes say, so it is what the engine does.

The bonus tick is above hearing at 23kHz, and two cycles of it last 88
microseconds. On a real Spectrum that is not a tone at all, it is a **click** —
the cone moves once and stops. Rendering the square wave honestly at 44.1kHz
reproduces exactly that, because four samples of a 23kHz square wave *is* a
click.

Footsteps and rungs are gated on position, not time: `$9D60` and `$9EDE` both
read the farmer's coordinate and `AND 3`, so one fires every four pixels of
travel. Walking speed and sound cadence are therefore locked together.

## Falling

`$9884`, once per frame-counter wrap while airborne:

```
LD L,1                  ; a single cycle
LD A,(farmer_pos_y)
XOR $FF                 ; H = 255 - y
LD H,A
CALL SoundSquareWave
```

The y axis is bottom-up, so as he falls `y` drops, `H` rises, the period
lengthens and **the pitch falls with him**. One cycle per frame at roughly 48Hz
is not a tone, it is a pulse train whose individual pulse width tracks his
height — the rasp you hear on a long drop.

## The pickup warble

`$9860`, in the main loop. An egg (`$9A0B`) or corn (`$9A21`) loads `sfx_timer`
with `$FF`; the main loop counts it down whenever `(frame_counter & 15) == 0`,
and plays two cycles when the **pre-decrement** value has `(v & 3) == 0`, at

```
H = ((v - 1) & 31) + 6
```

Read alone that formula suggests H sweeps 6..37 — a smooth 32-step sweep. It
does not. The `& 3` gate only lets every fourth value of `v` through, and for
`v ≡ 0 (mod 4)` the expression can only produce **eight** values:

```
37, 33, 29, 25, 21, 17, 13, 9
```

so it is a descending eight-note arpeggio, run eight times over, 63 blips in
about 0.6s. I had it written up as a sweep until a test that generated the
sequence independently and compared pitch sets disagreed with the prose.

## The tunes

`$AB60 PlayTune` walks a table of byte pairs:

```
LD C,(HL) / INC HL      ; duration
LD B,(HL) / INC HL      ; pitch
LD A,C / AND A / RET z  ; a zero duration ends the tune
CALL $2D28              ; STACK-A: push the duration
RST $28 / A4 05 38      ; stk-ten, divide, end-calc  -> duration / 10
LD A,B
CALL $2D28              ; push the pitch
CALL $03F8              ; ROM BEEP
```

So each pair is **(tenths of a second, semitones above middle C)**, and pitch 0
is middle C at 261.63Hz.

| Tune | At | Notes | Length |
|---|---|---|---|
| Theme | `$AE0C` | 46 | 6.4s |
| Death | `$AE6A` | 24 | 4.8s |

ROM BEEP blocks, and `$A6FE FarmerKill` calls it before it does anything else —
so **the original really does freeze for 4.8 seconds on every death**. The
engine keeps that, because it is what the game does, but any key cuts it short.
That is the one place the port deliberately gives the player something the
original did not.

## Playing it back

Sound is scheduled against the **simulated** clock, not wall time. The engine
drains a whole animation frame's worth of passes in one burst, so playing each
blip on arrival would stack a frame of footsteps into a single instant. Every
event carries the T-state count at which it happened; `Snd.play` converts that
to an `AudioContext` time and only resyncs when the two have genuinely drifted
apart.

Two details worth keeping:

- The voice cap counts scheduled **end times**, not live sources. The first
  version incremented a counter and decremented it in `onended`; in a harness
  where `onended` never fires the counter only ever climbed and the game went
  permanently silent after 28 sounds. A callback you cannot guarantee should not
  be the only thing that frees a resource.
- Sound is **not** part of the enhancement layer. The original had sound, so it
  belongs on the faithful side, and a test asserts the sound trace is identical
  with the art toggle either way.

## Verifying it

`tools/render_sound.js` stubs Web Audio with a recorder, drives the real engine
through a scripted session, and mixes what was actually scheduled into a WAV —
so it exercises the shipped code path rather than a reimplementation of it.
Measuring those WAVs back gives:

| | model | measured |
|---|---|---|
| walk_left | 3220 Hz / 1.55ms | 3227 Hz / 1.56ms |
| walk_right | 3220 Hz / 1.86ms | 3207 Hz / 1.88ms |
| climb_up | 4232 Hz / 4.73ms | 4245 Hz / 4.74ms |
| climb_down | 4232 Hz / 4.96ms | 4232 Hz / 4.97ms |
| tally | 4232 Hz / 0.95ms | 4200 Hz / 0.95ms |
| bonus_tick | 23179 Hz / 0.09ms | 0.09ms (too short to measure a pitch) |
| warble | 63 blips, 8 pitches | 63 blips, H matches on 63/63 |

The chain is: binary → simulator-verified timing model → `assets/sound.json` →
engine trigger points → Web Audio → rendered WAV → measured frequency back
inside 1% of the Z80.
