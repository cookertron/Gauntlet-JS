# Chuckie Egg — behavioural spec (recovered from the Z80)

Everything here is read out of the machine code, not inferred from play. Addresses
are in the game block (`$8214`–`$CBE7`). Tool: `tools/adis.py` (annotated disassembly
with the reference project's labels resolved).

---

## 1. Coordinate system — y runs BOTTOM-UP

This is the single most important thing to get right, and it explains several earlier
puzzles at once.

`$9404` (get screen address of a sprite) selects the display third with
`y>=128 → $4000` (top), `64..127 → $4800`, `<64 → $5000` (bottom), then computes
`(0x38 - (y & 0x38)) * 4` for the character row and `(y & 7) XOR 7` for the pixel row.
Both are inversions.

> **`y_screen = 191 − y_game`.** y is measured in pixels from the bottom of the
> screen, increasing upward. x is plain screen pixels, 0–255.

Consequences:

- The level data is stored bottom-up **because the game's own y axis is bottom-up.**
  The buffer is filled with a plain `LDIR` (`$A73D`, `$A7B7`) — no flipping anywhere.
- Tile lookup is `level_buffer + (y>>3)*32 + (x>>3)`, coded as
  `(y & 0xF8)*4 + (x>>3)`. Identical in the movement (`$9D17`) and draw (`$9A9A`)
  routines.
- Buffer row 0 is screen character row 23 (the bottom).
- **Verified:** Harry spawns at screen (100,168) → game y = 23. Buffer rows
  `23>>3 = 2` and `1` are screen char rows 21 and 22 — exactly the two cells his
  16px sprite occupies — with the floor at row 0 correctly *excluded*.

The play area is game y 0–167; the HUD occupies game y 168–191.

---

## 2. Timing — the game is FREE-RUNNING, not frame-synced

`RunGame` (`$9858`) opens with:

```
LD HL,($5C3D) / DEC HL / LD A,H / OR L / JR nz,$985E
```

`JR nz,$985E` targets *itself*, and nothing inside the loop changes the flags — so
this is not a wait, it is a one-shot guard that only passes because the loader wrote
`$0001` into `$5C3D`. **That is what the mysterious 2-byte tape block was for.**
`$5C3D` is read exactly once in the entire game and never written.

The IM2 interrupt handler (`$9C9C` → `$9CC2`) drives the Fuller Orator speech unit.
It does **not** pace the gameplay loop — but it is not inert either, and that
distinction matters:

> The ISR decrements `$7373`, and **`$A960` spins until `$7373` reaches `$FF`**
> (it is the "farmer dies" wait). Several transition/pause routines depend on the
> same 50 Hz timebase. Simulating without interrupts deadlocks the game there —
> which is exactly what happened on the first run of `tools/sim_play.py`.

Vector table: the game does `LD A,$B2 / LD I,A / IM 2`, and `$B2FF`/`$B300` both
hold `$9C`, so the vector resolves to `$9C9C` whatever the data bus floats to — the
classic run-of-identical-bytes trick.

So a port needs **both**: a free-running update for gameplay *and* a 50 Hz tick for
the interrupt-driven waits.

> **Every counter in this document is in main-loop iterations, not video frames.**
> Game speed is whatever the Z80 achieves. For the port, treat these as *ratios* and
> calibrate the absolute rate empirically against an emulator.

### The loop has two paths, and this changes everything

Tracing `RunGame` shows the common path is *very* short:

```
9858 985B 985C 985D 985E 9860 9863 9866 9868 987E 9881 9882 98B7 98BA 98BB -> 99DC -> 9858
```

That is ~15 instructions. `$987E` skips the whole airborne block when the farmer is
grounded, and `$98BB` (`JP nz,$99DC`) loops straight back. The **long** path — key
reading, timers, the ostrich update at `$9946`, and the farmer's *horizontal*
movement — only runs when `$72DC` wraps, i.e. **once per 130 passes**.

And on top of the long path, **each entity has its own countdown divider**. These are
easy to miss and they dominate the game's feel:

| Entity | Divider | Reload | Effective cadence |
|---|---|---|---|
| Farmer horizontal (`$9D08`) | — | — | every long pass |
| **Lifts** (`$A014`) | `$733C` | 2 | every 2 long passes |
| **Bonus tick** (`$A1B8`, B=1) | `$7346` | 10 | every 10 long passes |
| **Duck** (`$A0C8`) | `$734C` | 12 | every 12 long passes |
| **Ostriches** (`$911E`) | `$736B` | **3, or 2 when `cleared_levels >= $20`** | every 3 long passes, then round-robin over 5 slots |

> The ostrich divider reloading with **2 instead of 3 from `cleared_levels >= $20` (32)**
> is the documented **"pass 5: the ostriches move faster"** rule, straight out of the
> code. It makes them exactly 1.5× quicker.

So a single ostrich moves once per `130 × 3 × 5 = 1,950` passes. Reading "1 px per
iteration" literally over-states walking speed by 130×, and ignoring `$736B` and the
round-robin over-states ostriches by another 15×.

Also at `$9915`: while `$7355` (on-lift) is set, the farmer's y is incremented on the
lift's own cadence — that is how riding works.

Frame counter `$72DC` counts down and reloads at `$82` (130) — one "housekeeping
tick" per 130 iterations. The game timer subdivides that by 50 (`main_sub_timer`
`$7345` reloads `$32`), so **one displayed TIME unit = 130 × 50 = 6500 iterations**.

### The absolute rate — MEASURED

Not estimated any more. `tools/sim_timing.py` runs the real code in the Z80 simulator
inside a live level and measures it:

> **399 T-states per main-loop pass → 8,783 passes/sec on a 3.5 MHz Spectrum.**

Everything else follows, and the results are mutually consistent:

| Quantity | Rate | Real time |
|---|---|---|
| Main-loop pass | 399 T-states | 0.114 ms |
| Long path (1 per 130 passes) | 67.6 /sec | 14.8 ms |
| **Farmer walking** | 1 px per long pass | **67.6 px/sec** — 3.8 s to cross the screen |
| **Ostrich** | 4 px per 1,950 passes | **17.3 px/sec measured** — ~4× slower than the farmer |
| **Lifts** | 1 px per 2 long passes | ~33.8 px/sec |
| **Duck** | ±vel (max 5) per 12 long passes | up to ~28 px/sec |
| **Full jump arc** | 5,602 short passes | **0.64 s** |
| **TIME unit** | measured directly | **0.134 s** → 900 units ≈ **2 minutes** per level |

The farmer figure is cross-checked two independent ways: a live run moved him 99 px
(1 px per long pass = 12,870 passes = 1.47 s → 67.6 px/sec), which matches
`8783 / 130 = 67.6` exactly. A 3.8-second screen crossing and a 2-minute level timer
are both right for the real game, which is the sanity check that matters.

The ostrich figure was **measured, and it corrected a bad derivation**. Predicting
from "4 px per 5 long passes" gives 54 px/sec; the real answer is 17.3 px/sec because
of the `$736B` divider (3). Predicted from the full chain — `8783 / (130×3×5) × 4 =
18.0 px/sec` — against 17.3 measured, the small shortfall being passes spent turning
rather than moving.

**Measure net displacement and you will get this wrong.** A patrolling ostrich
oscillates: sampling x over 13.6 s gave
`104 → 112 → 96 → 84 → 80 → 64 → 52 → 68 → 84 → 100 → 116`, returning near its
start. An early run reported "0 px/sec" for exactly this reason. Accumulate distance
travelled, not end-minus-start.

**Caveat on the TIME tick.** Measured at **1,181 passes** per unit. Reading the code
naively — housekeeping every 130 passes, `main_sub_timer` reloading `$32` (50) —
predicts 6,500. The measurement is authoritative and gives the correct 2-minute
level; the discrepancy means `$A1B8` or the `$7347` gate does something I have not
fully unpicked. Use the measured figure.

Caveat on the harness: pressing a key means returning the *mask* stored in
`$732E`.. verbatim (the key's bit is the one that is 0 in the mask). Inverting it
presses every other key on that half-row — which is how an early run had Harry
walking left when told to go right.

---

## 3. Farmer — ground movement (`$9D08`)

- **Speed: 1 pixel per iteration** (`INC (IX)` / `DEC (IX)`).
- **X limits:** blocked at `x == 1`; blocked when `x >= $EE` (238). Sprite is 16 wide,
  so 238+16 = 254.
- **Direction encoding:** `0` = right, `4` = left, `13` = climbing. It doubles as the
  sprite frame base, so frame index = `anim_frame(0..3) + direction`. The right and
  left sprite blocks are contiguous (`$8DF0` + 128 = `$8E70`) — one array of 8 frames.
- **Animation:** `anim_frame` cycles 0–3 each iteration; forced to `3` (standing) when
  blocked or no key held.
- **Footstep sound** every 4 pixels (`AND 3`).
- **Column quirk:** when facing right, the column used for the tile lookup is
  `(x-1)>>3`, not `x>>3`.

### Collision

> **A tile blocks movement if its value is ≥ 5.**

So floor (`$05`) and birdcage (`$A8`–`$B5`) block; blank, ladders, eggs and corn do
not. **Two cells are tested** — buffer rows `R = y>>3` and `R-1` — which are exactly
the farmer's two body rows. Moving left tests column `c`; moving right tests `c+2`.

---

## 4. Farmer — jumping and gravity

### Take-off (`$9975`)

```
airbourne ($7325) = 2
phase     ($7327) = $8C  (140)
counter   ($7328) = 0
y_vel     ($732A) = +1   (rising; y is bottom-up)
on_lift   ($7355) = 0
```

Horizontal velocity `$7326` is taken from the direction **held at take-off**:
right → `+1`, left → `−1`, neither → `0`. It cannot be changed mid-air.

### The phase mechanism (`$A2B5`)

`$7328` counts down once per iteration; when it hits zero the farmer moves **one
pixel** vertically and `$7328` is reloaded from `$7327`. So **`$7327` is the delay
per pixel — larger means slower.**

| State | Rule |
|---|---|
| Rising (`y_vel = +1`) | `phase += 10`. When it wraps to `4` (i.e. was 250) → apex: `y_vel = 0`, `phase = 0` |
| Apex (`y_vel = 0`) | `y_vel = −1`, `phase = $FA` (250) |
| Falling (`y_vel = −1`) | `phase -= 10`, **clamped to a minimum of 40** (terminal velocity) |

Rise is therefore *decelerating* (delay 150→250) and fall *accelerating*
(delay 250→40). Starting at 140 and stepping +10 to 250 gives **≈11–12 pixels of
rise** — about 1.5 character cells.

### A pass is NOT a constant cost

Measured with `tools/sim_fall.py` inside a live level:

| State | T-states/pass | passes/sec |
|---|---|---|
| grounded | **406** | 8,617 |
| **airborne** | **560** | **6,250** |

The airborne path runs the whole `$A21C` / `$A2B5` chain on every pass, so the
machine simply gets through 1.38x fewer of them. Driving a port at one fixed
pass rate makes **falls about 1.4x too fast**, and because fall speed is
`1/phase` the error is most obvious as the delay shrinks -- it reads as "slow,
then suddenly very fast".

Real falls measured at the top of the ramp: ~30 px/sec around phase 230,
reaching ~156 px/sec at the phase floor of 40. Spend real time in **T-states**
and charge each pass at its true cost rather than counting passes.

### Farmer sprite frames -- two traps

- **Standing.** `$9DAB` sets `anim = 3` when blocked or idle, but `$9DB2` then
  runs `INC A / AND 3` **unconditionally**, so the stored frame becomes **0**.
  Standing draws frame 0 (feet together). Returning early after setting 3 leaves
  him frozen mid-stride.
- **Airborne.** `$A30F` draws with a **fixed frame 1**, not the walk counter, so
  the legs do not cycle during a jump or fall.

### Validated arc (`tools/sim_jump.py`)

Simulating the mechanism literally produces:

| | Standing jump | Running jump |
|---|---|---|
| Rise | **+11 px** | +11 px |
| Iterations to apex | 2,456 | 2,456 |
| Horizontal travel back to launch height | 0 | **≈35 px (≈4.4 cells)** |

An 11px rise combined with the 8px landing snap means Harry can just reach a platform
**one character cell higher**, and a running jump clears roughly a **4-cell gap**.
Both match the real game, which is a good check on the whole reading.

- **Ceiling:** if `y >= $A7` (167, top of the play area) while rising, it flips
  straight to falling.
- **Airborne horizontal** (`$A22A`) moves 1px per `$72DC` tick, and **bounces off the
  screen edges**: at `x == 0` velocity is forced to `+1`, at `x >= $EE` to `−1`.

### Walking off an edge (`$B34C`, "MovePlayerToEndOfPlatform")

Called from `$99D6` only when `on_lift == 0`. This is what actually starts a
fall, and all three of its quirks are load-bearing:

```
LD A,(farmer_pos_x) / AND 7 / RET z    ; does NOTHING when x is cell-aligned
CALL $9E34 / SBC HL,$3F                ; tile under the feet (2 rows down, +1 col)
CP 5  / RET nc                         ; >= 5 supports -- INCLUDING the birdcage
CP 1  / RET z    CP 2 / RET z          ; ladder halves support
LD A,1 / LD (farmer_airbourne),A       ; the TRANSIENT state, not a straight drop
LD D,$FF / (dir==0 ? D=1) / LD (farmer_jump_dir),A
LD A,4 / LD (farmer_jump_phase),A      ; 4-tick delay before the drop
```

- **The `AND 7 / RET z` gate gives one extra pixel of overhang.** Checking every
  step drops him a pixel early, which is visible.
- Support is `>= 5` **or** a ladder. Eggs and corn (3/4) do *not* support you;
  the birdcage does.
- It enters `airbourne = 1`, and `$A294` only decrements that phase on the
  `frameCtr == 1` pass — so the transient really lasts **4 long passes** before
  the fall proper begins. `$A29F` then zeroes the horizontal velocity, so he
  drops straight down.

`$A21C`'s dispatch is worth writing out, because the third case does nothing at
all and is easy to collapse away:

| frameCtr | airbourne | action |
|---|---|---|
| == 1 | any | airborne horizontal + lift landing, then `$A294` |
| != 1 | != 1 | `$A2B5` vertical step |
| != 1 | == 1 | **nothing** |

### The aligned-overhang perch (an original quirk, not a port bug)

`$B34C` (stand) and `$A356` (land) test the **same cell** but not with the same
rules, and `$B34C` additionally skips the test entirely when `x & 7 == 0`. So
there is exactly one pixel per platform edge where the farmer can rest on
nothing -- and a vertical jump from it cannot land, dropping him to the floor
below.

Level 1, right end of the mem-row-4 platform, y = 55:

```
  x    x&7  support   $B34C verdict              vertical jump lands at
 231     7      5     supported                  y=55
 232     0      0     SKIPPED -> stays grounded  y=23   <- the perch
 233     1      0     falls                      y=23
 238     6      0     falls*                     y=55   (* bounced back by the
 239     7      0     falls*                     y=55      x>=$EE edge reversal)
```

**Verified against the real Z80** with `tools/sim_edgejump.py 55 228 240` and
diffed against the engine with `tools/cmp_edgejump.js 55 228 240` -- all twelve
positions identical, including the `238/239` case where the `x >= $EE` bound
flips the horizontal velocity and carries him back over the platform.

Locked in as a regression test so the quirk cannot be "fixed" by accident.

### Landing (`$A356`)

The cell checked is the buffer entry at `HL − 63` from the farmer's cell — two rows
lower and one column right, i.e. **under his feet**.

- tile `0` → keep falling
- tile `5` (floor) → land
- tile `≥ 3` (egg/corn/cage) → keep falling
- tile `1`/`2` (ladder half) → land only if the *adjacent* half's cell is floor

Landing additionally requires **`(y + 1) mod 8 == 0`**, so the farmer snaps to
8-pixel alignment. On landing, `airbourne = 0`, and `direction 13` resets to `0`.

### Edge bounce (`$A32C`–`$A353`)

While falling with non-zero horizontal velocity, if the neighbouring cell in the
direction of travel is floor and the sub-cell offset is past a threshold
(`x mod 8 >= 4` moving left, `>= 3` moving right), the horizontal velocity is
**reversed** with `XOR $FE` (`$FF`↔`$01`). This is the characteristic Chuckie Egg
"kick off the platform edge" behaviour.

---

## 5. Entity collision (`$9E66`)

The reference disassembly names this `FarmerVerticalMovement` / "checks if there's
enough headroom for a jump". **That label is wrong** — it is the farmer↔entity
overlap test, called from `$98B0` over the five ostrich slots. Given entity `(E, D)`:

> Overlap when `E − 7 ≤ farmer_x ≤ E + 5` **and** `D + 1 ≤ farmer_y ≤ D + 28`.

A 13-wide, 28-tall box. Returns `1` in A on overlap (→ death), `0` otherwise.

---

## 6. Lifts (`$A014`)

- `$7350` = the lift **column x** for the level; `$FF` means the level has none.
  Only ever *read* (at `$9DC6`, `$A014`, `$A256`) — it is written during level init
  from a per-level structure I have not yet located. Measured values (screenshots):

  | Level | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
  |---|---|---|---|---|---|---|---|---|
  | column x | — | — | 64 | 144 | 200 | 120 | 240 | — |

- Two lifts, y at `$7351` and `$7354` (with state words at `$734E`/`$7352`).
- **They rise continuously:** `INC A` on y each update (up-screen, since y is
  bottom-up). When `y >= $A6` (166) the state reloads from a table at `$9787` and
  `y` resets to `3`. So travel is y 3 → 166, then wrap.
- At level start the pair are **64 pixels apart** (measured: game y ≈ 9 and 73).
- **Riding** (`$A256`): the farmer lands on a lift when his x is within the window
  `lift_x − 9 ≤ x < lift_x + 10` and his y falls in a 6-pixel band
  `lift_y + 10 … lift_y + 15`. On landing: `on_lift = 1`, `airbourne = 0`, and he is
  seated at **`y = lift_y + 17`**.

---

## 7. Ostriches (`$911E`)

Five slots at `$7357`, **4 bytes each**: `+0` x, `+1` y, `+2` state, `+3` spare.
`x == $FF` marks an empty slot — which is why levels carry 2–4 of a possible 5.

### Pass rule, straight from the code

```
LD A,(cleared_levels)
CP 8    / JR c,active      ; 0..7   -> ostriches
CP $10  / RET c            ; 8..15  -> NO ostriches
                           ; >=16   -> ostriches again
```

This is exactly the documented "pass 2 has the duck but no ostriches", and it matches
our screenshot finding of **zero ostriches on level 9**.

### Update model

**One ostrich is updated per call**, round-robin via the index at `$7356` (0–4, wraps
at 5). So each ostrich moves once per five calls — and this holds regardless of how
many are active, because empty slots `RET` early but still consume their turn.
Ostrich speed is therefore constant at **4 px per 5 passes = 0.8 px/pass**, against
the farmer's 1 px/pass. You can just outrun them.

### State machine (`$9178` walking, `$91F3` climbing)

| State | Meaning | Movement per update |
|---|---|---|
| 1 | walk left | `x -= 4` |
| 2 | walk right | `x += 4` |
| 3 | **climb down** | `y -= 4` |
| 4 | **climb up** | `y += 4` |

(3 and 4 are settled by `$920E`: `SUB 2` then `DJNZ` selects `y+4` for state 4 and
`y+4-8 = y-4` for state 3. Since y is bottom-up, state 4 goes up the screen.)

**Turning while walking** (`$917C`) — only when `x & 4 == 0`, i.e. at 8px boundaries.
Probe the tile at `(x ± 8, y − 1)`, ahead and just below. If it is **not** floor (`5`)
and **not** a ladder half (`1`/`2`), reverse: `state = 3 − state`. That is what keeps
them on their platform.

**Mounting a ladder** (`$91BA`) — when `x & 4 != 0`, gated by a **50% random bit**.
A second random bit picks which direction to try first; it then probes for tile `1`
(ladder-left) at `y − 8` (one cell below → state 3, climb down) or `y + 16` (two cells
above → state 4, climb up). Both are tried before giving up.

**Turning while climbing** (`$91F7`) — only when `y & 4 == 0`. Probe ahead
(`y − 8` when descending, `y + 16` when ascending); if it is not a ladder, reverse
with `state = 7 − state`.

**Dismounting** (`$9225`) — when `y & 4 == 0`, gated by the same 50% random bit, with
a second bit choosing which side to try first. Probe for floor (`5`) at `(x − 8, y − 8)`
→ state 1 (walk left), or `(x + 16, y − 8)` → state 2 (walk right).

There is also `$9265`, "check if an ostrich can eat some corn", called on the walking
path — the eating animation frames exist as separate sprites.

### Eating corn (`$9265`) — and states 7/8

This was the missing piece. On the walking path, when `x & 4 == 0`, `$9265` runs
**before** the turn check:

```
LD A,E / ADD A,8 / JR c,restore        ; probe = x+8, bail on overflow
LD A,(HL) / CP 2 / JR z,check          ; state 2 uses x+8
LD A,E / SUB $10 / JR c,restore        ; state 1 uses x-8
check: EX DE,HL / CALL $9E34           ; tile at (probe_x, y)  -- note y, not y-1
CP 4 / JR nz,restore                   ; corn?
LD (HL),0                              ; eat it: clear the tile
LD A,(DE) / ADD A,6 / LD (DE),A        ; state += 6   (1->7, 2->8)
... POP HL / POP HL / POP HL / RET     ; THREE pushes, FOUR pops
```

That pop imbalance is deliberate: it discards its own caller's return address, so
**eating abandons the rest of the update** — the ostrich does not move that turn.

The next update sees state ≥ 7 and takes the `$9160` branch, which does `SUB 6` to
restore the walking state and only redraws. So **eating costs the ostrich two
updates of movement**, which is why they visibly pause at corn.

| State | Meaning |
|---|---|
| 7 | just ate, was walking left — reverts to 1 |
| 8 | just ate, was walking right — reverts to 2 |

### Status: VERIFIED EXACT

`tools/ostrich_diff.py` traces every `CALL $911E` in the simulator — capturing all
five slots, the round-robin index, the PRNG pointer and the level buffer before and
after — and replays each transition through the Python model.

> **900/900 transitions match. The model is exact.**

And the trace exercises every branch, so that number means something:

| Path | Updates |
|---|---|
| empty slot (level 1 uses 2 of 5) | 540 |
| state 1 — walking left | 127 |
| state 2 — walking right | 116 |
| state 3 — climbing down | 72 |
| state 4 — climbing up | 41 |
| states 7/8 — post-eat pause | 4 |
| corn actually eaten (level buffer changed) | 4 |

Two things had to be right for that. First, the PRNG: both random bits in one update
come from **the same ROM byte** (bit 0 gates the ladder/dismount check, bit 1 chooses
which direction to try first). Drawing two independent bits is wrong. Second, eating
had to be modelled, including the abandoned update.

With those fixed, `tools/sim_ostrich.py` — which runs the model against all 8 levels
and checks the ostriches never stand over empty space — goes from **1,019
off-platform states to zero**, on every level, while eating corn as expected.

### Jumping onto a ladder

`$99C1` skips the grounded work while airborne (`JR nz,$99D9`), but **`$99D9
CALL $9E98` — the ladder MOUNT — runs unconditionally**. So the farmer can grab
a ladder mid-jump, and `$9F2E` clears `airbourne`, cancelling the jump. Easy to
miss because the dispatcher *looks* like it is gating everything on airborne.

### Randomness

`$9128` implements a pseudo-random bit: it advances a pointer at `$736C`, forces
`H = 0` so it walks **ROM page 0 (`$0000`–`$00FF`)**, and tests bit 0 of the byte
found there. That bit gates the ladder/dismount check; bit 1 of the *same* byte
picks which direction to try first.

> **The substitute generator must not be an LCG mod 256.** Every such LCG has a
> period-2 least significant bit, and the game gates on **bit 0** while the
> ostrich's `x & 4` *also* flips on every 4px move. The two lock anti-phase and
> the ostrich then never tests for a ladder at all. A `v*73+41` table produced a
> perfect `1010101010…` and left level 2's right-hand ostrich unable to climb
> for its entire life. Use a xorshift and take middle bits — or the real ROM's
> first 256 bytes for exact fidelity. `tools/check_ostrich_levels.py` verifies
> every ostrich on every level reaches a climbing state.

---

## 8. The duck (`$A0C8`) — 2D homing with acceleration

Position at `$7348` (x) / `$7349` (y). And two variables the reference disassembly
lists as **unknown**, which this routine identifies:

> **`$734A` = duck velocity X, `$734B` = duck velocity Y.** Both signed, clamped to
> **±5**.

Each update, per axis independently:

```
if duck_pos < farmer_pos:  vel += 1   (clamp +5)
if duck_pos > farmer_pos:  vel -= 1   (clamp -5, stored as $FB)
if equal:                  vel unchanged
pos += vel
```

Bounds are the same `$EE` limit as the farmer; the move is reverted if it would
exceed them. **The duck ignores terrain entirely — it flies.**

This independently confirms the reference's own observations: it notes `$734A`
"counts up from `$00` to `$05` then remains there" and `$734B` "counts down from
`$FF` to `$FB`". At level start the caged duck (x≈11, game y≈149) is left of and
above Harry (x=100, y=23), so vel_x ramps to **+5** and vel_y to **−5**. Exactly as
described.

It also matches our six chase frames, where the duck's facing tracked Harry **6/6**.

---

## 8a. Tile colour — SOLVED (`$9538` / table `$984F`)

Colour is not in the level data; it is looked up per tile when the attribute file
is refreshed. From `$9555`:

```
LD A,(HL)          ; tile ID from the level buffer
CP 9
JR c,$955E
LD A,6             ; tile >= 9 (the birdcage, $A8-$B5) -> attr 6, hard-coded
JR $9566
$955E: ADD A,$4F / LD B,$98 / LD C,A / LD A,(BC)   ; fetch ($984F + tile)
$9566: LD (DE),A   ; write to the attribute file
```

Table at **`$984F`**, indexed by tile ID:

| Tile | Attr | Ink | Matches measurement |
|---|---|---|---|
| 0 blank | `$06` | yellow (no pixels, so invisible) | — |
| 1 ladder left | `$03` | magenta | ✓ |
| 2 ladder right | `$03` | magenta | ✓ |
| 3 egg | `$07` | white | ✓ |
| 4 corn | `$03` | magenta | ✓ |
| 5 floor | `$04` | green | ✓ |
| ≥ 9 (birdcage) | `$06` | yellow | ✓ |

Every colour we had measured empirically is confirmed by the code.

> **All values are < 64, so the BRIGHT bit is never set.** The whole game renders in
> the *non-bright* palette (`$D7`-level components, not `$FF`). Sampling real frames
> confirms it: floor RGB(0,200,0), ladder (200,0,200), ostrich (0,200,200), Harry
> (200,200,0), HUD paper (200,0,0) with (200,200,200) ink. Rendering with the bright
> set is visibly wrong.

Note `$B130`, which the reference disassembly labels "update the screen colour for the
given coordinate", only writes to `$5800`/`$5820`/`$5840` — the three **HUD**
attribute rows. It has nothing to do with the play area.

## 8b. Sprite selection and animation (`$929C`, `$A17B`)

`$929C` both draws an ostrich **and** performs the player-collision test — on
overlap it does `LD B,6 / POP HL ×6 / RET`, unwinding six return addresses to
abandon everything and kill the farmer.

Its sprite choice is **positional, not a frame counter**:

```
if (A == 4) A = 3                    ; both climb states share base 3
if (A >= 7) use A directly           ; eating
else if (A == 3) { if (y & 4) A++ }  ; climb frames alternate on y & 4
else             { if (x & 4) A += 4 } ; walk frames alternate on x & 4
HL = $900E + A*32                    ; then read BACKWARDS (DE -= 2 per row)
```

Because the draw walks the data backwards, index `i` resolves to the block
starting at **`$8FF0 + i*32`**:

| Index | Block | Used for |
|---|---|---|
| 1 / 5 | ostrich_left / ostrich_left_walk | state 1, alternating on `x & 4` |
| 2 / 6 | ostrich_right / ostrich_right_walk | state 2, alternating on `x & 4` |
| 3 / 4 | ostrich_climbing frames 0 / 1 | states 3–4, alternating on `y & 4` |
| 7 / 8 | ostrich_eating_left / _right | states 7–8 (the post-eat pause) |

So the eating pause is visible: the ostrich bends over the corn for its two
skipped updates.

**The duck** (`$A17B`) picks base frame **8** (right) or **10** (left) by
comparing `farmer_x` against `duck_x` — it always faces the farmer — then adds a
toggle held at **`$734D`**, flipped `XOR 1` on every duck update. At one update
per 12 long passes that is about 5.6 flaps/sec.

## 8c. Corn stalls the clock (`$9A17`)

```
CP 4 / RET nz
LD (HL),0 / LD B,5 / CALL $A3A7        ; clear the tile, score it
LD HL,$FFFF / LD (main_sub_timer),HL   ; ONE 16-bit write hits BOTH sub-timers
```

`$7345` (time, normally reloading `$32`) and `$7346` (bonus, normally `$0A`) are
both set to `$FF`. That is ~5 TIME units and ~25 BONUS steps of delay — roughly
0.7 s where the clock visibly stalls. Missing this makes corn feel pointless.

Eggs (`$99F1`) clear the tile and decrement `cleared_eggs`; when it reaches zero
the routine pops an extra return address to abandon the update — level complete.

## 8c2. Ladder dismount, exactly ($9F60)

Gated on `(y + 1) & 7 == 0`, then from the farmer's own cell index:

```
LEFT:  HL -= 1        RIGHT: HL += 2      (same asymmetry as the walk routine)
       CP 5 / RET nc                      ; target cell passable
HL -= 32   CP 5 / RET nc                  ; one row below passable
HL -= 32   AND A / RET z / CP 9 / RET nc  ; two rows below: 1..8
```

Two things I got wrong first time and that matter:

- the support cell may be **anything in 1..8**, not just floor (5) — ladders and
  eggs count, so requiring floor makes the dismount silently fail;
- LEFT probes `col-1` but RIGHT probes `col+2`.

The valid exits are genuinely sparse. On level 1's main ladder (col 10) they sit
at y = 23, 55, 87, 151 — wherever column 12 has support two rows down.

## 8d. The three ways the farmer dies

All three use the same stack-unwind idiom -- popping return addresses to abandon
the update entirely -- which is easy to mistake for "do nothing".

| Cause | Where | Rule |
|---|---|---|
| Touched by an ostrich | `$929C` / `$9E66` | box `x in (e-8, e+5]`, `y in (d, d+28]` -- asymmetric, 28 tall |
| Touched by the duck | **`$9B9E`** | box `x in (fx-8, fx+8]`, `y in (fy-9, fy+9]` -- **symmetric, and a different shape entirely** |
| Fell through the floor | `$A305` | `CP $10 / JR nc / POP HL / POP HL / RET` -- dropping below y=16 |
| Carried to the top on a lift | `$991E` | while `on_lift`, y is incremented each lift tick; `CP $A5 / RET nc` at y>=165 |

### Ostriches snap to character columns

`$929C` draws two bytes wide from the address `$9404` returns, and that address
comes from `x >> 3` — **the sub-cell x is discarded**. Ostriches move 4px at a
time, so half their positions are `8k+4`; drawing at the raw x leaves them
visibly off ladders and platforms by 4px.

This is also *why* the walk frame alternates on `x & 4`: the sprite cannot shift
sub-cell, so the second frame conveys the half-step. The farmer and duck go
through `$9A4C` instead, which does shift, and are not snapped — which is how
Harry moves smoothly at 1px per long pass.

> The duck and the ostriches do **not** share a collision box. Using the ostrich
> box for the duck makes contact feel wrong -- it is far too tall and offset
> upward. The duck's is a plain +-8 / +-9 window around the farmer.

## 8e. Dying does NOT restore the level

`$A6FE` (FarmerKill) plays the death tune, waits on the ISR at `$A960`, clears
the screen, and then falls into **`$A72D`**, which does:

```
LD HL,level_buffer / LD BC,$02A0 / PUSH HL / POP DE
LD A,(current_player) / (ADD HL,BC) x player
EX DE,HL / LDIR          ; copy the CURRENT buffer into the player's slot
```

`$A7AA` copies it back for the next life. So **eggs and corn you already
collected stay collected across a death** -- only the entities and the timers
reset. Restoring the whole level on death makes it markedly easier than the
original.

Verified by killing the farmer in the simulator (`tools/sim_death.py`):
8 eggs / 8 corn before `FarmerKill`, 8 eggs / 8 corn after the respawn.

## 9. Scoring and timers

- Egg pickup (`$99DF`) is tested at the sprite's **centre**, `(x+8, y−8)`.
- Egg value scales with progress: `n = min(cleared_levels >> 2, 9)`, then a loop adds
  10 `n+1` times. So the per-egg award steps up every 4 levels, capped at the 10th tier.
- `main_sub_timer` `$7345` reloads `$32` (50); one TIME unit = 6500 main-loop iterations.
- **Starting bonus = (level − 1) × 1000 + 990** (verified from the HUD on all 9
  captured levels — see `NOTES-game.md`).

---

## 10. Variable map (farmer block, `IX = $72D8`)

| Addr | IX offset | Name | Meaning |
|---|---|---|---|
| `$72D8` | +0 | `farmer_pos_x` | 1–238 |
| `$72D9` | +1 | `farmer_pos_y` | bottom-up, play area 0–167 |
| `$72DA` | +2 | `anim_frame` | 0–3 |
| `$72DB` | +3 | `farmer_direction` | 0 right, 4 left, 13 climbing |
| `$72DC` | +4 | frame counter | reloads `$82` (130) |
| `$7325` | +$4D | `farmer_airbourne` | 0 grounded, 1 stepping off, 2 in air |
| `$7326` | +$4E | airborne x velocity | `+1` / `0` / `$FF` |
| `$7327` | +$4F | **phase** | per-pixel delay; 140 at take-off |
| `$7328` | +$50 | counter | counts down to trigger a vertical step |
| `$732A` | +$52 | y velocity | `+1` rising, `0` apex, `$FF` falling |
| `$7355` | +$7D | `on_lift` | 1 when riding a lift |

Others: `$7348`/`$7349` duck x/y · `$734A`/`$734B` **duck velocity x/y** ·
`$734E`,`$7351` lift 1 · `$7352`,`$7354` lift 2 · `$7350` lift column (`$FF` = none) ·
`$7356` ostrich round-robin index · `$7357`+ five 4-byte ostrich slots ·
`$736C` PRNG pointer.

---

## 11. Open items

1. ~~Where `$7350` and the ostrich slots are initialised per level.~~ **SOLVED** —
   ostrich table `$945B` (loader `$AF79`), lift table `$9787` (loader `$B0D0`),
   duck pinned by `$A16E`. See `NOTES-game.md`; extracted to `assets/spawns.json`.
2. **Ostrich movement logic itself** — `$9178` onward (the branch past the
   draw/erase) and the helper `$929C` still need decoding. We have the update
   cadence, slot layout, pass rule and randomness source, but not the patrol/turn
   rules or ladder-climbing decisions.
3. **Absolute timing.** Everything is in loop iterations; the real-world rate needs
   measuring against an emulator. This is the first job for the differential harness.
4. Attribute/colour logic at `$B130` — still not decoded, though the palette is
   settled empirically.

## 12. Corrections to send upstream

The reference disassembly is excellent on structure but has two things we can improve:

1. `$9E66` is labelled `FarmerVerticalMovement` / "checks if there's enough headroom
   for a jump". It is actually the **farmer↔entity overlap test** (13×28 box), called
   from `$98B0` across the ostrich slots.
2. `$734A` and `$734B` are marked "Unknown variable". They are the **duck's X and Y
   velocities**, clamped to ±5.

Plus the `$5C3D` answer from `NOTES-game.md` (the 2-byte block loads to `SYSVAR_ERR_SP`,
and `RunGame` depends on it being `$0001`).


---

## Appendix: the enhancement layer

Each enhancement lifts a compromise the Z80 actually made, and none of them
touch the simulation (`tools/test_game.js` asserts 400 frames are identical with
the toggle on and off).

| Enhancement | The compromise it lifts |
|---|---|
| **Smooth ostriches** | `$929C` draws them from a cell-aligned address with no bit rotation, so they lurch 4px between columns. The farmer gets `RR (HL)` in `$9A4C` and moves smoothly; the ostriches were simply not worth the cycles. Rendered by easing toward the **snapped** position -- see the warning below. |
| **Landing dust** | The phase counter at impact already encodes how hard he hit -- 250 is a gentle step-down, 40 is terminal velocity -- so the puff size comes free from `(250 - phase)`. |
| **Footstep dust** | `$9D67` already fires a sound every 4 pixels (`AND 3`); the same cadence drives one dust pixel. |
| **Egg / corn scatter** | `$99F1` and `$9A17` clear the tile; a few palette-coloured pixels sell it. |
| **The teeter** | `$B34C` sets `phase = 4`, a genuine 4-tick hesitation at the lip, and the aligned-overhang perch leaves him stood on nothing. Both become a 1px wobble. |

Particles deliberately stay Spectrum-native: 1x1 and 2x2 blocks, the eight
palette inks only, no alpha (they fade by dropping to blue), integer positions.

Still on the list, held back because they are the ones most likely to tip it out
of the Spectrum look: proper sprite masking (`$930D` is `OR (HL)`, so sprites
merge with the background), per-pixel colour within a cell, extra in-between
animation frames, and non-blocking layered audio (`$9CA4` is a busy-loop that
freezes the game for the duration of every sound).

### The ostriches were ALREADY smooth: the walk sprites are pre-shifted

I got this wrong three times in a row by reasoning about the addressing instead
of looking at the artwork. `$929C` does discard the sub-cell x and draw from a
cell-aligned address -- but **the walk sprites are pre-shifted 4px inside their
16px art cell, which exactly cancels it.**

Verified byte-exactly from the extracted graphics:

```
(ostrich_left  >> 4) == ostrich_left_walk    on rows 3..11   (neck + torso)
(ostrich_right >> 4) == ostrich_right_walk   on rows 3..11
```

Only rows 0-2 (beak open/closed) and 12-15 (legs together/splayed) are genuine
animation. Ink columns:

| Sprite | Ink cols |
|---|---|
| ostrich_left / ostrich_right | 0..7 |
| ostrich_left_walk / ostrich_right_walk | **4..11** |
| ostrich_climbing (both frames) | 4..11 (constant -- a climber's x is always 8k+4) |

> **The invariant: `(x >> 3) * 8` + the art's built-in ink offset (0 or 4) == x,
> always.** The hardware throws the sub-cell x away and the artwork puts it
> straight back. It is the same pre-shifted-sprite trick the farmer gets from
> `RR (HL)` in `$9A4C`, just baked into the pixels instead of computed.

So a walking ostrich glides evenly at 4px per update. **There is no lurch to
fix.** Easing the draw position *adds* one: the art contributes its own
alternating +4, so the body jerks 4px backwards once per stride, 4.5 times a
second. Measured body-delta histograms while walking left:

```
original  {-4: 8, 0: 111}          eased  {-4: 4, -1: 33, 0: 78, +4: 4}
```

Those `+4` entries are a visible moonwalk. The only thing that genuinely snaps
to the character grid is the cyan **attribute** block (`$9332`: one cell wide
when `x & 4 == 0`, two when set) -- a colour artefact, not a position one.

### Eating draws 8px left ($928C)

`$9265` finishes with `CP 8 / JR z / LD A,L / SUB 8 / LD L,A`, so the LEFT-facing
eating frame (state 7) is drawn a whole character further left, letting the
bird's neck reach into the corn cell it just emptied. State 8 (right-facing) is
drawn in place. Without the offset it pecks at empty air.

## Appendix: the enhancement layer

Each enhancement lifts a compromise the Z80 actually made, and none of them
touch the simulation (`tools/test_game.js` asserts 400 frames are identical with
the toggle on and off).

| Enhancement | The compromise it lifts |
|---|---|
| **Smooth ostriches** | `$929C` draws them from a cell-aligned address with no bit rotation, so they lurch 4px between columns. The farmer gets `RR (HL)` in `$9A4C` and moves smoothly; the ostriches were simply not worth the cycles. Rendered by easing toward the **snapped** position -- see the warning below. |
| **Landing dust** | The phase counter at impact already encodes how hard he hit -- 250 is a gentle step-down, 40 is terminal velocity -- so the puff size comes free from `(250 - phase)`. |
| **Footstep dust** | `$9D67` already fires a sound every 4 pixels (`AND 3`); the same cadence drives one dust pixel. |
| **Egg / corn scatter** | `$99F1` and `$9A17` clear the tile; a few palette-coloured pixels sell it. |
| **The teeter** | `$B34C` sets `phase = 4`, a genuine 4-tick hesitation at the lip, and the aligned-overhang perch leaves him stood on nothing. Both become a 1px wobble. |

Particles deliberately stay Spectrum-native: 1x1 and 2x2 blocks, the eight
palette inks only, no alpha (they fade by dropping to blue), integer positions.

Still on the list, held back because they are the ones most likely to tip it out
of the Spectrum look: proper sprite masking (`$930D` is `OR (HL)`, so sprites
merge with the background), per-pixel colour within a cell, extra in-between
animation frames, and non-blocking layered audio (`$9CA4` is a busy-loop that
freezes the game for the duration of every sound).

### Warning: the ostrich cell-snap is load-bearing

Smoothing an ostrich by interpolating its **raw x** breaks two things at once,
and it is not obvious until you look:

1. **Ladder alignment.** The mount at `$91BA` requires `x & 4` to be set, so a
   climbing ostrich *always* sits at `8k+4` -- and `$929C` draws it at `8k`. The
   sprite is meant to be 4px left of the logical position. Drawing at the raw x
   parks it 4px right of the ladder.
2. **The walk cycle.** Logical x steps `8k -> 8k+4 -> 8k+8`, but the snapped draw
   position goes `8k -> 8k -> 8k+8`. The sprite **holds still for one update
   while the leg frame flips**, then moves 8px. The snap and the `x & 4` frame
   alternation are one mechanism: the second frame conveys the half-step the
   sprite cannot make. Interpolating the raw x decouples them and the stride
   stops reading as a stride.

The fix is to ease toward `(x >> 3) * 8`, never `x`. `tools/test_game.js` asserts
a climbing ostrich settles exactly on the snapped cell and never parks at the
`+4` raw-x offset.

**And the stride has to keep its duty cycle.** Two wrong turns were taken here
before the right one; both are worth recording.

*Wrong turn 1 -- ease toward the raw x.* Parks a climbing ostrich 4px beside the
ladder, because the mount forces `x = 8k+4` while `$929C` draws at `8k`.

*Wrong turn 2 -- ease toward the SNAPPED x and key the frame off the eased
position.* The snapped target only changes on the *stand* update:

```
update   logical x   snapped draw x   frame
   0          0             0         stand
   1          4             0         WALK     <- sprite stationary here
   2          8             8         stand    <- ...and gliding here
   3         12             8         WALK
```

...so the sprite glides while showing the stand pose. Keying the frame off the
eased position instead *starves the stride*: the eased value settles on
multiples of 8, where bit 2 is always clear, so the walk pose showed on
**1 frame in 14** against the original's **7 in 14**. Measured with
`tools/anim_strip.js`, which renders consecutive frames as a filmstrip -- the
mistake was reasoning about the numbers instead of looking at the animation.

**What actually works:**

| | Rule |
|---|---|
| Frame | Keep it on the **logical** `x & 4` / `y & 4`, exactly as the original. That is a clean 50/50 alternation once per update, and the original is perfectly happy to show the walk pose while the sprite is stationary. |
| Position, walking | Ease toward **`x - 2`**. The original draws at `8k` for a logical x of `8k` or `8k+4`, so the sprite lags by 0 or 4 -- mean 2. Tracking `x-2` gives the same average placement, moves every update, and so stays in step with the frame flip. |
| Position, climbing | Ease toward **`(x >> 3) * 8`**. x never changes while climbing, so smoothness is irrelevant and only alignment matters. |
| Ease rate | **~18 px/sec** = 4px per 0.2263s, the true speed. Faster and it arrives early and waits, which reintroduces a stutter. |

Result: identical pose cadence (4 stand / 3 walk / 3 stand over the same span),
but gliding where the original teleports.


## Appendix: decorative scenery

Farmyard dressing, drawn behind the tiles and gated on the Enhanced toggle.

**Colour discipline is the whole game here.** The play area already speaks a
language, and any decoration that borrows from it reads as a gameplay object:

| Ink | Already means |
|---|---|
| green | floor |
| magenta | ladders **and** corn |
| white | eggs |
| yellow | birdcage, Harry, lifts, duck |
| cyan | ostriches |
| **red** | HUD paper only -- free in the play area |
| **blue** | unused anywhere -- free |

So scenery uses **blue** for anything structural (it reads as background) and
**red** only for 1-2px accents, where it cannot be mistaken for anything. White
is allowed for single pixels (a 1px fly is not an 8x8 egg). A test asserts the
decoration layer never paints with any other ink.

| Piece | Ink | Placement |
|---|---|---|
| Vines | blue | hang from platform undersides, 2-3 cells, gentle sway |
| Flowers | blue stem, red head | grow on platform tops where two cells are clear |
| Cobwebs | blue | the two top corners of the play area |
| Flies | white 1px | circuits biased toward the corn |
| Dust motes | blue 1px | drift slowly upward, wrap around |
| Feathers | white 1px | shed when the duck's velocity hits the +-5 clamp |
| Mouse | blue | occasional scurry along the bottom floor |
| Straw | blue | flecks on the top edge of the bottom floor, ~1 cell in 3 |
| Window | blue | first clear 2x2 block one row below the top, searched from the right |
| Hens | blue | two per level on platform tops; peck, and flush when the farmer nears |
| Egg glint | white | a cross opening and shutting on one uncollected egg, ~15/min |
| Grass | blue | tufts on platform tops, ~38% of eligible cells; bends when brushed |

Shape matters as well as colour: the first vine art was a straight stem with
symmetric leaves and read as a ladder even in blue. Thin, wandering tendrils
with offset leaves do not. The hens went the same way -- the first pass had a
wide head that read as a mushroom at 8px; the beak had to stick out before the
shape resolved. They are 8x8 against the ostrich's 16x16 and blue against its
cyan, so at no size do the two get confused.

Placement is deterministic per level (`seeded(level)`), so scenery never
shimmers between frames, and everything is rebuilt in place rather than
reassigned so exported references stay valid.

### Wind

One global `gust` value, ramping over a half-sine for 1.6-3.2s roughly every
twelve seconds, drives the vines (up to 3px at the tip), the flower heads, the
straw and the drifting motes and feathers all at once. Nothing else in the
enhancement layer is shared like this, and it is what stops the decorations
reading as a set of unrelated loops -- the screen leans as one place.

Because the gust and a vine's own idle sway are the same size, a test that
samples the layer at different moments is flaky. `setGust` renders all three
readings at a single instant instead.

### Hens

Perched, they double-peck every three seconds. Bring the farmer's middle within
30px of a hen's and it flushes.

**A flush is not a hop from A to B.** The first version interpolated a parabola
from the perch it left to the perch it was going to, and it moved like a frog:
the destination was fixed at take-off, the arc was symmetric, and the drawn
height reversed exactly once. Now the hen does not know where it is going when
it leaves.

| Phase | What it does |
|---|---|
| Burst | up at 96px/s, away from whichever side the farmer came from, two feathers shed |
| Loose | ~0.6s climbing and thrashing -- `vy` steered toward -62, `vx` given a 5.5Hz wobble, air drag on top |
| Homing | picks a perch **from where it has ended up**, then springs onto it |
| Settle | snaps to the perch, 0.7s before it can be flushed again |

The approach is a spring-damper (k=25, c=8, so zeta≈0.8) rather than capped
steering. Capped steering was the second failure: the hen sailed past its perch
and orbited it, never satisfying the arrival test, until the 4s failsafe fired.
A spring decelerates into the spot the way a bird flares onto a branch.

The perch is chosen nearest-first with a heavy penalty for anywhere behind the
hen's current heading -- a bird that turns round and crosses back over the
farmer looks lost rather than startled.

The wingbeat lives in the **drawn** position, not the physics: `sin` of the flap
phase, +-2.2px at 6.5Hz. Without it the steering is smooth and the bird reads as
a paper dart. It also means a test for "does it flap" has to measure the drawn
height, not `vy` -- counting reversals in `vy` was measuring the wrong thing and
happily reported a hop as flight.

Measured over 192 flushes across all eight levels: 1.15s aloft on average, 0.59s
of that flapping loose, 1.52s at worst, every one landing on a real perch and
none leaving the play area.

Two art notes. The raised-wing frame first had the wing tips as loose pixels
above the body and read as a bug with antennae -- the tips have to connect down
to the shoulders. And the hens moved from the back layer to the front, because a
bird flapping across the screen should not disappear behind a platform. Sprites
still draw after both layers, so the farmer passes in front.

### Grass

The one piece the player can push around. Each tuft is 3-5 blades kept as
`(offset, height, tilt)` rather than bitmap frames, drawn as lines with the
offset growing as the *square* of the height -- so the base stays planted and
only the tip swings, which is what grass does. The per-blade `tilt` splays the
tuft at rest; without it three straight verticals read as a fence.

Anything moving horizontally at the same height brushes it: the farmer, the
ostriches and the mouse. A damped spring (k=120, c=6.5) takes it back, peaking
around 0.97 -- just short of the 1.5 clamp -- with one overshoot of about a
third, settling roughly 0.6s after the walker has gone. The gust leans the
whole stand on top of that.

A teleport is not a walk past: `loadLevel` moves the farmer instantly, so
`brush` ignores any step over 12px, and `buildScenery` re-seeds the previous
positions.

Measuring this needs the mouse held off. It runs the bottom floor and brushes
the same tufts, so a test that just walks the farmer past one measures
whichever of the two passed most recently -- which is how it first failed.

### Decoration driven by game state

Two pieces are not ambient. **Feathers** shed only while the duck's velocity is
at its +-5 clamp. The **cage rattles** -- every cell of the birdcage jitters 1px
sideways -- in bursts of about a third of a second, roughly every seven seconds,
and only while `duckFree` is false. Once the duck is out on level 2 the cage
stops for good. Both tie the ornament to something the player can already see,
which is the difference between decoration and wallpaper.

### End of level

`$A675` counts the BONUS into the score a unit at a time, calling `$A685`'s blip
on each, and stopping when it reaches zero. That loop is kept exactly: one blip
per ten points, the authentic pitch, the score climbing as the bonus falls.
Adding the remaining TIME afterwards is **new** -- the original does not tally
it -- as is everything visual.

| Phase | |
|---|---|
| Bonus | 10 points a step, `$A685` blip, a yellow mote flies from the BONUS readout to the SCORE |
| Time | 10 points a step, and the pitch climbs with the count, cyan motes from the TIME readout |
| Done | "LEVEL nn CLEARED", then on |

The rate winds up from 18 to 150 steps a second, so a full board takes about
2.4s of counting inside a 4s sequence. Space skips it and awards the remainder;
nothing is lost either way, and a test checks that.

The digits **roll like an odometer**: a changed digit slides up out of its cell
while the new one comes in from below, over 0.07s, clipped to the 8px band by
`blitCellClip`. The last two digits of the score are therefore permanently
mid-roll while the count is at full speed, which is exactly what a real counter
does. Roll ages advance in `advanceVisuals`, so the speed is time-based rather
than render-call based.

Two things the motes taught me. The score sits **above** the bonus on the
header, so bowing the arc upward at the amplitude I first used threw most of
them off the top of the screen -- the stream looked sparse because half of it
was outside the canvas. And the header is its own colour context: yellow and
cyan are free there (the play area's rule about blue and red does not apply, and
these are not gameplay objects), which is what lets the two phases read apart at
a glance.

The arithmetic never consults the art toggle -- only the motes do -- so the same
score comes out either way.

### The death ghost

Harry is not drawn during the 1.4s death pause -- `render()` only draws him in
`play` and `levelclear` -- so the screen just loses him. A ghost fills that gap:
spawned in `die()` from his last position, it rises about 40px, easing off as it
goes, swayed by the same wind as the vines, and dissolves over the last third.

Two 16x16 frames, alternating at 4Hz. Between them the whole body bobs a row,
the arms lift, the mouth opens and the hem of the sheet waves. The eyes and
mouth are **holes in the ink**, which is the only way to get detail into a
single-colour sprite -- and because nothing is masked (`$930D` is `OR (HL)`, so
the game never masked either), a ladder behind the ghost shows through its eyes.
That is what the hardware would have done.

It is white, which relaxes the "white for single pixels only" rule above. The
justification is that a 16x16 sprite climbing the screen is in no danger of
being read as a static 8x8 egg; yellow would have blended into the lifts and
the cage, and no other ink was free.

The death overlay dims the screen to 72% black, which left the ghost at 28%
brightness -- invisible. The dying message now uses a `.soft` variant at 34%
with a text-shadow instead, and the hard dim returns when the screen settles
into GAME OVER.

`tools/ghost_strip.js` lays the rise out as a filmstrip. The test measures the
drawn top edge by rendering twice with the ghost suppressed and diffing, so it
checks the sprite reaches the canvas and not merely that a flag was set.

`tools/check_deco.js` runs a long game and reports how often each of these
fires; the cage rattle is measured by rendering twice with only the flag
changed and diffing the draw list, so a rattle that never reaches the canvas
still reads as absent.
