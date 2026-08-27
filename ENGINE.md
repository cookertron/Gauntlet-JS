# The JavaScript engine

How `web/game.html` was built out of the Z80, and where every rule in it came from.

This is the engineering companion to [`NOTES-physics.md`](NOTES-physics.md). That
document is the *behavioural spec* — what the rules are. This one is about the
*engine* — how the original's structure was carried across, which of its idioms had
to survive translation, and how each claim was checked.

---

## 1. What the artefact is

One self-contained HTML file. No build step at runtime, no dependencies, no network
access — the Artifact CSP forbids it, and a file you can email is worth more than a
file you have to serve.

```
web/game.template.html      the SOURCE          2,742 lines
  <script type="application/json" id="gamedata">   /*__GAMEDATA__*/
  <script type="application/json" id="sounddata">  /*__SOUND__*/
  <script> (function(){ ... })() </script>          the engine, one IIFE

python tools/build_game.py  →  web/game.html     160 KB, playable
```

`build_game.py` inlines `assets/gamedata.json` (levels, graphics, spawns) and
`assets/sound.json` (the beeper model and both tunes) into the placeholders, escaping
`</` so the payload cannot break out of its `<script>` element, then re-parses both
blocks to prove the escaping held.

**`web/game.html` is generated.** Edit the template.

The engine exports a single test hook, `globalThis.__CHUCKIE__`, through which
`tools/test_game.js` drives the whole simulation headlessly against a stubbed DOM.
That hook is the only reason any of the verification below is possible.

## 2. The choice: reimplement, don't transliterate

You can port a Z80 game two ways.

**Transliterate.** Turn each instruction into an equivalent statement, keep the
register file as variables, keep the memory map as an array. It is provably faithful
and it is nearly unreadable, and every future change fights the machine it came from.

**Reimplement.** Write the rules as ordinary code. Readable, extensible — and
completely unfalsifiable unless you have some way of knowing whether the rules are
right.

This project takes the second road and pays for it with the third thing:

> **Every rule must be checkable against the original, and when they disagree the Z80
> is right.**

In practice that meant building the verification harness *first* (§9) and treating
every plausible-looking JS routine as a hypothesis until the simulator agreed with it.
Section 10 lists the ones that did not survive. There are more of them than I
expected, and none of them looked wrong.

## 3. The shape of the original

Chuckie Egg's main loop is **free-running**. There is no frame sync, no `HALT`, no
interrupt-driven tick governing gameplay — the game moves at whatever speed the Z80
gets through the loop.

```
;; $9858  Run the game
RunGame:  LD HL,($5C3D)      ; the loader's 2-byte block; a one-shot guard
          DEC HL
          LD A,H
          OR L
          JR nz,$985E        ; spins here once, forever after it is non-zero
          LD HL,sfx_timer    ; $7370
          LD A,(farmer_frame_ptr)   ; $72DC
          ...
```

Inside it there are **two paths**:

| | |
|---|---|
| **short path** | ~15 instructions, every pass |
| **long path** | once per **130** passes, when the counter at `$72DC` (reload `$82`) hits zero |

The long path is where input is read, timers tick, and entities move. Entities are
further thinned by their own dividers, so each moves on a different sub-multiple:

| Entity | Divider | Address |
|---|---|---|
| Lifts | 2 | `$733C` |
| Bonus | 10 | `$7346` |
| Duck | 12 | `$734C` |
| Ostriches | 3, or 2 once `cleared_levels >= $20` | `$736B` |

The JS mirrors this exactly. `onePass()` is one turn of the Z80's loop; it decrements
`frameCtr` and calls `longPath()` when it wraps; `longPath()` decrements each divider
and calls the entity updates when *they* wrap. The structure is not an approximation
of the original's — it is the same structure.

```js
function onePass(){
  const g = Game;
  g.simT += g.airbourne ? T_AIRBORNE : T_GROUNDED;
  g.passes++;
  ...
  if(--g.frameCtr === 0){ g.frameCtr = FRAME_RELOAD; longPath(); }
}
```

There is one more layer of dispatch inside the airborne path, at `$A21C`, and it is
worth quoting because it is easy to read past:

```
;; $A21C  Check if the farmer is falling.
          LD A,(farmer_frame_ptr)
          DEC A
          JR z,$A22A          ; frameCtr == 1 -> horizontal + lift landing, then $A294
          LD A,(farmer_airbourne)
          DEC A
          JP nz,$A2B5         ; airbourne != 1 -> vertical step
          RET                 ; airbourne == 1 and frameCtr != 1 -> nothing at all
```

That last `RET` is the platform-edge teeter: for the four long-passes that
`airbourne == 1`, most passes do nothing whatsoever. Miss it and the hesitation before
the drop disappears.

## 4. Time — the hardest single problem

A free-running loop has no natural mapping onto `requestAnimationFrame`. The naïve
port runs one pass per animation frame and is wrong by three orders of magnitude; the
next-naïve one runs a fixed number of passes per frame and is wrong in a subtler way.

**Passes do not cost a constant amount of time.** Measured in the simulator
(`tools/sim_fall.py`):

```
grounded    406 T-states/pass   ->  8,617 passes/sec
airborne    560 T-states/pass   ->  6,250 passes/sec     (1.38x the cost)
```

The airborne path runs the whole `$A21C`/`$A2B5` chain every pass, so the machine
simply gets through fewer of them — **the game slows down while Harry is in the air.**
Driving the simulation at one fixed rate makes falls about 1.4× too fast, and no
amount of tuning a gravity constant fixes it, because the error is in the clock rather
than the physics.

So the engine spends real time in **T-states** and charges each pass at its true cost:

```js
g.passAcc += dt * CPU_HZ;                                   // 3,500,000
if(g.passAcc > CPU_HZ * 0.25) g.passAcc = CPU_HZ * 0.25;     // no spiral after a stall
for(;;){
  const cost = g.airbourne ? T_AIRBORNE : T_GROUNDED;
  if(g.passAcc < cost) break;
  g.passAcc -= cost;
  onePass();
  if(g.mode !== "play") return;
}
```

`Game.simT` accumulates the same T-states monotonically and is the engine's real
clock. Everything that has to be frame-rate independent is keyed to it rather than to
animation frames: the run timer, sound scheduling, ghost playback and the attract-mode
input stream. Two separate bugs in this project came from something being keyed to
displayed frames instead — see [`NOTES-modern.md`](NOTES-modern.md).

**One thing here is not derived from the code.** The TIME chain read naïvely
(housekeeping every 130 passes, `main_sub_timer` reloading `$32`) predicts 6,500
passes per TIME unit. Running the real game gives **1,181**, and only the measured
figure produces the ~2-minute level the original has. The engine uses the measurement.
The discrepancy is unexplained and is listed as open.

## 5. Space

**The Y axis is bottom-up.** Harry's `y` increases upward, so drawing is
`screen_y = 191 - y`. Ostriches are drawn by a different routine (`$929C`) with a
different origin, `176 - y`. Getting this wrong renders every level upside down, which
is exactly what happened first — a pixel-diff against a real frame scored 35.8%
against 96.3% for the corrected version, which is how it was caught.

**Tile lookup is reproduced arithmetically, not conceptually.** `$9E34` computes the
level-buffer offset like this:

```
;; $9E34  Get the UDG at the specified location
          EX DE,HL
          LD HL,level_buffer     ; $61A8
          LD A,D                 ; y
          AND 0xf8               ; row, 8px granularity
          LD B,0
          SLA A                  ; x2 ... continues to x4
          ...
```

```js
function tileAt(buf, x, y){
  x &= 0xFF; y &= 0xFF;
  const i = ((y & 0xF8) * 4) + (x >> 3);
  return (i >= 0 && i < buf.length) ? buf[i] : 0;
}
```

The masking is not defensive — `& 0xFF` is the Z80's register width, and rules
elsewhere depend on the wrap.

**Graphics have two byte layouts, and confusing them is silent corruption.** Tiles and
fonts store each 8×8 cell as 8 consecutive bytes (*cell-major*); sprites interleave by
pixel row across the whole sprite (*row-major*). The extractor tags each block with a
`layout` field. The birdcage came out as noise and Harry as a mangled smear before this
was understood.

## 6. Four routines, side by side

### `$B34C` — the platform edge

The most-loved single detail in the game, and the one I got wrong twice.

```
;; $B34C  Shunt the player to the end of the platform if they're near it
MovePlayerToEndOfPlatform:
          LD A,(farmer_pos_x)
          AND 7
          RET z              ; <-- 8px-aligned? then no check at all
          LD HL,(farmer_pos_x)
          CALL $9E34         ; the tile under him
          LD BC,0x3f
          AND A
          SBC HL,BC          ; ...one row down, minus one
          LD A,(HL)
          CP 5
          RET nc             ; floor or above: supported
          CP 1
          RET z              ; ladder: supported
          CP 2
          RET z              ; ladder: supported
          LD A,1             ; otherwise: airbourne = 1, the teeter
```

```js
function movePlayerToEndOfPlatform(){
  const g = Game;
  if((g.fx & 7) === 0) return;                  // AND 7 / RET z
  const i = tileIndex(g.fx, g.fy) - 63;         // SBC HL,$3F
  const t = (i >= 0 && i < g.buf.length) ? g.buf[i] : 0;
  if(t >= 5 || t === 1 || t === 2) return;      // CP 5 / CP 1 / CP 2
  trace("edge");
  g.airbourne = 1;                              // a TRANSIENT state, not a fall
  g.jumpDirX = (g.dir === 0) ? 1 : -1;
  g.phase = 4;
}
```

Three things in ten instructions that a reimplementation from memory will not produce:

- **`AND 7 / RET z`** means the check is skipped entirely when `x` is 8px-aligned, so
  there is exactly one pixel per platform edge where Harry stands on thin air. It
  looks like a bug. It is the feel of the game.
- **`CP 5 / RET nc`** is `>=`, so the birdcage tiles (`$A8`+) count as support too.
- **`airbourne = 1`** is a transient, not "falling". `$A21C` above turns it into a
  four-pass hesitation before the drop.

I originally invented an edge check that looked reasonable and had none of these.
Anthony reported that Harry fell off the edge slightly early, which is what sent me to
`$B34C` in the first place.

### `$9128` — the ostrich PRNG

```
          LD HL,($736C)      ; a pointer, not a seed
          INC HL
          LD H,0             ; ...forced back into ROM page 0
          LD ($736C),HL
          LD C,1
          BIT 0,(HL)         ; bit 0 gates ladder / dismount
```

It is not a generator at all — it walks a pointer through the Spectrum ROM and reads
bits out of whatever bytes happen to be there. Two decision bits come from the **same
byte**, which matters: they are correlated, and any replacement has to preserve that.

```js
g.prng = (g.prng + 1) & 0xFF;
const byte = ROM[g.prng];
const gate   = (byte & 1) === 0 ? 1 : 0;   // bit 0 gates ladder / dismount
const prefer = (byte >> 1) & 1;            // bit 1 picks which to try first
```

The first substitute was an LCG mod 256. Every such generator has a **period-2 low
bit** — `1010101010…` — and because `x & 4` in the ostrich's own position flips on the
same cadence, the two locked anti-phase and the right-hand ostrich on level 2 could
never climb. The fix is a xorshift sampled from its **middle** bits. This is the one
place the engine deliberately does not reproduce the original (it would need a
Spectrum ROM), and the constraint it has to satisfy is statistical rather than exact.

### `$9CA4` — the beeper

Every in-game sound in Chuckie Egg goes through this. There are exactly two
`OUT ($FE),A` instructions in the whole binary and both are here.

```
$9CA4 SoundSquareWave:
          LD A,$10           ; bit 4 = speaker
          OUT ($FE),A
          LD B,H
          DJNZ $             ; delay
          XOR A
          OUT ($FE),A
          LD B,H
          DJNZ $
          DEC L
          JR nz,SoundSquareWave
          RET
```

**H is the half-period delay count, L the number of cycles.** The two halves are not
equal — the loop overhead all falls on the low one:

```
high   = 13H + 14   T-states
low    = 13H + 33
period = 26H + 47                  (H = 0 means 256; DJNZ with B=0 wraps)
```

Those figures are **not hand-counted**. `tools/extract_sound.py` runs the real routine
in the simulator with `HL` set, records every speaker edge with its T-state, and
checks the model against them for H across the whole useful range. Hand-counting is
exactly where a `DJNZ` wrap or a `JR`/`RET` split goes wrong.

The browser then renders the same square wave from those figures, and
`tools/render_sound.js` mixes what the engine actually schedules into a WAV so the
frequencies can be measured back. Every effect lands within 1% on both pitch and
duration.

### `$9860` — the pickup warble, and reading a formula wrongly

An egg or corn loads `sfx_timer` with `$FF`; the main loop counts it down and blips
when the pre-decrement value has `(v & 3) == 0`, at `h = ((v - 1) & 31) + 6`.

Read alone, that formula says H sweeps 6..37 — a smooth 32-step sweep, and that is
what I wrote in the notes. It does not. The `& 3` gate only lets every fourth value of
`v` through, and for `v ≡ 0 (mod 4)` the expression can only produce **eight** values:

```
37, 33, 29, 25, 21, 17, 13, 9
```

It is a descending eight-note arpeggio run eight times, 63 blips in about 0.6s. A test
that generated the sequence independently and compared pitch *sets* disagreed with the
prose, and the test was right. Worth recording because the error was in reading the
code, not in running it — the implementation had been correct all along.

## 7. Z80 idioms that had to survive translation

**Stack unwinding as control flow.** Several routines abandon an update by executing
extra `POP`s and returning past their caller. It reads like a no-op if you are
skimming for arithmetic. It is how death, corn-eating and level-completion all signal
"stop, this update is over". Two whole death conditions — falling off the bottom of
the screen (`$A305`) and being carried into the ceiling by a lift (`$991E`) — were
missing from the engine because the unwind was read as dead code.

**Byte wrapping is load-bearing.** `DEC` on a counter holding 0 gives 255, and the
vertical-step routine relies on it: a phase of 0 means a full 256-pass wait, not an
immediate one.

```js
g.ctr = (g.ctr - 1) & 0xFF;      // DEC on 0 gives 255
if(g.ctr !== 0) return;
...
g.ctr = g.phase;                 // 0 means 256 passes
```

**Fall-through that looks like a bug.** `$9DAB` sets the walk animation to 3 when
blocked or idle — and then `$9DB2` runs `INC A / AND 3` **unconditionally**, so a
standing farmer lands on frame 0, feet together, not frame 3. Returning early from the
JS "for clarity" put him permanently mid-stride.

**Pre-shifted sprite art.** The walk frames are the stand frames shifted 4px *inside*
their 16px cell — verified byte-exactly, `(ostrich_left >> 4) == ostrich_left_walk` on
rows 3–11. So `(x >> 3) * 8 + the built-in offset == x` always, and the original's
8-pixel-quantised movement was already smooth. Three separate attempts to "improve" it
with interpolation all made it worse, the last one manufacturing a visible backwards
jerk. Reverted entirely.

> The habit that would have saved all four: **when a routine looks like it is cutting a
> corner, find out what compensates before fixing it.**

## 8. The routine map

Where each part of the engine came from. Addresses are in the original binary
(`ORG $8214`); the JS is in `web/game.template.html`.

| Z80 | What it does | JS |
|---|---|---|
| `$9858` | main loop entry | `onePass()` |
| `$72DC` | frame counter, reload `$82` | `Game.frameCtr` / `FRAME_RELOAD` |
| `$A21C` | airborne dispatch | `onePass()` airborne branch |
| `$A294` / `$A2B5` | transient vs vertical step | `fallOrVertical()` / `verticalStep()` |
| `$7327` | jump phase counter — the whole arc lives in this byte | `Game.phase` / `doJump()` |
| `$B34C` | platform edge, the one-pixel overhang | `movePlayerToEndOfPlatform()` |
| `$9955` | jump, gated on `airbourne` alone | `update()` jump test |
| `$9D67` / `$9DA3` | walk left / right, `x & 3` cadence | `horizontalMove()` |
| `$9E98` / `$9F60` | ladder mount / dismount | `ladderUpDown()` / `ladderDismount()` |
| `$9E34` | tile lookup | `tileAt()` / `tileIndex()` |
| `$99DF` | pickup test at the sprite centre | `pickups()` |
| `$9A0B` / `$9A21` | egg / corn, and corn stalling both sub-timers | `pickups()` |
| `$911E`–`$9265` | ostrich state machine | `ostrichUpdate()` |
| `$9128` | ostrich decision bits | `ROM[]` + `Game.prng` |
| `$9265` | ostrich eating, drawn 8px left | `OSTRICH_SPR` offset |
| `$A0C8` | duck update, runs in full while caged | `duckUpdate()` |
| `$9B9E` | duck collision box (±8, ±9) | `duckOverlaps()` |
| `$9E66` | ostrich collision box (13×28) | `overlaps()` |
| `$A014` | lifts: rise 1px, recycle from y>=166 to y=3 | `liftUpdate()` |
| `$A305` / `$991E` | bottom-of-screen and lift-ceiling deaths | `die()` paths |
| `$A6FE` | FarmerKill; plays `death_tune` first | `die()` |
| `$A72D` | death keeps collected items | `loadLevel(n, true)` |
| `$A675` / `$A685` | end-of-level bonus tally and its blip | `tallyStep()` |
| `$9CA4` | SoundSquareWave | `Snd.play()` |
| `$9860` | the pickup warble | `onePass()` sfx slot |
| `$984F` | tile colour table | `TILE_INK` |
| `$B3B0` | level layouts, stride `$2A0` | `DATA.levels` |
| `$945B` / `$9787` / `$9808` | ostrich / lift / duck spawn tables | `DATA.levels[].spawns` |
| `$AE0C` / `$AE6A` | title and death tunes | `SND.tunes` |

Turn on **Z80 view** in the running game and it will name these live as they fire,
with hit counts.

## 9. The verification harness

### The simulator

`tools/sim.py` builds the 64K image exactly as the tape loader leaves it and runs it
under SkoolKit's Z80 `Simulator`. Two things about it are not obvious:

- **No Spectrum ROM is shipped**, so `$0000`–`$3FFF` holds a deterministic pattern.
  Gameplay never calls the ROM — but the ostrich PRNG *reads* page 0, so decisions in
  simulation are deterministic rather than authentic. Timing is unaffected.
- **Interrupts must be armed only after `$A420`.** The game does not select IM 2 until
  `$A41E`; firing an interrupt earlier dispatches in IM 1 to `$0038` and straight into
  the dummy ROM. But they *must* be armed, because `$A960` spins until the ISR
  decrements `$7373` — without interrupts the simulation deadlocks there.

### Differential testing

The strongest single check in the project. `tools/ostrich_diff.py` steps the real Z80
and the JS ostrich model side by side and compares every state transition:
**900/900**. That is what makes "verified exact" a statement rather than a hope.

### Pixel diff

`tools/pixel_diff.py` compares a rendered frame against a capture from the real game:
**99.44%**. This is what caught the upside-down levels (35.8% → 96.3%) and several
palette errors — the floor is green, not white; ladders and corn are magenta, not
yellow and cyan.

### Headless tests

`tools/test_game.js` stubs just enough DOM — `document`, `localStorage`,
`requestAnimationFrame`, a canvas that records `fillRect` when armed — evaluates the
engine's `<script>` block, and drives it through `__CHUCKIE__`. **235 assertions.**

The canvas stub matters more than it sounds. Several defects in this project were on
the *presentation* side of the line and invisible to state-only tests: an overlay that
survived a rewind, a ghost that stopped racing, a decoration that was computed but
never painted. Tests that assert on the recorded draw list catch those; tests that
assert on `Game` do not.

### The enhancement assertion

```
400 frames of simulation are byte-identical with Enhanced on and off
```

This is the guard rail that lets the enhancement layer exist at all. Anything that
would change the simulation fails it immediately.

## 10. What the simulator proved wrong

Every one of these looked right when written.

| Assumption | Reality | Caught by |
|---|---|---|
| Levels read top-down | Stored **bottom-up** | pixel diff, 35.8% vs 96.3% |
| One graphics layout | **Two** — cell-major tiles, row-major sprites | garbled birdcage |
| Bright Spectrum palette | Game **never sets BRIGHT**; `#D7`, not `#FF` | frame capture |
| Constant pass cost | **560 airborne vs 406** grounded | falls 1.4× too fast |
| An LCG will do for the PRNG | Period-2 low bit locks anti-phase with `x & 4` | an ostrich that never climbed |
| Ostriches share a collision box with the duck | `$9B9E` is a **separate, symmetric** box | play feel |
| Stack unwind is a no-op | It is the **death signal** | two missing death conditions |
| A sensible edge check | `$B34C`'s `AND 7 / RET z` overhang | Harry fell early |
| Standing sets frame 3 | `$9DB2` increments **unconditionally** → frame 0 | feet apart at rest |
| Corn stalls the TIME timer | `$9A21` writes `$FFFF` to **both** sub-timers | timers kept running |
| Death resets the level | `$A72D` **keeps** collected items | items reappeared |
| Ostrich motion needs smoothing | Sprites are **pre-shifted 4px**; it was already smooth | filmstrip; reverted |
| The warble sweeps 6..37 | The gate leaves **eight** pitches | an independent test |

Six of these were reported by Anthony from play, not found by me from code. That is a
better ratio for him than for me, and the reason the harness kept growing.

## 11. The enhancement boundary

Everything added on top is **presentation only**, and the boundary is enforced rather
than intended:

- the simulation is never *read from* by the enhancement layer for anything but
  display state;
- a test asserts 400 frames are byte-identical with it on and off;
- decoration is restricted to the two inks the game leaves free — **blue** (unused
  anywhere) and **red** (HUD paper only) — because green means floor, magenta means
  ladders and corn, white means eggs, yellow means the cage and Harry, and cyan means
  ostriches. A green frond would read as a platform;
- **sound is not in the enhancement layer.** The original had sound, so it belongs on
  the faithful side, and a test asserts the sound trace is identical with the art
  toggle either way.

The four features that only exist because the port is a deterministic simulation —
rewind, ghosts, attract mode, the Z80 view — are documented separately in
[`NOTES-modern.md`](NOTES-modern.md).

## 12. Engine anatomy

Where things live in `web/game.template.html`:

| Lines | |
|---|---|
| ~205–330 | `Snd` — the beeper model and Web Audio scheduling |
| ~330–400 | palette, graphics decode, the two byte layouts |
| ~400–445 | coordinates, tile lookup, the timing constants |
| ~445–515 | input: keyboard, gamepad, touch, replay, the jump latch |
| ~515–615 | `Game` state and the PRNG substitute |
| ~615–820 | the farmer: walk, ladders, jump, edge, pickups, death |
| ~820–965 | ostriches, duck, lifts |
| ~965–1080 | `longPath()`, `onePass()` |
| ~1080–1250 | flow: death, tally, level advance |
| ~1250–1410 | recording, ghosts, attract mode |
| ~1410–1460 | the Z80 provenance table |
| ~1465–1600 | rewind |
| ~1600–1900 | the enhancement layer: particles, scenery, hens, grass |
| ~1900–2350 | render |
| ~2350–2742 | the animation loop and the page UI |

## 13. Known gaps

- **`$929C`'s attribute path (`$9332`) is undecoded.** Ostrich colour is currently
  applied from the tile table rather than from whatever that routine does.
- **The TIME chain discrepancy** (§4) is unexplained. The engine uses the measurement.
- **The PRNG is a substitute**, not the original. It preserves the correlation between
  the two decision bits but not the exact sequence, because that would need a
  Spectrum ROM.
- **`on_lift` is cleared when walking out of the shaft.** Nothing in the original
  appears to clear it there; without it Harry rides thin air. Flagged as mine.
- **The death bonus refill** (`$A72D`) is faithful and stays, but it hands a run free
  points; the ghost ranking charges them back to within ~11 points.

---

*Chuckie Egg © 1984 A&F Software; designed and developed by Nigel Alderton. This
document describes a study of how the original works.*
