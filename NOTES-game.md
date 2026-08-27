# Chuckie Egg — game block analysis (0x8214–0xCBE7)

Reference disassembly: `reference/chuckie-egg-disassembly/` (Michael R. Cook, SkoolKit,
GPL-free but copyrighted as a work — see its README). **Verified against our tape.**

## Verification

`tools/verify_skool.py` cross-checks every `DEFB` line in `chuckie-egg.skool` against
our extracted `04_headerless.bin`:

```
DEFB bytes compared : 9748
mismatching lines   : 0
skipped lines       : 69  (strings/expressions/out-of-range)
```

The disassembly's `.t2s` also independently confirms every address I derived from the
loader: org `$5CCB`, game load `$8214`, STKBOT/STKEND `$5DC0`, start `42000` (`$A410`),
loader block ending `$6323` with 7 trailing bytes.

**Open question in their `.t2s` that we can answer:** they note
`; ram load=4,xxxxx  # load two 2-bytes ($01,$00) - which address?`
From the loader at `$5FA2`: `LD IX,$5C3D / LD DE,$0002 / SCF / LD A,$FF / CALL $0556`
— it loads into **`$5C3D` (SYSVAR_ERR_SP)**. Worth sending upstream.

## Coverage assessment

| Layer | Status |
|---|---|
| Data blocks | 174 byte + 16 word + 17 text + 28 space blocks, all typed |
| Graphics | all tiles/sprites/fonts annotated **with dimensions** |
| Levels | all 8 located and decoding cleanly |
| Named variables | 152 `label=` definitions |
| Code routines | 96, **all titled and cross-referenced** |
| Instruction comments | **~10%** (366 of 3536 lines) |

So: the *data* layer is essentially solved. The *code* layer tells us what each routine
does but usually not how — the physics routines are titled but their bodies are bare.
That is the remaining reverse-engineering work, and it is now well-targeted.

## Verified against a real emulator frame

`actual_screen_render.bmp` (level 1, live game) settled three things that had been
assumption rather than fact. Tools: `compare_screen.py`, `pixel_diff.py`.

### 1. Level rows are stored BOTTOM-UP

The first row in memory is the **bottom** of the screen. Scoring the decode both ways:

```
top-down (as stored)      78/218 cells match  (35.8%)
bottom-up (flipped)      210/218 cells match  (96.3%)
```

`export_gamedata.py` now emits `tiles` already flipped into screen order, keeping
`tiles_raw` for provenance.

### 2. Two different byte layouts are in use

Rendering both interpretations side by side (`extracted/layout_test.png`) was decisive:

| Data | Layout | Evidence |
|---|---|---|
| Tiles, fonts, UI labels | **cell-major** — each 8×8 cell is 8 *consecutive* bytes | perfect birdcage/ladder; corroborated by the disassembly's `#UDGARRAY<n>;<start>-<end>-8` macros, whose step of 8 means exactly this |
| Sprites (`sprites_*`) | **row-major** — interleaved, `cw` bytes per pixel row | perfect farmer and ostrich; matches a blitter that draws a full 16px row at a time |

Each block now carries a `layout` field in `assets/gamedata.json` so no consumer has
to guess.

### 3. The palette (colour is computed at runtime, not stored)

| Element | Colour | Confidence |
|---|---|---|
| Floor | green | 106/106 cells |
| Ladder | magenta | 66/72 (rest occluded by the lift) |
| Corn | magenta | 9/10 |
| Egg | white | 11/12 |
| Birdcage | yellow | 18/18 |
| Farmer (Harry) | yellow | observed |
| Ostrich, lift | cyan | observed |
| HUD | white ink on red paper | observed |

Screen layout is **24 character rows: 3 of HUD, then the 21 play rows**.

### Result

After fixing row order and cell layout, a pixel-level diff of the static tile layer
against the real frame gives:

```
grid fit -> 99.44% of play-area pixels agree
cells differing: 35 of 672
```

and all 35 are accounted for: the duck sprite inside the cage, the ostrich, the cyan
lift on the ladder, Harry's differing position in that frame, and a few 1px
resampling artifacts at the right edge. **The static tile layer is pixel-exact.**

Still to do: entity spawn positions (ostriches, duck, lifts) are not in the level
data — they come from tables we haven't located yet.

## Entity spawns — EXACT (read from the game's own tables)

> **This supersedes the screenshot-derived section below.** Screenshots catch
> entities mid-animation; these are the real spawn values. Tool:
> `tools/extract_spawns.py` → `assets/spawns.json`.

### Y conventions differ by entity

Both verified against 100%-confidence sprite matches:

| Entity | Convention | Drawn by |
|---|---|---|
| Farmer, duck, lifts | `screen_y = 191 − y` | `$9A4C` |
| **Ostriches** | `screen_y = 176 − y` | `$929C` |

The ostrich y sits 15 lower — its draw routine differs. Getting this wrong shifts
every ostrich by 15px, so it matters.

### Farmer

`x = 100, y = 23` → screen **(100, 168)**, identical on all 8 levels.

### Duck — `$A16E`

```
LD A,(cleared_levels) / CP 8 / JR nc,free
LD HL,$9808 / LD ($7348),HL      ; x=8, y=152
```

So the duck is **pinned at x=8, y=152 → screen (8, 39)** for the whole of pass 1,
and released from pass 2 onward, flying from that position. Confirmed by template
matching level 1 and level 4 at **100%** — both give screen (8,39) exactly. (The
earlier (11,42) reading came from level 9, where the duck is *already free* and had
drifted 3px.)

### Ostriches — table `$945B`, loader `$AF79`

`$15` (21) bytes per level, indexed `$945B + $15 × (level + 1)`:

```
[count][5 × (x, y, state, spare)]
```

**Every level defines five ostriches.** The count byte activates 2–4 of them on
passes 1–3; on pass 4+ (`cleared_levels >= 24`) the loader forces the count to `$14`
(20 bytes), activating **all five**. That is literally what "extra ostriches on the
fourth pass" means — the spares are already sitting in the table.

Slots are pre-cleared to `$FF`, and for `8 ≤ cleared_levels < 16` (pass 2) the load
is skipped entirely — no ostriches.

State byte: `1` = walk left, `2` = walk right, `3`/`4` = climbing (provisional,
from matching spawn poses).

| Level | Active | Table | Spawns (screen, active only) |
|---|---|---|---|
| 1 | 2 | `$9470` | (104,40) (72,72) |
| 2 | 3 | `$9485` | (16,168) (72,40) (224,104) |
| 3 | 3 | `$949A` | (16,72) (232,144) (112,48) |
| 4 | 4 | `$94AF` | (40,168) (216,168) (216,40) (120,40) |
| 5 | 4 | `$94C4` | (16,136) (40,104) (40,72) (168,104) |
| 6 | 4 | `$94D9` | (24,168) (24,72) (192,136) (232,72) |
| 7 | 3 | `$94EE` | (200,40) (12,88) (188,112) |
| 8 | 3 | `$9503` | (124,64) (124,128) (160,168) |

This corrects two screenshot counts: **level 5 has 4 ostriches** (I measured 3) and
**level 7 has 3** (I measured 2) — captures missed one each.

### Lifts — table `$9787`, loader `$B0D0`

4 bytes per level, indexed `$9787 + 4 × (level + 1)`:
`[state_lo, state_hi, column_x, lift1_y]`, copied into `$734E`–`$7351`.
`column_x == $FF` means the level has none.

Lift 2 is **derived**, not tabulated: its y is hard-coded `$43` (67) at `$B0EF`, and
its state word is lift 1's **minus `$0800`** — exactly one display third, i.e. the
constant **64-pixel separation** we measured.

| Level | Column x | lift1 y | lift2 y | screen y |
|---|---|---|---|---|
| 1, 2, 8 | `$FF` — none | | | |
| 3 | 64 | 3 | 67 | 188, 124 |
| 4 | 144 | 3 | 67 | 188, 124 |
| 5 | 200 | 3 | 67 | 188, 124 |
| 6 | 120 | 3 | 67 | 188, 124 |
| 7 | 240 | 3 | 67 | 188, 124 |

Cross-checked two ways: decoding the state word `$54E8` as a display-file address
gives screen (64, 188), and `$54E8 − $0800` gives (64, 124) — exactly `191 − 3` and
`191 − 67`. Both screenshot measurements were **6px risen**, consistently, because
the lifts start moving immediately.

---

## Entity positions from screenshots (superseded — kept for provenance)

`screenshots/level1..8.png` are level-start captures; `level9-bird1..6.png` show the
duck chase on pass 2. Tools: `analyse_shots.py`, `extract_entities.py`, `read_hud.py`.
Output: `assets/entities.json`, merged into `gamedata.json` as `levels[].spawns`.

Geometry is exact here — the shots are a clean 2× of a 320×256 bordered frame, so the
256×192 screen sits at (64,64) with 16px cells. No sub-pixel fitting needed.

**All 8 layouts re-verified** against these independent captures: 93.6%–100% cell
agreement, every miss traced to an entity occluding a tile.

### Harry

**Spawns at pixel (100,168), `sprites_farmer_right` frame 0, on every single level** —
100% template match all eight times. A hard constant.

Note the detection subtlety: on level 4 he spawns in front of the main magenta ladder,
so a colour-anomaly detector misses him entirely (magenta stays dominant). Template
matching the sprite bitmap finds him regardless. Worth remembering for future frames.

### Ostriches (cyan, 16×16)

| Level | Count | Spawns (screen px, with pose) |
|---|---|---|
| 1 | 2 | (104,40) right_walk · (64,72) left |
| 2 | 3 | (80,40) right · (232,104) right · (16,168) right_walk |
| 3 | 3 | (112,48) right_walk · (8,72) eating_left · (224,144) left_walk |
| 4 | 4 | (120,40) right_walk · (216,40) right_walk · (32,168) left_walk · (216,168) right_walk |
| 5 | 3 | (32,72) left_walk · (40,104) right_walk · (168,104) right_walk |
| 6 | 4 | (16,72) eating_left · (232,72) right_walk · (184,136) left_walk · (16,168) left_walk |
| 7 | 2 | (8,84) climbing · (184,116) climbing |
| 8 | 3 | (120,60) climbing · (120,124) climbing · (152,168) eating_left |

Each spawns in a *specific pose*, not a default one.

### Lifts

**Not a sprite** — a solid 16×4 yellow filled rectangle (exactly 64 lit pixels), which
is why no lift graphic exists in the graphics blocks. Levels **3–7 only**; 1, 2 and 8
have none. Always **two per level, in the same column, exactly 64px apart vertically**
— they cycle in a shaft.

| Level | Column x | y positions |
|---|---|---|
| 3 | 64 | 118, 182 |
| 4 | 144 | 118, 182 |
| 5 | 200 | 120, 184 |
| 6 | 120 | 118, 182 |
| 7 | 240 | 119, 183 |

The small y variation between levels is capture timing — the 64px separation is exact
in every case.

### The duck

Sits caged at px **(11,42)** until released. On pass 2 (level 9+) it flies free and
homes on Harry. From the six chase frames, its facing tracks Harry **6/6**:

| Frame | Duck | Harry | Δx | Faces |
|---|---|---|---|---|
| bird1 | (11,42) caged | (100,168) | — | — |
| bird2 | (63,94) | (180,168) | +117 | right ✓ |
| bird3 | (117,169) | (50,168) | −67 | left ✓ |
| bird4 | (98,66) | (168,40) | +70 | right ✓ |
| bird5 | (143,40) | (219,43) | +76 | right ✓ |
| bird6 | (163,125) | (112,136) | −51 | left ✓ |

It moves freely in both axes and ignores platforms — it flies. Level 9 confirms the
8-level rotation (same layout as level 1) and the documented "pass 2 = duck, no
ostriches" rule: **zero ostriches detected**.

## HUD

Read back exactly using the game's own extracted fonts (`read_hud.py`).

- **Screen is 24 character rows: 3 HUD, then 21 play rows.**
- Row 0: `SCORE` label + 6 digits — **uses `font_all` ($85F0), digits at glyph index 16**
- Row 1: lives icons (yellow)
- Row 2: `PLAYER`/`LEVEL`/`BONUS`/`TIME` — **uses `font_numbers_bold` ($89E0)**
- White ink on red paper throughout.

**Starting bonus = (level − 1) × 1000 + 990.** Verified on all 9 distinct levels:
990, 1990, 2990, 3990, 4990, 5990, 6990, 7990, 8990. Starting time is 900.

Observed score progression across the captured playthrough (levels 1→9):
0, 2330, 5240, 8440, 13440, 20170, 28040, 36770, 47780.

## Level data — SOLVED

- Base `$B3B0`, stride `$2A0` (672 bytes), 8 levels → `$B3B0, $B650, $B8F0, $BB90,
  $BE30, $C0D0, $C370, $C610`
- Grid is **32 × 21** tiles (672 = 32 × 21)
- Level buffer (working copy) at `$61A8`, same `$2A0` size

Tile IDs:

| Byte | Meaning |
|---|---|
| `00` | blank |
| `01` | ladder (left half) |
| `02` | ladder (right half) |
| `03` | egg |
| `04` | corn / seed |
| `05` | floor / platform |
| `A8`–`A9` | birdcage handle |
| `AA`–`B5` | birdcage body (12 cells: 4 top, 4 middle, 4 bottom) |

`tools/levels.py` decodes all 8. Every level yields **exactly 12 eggs**, matching the
game rule, with **zero unrecognised bytes** — strong evidence the format is fully
understood. Corn counts: 10, 7, 10, 6, 13, 10, 4, 38.

## Graphics map

Tiles / UI (8×8 unless noted):

| Addr | Contents |
|---|---|
| `$84F0` | blank tile |
| `$84F8` | ladder tile (16×8) |
| `$8508` | egg |
| `$8510` | corn |
| `$8518` | floor |
| `$8548` | "SCORE" label (24×8) |
| `$8560` | "PLAYER" label (32×8) |
| `$8580` | "TIME" label (24×8) |
| `$85C8` | "BONUS" label (24×8) |
| `$85F0` | font: A–Z, digits, punctuation |
| `$87F8` | font: A–Z bold |
| `$88E0` | registered symbol |
| `$88E8` | copyright symbol |
| `$8968` | "A+F CHUCKIE EGG" text graphic |
| `$89C8` | "LEVEL" label (24×8) |
| `$89E0` | font: bold numbers |
| `$8A30` | birdcage handle tiles (16×8) |
| `$8A40` | birdcage tiles (32×24) |
| `$8AA0` | lives icon |
| `$8AA8` | high-score cursor icon |
| `$8AB0`–`$8B30` | instruction-screen headings (UP/DOWN/LEFT/RIGHT/JUMP/TYPE) |

Sprites:

| Addr | Contents |
|---|---|
| `$8DF0` | farmer animation, right-facing |
| `$8E70` | farmer animation, left-facing |
| `$8F90` | farmer animation, climbing |
| `$8EF0` | duck, right-facing (2 × 16×16) |
| `$8F30` | duck, left-facing (2 × 16×16) |
| `$9010` | ostrich left-facing (16×16) |
| `$9030` | ostrich right-facing (16×16) |
| `$9050` | ostrich climbing (2 × 16×16) |
| `$9090` / `$90B0` | ostrich walking left / right |
| `$90D0` / `$90F0` | ostrich eating left / right |

## Key routines for a port

Rendering:
- `$93DD` get screen+attribute address of a sprite
- `$9404` get screen address of a sprite
- `$9438` get the gfx at a location
- `$9538` redraw attributes after a sprite moves
- `$9A4C` fetch and draw a sprite
- `$B130` update screen colour for a coordinate

Gameplay / physics — **these are the ones needing deeper annotation**:
- `$9D08` left/right input and player movement
- `$9E66` check headroom for a jump
- `$9E98` / `$9F60` move player onto / off a ladder
- `$A21C` check if the farmer is falling
- `$A256` move player up/down after jumping or falling
- `$A294` make the player fall if in mid-air
- `$A30C` farmer jumping/falling
- `$A014` move the lifts
- `$A0C8` move the mother duck
- `$911E` move the ostriches
- `$A3A7` update the score
- `$9C9C` IM2 interrupt entry point

Working state:
- `$72A0` sprite buffer, `$72DD` sprite/background composition buffer
- `$6EC8` current score (6 BCD-ish digit bytes), `$6ECE`–`$6EE5` per-player scores
- `$6EE6` cleared eggs, `$6EEB` cleared levels, `$6EF0`–`$6EF3` lives per player
- player position tracked around `$72D8`/`$72D9`, state flags `$7326`/`$732A`

## Game rules (from the reference README)

- 12 eggs per level; 8 unique levels rotating indefinitely
- Pass 2: duck uncaged, no ostriches
- Pass 3: both duck and ostriches
- Pass 4: additional ostriches
- Pass 5: duck and ostriches move faster
- After 40 levels, the last 8 repeat indefinitely
